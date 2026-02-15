# app/routes/api.py
from __future__ import annotations

import logging
import os
from flask import Blueprint, current_app, jsonify, request

log = logging.getLogger("drvibey.api")

from app.models import User, ProviderAccount, ListenerProfile, Generation
from app.services.openai_prompt import generate_suno_payload_with_openai
from app.services.drvibey_chat import (
    get_initial_message,
    get_next_question,
    build_q2_from_tracks,
    cleanup_ocr_tracks,
    synthesize_profile,
    SKIP_TOKEN,
)
from app.services.ocr import (
    extract_tracks_from_images,
    save_upload_to_temp,
)

from app.extensions import db
from app.jobs.queue import enqueue
from app.jobs.tasks import (
    ingest_spotify_top_tracks,
    ingest_youtube_liked_videos,
    rebuild_profile_pipeline,
    run_generation_pipeline,
)

api_bp = Blueprint("api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------
# No-auth user resolution
# ---------------------------------------------------------------------
def _get_default_user_id() -> int:
    """
    Priority:
      1) body["user_id"] (handled in _resolve_user_id)
      2) current_app.config["DEFAULT_USER_ID"]
      3) env DRVIBEY_DEFAULT_USER_ID or DEFAULT_USER_ID
      4) fallback = 1
    """
    cfg_val = current_app.config.get("DEFAULT_USER_ID")
    if cfg_val is not None:
        try:
            return int(cfg_val)
        except Exception:
            pass

    for k in ("DRVIBEY_DEFAULT_USER_ID", "DEFAULT_USER_ID"):
        v = os.getenv(k)
        if v:
            try:
                return int(v)
            except Exception:
                pass

    return 1


def _resolve_user_id(body: dict) -> int:
    if isinstance(body, dict) and body.get("user_id") is not None:
        try:
            return int(body["user_id"])
        except Exception:
            pass
    return _get_default_user_id()


def _set_generation_mood(gen: Generation, mood_value) -> None:
    """
    Your templates suggest Generation has `mood` (string like 'energetic').
    Older/alternate schemas might use `mood_id` or `mood_key`.
    We set whichever exists to avoid TypeError on constructor.
    """
    if mood_value is None:
        return

    # normalize to a tidy string (your moods are string ids)
    mood = str(mood_value).strip().lower()
    if not mood:
        return

    if hasattr(gen, "mood"):
        setattr(gen, "mood", mood)
    elif hasattr(gen, "mood_id"):
        setattr(gen, "mood_id", mood)
    elif hasattr(gen, "mood_key"):
        setattr(gen, "mood_key", mood)


# ---------------------------------------------------------------------
# JS: POST /api/ingest
# body: { provider: "spotify", source: "top" }
# ---------------------------------------------------------------------
@api_bp.post("/ingest")
def ingest():
    body = request.get_json(silent=True) or {}
    user_id = _resolve_user_id(body)

    provider = (body.get("provider") or "").strip().lower()
    source = (body.get("source") or "").strip().lower()
    provider_account_id = body.get("provider_account_id")

    job = None

    if provider == "spotify":
        if source not in ("top", "top_tracks", "top-tracks"):
             return jsonify({"ok": False, "error": f"Unsupported source '{source}' for Spotify"}), 400
        job = enqueue(current_app, ingest_spotify_top_tracks, user_id, provider_account_id)

    elif provider == "youtube":
        if source not in ("liked", "liked_videos"):
             return jsonify({"ok": False, "error": f"Unsupported source '{source}' for YouTube"}), 400
        job = enqueue(current_app, ingest_youtube_liked_videos, user_id, provider_account_id)

    else:
        return jsonify({"ok": False, "error": f"Unsupported provider '{provider}'"}), 400

    return jsonify({"ok": True, "job_id": job.id, "user_id": user_id}), 200


# ---------------------------------------------------------------------
# JS: POST /api/profile/rebuild
# no body (but we allow JSON anyway)
# ---------------------------------------------------------------------
@api_bp.post("/profile/rebuild")
def profile_rebuild():
    body = request.get_json(silent=True) or {}
    user_id = _resolve_user_id(body)
    provider_account_id = body.get("provider_account_id")

    job = enqueue(current_app, rebuild_profile_pipeline, user_id, provider_account_id)
    return jsonify({"ok": True, "job_id": job.id, "user_id": user_id})


# ---------------------------------------------------------------------
# JS: POST /api/generate
# body: { mood, instrumental, custom_mode, title_hint, style_hint }
# ---------------------------------------------------------------------
@api_bp.post("/generate")
def generate():
    from flask import session as flask_session
    if not flask_session.get("is_authenticated"):
        return jsonify({"ok": False, "error": "Please sign in to generate music"}), 403

    body = request.get_json(silent=True) or {}
    user_id = _resolve_user_id(body)

    mood_value = body.get("mood") or body.get("mood_id") or "energetic"
    instrumental = bool(body.get("instrumental", False))
    custom_mode = bool(body.get("custom_mode", False))
    title = (body.get("title_hint") or body.get("title") or "").strip()
    style = (body.get("style_hint") or body.get("style") or "").strip()

    # New multi-step fields
    try:
        mood_intensity = float(body.get("mood_intensity", 0.5))
    except (TypeError, ValueError):
        mood_intensity = 0.5
    activity_id = (body.get("activity") or "").strip().lower() or None
    song_reference = (body.get("song_reference") or "").strip() or None
    genre_override = (body.get("genre") or "").strip() or None
    bpm_target = body.get("bpm") or None
    if bpm_target is not None:
        try:
            bpm_target = int(bpm_target)
        except (TypeError, ValueError):
            bpm_target = None

    lp = (
        ListenerProfile.query
        .filter_by(user_id=user_id)
        .order_by(ListenerProfile.created_at.desc())
        .first()
    )
    if not lp:
        return jsonify({"ok": False, "error": "No listener profile found. Build profile first."}), 400

    cerebras_api_key = current_app.config.get("CEREBRAS_API_KEY", "") or os.getenv("CEREBRAS_API_KEY", "")
    llm_model = current_app.config.get("LLM_MODEL", "llama-3.3-70b")

    try:
        out = generate_suno_payload_with_openai(
            cerebras_api_key=cerebras_api_key,
            model=llm_model,
            profile_json=lp.profile_json,
            mood_id=str(mood_value),
            mood_intensity=mood_intensity,
            activity_id=activity_id,
            instrumental=instrumental,
            song_reference=song_reference,
            genre_override=genre_override,
            bpm_target=bpm_target,
            custom_mode=custom_mode,
            title_hint=title,
            style_hint=style,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": "Prompt generation failed", "details": str(e)}), 500

    # Create Generation row with all fields
    gen = Generation(user_id=user_id)
    gen.listener_profile_id = lp.id
    _set_generation_mood(gen, mood_value)

    if hasattr(gen, "instrumental"):
        gen.instrumental = instrumental
    else:
        setattr(gen, "instrumental", instrumental)

    if hasattr(gen, "custom_mode"):
        gen.custom_mode = custom_mode
    else:
        setattr(gen, "custom_mode", custom_mode)

    if hasattr(gen, "title"):
        gen.title = title or None
    else:
        setattr(gen, "title", title or None)

    if hasattr(gen, "style"):
        gen.style = style or None
    else:
        setattr(gen, "style", style or None)

    # Persist new multi-step fields
    gen.mood_intensity = mood_intensity
    gen.activity = activity_id
    gen.song_reference = song_reference
    gen.genre = genre_override
    gen.bpm = bpm_target

    gen.openai_prompt = out.get("openai_prompt") or {}
    gen.suno_request = out.get("suno_payload") or {}
    gen.status = "queued"

    db.session.add(gen)
    db.session.commit()

    job = enqueue(current_app, run_generation_pipeline, gen.id)
    return jsonify({"ok": True, "generation_id": gen.id, "job_id": job.id})


# ---------------------------------------------------------------------
# JS: GET /api/generation/<id>
# expects: { status, result, error }
# ---------------------------------------------------------------------
@api_bp.get("/generation/<int:generation_id>")
def generation_status(generation_id: int):
    gen = db.session.get(Generation, generation_id)
    if not gen:
        return jsonify({"ok": False, "error": "Not found"}), 404

    status = gen.status or "unknown"
    result = gen.result_json or {}

    error = None
    if status == "failed":
        if isinstance(result, dict) and result.get("error"):
            error = str(result.get("error"))
        else:
            error = "Generation failed"

    return jsonify(
        {
            "ok": True,
            "generation_id": gen.id,
            "status": status,
            "result": result,
            "error": error,
        }
    )


# =====================================================================
# drVibey Chat Endpoints
# =====================================================================

# ---------------------------------------------------------------------
# POST /api/chat/message
# body: { init: true }  for the first message (returns Q1)
#   OR  { user_message: str, current_question: int }  for subsequent
# Returns: { ok, reply, question_number, input_type, skippable, ... }
# ---------------------------------------------------------------------
@api_bp.post("/chat/message")
def chat_message():
    body = request.get_json(silent=True) or {}

    # Initial message — return Q1 (screenshot prompt)
    if body.get("init"):
        log.info("[chat/message] init request -> returning Q1 (screenshot)")
        msg = get_initial_message()
        return jsonify({"ok": True, **msg})

    current_question = int(body.get("current_question", 1))
    user_message = (body.get("user_message") or "").strip()

    # Handle skips: treat as answered with skip token
    is_skip = user_message == SKIP_TOKEN
    log.info("[chat/message] user answered Q%d%s: %s",
             current_question, " (skip)" if is_skip else "", user_message[:80])

    # Return next question -- no LLM call needed, all questions are fixed
    try:
        result = get_next_question(current_question)
        log.info("[chat/message] -> Q%s, input_type=%s, complete=%s",
                 result.get("question_number"),
                 result.get("input_type"),
                 result.get("is_complete", False))
        return jsonify({"ok": True, **result})
    except Exception as e:
        log.exception("[chat/message] error")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------
# POST /api/chat/upload-screenshots
# multipart form: files[] = image files (3-10)
# Returns: { ok, tracks: [...], raw_count }
# ---------------------------------------------------------------------
@api_bp.post("/chat/upload-screenshots")
def chat_upload_screenshots():
    files = request.files.getlist("files")
    log.info("[chat/upload-screenshots] received %d files", len(files) if files else 0)

    if not files or len(files) < 3:
        return jsonify({"ok": False, "error": "Please upload at least 3 screenshots"}), 400
    if len(files) > 10:
        return jsonify({"ok": False, "error": "Maximum 10 screenshots allowed"}), 400

    # Validate file types
    allowed_ext = {".png", ".jpg", ".jpeg", ".webp", ".heic"}
    temp_paths = []

    try:
        for f in files:
            ext = os.path.splitext(f.filename or "")[1].lower()
            if ext not in allowed_ext:
                return jsonify({
                    "ok": False,
                    "error": f"Unsupported file type: {ext}. Use PNG, JPG, or WEBP.",
                }), 400
            path = save_upload_to_temp(f)
            temp_paths.append(path)

        # Run OCR locally via ocrmac
        parsed_tracks, raw_texts = extract_tracks_from_images(temp_paths)

        # Use Cerebras to clean up OCR results
        cerebras_api_key = (
            current_app.config.get("CEREBRAS_API_KEY", "")
            or os.getenv("CEREBRAS_API_KEY", "")
        )
        llm_model = current_app.config.get("LLM_MODEL", "llama-3.3-70b")

        if cerebras_api_key and parsed_tracks:
            try:
                cleaned = cleanup_ocr_tracks(
                    cerebras_api_key=cerebras_api_key,
                    model=llm_model,
                    parsed_tracks=parsed_tracks,
                    raw_texts=raw_texts,
                )
                tracks = cleaned
            except Exception:
                tracks = parsed_tracks
        else:
            tracks = parsed_tracks

        # Build Q2 dynamically from the cleaned tracks
        q2_data = build_q2_from_tracks(tracks)

        return jsonify({
            "ok": True,
            "tracks": tracks,
            "raw_count": len(parsed_tracks),
            "cleaned_count": len(tracks),
            "next_question": q2_data,
        })

    finally:
        # Clean up temp files
        for p in temp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


# ---------------------------------------------------------------------
# POST /api/chat/build-profile
# body: { user_id, history: [...], tracks: [...] }
# Returns: { ok, profile, diagnosis, listener_profile_id }
# ---------------------------------------------------------------------
@api_bp.post("/chat/build-profile")
def chat_build_profile():
    body = request.get_json(silent=True) or {}
    user_id = _resolve_user_id(body)

    history = body.get("history") or []
    tracks = body.get("tracks") or []

    log.info("[chat/build-profile] user_id=%s, history=%d msgs, tracks=%d",
             user_id, len(history), len(tracks))

    if not history:
        return jsonify({"ok": False, "error": "No conversation history provided"}), 400

    cerebras_api_key = (
        current_app.config.get("CEREBRAS_API_KEY", "")
        or os.getenv("CEREBRAS_API_KEY", "")
    )
    llm_model = current_app.config.get("LLM_MODEL", "llama-3.3-70b")

    if not cerebras_api_key:
        return jsonify({"ok": False, "error": "CEREBRAS_API_KEY not configured"}), 500

    try:
        profile_json, diagnosis_json = synthesize_profile(
            cerebras_api_key=cerebras_api_key,
            model=llm_model,
            history=history,
            extracted_tracks=tracks,
        )

        # Save to DB
        existing = (
            ListenerProfile.query
            .filter_by(user_id=user_id)
            .order_by(ListenerProfile.version.desc())
            .first()
        )
        next_version = (existing.version + 1) if existing else 1

        lp = ListenerProfile(
            user_id=user_id,
            version=next_version,
            built_from_track_count=len(tracks),
            profile_json=profile_json,
            explain_json={"diagnosis": diagnosis_json, "source": "drvibey_chat"},
        )
        db.session.add(lp)
        db.session.commit()

        log.info("[chat/build-profile] saved ListenerProfile id=%s version=%d for user=%s",
                 lp.id, next_version, user_id)

        return jsonify({
            "ok": True,
            "profile": profile_json,
            "diagnosis": diagnosis_json,
            "listener_profile_id": lp.id,
        })

    except Exception as e:
        log.exception("[chat/build-profile] error")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------
# GET /api/profile
# Returns the current user's latest listener profile + diagnosis
# ---------------------------------------------------------------------
@api_bp.get("/profile")
def get_profile():
    from flask import session as flask_session
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Not logged in"}), 401

    lp = (
        ListenerProfile.query
        .filter_by(user_id=user_id)
        .order_by(ListenerProfile.version.desc())
        .first()
    )

    if not lp:
        return jsonify({"ok": True, "has_profile": False})

    profile_json = lp.profile_json or {}
    explain = lp.explain_json or {}
    diagnosis_json = explain.get("diagnosis", {})

    return jsonify({
        "ok": True,
        "has_profile": True,
        "profile": profile_json,
        "diagnosis": diagnosis_json,
        "listener_profile_id": lp.id,
    })


# ---------------------------------------------------------------------
# GET /api/generations
# Returns current user's generations list (most recent first)
# ---------------------------------------------------------------------
@api_bp.get("/generations")
def list_generations():
    from flask import session as flask_session
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Not logged in"}), 401

    gens = (
        Generation.query
        .filter_by(user_id=user_id)
        .order_by(Generation.created_at.desc())
        .limit(50)
        .all()
    )

    items = []
    for g in gens:
        item = {
            "id": g.id,
            "mood": g.mood,
            "activity": g.activity,
            "status": g.status,
            "is_favourite": bool(g.is_favourite),
            "created_at": g.created_at.isoformat() if g.created_at else None,
            "title": None,
            "cover_url": None,
            "audio_url": None,
            "download_url": None,
        }

        # Extract song info from result_json for succeeded generations
        if g.status == "succeeded" and g.result_json:
            song = _extract_song(g.result_json)
            if song:
                item["title"] = song.get("title")
                item["cover_url"] = (
                    song.get("imageUrl") or song.get("sourceImageUrl")
                    or song.get("image_url") or song.get("source_image_url")
                )
                audio_url, download_url = _extract_urls(song)
                item["audio_url"] = audio_url
                item["download_url"] = download_url

        items.append(item)

    return jsonify({"ok": True, "generations": items})


# ---------------------------------------------------------------------
# GET /api/generations/favourites
# Returns only favourited generations for the current user
# ---------------------------------------------------------------------
@api_bp.get("/generations/favourites")
def list_favourites():
    from flask import session as flask_session
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Not logged in"}), 401

    gens = (
        Generation.query
        .filter_by(user_id=user_id, is_favourite=True)
        .order_by(Generation.created_at.desc())
        .limit(50)
        .all()
    )

    items = []
    for g in gens:
        item = {
            "id": g.id,
            "mood": g.mood,
            "activity": g.activity,
            "status": g.status,
            "is_favourite": True,
            "created_at": g.created_at.isoformat() if g.created_at else None,
            "title": None,
            "cover_url": None,
            "audio_url": None,
            "download_url": None,
        }

        if g.status == "succeeded" and g.result_json:
            song = _extract_song(g.result_json)
            if song:
                item["title"] = song.get("title")
                item["cover_url"] = (
                    song.get("imageUrl") or song.get("sourceImageUrl")
                    or song.get("image_url") or song.get("source_image_url")
                )
                audio_url, download_url = _extract_urls(song)
                item["audio_url"] = audio_url
                item["download_url"] = download_url

        items.append(item)

    return jsonify({"ok": True, "generations": items})


# ---------------------------------------------------------------------
# PATCH /api/generation/<id>/favourite
# Toggle or set is_favourite on a generation
# Body: { "is_favourite": true/false }
# ---------------------------------------------------------------------
@api_bp.patch("/generation/<int:generation_id>/favourite")
def toggle_favourite(generation_id: int):
    from flask import session as flask_session
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Not logged in"}), 401

    gen = Generation.query.filter_by(id=generation_id, user_id=user_id).first()
    if not gen:
        return jsonify({"ok": False, "error": "Not found"}), 404

    body = request.get_json(silent=True) or {}
    if "is_favourite" in body:
        gen.is_favourite = bool(body["is_favourite"])
    else:
        # Toggle if no explicit value given
        gen.is_favourite = not gen.is_favourite

    db.session.commit()
    return jsonify({"ok": True, "is_favourite": gen.is_favourite})


# ---------------------------------------------------------------------
# GET /api/generation/<id>/download-url
# Returns the final (non-stream) download URL, re-fetching from Suno
# if not yet available in result_json.
# ---------------------------------------------------------------------
@api_bp.get("/generation/<int:generation_id>/download-url")
def generation_download_url(generation_id: int):
    from flask import session as flask_session
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Not logged in"}), 401

    gen = Generation.query.filter_by(id=generation_id, user_id=user_id).first()
    if not gen:
        return jsonify({"ok": False, "error": "Not found"}), 404

    # 1. Check if we already have a final download URL in result_json
    song = _extract_song(gen.result_json) if gen.result_json else None
    if song:
        _, download_url = _extract_urls(song)
        if download_url:
            return jsonify({"ok": True, "download_url": download_url})

    # 2. No final URL yet -- re-fetch from Suno API once
    task_id = None
    if gen.result_json and isinstance(gen.result_json, dict):
        task_id = gen.result_json.get("taskId") or gen.suno_job_id
    if not task_id:
        task_id = gen.suno_job_id

    if not task_id:
        return jsonify({"ok": True, "download_url": None})

    try:
        from app.services.suno_client import SunoClient
        suno_base_url = current_app.config.get("SUNO_BASE_URL", "") or os.getenv("SUNO_BASE_URL", "https://api.sunoapi.org")
        suno_api_key = current_app.config.get("SUNO_API_KEY", "") or os.getenv("SUNO_API_KEY", "")

        suno = SunoClient(
            base_url=suno_base_url,
            api_key=suno_api_key,
            timeout_s=int(os.getenv("SUNO_TIMEOUT_S", "30")),
        )
        details = suno.get_generation_details(str(task_id))

        # Merge fresh details into result_json
        if details:
            updated = {**(gen.result_json or {}), "record_info": details}
            suno_status = (details.get("data", {}).get("status") or "").upper()
            if suno_status:
                updated["suno_status"] = suno_status
            gen.result_json = updated
            db.session.commit()

        # Check again for download URL
        song = _extract_song(gen.result_json)
        if song:
            _, download_url = _extract_urls(song)
            if download_url:
                return jsonify({"ok": True, "download_url": download_url})

    except Exception as e:
        log.warning("download-url re-fetch failed for gen %s: %s", generation_id, e)

    return jsonify({"ok": True, "download_url": None})


def _extract_urls(song):
    """Return (audio_url, download_url) from a song object.

    audio_url   = best URL for real-time playback (prefer stream)
    download_url = final .mp3 URL only (non-stream); None if not yet available
    """
    if not song:
        return None, None

    # Final / downloadable MP3 URLs (non-stream)
    download_url = (
        song.get("audioUrl") or song.get("sourceAudioUrl")
        or song.get("audio_url") or song.get("source_audio_url")
        or None
    )

    # Streaming URLs (for real-time playback)
    stream_url = (
        song.get("streamAudioUrl") or song.get("sourceStreamAudioUrl")
        or song.get("stream_audio_url") or song.get("source_stream_audio_url")
        or None
    )

    # For playback: prefer stream (plays immediately), fall back to final
    audio_url = stream_url or download_url

    return audio_url, download_url


def _extract_song(result_json):
    """Extract the first song object from various result_json shapes."""
    tracks = None
    if isinstance(result_json, dict):
        ri = result_json.get("record_info", {})
        if isinstance(ri, dict):
            rd = ri.get("data", {})
            if isinstance(rd, dict):
                resp = rd.get("response", {})
                if isinstance(resp, dict):
                    tracks = resp.get("sunoData") or resp.get("data")

        if not tracks:
            tracks = result_json.get("final")
        if not tracks:
            d = result_json.get("data", {})
            if isinstance(d, dict):
                resp = d.get("response", {})
                if isinstance(resp, dict):
                    tracks = resp.get("sunoData")
                if not tracks:
                    tracks = d.get("data")

    if isinstance(tracks, list) and len(tracks) > 0:
        return tracks[0]
    return None
