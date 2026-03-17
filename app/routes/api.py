# app/routes/api.py
from __future__ import annotations

import logging
import os
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func

log = logging.getLogger("drvibey.api")

from app.models import User, ProviderAccount, ListenerProfile, Generation
from app.services.drvibey_chat import (
    get_initial_message,
    get_next_question,
    build_q2_from_tracks,
    cleanup_ocr_tracks,
    synthesize_profile,
    SKIP_TOKEN,
)
from app.services.psychoacoustic import generate_test_config, score_test
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

SUPPORTED_GENERATION_LANGUAGES = {
    "english",
    "spanish",
    "french",
    "chinese",
    "korean",
    "japanese",
    "russian",
}

PLAYBACK_WINDOW_MINUTES = 90


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


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_id() -> str:
    rid = getattr(g, "request_id", None)
    if rid:
        return rid

    incoming = (request.headers.get("X-Request-Id") or "").strip()
    rid = incoming or uuid.uuid4().hex
    g.request_id = rid
    return rid


def _json_ok(payload: dict, status: int = 200):
    out = {
        "ok": True,
        "request_id": _request_id(),
        "server_time": _iso_utc_now(),
    }
    if isinstance(payload, dict):
        out.update(payload)
    return jsonify(out), status


def _json_error(message: str, status: int, code: str):
    return jsonify(
        {
            "ok": False,
            "request_id": _request_id(),
            "server_time": _iso_utc_now(),
            "error": {"code": code, "message": message},
        }
    ), status


def _require_session_user_id():
    from flask import session as flask_session

    user_id = flask_session.get("user_id")
    if not user_id:
        return None
    try:
        return int(user_id)
    except Exception:
        return None


def _generation_summary(gen: Generation) -> dict:
    return {
        "id": gen.id,
        "user_id": gen.user_id,
        "listener_profile_id": gen.listener_profile_id,
        "mood": gen.mood,
        "mood_intensity": gen.mood_intensity,
        "activity": gen.activity,
        "song_reference": gen.song_reference,
        "genre": gen.genre,
        "bpm": gen.bpm,
        "status": gen.status,
        "is_favourite": bool(gen.is_favourite),
        "like_status": gen.like_status,
        "created_at": gen.created_at.isoformat() if gen.created_at else None,
    }


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
    req_started_at = time.perf_counter()

    body = request.get_json(silent=True) or {}
    user_id = _resolve_user_id(body)

    mood_value = body.get("mood") or body.get("mood_id") or "energetic"
    instrumental = bool(body.get("instrumental", False))
    custom_mode = True
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
    language = (body.get("language") or "english").strip().lower() or "english"
    if language not in SUPPORTED_GENERATION_LANGUAGES:
        language = "english"
    surprise_me = bool(body.get("surprise_me", False))
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

    # Queue-first path: persist generation quickly, then let worker build prompt + run provider calls.
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

    # Worker fills these in. Keep JSON fields non-null for schema compatibility.
    gen.openai_prompt = {}
    gen.suno_request = {
        "controls": {
            "language": language,
            "surprise_me": surprise_me,
        }
    }
    gen.status = "queued"

    db.session.add(gen)
    db.session.commit()

    committed_ms = int((time.perf_counter() - req_started_at) * 1000)
    log.info(
        "[generate] queued row committed generation_id=%s user_id=%s in %dms",
        gen.id,
        user_id,
        committed_ms,
    )

    try:
        job = enqueue(current_app, run_generation_pipeline, gen.id)
    except Exception as e:
        gen.status = "failed"
        gen.result_json = {
            "error": "Failed to enqueue generation job",
            "details": str(e),
        }
        db.session.commit()
        log.exception("[generate] enqueue failed for generation_id=%s", gen.id)
        return jsonify({"ok": False, "error": "Generation queue unavailable"}), 503

    total_ms = int((time.perf_counter() - req_started_at) * 1000)
    log.info(
        "[generate] enqueue succeeded generation_id=%s job_id=%s total=%dms",
        gen.id,
        job.id,
        total_ms,
    )
    return jsonify({"ok": True, "generation_id": gen.id, "job_id": job.id})


