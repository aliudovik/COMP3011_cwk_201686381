# app/jobs/tasks.py
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Union

from flask import current_app

from app.crypto import TokenCipher
from app.extensions import db
from app.models import Generation, ListenerProfile, OAuthToken, ProviderAccount, TrackCandidate, User
from app.services.openai_prompt import generate_suno_payload_with_openai
from app.services.profile_builder import build_profile
from app.services.suno_client import SunoClient
from app.services.providers.spotify import SpotifyProvider
from datetime import datetime, timezone
from app.services.providers.youtube import YouTubeProvider

# IMPORTANT: must match your OAuth app redirect
SPOTIFY_REDIRECT_URI = "https://drvibey.com/callback/spotify"


# -----------------------------------------------------------------------------
# App-context helper (works both in-request and in background worker)
# -----------------------------------------------------------------------------
def _run_in_app_context(fn, *args, **kwargs):
    """
    If called inside a request, uses the existing Flask app.
    If called from a worker with no app context, creates an app like the old version.
    """
    app = None
    try:
        app = current_app._get_current_object()
    except Exception:
        app = None

    app = app or kwargs.pop("app", None)

    if app is None:
        # Import here to avoid circular imports at module import time
        from app import create_app
        app = create_app()

    with app.app_context():
        return fn(*args, **kwargs)


# =============================================================================
# JS-friendly dispatch wrappers (match your frontend payloads)
# =============================================================================



def api_ingest(user_id: int, provider: str, source: str = "top", provider_account_id: Optional[int] = None):
    """
    Routes ingestion requests to the correct provider.
    Supported:
      - Spotify (top_tracks)
      - YouTube (liked_videos)
    """
    provider_n = (provider or "").strip().lower()
    source_n = (source or "").strip().lower()

    # --- SPOTIFY HANDLER ---
    if provider_n == "spotify":
        if source_n in ("top", "top_tracks", "top-tracks"):
            # Assuming this function already exists in your code
            return ingest_spotify_top_tracks(user_id=user_id, provider_account_id=provider_account_id)
        else:
            return {"ok": False, "error": f"Spotify source '{source}' not supported. Use 'top_tracks'."}

    # --- YOUTUBE HANDLER ---
    elif provider_n == "youtube":
        # We map 'liked', 'liked_videos', or 'top' to the liked videos logic
        if source_n in ("liked", "liked_videos", "top"):
            return ingest_youtube_liked_videos(user_id=user_id, provider_account_id=provider_account_id)
        else:
            return {"ok": False, "error": f"YouTube source '{source}' not supported. Use 'liked_videos'."}

    # --- UNSUPPORTED ---
    else:
        return {"ok": False, "error": f"Unsupported provider '{provider}'"}

def ingest_youtube_liked_videos(user_id: int, provider_account_id: Optional[int] = None):
    """
    Connects to YouTube, fetches liked music videos, and stores them as TrackCandidates.
    """
    # 1. Fetch credentials
    # Assuming you store these in .env like SPOTIFY_CLIENT_ID
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "https://drvibey.com/callback/youtube")

    if not client_id or not client_secret:
        return {"ok": False, "error": "Server missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET"}

    query = db.session.query(ProviderAccount).filter_by(user_id=user_id, provider="youtube")

    # 2. Get account
    pa = query.order_by(ProviderAccount.created_at.desc()).first()
    if not pa:
        return {"ok": False, "error": "User has not linked a YouTube account."}

    # ✅ use OAuthToken (NOT pa.access_token)
    access_token = _youtube_access_token_from_oauth(pa.id)
    if not access_token:
        return {"ok": False, "error": "YouTube token not found"}

    # 3. Initialize Provider and Fetch
    yt_provider = YouTubeProvider(client_id, client_secret, redirect_uri)
    tracks = yt_provider.ingest_liked_music_videos(access_token=access_token, limit=50)



    if not tracks:
        return {"ok": True, "count": 0, "msg": "No music videos found in recent likes."}

    # 4. Save to DB (TrackCandidate)
    count = 0
    for t in tracks:
        # YouTube data mapping
        track_name = t.get("name")
        # safely get first artist name
        artist_name = "Unknown"
        if t.get("artists") and isinstance(t["artists"], list):
            artist_name = t["artists"][0].get("name", "Unknown")

        # Create Candidate
        tc = TrackCandidate(
            provider_account_id=pa.id,
            source="youtube_liked",
            provider_track_id=t.get("id"),
            title=track_name[:200],
            artists=artist_name[:200],
            album="YouTube",
            # YouTube ID is useful to prevent dupes
            isrc=t.get("id"),
            duration_ms=0,
            popularity=50
        )
        db.session.add(tc)
        count += 1

    db.session.commit()
    return {"ok": True, "count": count, "provider": "youtube"}

