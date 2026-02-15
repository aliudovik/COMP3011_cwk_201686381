# app/services/openai_prompt.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

# from openai import OpenAI
from cerebras.cloud.sdk import Cerebras

from app.services.moods import MOOD_SHIFTS, MOODS
from app.services.activities import ACTIVITIES, ACTIVITY_SHIFTS


def _mood_label(mood_id: str) -> str:
    for m in MOODS:
        if m.get("id") == mood_id:
            return m.get("label") or mood_id
    return mood_id


def _activity_label(activity_id: str) -> str:
    for a in ACTIVITIES:
        if a.get("id") == activity_id:
            return a.get("label") or activity_id
    return activity_id


def _compact_profile_context(profile_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Works with:
      - your OpenAI-inferred profile schema (dominant_genres/subgenres/vibe_keywords/production_traits/etc.)
      - fallback for older profile schemas (if any)
    """
    if not isinstance(profile_json, dict):
        return {}

    # Newer “OpenAI inferred”
    if profile_json.get("method") == "openai_metadata_inference" or "dominant_genres" in profile_json:
        keep = {
            "summary": profile_json.get("summary"),
            "dominant_genres": profile_json.get("dominant_genres"),
            "subgenres": profile_json.get("subgenres"),
            "vibe_keywords": profile_json.get("vibe_keywords"),
            "instrumentation": profile_json.get("instrumentation"),
            "production_traits": profile_json.get("production_traits"),
            "confidence": profile_json.get("confidence"),
        }
        return {k: v for k, v in keep.items() if v}

    # Fallback (older schema)
    keep = {
        "tempo_bpm": profile_json.get("tempo_bpm"),
        "key_mode_top": profile_json.get("key_mode_top"),
        "genre_buckets_top": profile_json.get("genre_buckets_top"),
        "mood_buckets_top": profile_json.get("mood_buckets_top"),
        "summary": profile_json.get("summary"),
    }
    return {k: v for k, v in keep.items() if v}


def _extract_text(resp: Any) -> str:
    # Responses API
    if hasattr(resp, "output_text"):
        try:
            return (resp.output_text or "").strip()
        except Exception:
            pass
    # Chat Completions
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _call_openai_for_json(client: Cerebras, model: str, system: str, user_obj: Dict[str, Any], temperature: float) -> Tuple[Dict[str, Any], str]:
    raw = ""

    # Try Responses API first
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

    # Fallback to chat.completions
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


def generate_suno_payload_with_openai(
    cerebras_api_key: str,
    model: str,
    profile_json: Dict[str, Any],
    mood_id: str,
    mood_intensity: float = 0.5,
    activity_id: str = None,
    instrumental: bool = False,
    song_reference: str = None,
    genre_override: str = None,
    bpm_target: int = None,
    custom_mode: bool = False,
    title_hint: str = "",
    style_hint: str = "",
) -> Dict[str, Any]:
    """
    Returns:
      {
        "openai_prompt": {...trace...},
        "suno_payload": {
            "prompt": "...",
            "instrumental": true/false,
            "customMode": true/false,
            "title": "...",
            "style": "...",
            "model": "V4_5ALL"
        }
      }
    """
    mood_label = _mood_label(mood_id)
    shift = MOOD_SHIFTS.get(mood_id, {})
    ctx = _compact_profile_context(profile_json)

    # Resolve activity context
    act_label = _activity_label(activity_id) if activity_id else None
    act_shift = ACTIVITY_SHIFTS.get(activity_id, {}) if activity_id else {}

    # Deterministic fallback if no key
    if not cerebras_api_key:
        title = (title_hint or f"{mood_label} Echo").strip()
        style = (style_hint or "highly personalised to listener profile").strip()
        extra_parts = []
        if act_label:
            extra_parts.append(f"Activity: {act_label}.")
        if genre_override:
            extra_parts.append(f"Genre: {genre_override}.")
        if bpm_target:
            extra_parts.append(f"Target BPM: {bpm_target}.")
        extras = " ".join(extra_parts)
        prompt = (
            f"Title: {title}\n"
            f"Style: {style}\n\n"
            f"Create an original {mood_label.lower()} track (intensity {mood_intensity:.0%}) "
            f"aligned with the listener profile, applying this mood shift: {shift}. "
            f"{extras} "
            "No lyrics. No artist names. No references to specific songs. "
            f"Listener taste summary: {ctx.get('summary','')}\n"
        ).strip()

        return {
            "openai_prompt": {"note": "OPENAI_API_KEY not set; deterministic prompt used", "ctx": ctx},
            "suno_payload": {
                "prompt": prompt,
                "instrumental": bool(instrumental),
                "customMode": bool(custom_mode),
                "title": title,
                "style": style,
                "model": "V5",
            },
        }

    #model = os.getenv("OPENAI_MODEL", "gpt-5-nano")
    client = Cerebras(api_key=cerebras_api_key)

    system = (
        "You write extremely effective prompts for Suno-style music generation.\n"
        "Return ONLY valid JSON with keys: title, style_tags, prompt.\n"
        "Hard rules:\n"
        "- Do NOT include any artist names or track titles.\n"
        "- Do NOT include lyrics or lyric-writing instructions.\n"
        "- If a style_reference_song is provided, extract ONLY its sonic/production characteristics. "
        "NEVER include the song title or artist name in your output.\n"
        "- Focus on sound, production, arrangement, energy, textures, drums/bass/melody/mix.\n"
        "- Keep it highly personalized to the given listener profile.\n"
        "- If an activity context is given, tailor the track to suit that activity.\n"
        "- If a genre_preference is given, bias toward that genre.\n"
        "- If a target_bpm is given, aim for that tempo.\n"
        "- mood_intensity ranges from 0 (subtle) to 1 (extreme). Scale the mood qualities accordingly.\n"
        "- Keep prompt under 180 characters. EXTREMELY IMPORTANT, DO NOT BREAK THIS RULE!\n"
    )

    user_obj = {
        "target_mood": mood_label,
        "mood_intensity": mood_intensity,
        "mood_shift": shift,
        "activity": act_label,
        "activity_shift": act_shift,
        "instrumental": bool(instrumental),
        "style_reference_song": song_reference or None,
        "genre_preference": genre_override or None,
        "target_bpm": bpm_target or None,
        "listener_profile_compact": ctx,
        "user_hints": {"title_hint": title_hint or None, "style_hint": style_hint or None},
        "output_schema": {
            "title": "string",
            "style_tags": ["string"],
            "prompt": "string (no lyrics)",
        },
    }

    obj, raw = _call_openai_for_json(client, model=model, system=system, user_obj=user_obj, temperature=1)

    title = (obj.get("title") or title_hint or f"{mood_label} Echo").strip()
    style_tags = obj.get("style_tags")
    if not isinstance(style_tags, list):
        style_tags = []
    style_tags = [str(x).strip() for x in style_tags if str(x).strip()][:8]

    body = (obj.get("prompt") or "").strip()
    if not body:
        body = f"Create an original {mood_label.lower()} track aligned with the listener profile and mood shift: {shift}. No lyrics."

    style_line = (style_hint or ", ".join(style_tags) or "highly personalised").strip()
    final_prompt = f"Title: {title}\nStyle: {style_line}\n\n{body}".strip()

    return {
        "openai_prompt": {"system": system, "user": user_obj, "raw": raw, "model": model},
        "suno_payload": {
            "prompt": final_prompt,
            "instrumental": bool(instrumental),
            "customMode": bool(custom_mode),
            "title": title,
            "style": style_line,
            "model": "V4_5ALL",
        },
    }
