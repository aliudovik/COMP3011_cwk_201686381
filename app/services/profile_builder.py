# app/services/profile_builder.py
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

#from openai import OpenAI
from cerebras.cloud.sdk import Cerebras

from app.models import ProviderAccount, TrackCandidate
from app.services.profile_image import generate_profile_avatar_url, normalize_avatar_identity
from app.services.type_catalog import TYPE_CATALOG


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_text(resp: Any) -> str:
    if hasattr(resp, "output_text"):
        try:
            return (resp.output_text or "").strip()
        except Exception:
            pass
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def _derive_listener_type(axes: Dict[str, Any]) -> str:
    intensity = _clamp01(axes.get("intensity_seeking"), 0.5)
    novelty = _clamp01(axes.get("novelty_drive"), 0.5)
    openness = _clamp01(axes.get("emotional_openness"), 0.5)
    introspection = _clamp01(axes.get("introspection_bias"), 0.5)

    c1 = "F" if novelty >= 0.5 else "N"
    c2 = "V" if openness >= 0.5 else "I"
    c3 = "P" if intensity >= 0.5 else "C"
    c4 = "D" if introspection >= 0.5 else "R"
    return f"{c1}{c2}{c3}{c4}"


def _normalize_listener_type_code(raw_code: Any, axes: Dict[str, Any]) -> str:
    code = str(raw_code or "").strip().upper()
    if code in TYPE_CATALOG:
        return code
    return _derive_listener_type(axes)


def _needs_soul_signature_rewrite(raw: Any) -> bool:
    text = str(raw or "").strip()
    if not text:
        return True

    words = text.split()
    wc = len(words)
    if wc < 45 or wc > 130:
        return True

    low = text.lower()
    if re.search(r"\b\d{2,3}\s?bpm\b", low):
        return True

    technical_tokens = [
        "genre",
        "subgenre",
        "bpm",
        "tempo",
        "production",
        "mix",
        "style tags",
        "style vectors",
        "vocal-led",
        "instrumental-led",
    ]
    hits = sum(1 for tok in technical_tokens if tok in low)
    return hits >= 2


def _build_soul_signature(profile: Dict[str, Any]) -> str:
    artists = profile.get("identity_artists") if isinstance(profile.get("identity_artists"), list) else []
    anchor = str(artists[0]).strip() if artists else ""
    anchor_line = f", especially {anchor}," if anchor else ""

    return (
        "You feel music in full color: every bassline, pause, and vocal shade lands straight in your chest. "
        f"The artists you return to{anchor_line} mirror how deep and intentional your inner world is - intense, tender, and impossible to fake. "
        "You naturally choose songs that turn heavy feelings into beauty and momentum, and that is your gift: "
        "you make emotion look graceful, then pass that spark to everyone around you."
    )


# Change type hint from client: OpenAI to client: Cerebras
def _call_openai_for_json(client: Cerebras, model: str, system: str, user_obj: Dict[str, Any], temperature: float = 1) -> Tuple[Dict[str, Any], str]:
    raw = ""

    if hasattr(client, "responses"):
        try:
            r = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
                ],
                temperature=temperature,
            )
            raw = _extract_text(r)
        except Exception:
            raw = ""

    if not raw:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
            ],
            temperature=temperature,
        )
        raw = _extract_text(r)

    try:
        obj = json.loads(raw)
        return (obj if isinstance(obj, dict) else {}), raw
    except Exception:
        return {}, raw


def _collect_candidate_tracks(session, user_id: int, max_tracks: int) -> List[Dict[str, Any]]:
    q = (
        session.query(TrackCandidate)
        .join(ProviderAccount, TrackCandidate.provider_account_id == ProviderAccount.id)
        .filter(ProviderAccount.user_id == user_id)
        .order_by(TrackCandidate.observed_at.desc())
        .limit(int(max_tracks))
    )
    rows = q.all()

    tracks: List[Dict[str, Any]] = []
    seen = set()

    for c in rows:
        title = (c.title or "").strip()
        artists = (c.artists or "").strip()
        if not title or not artists:
            continue

        key = (artists.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)

        tracks.append(
            {
                "title": title,
                "artists": artists,
                "album": (c.album or "").strip() or None,
                "duration_ms": c.duration_ms,
                "isrc": (c.isrc or "").strip() or None,
                "popularity": c.popularity,
                "source": c.source,
            }
        )

    return tracks