def api_profile_rebuild(user_id: int, provider_account_id: Optional[int] = None):
    """
    Matches JS:
      POST /api/profile/rebuild
    """
    return rebuild_profile_pipeline(user_id=user_id, provider_account_id=provider_account_id)


def api_generate(
        user_id: int,
        mood: Optional[Union[str, int]],
        instrumental: bool,
        custom_mode: bool,
        title_hint: str,
        style_hint: str,
):
    """
    Matches JS:
      POST /api/generate body: { mood, instrumental, custom_mode, title_hint, style_hint }
    Returns: { ok, generation_id }
    """
    return _run_in_app_context(
        _api_generate_impl,
        user_id,
        mood,
        instrumental,
        custom_mode,
        title_hint,
        style_hint,
    )


def _api_generate_impl(
        user_id: int,
        mood: Optional[Union[str, int]],
        instrumental: bool,
        custom_mode: bool,
        title_hint: str,
        style_hint: str,
):
    # 1) get latest listener profile
    lp = (
        ListenerProfile.query
        .filter_by(user_id=user_id)
        .order_by(ListenerProfile.created_at.desc())
        .first()
    )
    if not lp or not lp.profile_json:
        return {"ok": False, "error": "No listener profile found. Build profile first."}

    # 2) Cerebras prompt FIRST
    mood_id = (str(mood).strip().lower() if mood is not None else "energetic") or "energetic"
    # CHANGE: Get Cerebras Key
    cerebras_api_key = current_app.config.get("CEREBRAS_API_KEY", "") or os.getenv("CEREBRAS_API_KEY", "")

    out = generate_suno_payload_with_openai(
        cerebras_api_key=cerebras_api_key,  # <--- New param name
        model=current_app.config.get("LLM_MODEL", "llama-3.3-70b"),  # <--- Pass the model
        profile_json=lp.profile_json,
        mood_id=mood_id,
        instrumental=bool(instrumental),
        custom_mode=bool(custom_mode),
        title_hint=(title_hint or "").strip(),
        style_hint=(style_hint or "").strip(),
    )

    # 3) Insert valid Generation row (openai_prompt + suno_request are NOT NULL in your model)
    gen = Generation(
        user_id=user_id,
        listener_profile_id=lp.id,
        mood=mood_id,
        openai_prompt=out.get("openai_prompt") or {},
        suno_request=out.get("suno_payload") or {},
        status="queued",
    )

    db.session.add(gen)
    db.session.commit()

    queued_via = _enqueue_generation_job(gen.id)

    return {
        "ok": True,
        "generation_id": gen.id,
        "status": gen.status,
        "queued_via": queued_via,
    }


def api_get_generation(user_id: int, generation_id: int):
    """
    Matches JS poll:
      GET /api/generation/<id>
    Returns: { status, result, error? }
    """
    return _run_in_app_context(_api_get_generation_impl, user_id, generation_id)


def _api_get_generation_impl(user_id: int, generation_id: int):
    gen: Optional[Generation] = db.session.get(Generation, generation_id)
    if not gen or getattr(gen, "user_id", None) != user_id:
        return {"ok": False, "status": "not_found", "error": "Generation not found"}

    result = getattr(gen, "result_json", None) or {}
    status = getattr(gen, "status", None) or "unknown"

    error = None
    if status == "failed":
        if isinstance(result, dict) and result.get("error"):
            error = str(result.get("error"))
        else:
            error = "Generation failed"

    return {
        "ok": True,
        "generation_id": gen.id,
        "status": status,
        "result": result,
        "error": error,
    }