# ---------------------------------------------------------------------
# JS: GET /api/generation/<id>
# expects: { status, result, error }
# ---------------------------------------------------------------------
@api_bp.get("/generation/<int:generation_id>")
def generation_status(generation_id: int):
    gen = db.session.get(Generation, generation_id)
    if not gen:
        return _json_error("Generation not found", 404, "not_found")

    user_id = _require_session_user_id()
    if user_id and gen.user_id != user_id:
        return _json_error("Forbidden", 403, "forbidden")

    status = gen.status or "unknown"
    result = gen.result_json or {}
    result = _sanitize_generation_result_for_client(
        result,
        allow_playback=_is_generation_playable(gen),
    )

    error = None
    if status == "failed":
        if isinstance(result, dict) and result.get("error"):
            error = str(result.get("error"))
        else:
            error = "Generation failed"

    return _json_ok(
        {
            "generation_id": gen.id,
            "status": status,
            "result": result,
            "error": error,
            "is_favourite": gen.is_favourite,
            "like_status": gen.like_status,
            "created_at": gen.created_at.isoformat() if gen.created_at else None,
        }
    )


@api_bp.patch("/generation/<int:generation_id>")
def update_generation(generation_id: int):
    user_id = _require_session_user_id()
    if not user_id:
        return _json_error("Not logged in", 401, "unauthorized")

    gen = Generation.query.filter_by(id=generation_id, user_id=user_id).first()
    if not gen:
        return _json_error("Generation not found", 404, "not_found")

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not body:
        return _json_error("Request body must be a non-empty JSON object", 400, "validation_error")

    mutable_fields = {"activity", "song_reference", "genre", "is_favourite", "like_status"}
    changed = False

    if "mood" in body:
        mood_value = str(body.get("mood") or "").strip().lower()
        if not mood_value:
            return _json_error("mood cannot be empty", 400, "validation_error")
        _set_generation_mood(gen, mood_value)
        changed = True

    if "mood_intensity" in body:
        try:
            mood_intensity = float(body.get("mood_intensity"))
        except (TypeError, ValueError):
            return _json_error("mood_intensity must be a number between 0 and 1", 400, "validation_error")
        if mood_intensity < 0 or mood_intensity > 1:
            return _json_error("mood_intensity must be between 0 and 1", 400, "validation_error")
        gen.mood_intensity = mood_intensity
        changed = True

    if "bpm" in body:
        bpm_raw = body.get("bpm")
        if bpm_raw in (None, ""):
            gen.bpm = None
            changed = True
        else:
            try:
                bpm_value = int(bpm_raw)
            except (TypeError, ValueError):
                return _json_error("bpm must be an integer", 400, "validation_error")
            if bpm_value < 1:
                return _json_error("bpm must be a positive integer", 400, "validation_error")
            gen.bpm = bpm_value
            changed = True

    for key in mutable_fields:
        if key not in body:
            continue
        if key == "like_status":
            new_status = body.get("like_status")
            if new_status not in ("liked", "disliked", None):
                return _json_error("like_status must be 'liked', 'disliked', or null", 400, "validation_error")
            gen.like_status = new_status
            changed = True
            continue
        if key == "is_favourite":
            gen.is_favourite = bool(body.get("is_favourite"))
            changed = True
            continue

        value = body.get(key)
        if value is None:
            setattr(gen, key, None)
        else:
            setattr(gen, key, str(value).strip() or None)
        changed = True

    if not changed:
        return _json_error("No valid updatable fields provided", 400, "validation_error")

    db.session.commit()
    return _json_ok({"generation": _generation_summary(gen)})


