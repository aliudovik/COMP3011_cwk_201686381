# app/jobs/tasks.py
from __future__ import annotations

import os
import threading
import time
import logging
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

log = logging.getLogger("drvibey.jobs")

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

    # use OAuthToken (NOT pa.access_token)
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
        model=current_app.config.get("LLM_MODEL", "gpt-oss-120b"),  # <--- Pass the model
        profile_json=lp.profile_json,
        mood_id=mood_id,
        instrumental=bool(instrumental),
        language="english",
        surprise_me=False,
        custom_mode=True,
        title_hint=(title_hint or "").strip(),
        style_hint=(style_hint or "").strip(),
    )

    # 3) Insert valid Generation row (openai_prompt + suno_request are NOT NULL in your model)
    openai_prompt_data = out.get("openai_prompt") or {}
    openai_prompt_data["lyrics_brief"] = out.get("lyrics_brief") or ""
    gen = Generation(
        user_id=user_id,
        listener_profile_id=lp.id,
        mood=mood_id,
        openai_prompt=openai_prompt_data,
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
    llm_model = current_app.config.get("LLM_MODEL", "gpt-oss-120b")

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
    pipeline_started_at = time.perf_counter()
    gen: Generation = db.session.get(Generation, generation_id)
    if not gen:
        return {"ok": False, "error": f"Generation {generation_id} not found"}

    queue_wait_ms = None
    if getattr(gen, "created_at", None):
        created_at = gen.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        queue_wait_ms = int((datetime.now(timezone.utc) - created_at).total_seconds() * 1000)

    log.info(
        "[generation] worker picked generation_id=%s status=%s queue_wait_ms=%s",
        generation_id,
        gen.status,
        queue_wait_ms,
    )

    gen.status = "running"
    db.session.commit()

    suno_payload = getattr(gen, "suno_request", None) or {}
    if not isinstance(suno_payload, dict):
        suno_payload = {}

    prompt = (suno_payload.get("prompt") or "").strip()

    # Queue-first API stores empty suno_request; worker builds it here.
    if not prompt:
        lp = (
            ListenerProfile.query
            .filter_by(user_id=gen.user_id)
            .order_by(ListenerProfile.created_at.desc())
            .first()
        )
        if not lp or not lp.profile_json:
            gen.status = "failed"
            gen.result_json = {"error": "No listener profile found. Build profile first."}
            db.session.commit()
            return {"ok": False, "error": "No listener profile"}

        cerebras_api_key = current_app.config.get("CEREBRAS_API_KEY", "") or os.getenv("CEREBRAS_API_KEY", "")
        mood_id = str(getattr(gen, "mood", None) or "energetic")
        gen_controls = {}
        if isinstance(suno_payload.get("controls"), dict):
            gen_controls = suno_payload.get("controls") or {}
        language = str(gen_controls.get("language") or "english").strip().lower() or "english"
        surprise_me = bool(gen_controls.get("surprise_me", False))

        out = generate_suno_payload_with_openai(
            cerebras_api_key=cerebras_api_key,
            model=current_app.config.get("LLM_MODEL", "gpt-oss-120b"),
            profile_json=lp.profile_json,
            mood_id=mood_id,
            mood_intensity=getattr(gen, "mood_intensity", None),
            activity_id=getattr(gen, "activity", None),
            instrumental=bool(getattr(gen, "instrumental", False)),
            song_reference=getattr(gen, "song_reference", None),
            genre_override=getattr(gen, "genre", None),
            bpm_target=getattr(gen, "bpm", None),
            language=language,
            surprise_me=surprise_me,
            custom_mode=bool(getattr(gen, "custom_mode", False)),
            title_hint=getattr(gen, "title", "") or "",
            style_hint=getattr(gen, "style", "") or "",
        )

        openai_prompt_rebuilt = out.get("openai_prompt") or {}
        openai_prompt_rebuilt["lyrics_brief"] = out.get("lyrics_brief") or ""
        gen.openai_prompt = openai_prompt_rebuilt
        suno_payload = out.get("suno_payload") or {}
        gen.suno_request = suno_payload
        db.session.commit()
        prompt = (suno_payload.get("prompt") or "").strip()

    if not prompt:
        gen.status = "failed"
        gen.result_json = {"error": "Missing/empty prompt in suno_request."}
        db.session.commit()
        return {"ok": False, "error": "Empty prompt"}

    suno_base_url = current_app.config.get("SUNO_BASE_URL", "") or os.getenv("SUNO_BASE_URL", "https://api.sunoapi.org")
    suno_api_key = current_app.config.get("SUNO_API_KEY", "") or os.getenv("SUNO_API_KEY", "")
    suno = SunoClient(
        base_url=suno_base_url,
        api_key=suno_api_key,
        timeout_s=int(os.getenv("SUNO_TIMEOUT_S", "180")),
    )

    is_instrumental = bool(
        suno_payload.get("instrumental")
        or suno_payload.get("make_instrumental")
        or getattr(gen, "instrumental", False)
    )
    custom_mode = True
    style = (suno_payload.get("style") or getattr(gen, "style", "") or "").strip()
    title = (suno_payload.get("title") or getattr(gen, "title", "") or "").strip()
    negative_tags = (suno_payload.get("negative_tags") or suno_payload.get("negativeTags") or "").strip()
    vocal_gender = (suno_payload.get("vocal_gender") or suno_payload.get("vocalGender") or "").strip().lower()
    if vocal_gender not in ("m", "f"):
        vocal_gender = ""

    def _num_or_none(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    style_weight = _num_or_none(suno_payload.get("style_weight") or suno_payload.get("styleWeight"))
    weirdness_constraint = _num_or_none(
        suno_payload.get("weirdness_constraint") or suno_payload.get("weirdnessConstraint")
    )
    audio_weight = _num_or_none(suno_payload.get("audio_weight") or suno_payload.get("audioWeight"))
    persona_id = str(suno_payload.get("persona_id") or suno_payload.get("personaId") or "").strip()

    raw_model = (
        suno_payload.get("model")
        or suno_payload.get("desired_model")
        or current_app.config.get("SUNO_MODEL", "V5")
    )
    model_map = {
        "V5": "V5",
        "4.5-ALL": "V5",
        "V45ALL": "V5",
        "V4.5": "V5",
        "V4": "V5",
        "V3": "V5",
    }
    normalized = str(raw_model).upper().replace("_", "").replace(".", "").replace("-", "")
    model = model_map.get(normalized, raw_model)
    if model not in ("V4_5ALL", "V4", "V3_5", "V3", "V5"):
        model = "V5"

    app_base = current_app.config.get("APP_BASE_URL", "http://127.0.0.1:7777")
    callback_url = f"{app_base}/callback"

    # -----------------------------------------------------------------
    # Lyrics generation step (vocal tracks only)
    # -----------------------------------------------------------------
    if not is_instrumental:
        lyrics_brief = str((getattr(gen, "openai_prompt", None) or {}).get("lyrics_brief") or "").strip()
        if lyrics_brief:
            log.info(
                "[generation] starting lyrics generation generation_id=%s lyrics_brief_len=%d",
                generation_id,
                len(lyrics_brief),
            )
            try:
                lyrics_started_at = time.perf_counter()
                lyrics_resp = suno.generate_lyrics(prompt=lyrics_brief, callback_url=callback_url)
                lyrics_task_id = (lyrics_resp.get("data") or {}).get("taskId")

                if lyrics_task_id:
                    log.info(
                        "[generation] lyrics task created generation_id=%s lyrics_task_id=%s",
                        generation_id,
                        lyrics_task_id,
                    )
                    lyrics_details = suno.poll_until_lyrics_ready(
                        task_id=str(lyrics_task_id),
                        attempts=int(os.getenv("SUNO_LYRICS_POLL_ATTEMPTS", "60")),
                        sleep_s=float(os.getenv("SUNO_LYRICS_POLL_SLEEP_S", "1")),
                    )
                    lyrics_status = ((lyrics_details.get("data") or {}).get("status") or "").upper()
                    lyrics_ms = int((time.perf_counter() - lyrics_started_at) * 1000)

                    if lyrics_status == "SUCCESS":
                        lyrics_data = ((lyrics_details.get("data") or {}).get("response") or {}).get("data") or []
                        if lyrics_data and isinstance(lyrics_data[0], dict):
                            generated_lyrics = (lyrics_data[0].get("text") or "").strip()
                            if generated_lyrics:
                                prompt = generated_lyrics[:5000]
                                log.info(
                                    "[generation] lyrics injected generation_id=%s lyrics_len=%d latency_ms=%d",
                                    generation_id,
                                    len(prompt),
                                    lyrics_ms,
                                )
                            else:
                                log.warning(
                                    "[generation] lyrics SUCCESS but empty text, using original prompt generation_id=%s",
                                    generation_id,
                                )
                        else:
                            log.warning(
                                "[generation] lyrics SUCCESS but no data array, using original prompt generation_id=%s",
                                generation_id,
                            )
                    else:
                        log.warning(
                            "[generation] lyrics generation status=%s, falling back to original prompt generation_id=%s latency_ms=%d",
                            lyrics_status,
                            generation_id,
                            lyrics_ms,
                        )

                    gen.result_json = {
                        **(gen.result_json or {}),
                        "lyrics_task_id": lyrics_task_id,
                        "lyrics_status": lyrics_status,
                        "lyrics_details": lyrics_details,
                    }
                    db.session.commit()
                else:
                    log.warning(
                        "[generation] lyrics API returned no taskId, using original prompt generation_id=%s",
                        generation_id,
                    )
            except Exception as lyrics_err:
                log.warning(
                    "[generation] lyrics generation failed (%s), using original prompt generation_id=%s",
                    lyrics_err,
                    generation_id,
                )
        else:
            log.info(
                "[generation] no lyrics_brief available, using original prompt generation_id=%s",
                generation_id,
            )

    log.info(
        "[generation] submit task generation_id=%s prompt_len=%s model=%s instrumental=%s custom=%s",
        generation_id,
        len(prompt),
        model,
        is_instrumental,
        custom_mode,
    )

    try:
        suno_submit_started_at = time.perf_counter()
        response = suno.generate(
            prompt=prompt,
            is_instrumental=is_instrumental,
            custom_mode=custom_mode,
            style=style,
            title=title,
            model=model,
            callback_url=callback_url,
            negative_tags=negative_tags,
            vocal_gender=vocal_gender,
            style_weight=style_weight,
            weirdness_constraint=weirdness_constraint,
            audio_weight=audio_weight,
            persona_id=persona_id,
        )
        suno_submit_ms = int((time.perf_counter() - suno_submit_started_at) * 1000)
        log.info(
            "[generation] suno task created generation_id=%s submit_latency_ms=%d",
            generation_id,
            suno_submit_ms,
        )
    except Exception as e:
        gen.status = "failed"
        gen.result_json = {"error": "Suno API call failed", "details": str(e)}
        db.session.commit()
        return {"ok": False, "error": str(e)}

    data = response.get("data", {})
    task_id = data.get("taskId")
    if not task_id:
        gen.status = "failed"
        gen.result_json = {"error": "Suno API returned no taskId", "response": response}
        db.session.commit()
        return {"ok": False, "error": "No taskId returned"}

    gen.suno_job_id = str(task_id)
    gen.result_json = {"taskId": task_id, "initial_response": response}
    gen.status = "running"
    db.session.commit()

    poll_attempts = int(os.getenv("SUNO_POLL_ATTEMPTS", "60"))
    poll_sleep_s = int(os.getenv("SUNO_POLL_SLEEP_S", "2"))
    log.info(
        "[generation] polling task_id=%s attempts=%s sleep_s=%s",
        task_id,
        poll_attempts,
        poll_sleep_s,
    )

    try:
        poll_started_at = time.perf_counter()
        details = suno.poll_until_stream_ready(
            task_id=str(task_id),
            attempts=poll_attempts,
            sleep_s=poll_sleep_s,
        )
        poll_ms = int((time.perf_counter() - poll_started_at) * 1000)
        log.info(
            "[generation] poll returned generation_id=%s task_id=%s latency_ms=%d",
            generation_id,
            task_id,
            poll_ms,
        )
    except Exception as e:
        gen.result_json = {
            **(gen.result_json or {}),
            "poll_error": str(e),
        }
        db.session.commit()
        return {"ok": True, "taskId": task_id, "status": gen.status}

    data = details.get("data") or {}
    suno_status = (data.get("status") or "").upper()

    gen.result_json = {
        **(gen.result_json or {}),
        "record_info": details,
        "suno_status": suno_status,
    }

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
    elif suno_status in (
        "CREATE_TASK_FAILED",
        "GENERATE_AUDIO_FAILED",
        "CALLBACK_EXCEPTION",
        "SENSITIVE_WORD_ERROR",
    ):
        gen.status = "failed"
        failure_reason = (
            data.get("errorMessage")
            or data.get("error")
            or f"Suno generation failed with status: {suno_status or 'UNKNOWN'}"
        )
        gen.result_json = {
            **(gen.result_json or {}),
            "error": failure_reason,
        }

    # Fetch similar real-artist songs BEFORE committing "succeeded" so the
    # frontend poll picks them up together with the result.
    if gen.status == "succeeded":
        try:
            similar = _fetch_similar_songs(gen)
            if similar:
                gen.result_json = {**(gen.result_json or {}), "similar_songs": similar}
        except Exception as e:
            log.warning("[generation] similar songs fetch failed generation_id=%s: %s", generation_id, e)

    db.session.commit()

    total_ms = int((time.perf_counter() - pipeline_started_at) * 1000)
    log.info(
        "[generation] pipeline done generation_id=%s task_id=%s status=%s total_ms=%d",
        generation_id,
        task_id,
        gen.status,
        total_ms,
    )
    return {"ok": True, "taskId": task_id, "status": gen.status}


_SIMILAR_SONGS_FALLBACK = {
    "chill": [
        {"title": "Redbone", "artist": "Childish Gambino"},
        {"title": "Electric Feel", "artist": "MGMT"},
        {"title": "Tadow", "artist": "Masego & FKJ"},
        {"title": "Lost in Japan", "artist": "Shawn Mendes"},
        {"title": "Best Part", "artist": "Daniel Caesar ft. H.E.R."},
        {"title": "Put Your Records On", "artist": "Corinne Bailey Rae"},
    ],
    "happy": [
        {"title": "Happy", "artist": "Pharrell Williams"},
        {"title": "Walking on Sunshine", "artist": "Katrina and the Waves"},
        {"title": "Levitating", "artist": "Dua Lipa"},
        {"title": "Good as Hell", "artist": "Lizzo"},
        {"title": "Uptown Funk", "artist": "Bruno Mars"},
        {"title": "Shut Up and Dance", "artist": "WALK THE MOON"},
    ],
    "energetic": [
        {"title": "Titanium", "artist": "David Guetta ft. Sia"},
        {"title": "Blinding Lights", "artist": "The Weeknd"},
        {"title": "Can't Hold Us", "artist": "Macklemore & Ryan Lewis"},
        {"title": "Stronger", "artist": "Kanye West"},
        {"title": "Don't Stop Me Now", "artist": "Queen"},
        {"title": "Levels", "artist": "Avicii"},
    ],
    "sad": [
        {"title": "Someone Like You", "artist": "Adele"},
        {"title": "Skinny Love", "artist": "Bon Iver"},
        {"title": "Liability", "artist": "Lorde"},
        {"title": "Motion Sickness", "artist": "Phoebe Bridgers"},
        {"title": "All I Want", "artist": "Kodaline"},
        {"title": "Slow Dancing in the Dark", "artist": "Joji"},
    ],
    "focus": [
        {"title": "Experience", "artist": "Ludovico Einaudi"},
        {"title": "Weightless", "artist": "Marconi Union"},
        {"title": "Intro", "artist": "The xx"},
        {"title": "An Ending (Ascent)", "artist": "Brian Eno"},
        {"title": "Divenire", "artist": "Ludovico Einaudi"},
        {"title": "Nuvole Bianche", "artist": "Ludovico Einaudi"},
    ],
    "romantic": [
        {"title": "At Last", "artist": "Etta James"},
        {"title": "Thinking Out Loud", "artist": "Ed Sheeran"},
        {"title": "Love On Top", "artist": "Beyoncé"},
        {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars"},
        {"title": "Adorn", "artist": "Miguel"},
        {"title": "The Way You Look Tonight", "artist": "Frank Sinatra"},
    ],
    "aggressive": [
        {"title": "Killing In The Name", "artist": "Rage Against the Machine"},
        {"title": "HUMBLE.", "artist": "Kendrick Lamar"},
        {"title": "Bulls on Parade", "artist": "Rage Against the Machine"},
        {"title": "Lose Yourself", "artist": "Eminem"},
        {"title": "Enter Sandman", "artist": "Metallica"},
        {"title": "Bombtrack", "artist": "Rage Against the Machine"},
    ],
}


def _fetch_similar_songs(gen: Generation):
    """Return 3 real songs similar to the generated track.

    Tries a Cerebras LLM call first; falls back to a hardcoded mood-based
    catalog so results are always returned.
    """
    import json as _json
    import random

    # --- Try LLM ---
    try:
        result = _fetch_similar_songs_via_llm(gen)
        if result:
            return result
    except Exception as e:
        log.warning("[generation] similar songs LLM call failed, using fallback: %s", e)

    # --- Deterministic fallback ---
    mood = (gen.mood or "").strip().lower()
    pool = _SIMILAR_SONGS_FALLBACK.get(mood) or _SIMILAR_SONGS_FALLBACK.get("energetic", [])
    if not pool:
        return None
    picks = random.sample(pool, min(3, len(pool)))
    return [{"title": s["title"], "artist": s["artist"]} for s in picks]


def _fetch_similar_songs_via_llm(gen: Generation):
    """Cerebras LLM call for similar song suggestions."""
    import json as _json
    import re
    from cerebras.cloud.sdk import Cerebras

    cerebras_api_key = current_app.config.get("CEREBRAS_API_KEY", "") or os.getenv("CEREBRAS_API_KEY", "")
    if not cerebras_api_key:
        return None

    result_json = gen.result_json or {}
    song_title = ""

    tracks = None
    for extractor in [
        lambda r: ((r.get("record_info") or {}).get("data") or {}).get("response", {}).get("sunoData"),
        lambda r: ((r.get("record_info") or {}).get("data") or {}).get("response", {}).get("data"),
    ]:
        tracks = extractor(result_json)
        if tracks:
            break
    if tracks and isinstance(tracks, list) and len(tracks) > 0:
        song_title = tracks[0].get("title") or ""

    suno_req = gen.suno_request or {}
    song_style = (suno_req.get("style") or "").strip()
    mood = gen.mood or ""
    genre = gen.genre or ""

    context_parts = []
    if song_title:
        context_parts.append(f"Title: {song_title}")
    if song_style:
        context_parts.append(f"Style: {song_style}")
    if mood:
        context_parts.append(f"Mood: {mood}")
    if genre:
        context_parts.append(f"Genre: {genre}")
    context = ". ".join(context_parts) or "Unknown style"

    model = current_app.config.get("LLM_MODEL", "gpt-oss-120b")
    client = Cerebras(api_key=cerebras_api_key)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a music recommendation engine. Given a description of a generated song, "
                    "suggest exactly 3 real songs by real artists that sound similar or fit the same vibe. "
                    "Return ONLY a JSON array with 3 objects, each having \"title\" and \"artist\" keys. "
                    "No markdown, no explanation, just the JSON array."
                ),
            },
            {
                "role": "user",
                "content": f"Generated song: {context}",
            },
        ],
        temperature=0.7,
    )

    text = (resp.choices[0].message.content or "").strip()

    # Strip markdown code fences (```json ... ```, ``` ... ```, etc.)
    text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    suggestions = _json.loads(text)
    if isinstance(suggestions, list) and len(suggestions) > 0:
        return [{"title": s.get("title", ""), "artist": s.get("artist", "")} for s in suggestions[:3]]
    return None