def _enqueue_generation_job(generation_id: int) -> str:
    """
    Try RQ, then Celery, else dev-only background thread.
    """
    try:
        rq_ext = current_app.extensions.get("rq")
        if rq_ext:
            if hasattr(rq_ext, "get_queue"):
                q = rq_ext.get_queue()
                q.enqueue(run_generation_pipeline, generation_id)
            else:
                rq_ext.enqueue(run_generation_pipeline, generation_id)
            return "rq"
    except Exception:
        pass

    try:
        celery_ext = current_app.extensions.get("celery")
        if celery_ext and hasattr(celery_ext, "send_task"):
            celery_ext.send_task("app.jobs.tasks.run_generation_pipeline", args=[generation_id])
            return "celery"
    except Exception:
        pass

    try:
        t = threading.Thread(target=run_generation_pipeline, args=(generation_id,), daemon=True)
        t.start()
        return "thread"
    except Exception:
        return "none"


# =============================================================================
# Ingest: Spotify (metadata only) — UPDATED TOKEN HANDLING (OAuthToken + TokenCipher)
# =============================================================================

def _spotify_access_token_from_oauth(provider_account_id: int) -> Optional[str]:
    """
    OLD behavior:
      tok = OAuthToken.query.filter_by(provider_account_id=acct.id).first()
      cipher = TokenCipher(current_app.config["TOKEN_ENC_KEY"])
      access_token = cipher.decrypt(tok.access_token_enc)
    """
    tok = OAuthToken.query.filter_by(provider_account_id=provider_account_id).first()
    if not tok:
        return None

    access_token_enc = getattr(tok, "access_token_enc", None)
    if not access_token_enc:
        return None

    key = current_app.config.get("TOKEN_ENC_KEY") or os.getenv("TOKEN_ENC_KEY")
    if not key:
        # match old assumption: key must exist
        raise RuntimeError("TOKEN_ENC_KEY is not set (needed to decrypt OAuthToken.access_token_enc)")

    cipher = TokenCipher(key)
    try:
        return cipher.decrypt(access_token_enc)
    except Exception:
        return None


def _youtube_access_token_from_oauth(provider_account_id: int) -> Optional[str]:
    tok = OAuthToken.query.filter_by(provider_account_id=provider_account_id).first()
    if not tok or not tok.access_token_enc:
        return None

    key = current_app.config.get("TOKEN_ENC_KEY") or os.getenv("TOKEN_ENC_KEY")
    if not key:
        raise RuntimeError("TOKEN_ENC_KEY missing")

    cipher = TokenCipher(key)
    return cipher.decrypt(tok.access_token_enc)