@api_bp.delete("/generation/<int:generation_id>")
def delete_generation(generation_id: int):
    user_id = _require_session_user_id()
    if not user_id:
        return _json_error("Not logged in", 401, "unauthorized")

    gen = Generation.query.filter_by(id=generation_id, user_id=user_id).first()
    if not gen:
        return _json_error("Generation not found", 404, "not_found")

    db.session.delete(gen)
    db.session.commit()
    return "", 204


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
        llm_model = current_app.config.get("LLM_MODEL", "gpt-oss-120b")

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
    llm_model = current_app.config.get("LLM_MODEL", "gpt-oss-120b")

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
# POST /api/profile/share
# body: { user_id, listener_profile_id? }
# Returns: { ok, share_url, listener_profile_id }
# ---------------------------------------------------------------------
@api_bp.post("/profile/share")
def share_profile():
    body = request.get_json(silent=True) or {}
    user_id = _resolve_user_id(body)
    listener_profile_id = body.get("listener_profile_id")

    q = ListenerProfile.query.filter_by(user_id=user_id)
    if listener_profile_id is not None:
        q = q.filter_by(id=listener_profile_id)

    lp = q.order_by(ListenerProfile.version.desc()).first()
    if not lp:
        return jsonify({"ok": False, "error": "Profile not found"}), 404

    profile_json = dict(lp.profile_json or {})
    token = str(profile_json.get("share_token") or "").strip()
    if not token:
        token = uuid.uuid4().hex
        profile_json["share_token"] = token
        lp.profile_json = profile_json
        db.session.commit()

    base_url = request.url_root.rstrip("/")
    share_url = f"{base_url}/vibe/{lp.id}/{token}"

    return jsonify({
        "ok": True,
        "listener_profile_id": lp.id,
        "share_url": share_url,
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
            "like_status": g.like_status,
            "created_at": g.created_at.isoformat() if g.created_at else None,
            "title": None,
            "cover_url": None,
            "audio_url": None,
            "similar_songs": None,
        }

        if g.status == "succeeded" and g.result_json:
            song = _extract_song(g.result_json)
            if song:
                item["title"] = song.get("title")
                item["cover_url"] = (
                    song.get("imageUrl") or song.get("sourceImageUrl")
                    or song.get("image_url") or song.get("source_image_url")
                )
                audio_url, _dl = _extract_urls(
                    song,
                    allow_playback=_is_generation_playable(g),
                )
                item["audio_url"] = audio_url
            item["similar_songs"] = (g.result_json or {}).get("similar_songs")

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
            "like_status": g.like_status,
            "created_at": g.created_at.isoformat() if g.created_at else None,
            "title": None,
            "cover_url": None,
            "audio_url": None,
            "similar_songs": None,
        }

        if g.status == "succeeded" and g.result_json:
            song = _extract_song(g.result_json)
            if song:
                item["title"] = song.get("title")
                item["cover_url"] = (
                    song.get("imageUrl") or song.get("sourceImageUrl")
                    or song.get("image_url") or song.get("source_image_url")
                )
                audio_url, _dl = _extract_urls(
                    song,
                    allow_playback=_is_generation_playable(g),
                )
                item["audio_url"] = audio_url
            item["similar_songs"] = (g.result_json or {}).get("similar_songs")

        items.append(item)

    return jsonify({"ok": True, "generations": items})


# ---------------------------------------------------------------------
# PATCH /api/generation/<id>/favourite
# Toggle or set is_favourite on a generation
# Body: { "is_favourite": true/false }
# ---------------------------------------------------------------------
@api_bp.patch("/generation/<int:generation_id>/favourite")
def toggle_favourite(generation_id: int):
    user_id = _require_session_user_id()
    if not user_id:
        return _json_error("Not logged in", 401, "unauthorized")

    gen = Generation.query.filter_by(id=generation_id, user_id=user_id).first()
    if not gen:
        return _json_error("Generation not found", 404, "not_found")

    body = request.get_json(silent=True) or {}
    if "is_favourite" in body:
        gen.is_favourite = bool(body["is_favourite"])
    else:
        # Toggle if no explicit value given
        gen.is_favourite = not gen.is_favourite

    db.session.commit()
    return _json_ok({"is_favourite": gen.is_favourite})