def infer_taste_profile_with_openai(cerebras_api_key: str, tracks: List[Dict[str, Any]], model: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    if not cerebras_api_key:
        profile = {
            "method": "metadata_persona_inference",
            "model": model,
            "generated_at": _utc_now_iso(),
            "input_track_count": len(tracks),
            "confidence": "low",
            "dominant_genres": [],
            "subgenres": [],
            "vibe_keywords": [],
            "instrumentation": [],
            "production_traits": {},
            "tempo_preference": "varied",
            "energy_range": {"low": 0.2, "high": 0.8},
            "identity_artists": [],
            "listening_orientation": "vibe",
            "discovery_drive": 0.5,
            "emotion_regulation_strategy": "varied",
            "avatar_identity": "wonder",
            "profile_avatar_url": "",
            "contextual_preferences": {
                "focus_work": "",
                "active_energy": "",
                "emotional_processing": "",
                "social": "",
            },
            "suggested_artists": [],
            "listener_persona": {
                "archetype_name": "The Resonance Seeker",
                "listener_mbti_like": "FVCD",
                "temperament_axes": {
                    "intensity_seeking": 0.5,
                    "emotional_openness": 0.5,
                    "novelty_drive": 0.5,
                    "rhythmic_dependence": 0.5,
                    "introspection_bias": 0.5,
                },
                "explanation": "Insufficient data for a detailed music personality profile.",
            },
            "style_blueprint": {
                "style_vectors": [],
                "arrangement_preferences": "",
                "dynamic_profile": "",
                "vocal_treatment": "",
                "mix_character": "",
            },
            "prompt_translation_hints": {
                "must_include": [],
                "avoid": [],
                "tempo_targets": [],
                "energy_targets": [],
                "context_variants": {
                    "focus_work": "",
                    "active_energy": "",
                    "social": "",
                    "emotional_release": "",
                },
            },
            "summary": "Insufficient data to infer taste profile (CEREBRAS not set).",
            "soul_signature": "You have a rare ear for songs that feel emotionally true without becoming messy. You gravitate toward tracks where atmosphere, rhythm, and vocal color lock together with intent, so even high energy feels controlled and personal. Your taste signals discernment: you don't just want catchy moments, you want records that mirror your inner tempo and make your emotions feel understood.",
        }
        explain = {"notes": "Set CEREBRAS_API_KEY to enable metadata-based taste inference.", "top_influences": []}
        trace = {"note": "CEREBRAS_API_KEY not set; deterministic fallback used."}
        return profile, explain, trace

    compact = [f"{t['artists']} — {t['title']}" for t in tracks[:60]]

    system = (
        "Infer a deep listener taste profile from listening-history metadata (artist and track title only). "
        "Return ONLY valid JSON. Do NOT include lyrics. Do NOT request audio. "
        "Use concrete genre and production language, and include a music-personality profile with one of these codes only: "
        "FVPD, FVPR, FVCD, FVCR, FIPD, FIPR, FICD, FICR, NVPD, NVPR, NVCD, NVCR, NIPD, NIPR, NICD, NICR. "
        "Write soul_signature as a flattering but profile-grounded second-person portrait, around 60-90 words. "
        "soul_signature must be emotional and personal, not a technical style summary. Avoid BPM numbers and avoid genre-list wording. "
        "Output keys exactly as requested in output_schema."
    )

    user_obj = {
        "tracks": compact,
        "rules": [
            "Only use metadata (artist/title).",
            "No lyrics.",
            "Keep descriptions genre/production-based.",
            "Map output for direct prompt generation usage.",
        ],
        "output_schema": {
            "confidence": "low|medium|high",
            "dominant_genres": ["string"],
            "subgenres": ["string"],
            "vibe_keywords": ["string"],
            "instrumentation": ["string"],
            "production_traits": {"drums": "string", "bass": "string", "melody": "string", "mixing": "string", "vocals": "string"},
            "tempo_preference": "slow|mid|fast|varied",
            "energy_range": {"low": 0.0, "high": 1.0},
            "identity_artists": ["string"],
            "suggested_artists": ["string"],
            "listening_orientation": "lyrics|production|vibe|voice",
            "discovery_drive": 0.0,
            "emotion_regulation_strategy": "comfort|challenge|both|varied",
            "avatar_identity": "boy|girl|wonder",
            "contextual_preferences": {
                "focus_work": "string",
                "active_energy": "string",
                "emotional_processing": "string",
                "social": "string"
            },
            "listener_persona": {
                "archetype_name": "string",
                "listener_mbti_like": "one of: FVPD|FVPR|FVCD|FVCR|FIPD|FIPR|FICD|FICR|NVPD|NVPR|NVCD|NVCR|NIPD|NIPR|NICD|NICR",
                "temperament_axes": {
                    "intensity_seeking": 0.0,
                    "emotional_openness": 0.0,
                    "novelty_drive": 0.0,
                    "rhythmic_dependence": 0.0,
                    "introspection_bias": 0.0
                },
                "explanation": "string"
            },
            "style_blueprint": {
                "style_vectors": ["string"],
                "arrangement_preferences": "string",
                "dynamic_profile": "string",
                "vocal_treatment": "string",
                "mix_character": "string"
            },
            "prompt_translation_hints": {
                "must_include": ["string"],
                "avoid": ["string"],
                "tempo_targets": ["string"],
                "energy_targets": ["string"],
                "context_variants": {
                    "focus_work": "string",
                    "active_energy": "string",
                    "social": "string",
                    "emotional_release": "string"
                }
            },
            "summary": "string",
            "soul_signature": "around 60-90 words in second person, flattering but tied to the profile evidence",
            "explainability": {"notes": "string", "top_influences": [{"track": "Artist — Title", "because": "string"}]},
        },
    }

    client = Cerebras(api_key=cerebras_api_key)
    obj, raw = _call_openai_for_json(client, model=model, system=system, user_obj=user_obj, temperature=1)

    profile: Dict[str, Any] = {
        "method": "metadata_persona_inference",
        "model": model,
        "generated_at": _utc_now_iso(),
        "input_track_count": len(tracks),
        "confidence": obj.get("confidence") or "medium",
        "dominant_genres": obj.get("dominant_genres") or [],
        "subgenres": obj.get("subgenres") or [],
        "vibe_keywords": obj.get("vibe_keywords") or [],
        "instrumentation": obj.get("instrumentation") or [],
        "production_traits": obj.get("production_traits") or {},
        "tempo_preference": obj.get("tempo_preference") or "varied",
        "energy_range": obj.get("energy_range") or {"low": 0.2, "high": 0.8},
        "identity_artists": obj.get("identity_artists") or [],
        "suggested_artists": obj.get("suggested_artists") or [],
        "listening_orientation": obj.get("listening_orientation") or "vibe",
        "discovery_drive": obj.get("discovery_drive") if obj.get("discovery_drive") is not None else 0.5,
        "emotion_regulation_strategy": obj.get("emotion_regulation_strategy") or "varied",
        "avatar_identity": normalize_avatar_identity(obj.get("avatar_identity") or "wonder"),
        "profile_avatar_url": "",
        "contextual_preferences": obj.get("contextual_preferences") or {},
        "listener_persona": obj.get("listener_persona") or {},
        "style_blueprint": obj.get("style_blueprint") or {},
        "prompt_translation_hints": obj.get("prompt_translation_hints") or {},
        "summary": obj.get("summary") or "Taste profile inferred from track metadata.",
        "soul_signature": obj.get("soul_signature") or "",
    }

    if not isinstance(profile.get("suggested_artists"), list):
        profile["suggested_artists"] = []
    profile["suggested_artists"] = [str(x).strip() for x in profile.get("suggested_artists", []) if str(x).strip()][:8]

    if _needs_soul_signature_rewrite(profile.get("soul_signature")):
        profile["soul_signature"] = _build_soul_signature(profile)

    listener_persona = profile.get("listener_persona")
    if not isinstance(listener_persona, dict):
        listener_persona = {}
    axes = listener_persona.get("temperament_axes")
    if not isinstance(axes, dict):
        axes = {
            "intensity_seeking": 0.5,
            "emotional_openness": 0.5,
            "novelty_drive": float(profile.get("discovery_drive") or 0.5),
            "rhythmic_dependence": 0.5,
            "introspection_bias": 0.5,
        }
    listener_persona.setdefault("archetype_name", "The Resonance Seeker")
    listener_persona.setdefault("temperament_axes", axes)
    listener_persona["listener_mbti_like"] = _normalize_listener_type_code(
        listener_persona.get("listener_mbti_like"),
        axes,
    )
    listener_persona.setdefault(
        "explanation",
        "You gravitate to emotionally resonant production signatures and return to tracks that match your inner pacing.",
    )
    profile["listener_persona"] = listener_persona

    if not isinstance(profile.get("style_blueprint"), dict):
        profile["style_blueprint"] = {}
    profile["style_blueprint"].setdefault("style_vectors", profile.get("subgenres")[:3])
    profile["style_blueprint"].setdefault("arrangement_preferences", "dynamic but coherent builds")
    profile["style_blueprint"].setdefault("dynamic_profile", "mid-to-high contrast")
    profile["style_blueprint"].setdefault("vocal_treatment", "profile dependent")
    profile["style_blueprint"].setdefault("mix_character", "clear low-end with intentional texture")

    if not isinstance(profile.get("prompt_translation_hints"), dict):
        profile["prompt_translation_hints"] = {}
    profile["prompt_translation_hints"].setdefault("must_include", profile.get("vibe_keywords")[:4])
    profile["prompt_translation_hints"].setdefault("avoid", ["generic arrangement", "flat dynamics"])
    profile["prompt_translation_hints"].setdefault("tempo_targets", [profile.get("tempo_preference", "varied")])
    profile["prompt_translation_hints"].setdefault("energy_targets", ["dynamic", "emotionally shaped"])
    profile["prompt_translation_hints"].setdefault(
        "context_variants",
        {
            "focus_work": "clean transient control, low vocal distraction",
            "active_energy": "strong rhythmic propulsion, assertive low end",
            "social": "immediate hook and broad accessibility",
            "emotional_release": "high emotional contour and depth",
        },
    )

    imagerouter_api_key = os.getenv("IMAGEROUTER_API_KEY", "").strip()
    profile["profile_avatar_url"] = generate_profile_avatar_url(profile=profile, api_key=imagerouter_api_key)

    explain = obj.get("explainability") if isinstance(obj.get("explainability"), dict) else {}
    explain_json: Dict[str, Any] = {"notes": explain.get("notes") or "", "top_influences": explain.get("top_influences") or []}

    prompt_trace = {"system": system, "user": user_obj, "raw": raw, "model": model}

    return profile, explain_json, prompt_trace


def build_profile(user_id: int, session, cerebras_api_key: str, model: str, max_tracks: int = 200) -> Dict[str, Any]:
    candidates = _collect_candidate_tracks(session, user_id=user_id, max_tracks=max_tracks)

    profile_json, explain_json, prompt_trace = infer_taste_profile_with_openai(
        cerebras_api_key=cerebras_api_key,
        tracks=candidates,
        model=model,
    )

    explain_json = dict(explain_json or {})
    explain_json["_openai_trace"] = prompt_trace

    return {"built_from_track_count": len(candidates), "profile_json": profile_json, "explain_json": explain_json}