def _spotify_access_token_from_account(acc: ProviderAccount) -> Optional[str]:
    """
    Fallback only (kept so we don't break any alternate schema you might have).
    """
    for attr in ("access_token", "oauth_access_token", "token", "token_access"):
        v = getattr(acc, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()

    for attr in ("token_json", "oauth_json", "provider_json", "data", "meta", "payload"):
        d = getattr(acc, attr, None)
        if isinstance(d, dict):
            v = d.get("access_token") or d.get("token") or d.get("spotify_access_token")
            if isinstance(v, str) and v.strip():
                return v.strip()

    return None


def _normalize_spotify_track_item(item: dict) -> dict:
    title = (item.get("name") or "").strip()

    artists_list = item.get("artists") or []
    artists = ", ".join([(a.get("name") or "").strip() for a in artists_list if isinstance(a, dict)]).strip()

    album_obj = item.get("album") or {}
    album = (album_obj.get("name") or "").strip() if isinstance(album_obj, dict) else None

    external_ids = item.get("external_ids") or {}
    isrc = None
    if isinstance(external_ids, dict):
        isrc = (external_ids.get("isrc") or "").strip() or None

    return {
        "id": item.get("id"),
        "provider_track_id": item.get("id"),
        "title": title,
        "artists": artists,
        "album": album or None,
        "duration_ms": item.get("duration_ms"),
        "isrc": isrc,
        "popularity": item.get("popularity"),
        "observed_at": datetime.now(timezone.utc),
    }


def ingest_spotify_top_tracks(user_id: int, provider_account_id: Optional[int] = None):
    return _run_in_app_context(_ingest_spotify_top_tracks_impl, user_id, provider_account_id)


def _ingest_spotify_top_tracks_impl(user_id: int, provider_account_id: Optional[int]):
    q = ProviderAccount.query.filter_by(user_id=user_id, provider="spotify")
    if provider_account_id:
        q = q.filter_by(id=provider_account_id)

    accounts = q.all()
    if not accounts:
        return {"ok": False, "error": "No Spotify provider account linked."}

    client_id = (
            current_app.config.get("SPOTIFY_CLIENT_ID", "")
            or os.getenv("SPOTIFY_CLIENT_ID", "")
            or ""
    ).strip()

    if not client_id:
        return {"ok": False, "error": "SPOTIFY_CLIENT_ID not set"}

    upserted = 0
    skipped_no_token: List[int] = []

    # Provider must be created with (client_id, redirect_uri) per spotify.py
    provider = SpotifyProvider(client_id, SPOTIFY_REDIRECT_URI)

    for acc in accounts:
        # NEW: match old token handling first (OAuthToken + decrypt)
        access_token = _spotify_access_token_from_oauth(acc.id)

        # fallback only
        if not access_token:
            access_token = _spotify_access_token_from_account(acc)

        if not access_token:
            skipped_no_token.append(acc.id)
            continue

        # spotify.py returns raw items list
        raw_items = provider.ingest_top_tracks(access_token=access_token, limit=50, time_range="medium_term")
        tracks: List[dict] = []
        if isinstance(raw_items, list):
            for it in raw_items:
                if isinstance(it, dict):
                    tracks.append(_normalize_spotify_track_item(it))

        for t in tracks:
            provider_track_id = t.get("provider_track_id") or t.get("id")
            if not provider_track_id:
                continue

            title = (t.get("title") or "").strip()
            artists = (t.get("artists") or "").strip()
            if not title or not artists:
                continue

            cand = TrackCandidate.query.filter_by(
                provider_account_id=acc.id,
                provider_track_id=provider_track_id,
            ).first()

            if not cand:
                cand = TrackCandidate(
                    provider_account_id=acc.id,
                    provider_track_id=provider_track_id,
                    source="spotify_top_tracks",
                )
                db.session.add(cand)

            cand.title = title
            cand.artists = artists
            cand.album = (t.get("album") or "").strip() if isinstance(t.get("album"), str) else t.get("album")
            cand.album = cand.album or None
            cand.duration_ms = t.get("duration_ms")
            cand.isrc = (t.get("isrc") or "").strip() or None
            cand.popularity = t.get("popularity")
            cand.observed_at = t.get("observed_at") or datetime.now(timezone.utc)

            upserted += 1

    if upserted == 0 and skipped_no_token:
        # match old behavior: token not found is a hard failure
        return {
            "ok": False,
            "error": "token not found for linked spotify account(s)",
            "provider_account_ids": skipped_no_token,
        }

    db.session.commit()
    out = {"ok": True, "upserted": upserted}
    if skipped_no_token:
        out["skipped_no_token_provider_account_ids"] = skipped_no_token
    return out


# =============================================================================
# Taste Profile: OpenAI metadata inference
# =============================================================================

def build_profile_for_user(user_id: int):
    return _run_in_app_context(_build_profile_for_user_impl, user_id)


def _build_profile_for_user_impl(user_id: int):
    # CHANGE: Get Cerebras Key and Model
    cerebras_api_key = current_app.config.get("CEREBRAS_API_KEY", "") or os.getenv("CEREBRAS_API_KEY", "")
    llm_model = current_app.config.get("LLM_MODEL", "llama-3.3-70b")

    result = build_profile(
        user_id=user_id,
        session=db.session,
        cerebras_api_key=cerebras_api_key,  # <--- New param name
        model=llm_model,  # <--- New param name
        max_tracks=200,
    )

    lp = (
        ListenerProfile.query
        .filter_by(user_id=user_id)
        .order_by(ListenerProfile.created_at.desc())
        .first()
    )
    if not lp:
        lp = ListenerProfile(user_id=user_id)
        db.session.add(lp)

    lp.built_from_track_count = result.get("built_from_track_count") or 0
    lp.profile_json = result.get("profile_json") or {}
    lp.explain_json = result.get("explain_json") or {}
    db.session.commit()

    return {"ok": True, "built_from_track_count": lp.built_from_track_count}


def rebuild_profile_pipeline(user_id: int, provider_account_id: Optional[int] = None):
    return _run_in_app_context(_rebuild_profile_pipeline_impl, user_id, provider_account_id)


def _rebuild_profile_pipeline_impl(user_id: int, provider_account_id: Optional[int]):
    ingest_res = _ingest_spotify_top_tracks_impl(user_id=user_id, provider_account_id=provider_account_id)
    prof_res = _build_profile_for_user_impl(user_id=user_id)
    return {"ok": bool(ingest_res.get("ok") and prof_res.get("ok")), "ingest": ingest_res, "profile": prof_res}


# =============================================================================
# Generation pipeline: OpenAI prompt -> local suno-api
# =============================================================================

def run_generation_pipeline(generation_id: int):
    return _run_in_app_context(_run_generation_pipeline_impl, generation_id)


def _run_generation_pipeline_impl(generation_id: int):
    gen: Generation = db.session.get(Generation, generation_id)
    if not gen:
        return {"ok": False, "error": f"Generation {generation_id} not found"}

    gen.status = "running"
    db.session.commit()

    # ------------------------------------------------------------------
    # NEW: Use the stored Suno request that /api/generate already created
    # ------------------------------------------------------------------
    suno_payload = getattr(gen, "suno_request", None) or {}

    # normalize to dict
    if not isinstance(suno_payload, dict):
        suno_payload = {}

    prompt = (suno_payload.get("prompt") or "").strip()

    # Optional fallback (only for older/bad rows that somehow have empty suno_request)
    if not prompt:
        # You can either FAIL here (strict pipeline) or do a fallback.
        # Fallback shown below (safe for legacy rows):
        lp = ListenerProfile.query.filter_by(user_id=gen.user_id).first()
        if not lp or not lp.profile_json:
            gen.status = "failed"
            gen.result_json = {"error": "No listener profile found. Build profile first."}
            db.session.commit()
            return {"ok": False, "error": "No listener profile"}

        cerebras_api_key = current_app.config.get("CEREBRAS_API_KEY", "") or os.getenv("CEREBRAS_API_KEY", "")

        mood_id = (getattr(gen, "mood", None) or "energetic")
        mood_id = str(mood_id) if mood_id is not None else "energetic"

        out = generate_suno_payload_with_openai(
            cerebras_api_key=cerebras_api_key,  # <--- New param name
            model=current_app.config.get("LLM_MODEL", "llama-3.3-70b"),  # <--- Pass the model
            profile_json=lp.profile_json,
            mood_id=mood_id,
            instrumental=bool(getattr(gen, "instrumental", False)),
            custom_mode=bool(getattr(gen, "custom_mode", False)),
            title_hint=getattr(gen, "title", "") or "",
            style_hint=getattr(gen, "style", "") or "",
        )

        gen.openai_prompt = out.get("openai_prompt") or {}
        suno_payload = out.get("suno_payload") or {}
        gen.suno_request = suno_payload
        db.session.commit()

        prompt = (suno_payload.get("prompt") or "").strip()

    # If still empty, fail
    if not prompt:
        gen.status = "failed"
        gen.result_json = {"error": "Missing/empty prompt in suno_request."}
        db.session.commit()
        return {"ok": False, "error": "Empty prompt"}

    suno_base_url = current_app.config.get("SUNO_BASE_URL", "") or os.getenv("SUNO_BASE_URL", "https://api.sunoapi.org")
    suno_api_key = current_app.config.get("SUNO_API_KEY", "") or os.getenv("SUNO_API_KEY", "")

    suno = SunoClient(
        base_url=suno_base_url,
        api_key=suno_api_key,  # Changed from default_cookie to api_key
        timeout_s=int(os.getenv("SUNO_TIMEOUT_S", "180")),
    )

    prompt = (suno_payload.get("prompt") or "").strip()
    if not prompt:
        gen.status = "failed"
        gen.result_json = {"error": "OpenAI prompt generation returned empty prompt."}
        db.session.commit()
        return {"ok": False, "error": "Empty prompt"}

    # --- NEW V1 LOGIC START ---

    # 1. Prepare parameters for the V1 API
    # Handle both old format ("make_instrumental") and new format ("instrumental")
    is_instrumental = bool(
        suno_payload.get("instrumental")
        or suno_payload.get("make_instrumental")
        or getattr(gen, "instrumental", False)
    )
    custom_mode = bool(
        suno_payload.get("custom_mode")
        or suno_payload.get("customMode")
        or getattr(gen, "custom_mode", False)
    )

    # Ensure strings are valid (defaults if None)
    style = (suno_payload.get("style") or getattr(gen, "style", "") or "").strip()
    title = (suno_payload.get("title") or getattr(gen, "title", "") or "").strip()

    # Normalize model name - handle old format and ensure valid V1 API model
    raw_model = (
            suno_payload.get("model")
            or suno_payload.get("desired_model")
            or current_app.config.get("SUNO_MODEL", "V5")
    )
    # Map common variations to valid API model names
    model_map = {
        "V5": "V5",  # V5 doesn't exist yet, use V4_5ALL
        "4.5-ALL": "V5",
        "V45ALL": "V5",
        "V4.5": "V5",
        "V4": "V5",
        "V3": "V5",
    }
    normalized = str(raw_model).upper().replace("_", "").replace(".", "").replace("-", "")
    model = model_map.get(normalized, raw_model)
    if model not in ("V4_5ALL", "V4", "V3_5", "V3"):
        model = "V5"  # Safe default

    # The new API mandates a callback URL.
    # Use your app's base URL + a callback route (even if you don't have the route handlers yet, it's required)
    app_base = current_app.config.get("APP_BASE_URL", "http://127.0.0.1:7777")
    callback_url = f"{app_base}/callback"

    # Log the actual request being sent for debugging
    import logging
    logging.info(
        f"Suno API Request: prompt={len(prompt)} chars, model={model}, instrumental={is_instrumental}, custom_mode={custom_mode}")

    try:
        # 2. Call the updated SunoClient.generate()
        response = suno.generate(
            prompt=prompt,
            is_instrumental=is_instrumental,
            custom_mode=custom_mode,
            style=style,
            title=title,
            model=model,
            callback_url=callback_url
        )
    except Exception as e:
        gen.status = "failed"
        gen.result_json = {"error": "Suno API call failed", "details": str(e)}
        db.session.commit()
        return {"ok": False, "error": str(e)}

    # 3. Parse the V1 response: {"code": 200, "msg": "...", "data": {"taskId": "..."}}
    data = response.get("data", {})
    task_id = data.get("taskId")

    if not task_id:
        gen.status = "failed"
        gen.result_json = {"error": "Suno API returned no taskId", "response": response}
        db.session.commit()
        return {"ok": False, "error": "No taskId returned"}

    # 4. Update Generation record
    gen.suno_job_id = str(task_id)
    gen.result_json = {"taskId": task_id, "initial_response": response}
    gen.status = "running"
    db.session.commit()

    # --- NEW: poll official record-info endpoint (callbacks won't reach localhost) ---
    poll_attempts = int(os.getenv("SUNO_POLL_ATTEMPTS", "60"))
    poll_sleep_s = int(os.getenv("SUNO_POLL_SLEEP_S", "10"))

    try:
        details = suno.poll_until_stream_ready(
            task_id=str(task_id),
            attempts=poll_attempts,
            sleep_s=poll_sleep_s,
        )
    except Exception as e:
        # keep running but store polling error so UI can show it
        gen.result_json = {
            **(gen.result_json or {}),
            "poll_error": str(e),
        }
        db.session.commit()
        return {"ok": True, "taskId": task_id, "status": gen.status}

    data = details.get("data") or {}
    suno_status = (data.get("status") or "").upper()

    # Store latest details for UI/debug
    gen.result_json = {
        **(gen.result_json or {}),
        "record_info": details,
        "suno_status": suno_status,
    }

    # Check if we have streaming URL available (regardless of status)
    response = data.get("response") or {}
    tracks = response.get("sunoData") or response.get("data") or []
    has_stream_url = False
    if tracks and len(tracks) > 0:
        first_track = tracks[0]
        has_stream_url = bool(first_track.get("streamAudioUrl") or first_track.get("sourceStreamAudioUrl"))

    if suno_status in ("SUCCESS", "FIRST_SUCCESS") or has_stream_url:
        gen.status = "succeeded"
    elif suno_status in ("TEXT_SUCCESS", "PENDING"):
        gen.status = "running"
    elif suno_status in ("CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED", "CALLBACK_EXCEPTION",
                         "SENSITIVE_WORD_ERROR"):
        gen.status = "failed"
    # else: leave as running

    db.session.commit()
    return {"ok": True, "taskId": task_id, "status": gen.status}