# ---------------------------------------------------------------------
# PATCH /api/generation/<id>/like
# Set like_status on a generation: "liked", "disliked", or null
# Body: { "like_status": "liked" | "disliked" | null }
# ---------------------------------------------------------------------
@api_bp.patch("/generation/<int:generation_id>/like")
def set_like_status(generation_id: int):
    user_id = _require_session_user_id()
    if not user_id:
        return _json_error("Not logged in", 401, "unauthorized")

    gen = Generation.query.filter_by(id=generation_id, user_id=user_id).first()
    if not gen:
        return _json_error("Generation not found", 404, "not_found")

    body = request.get_json(silent=True) or {}
    new_status = body.get("like_status")
    if new_status not in ("liked", "disliked", None):
        return _json_error("Invalid like_status", 400, "validation_error")

    gen.like_status = new_status
    db.session.commit()
    return _json_ok({"like_status": gen.like_status})


@api_bp.get("/analytics/generations/summary")
def generation_analytics_summary():
    user_id = _require_session_user_id()
    if not user_id:
        return _json_error("Not logged in", 401, "unauthorized")

    days_param = request.args.get("days", "30")
    try:
        days = int(days_param)
    except (TypeError, ValueError):
        return _json_error("days must be an integer", 400, "validation_error")

    if days < 1 or days > 365:
        return _json_error("days must be between 1 and 365", 400, "validation_error")

    cutoff = _utcnow() - timedelta(days=days)
    base_q = Generation.query.filter(Generation.user_id == user_id, Generation.created_at >= cutoff)

    total_generations = base_q.count()
    favourite_count = base_q.filter(Generation.is_favourite.is_(True)).count()
    favourite_rate = round((favourite_count / total_generations), 4) if total_generations else 0.0

    status_rows = (
        db.session.query(Generation.status, func.count(Generation.id))
        .filter(Generation.user_id == user_id, Generation.created_at >= cutoff)
        .group_by(Generation.status)
        .all()
    )
    status_breakdown = {str(status or "unknown"): int(count) for status, count in status_rows}

    mood_rows = (
        db.session.query(Generation.mood, func.count(Generation.id))
        .filter(Generation.user_id == user_id, Generation.created_at >= cutoff, Generation.mood.isnot(None))
        .group_by(Generation.mood)
        .order_by(func.count(Generation.id).desc())
        .limit(5)
        .all()
    )
    top_moods = [{"mood": str(mood), "count": int(count)} for mood, count in mood_rows if mood]

    activity_rows = (
        db.session.query(Generation.activity, func.count(Generation.id))
        .filter(Generation.user_id == user_id, Generation.created_at >= cutoff, Generation.activity.isnot(None))
        .group_by(Generation.activity)
        .order_by(func.count(Generation.id).desc())
        .limit(5)
        .all()
    )
    top_activities = [{"activity": str(activity), "count": int(count)} for activity, count in activity_rows if activity]

    avg_mood_intensity = (
        db.session.query(func.avg(Generation.mood_intensity))
        .filter(
            Generation.user_id == user_id,
            Generation.created_at >= cutoff,
            Generation.mood_intensity.isnot(None),
        )
        .scalar()
    )

    return _json_ok(
        {
            "window_days": days,
            "total_generations": total_generations,
            "favourite_count": favourite_count,
            "favourite_rate": favourite_rate,
            "status_breakdown": status_breakdown,
            "top_moods": top_moods,
            "top_activities": top_activities,
            "avg_mood_intensity": round(float(avg_mood_intensity), 4) if avg_mood_intensity is not None else None,
        }
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_generation_playable(gen: Generation) -> bool:
    if not gen or not gen.created_at:
        return False
    created_at = gen.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    cutoff = created_at + timedelta(minutes=PLAYBACK_WINDOW_MINUTES)
    return _utcnow() <= cutoff


def _sanitize_generation_result_for_client(result_json, allow_playback: bool):
    if not isinstance(result_json, dict):
        return result_json

    sanitized = deepcopy(result_json)

    download_keys = (
        "audioUrl",
        "sourceAudioUrl",
        "audio_url",
        "source_audio_url",
    )
    playback_keys = (
        "streamAudioUrl",
        "sourceStreamAudioUrl",
        "stream_audio_url",
        "source_stream_audio_url",
    )

    def scrub(node):
        if isinstance(node, dict):
            for key in download_keys:
                node.pop(key, None)
            if not allow_playback:
                for key in playback_keys:
                    node.pop(key, None)
            for value in node.values():
                scrub(value)
        elif isinstance(node, list):
            for value in node:
                scrub(value)

    scrub(sanitized)
    return sanitized


def _extract_urls(song, allow_playback: bool = True):
    """Return (audio_url, download_url) from a song object.

    audio_url    = stream URL for playback when generation is still playable
    download_url = always None (downloads are disabled)
    """
    if not song:
        return None, None

    # Downloads are intentionally disabled for all generated songs.
    download_url = None

    # Streaming URLs (for real-time playback)
    stream_url = (
        song.get("streamAudioUrl") or song.get("sourceStreamAudioUrl")
        or song.get("stream_audio_url") or song.get("source_stream_audio_url")
        or None
    )

    # Playback expires after the configured window.
    audio_url = stream_url if allow_playback else None

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


# ---------------------------------------------------------------------
# Psychoacoustic personality assessment
# ---------------------------------------------------------------------
@api_bp.get("/psychoacoustic/config")
def psychoacoustic_config():
    """Return a randomized test configuration for a new session."""
    try:
        cfg = generate_test_config()
        return jsonify({"ok": True, **cfg})
    except Exception as exc:
        log.exception("psychoacoustic config error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@api_bp.post("/psychoacoustic/submit")
def psychoacoustic_submit():
    """
    Receive all 30 answers, score them, save profile, return result.

    Expected body:
    {
      "user_id": int | null,
      "audio_answers": [{"id": str, "value": int(1-6), "swapped": bool}, ...],
      "text_answers":  [{"id": str, "value": int(1-6), "swapped": bool}, ...]
    }
    """
    body = request.get_json(force=True, silent=True) or {}
    user_id = _resolve_user_id(body)

    audio_answers = body.get("audio_answers", [])
    text_answers = body.get("text_answers", [])

    if len(audio_answers) != 17:
        return jsonify({"ok": False, "error": f"Expected 17 audio answers, got {len(audio_answers)}"}), 400
    if len(text_answers) != 13:
        return jsonify({"ok": False, "error": f"Expected 13 text answers, got {len(text_answers)}"}), 400

    try:
        result = score_test(audio_answers, text_answers)
    except Exception as exc:
        log.exception("psychoacoustic scoring error")
        return jsonify({"ok": False, "error": str(exc)}), 500

    # Build profile_json and explain_json for storage
    profile_json = {
        "profile_type": "psychoacoustic",
        "psychoacoustic_code": result["psychoacoustic_code"],
        "axis_scores": result["axis_scores"],
        "audio_preferences": result["audio_preferences"],
    }

    explain_json = {
        "profile_type": "psychoacoustic",
        "code": result["psychoacoustic_code"],
        "title": result["title"],
        "sections": result["sections"],
        "axis_scores": result["axis_scores"],
    }

    # Save to DB
    try:
        lp = ListenerProfile(
            user_id=user_id,
            profile_json=profile_json,
            explain_json=explain_json,
            built_from_track_count=0,
        )
        db.session.add(lp)
        db.session.commit()

        log.info("Psychoacoustic profile saved: id=%s user=%s code=%s",
                 lp.id, user_id, result["psychoacoustic_code"])
    except Exception as exc:
        db.session.rollback()
        log.exception("psychoacoustic DB save error")
        return jsonify({"ok": False, "error": "Failed to save profile"}), 500

    return jsonify({
        "ok": True,
        "profile": profile_json,
        "diagnosis": explain_json,
        "listener_profile_id": lp.id,
    })
