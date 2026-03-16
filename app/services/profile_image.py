from __future__ import annotations

import logging
from typing import Any, Dict, List

import requests

log = logging.getLogger("drvibey.profile_image")

IMAGEROUTER_ENDPOINT = "https://api.imagerouter.io/v1/openai/images/generations"
IMAGEROUTER_MODEL = "stabilityai/sdxl-turbo"


def normalize_avatar_identity(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "♂" in text or text in ("boy", "male", "man"):
        return "boy"
    if "♀" in text or text in ("girl", "female", "woman"):
        return "girl"
    if "⭐" in text or "star" in text:
        return "wonder"
    return "wonder"


def _safe_list(value: Any, max_len: int = 4) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        s = str(item or "").strip()
        if s:
            out.append(s)
        if len(out) >= max_len:
            break
    return out


def _build_avatar_prompt(profile: Dict[str, Any]) -> str:
    avatar_identity = normalize_avatar_identity(profile.get("avatar_identity", "wonder"))

    identity_line = {
        "boy": "Create a cute and awesome anime boy avatar.",
        "girl": "Create a cute and awesome anime girl avatar.",
        "wonder": "Create a cute and awesome anime creature/monster avatar (friendly, expressive, non-scary).",
    }[avatar_identity]

    listener_type = (
        (profile.get("listener_persona") or {}).get("listener_mbti_like")
        if isinstance(profile.get("listener_persona"), dict)
        else ""
    )

    genres = ", ".join(_safe_list(profile.get("dominant_genres"), 3))
    subgenres = ", ".join(_safe_list(profile.get("subgenres"), 3))
    vibes = ", ".join(_safe_list(profile.get("vibe_keywords"), 5))
    emotions = ", ".join(
        _safe_list(((profile.get("emotional_profile") or {}).get("primary_emotions")), 3)
    )

    style_blueprint = profile.get("style_blueprint") if isinstance(profile.get("style_blueprint"), dict) else {}
    style_vectors = ", ".join(_safe_list(style_blueprint.get("style_vectors"), 4))
    arrangement = str(style_blueprint.get("arrangement_preferences") or "").strip()
    dynamic_profile = str(style_blueprint.get("dynamic_profile") or "").strip()
    mix_character = str(style_blueprint.get("mix_character") or "").strip()

    production_traits = profile.get("production_traits") if isinstance(profile.get("production_traits"), dict) else {}
    drums = str(production_traits.get("drums") or "").strip()
    bass = str(production_traits.get("bass") or "").strip()
    melody = str(production_traits.get("melody") or "").strip()
    vocals = str(production_traits.get("vocals") or "").strip()

    hints = profile.get("prompt_translation_hints") if isinstance(profile.get("prompt_translation_hints"), dict) else {}
    must_include = ", ".join(_safe_list(hints.get("must_include"), 5))
    avoid = ", ".join(_safe_list(hints.get("avoid"), 4))

    tempo_pref = str(profile.get("tempo_preference") or "varied").strip()
    energy = profile.get("energy_range") if isinstance(profile.get("energy_range"), dict) else {}
    energy_low = float(energy.get("low") or 0.2)
    energy_high = float(energy.get("high") or 0.8)

    context_prefs = profile.get("contextual_preferences") if isinstance(profile.get("contextual_preferences"), dict) else {}
    focus_work = str(context_prefs.get("focus_work") or "").strip()
    active_energy = str(context_prefs.get("active_energy") or "").strip()

    prompt = (
        "Anime portrait avatar, head-and-shoulders composition, clean background, high detail, soft lighting, highly stylized and cohesive art direction. "
        f"{identity_line} "
        "Make it visually personalized to this listener profile while keeping it adorable and striking. "
        f"Listener type: {listener_type or 'unknown'}. "
        f"Genres: {genres or 'mixed modern genres'}. "
        f"Subgenres: {subgenres or 'none'}. "
        f"Vibe keywords: {vibes or 'emotive, textured, atmospheric'}. "
        f"Primary emotions: {emotions or 'deep emotional resonance'}. "
        f"Style vectors: {style_vectors or 'cinematic anime pop, atmospheric glow, emotional detail'}. "
        f"Arrangement feel: {arrangement or 'balanced emotional arc with clear focal motifs'}. "
        f"Dynamics: {dynamic_profile or 'controlled swells with dramatic highlights'}. "
        f"Mix character: {mix_character or 'cinematic color harmony and expressive contrast'}. "
        f"Production cues - drums: {drums or 'clean but punchy'}, bass: {bass or 'rounded and expressive'}, melody: {melody or 'memorable and emotive'}, vocals: {vocals or 'airy and intimate'}. "
        f"Prompt must-include cues: {must_include or 'cohesive color story, expressive eyes, music-reactive styling'}. "
        f"Prompt avoid cues: {avoid or 'generic character design, cluttered composition'}. "
        f"Tempo preference: {tempo_pref}. Energy contour: {energy_low:.2f} to {energy_high:.2f}. "
        f"Context vibe: focus={focus_work or 'immersive clarity'}, active={active_energy or 'kinetic confidence'}. "
        "No text, no watermark, no logo, no UI, no real-person likeness, no celebrity resemblance, no explicit content."
    )
    return prompt


def _extract_image_url(response_json: Dict[str, Any]) -> str:
    if not isinstance(response_json, dict):
        return ""

    candidates: List[str] = []

    direct = response_json.get("url")
    if isinstance(direct, str) and direct.strip():
        candidates.append(direct.strip())

    data = response_json.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ("url", "image_url", "source_image_url"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
    elif isinstance(data, dict):
        for key in ("url", "image_url", "source_image_url"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

    for key in ("image_url", "source_image_url"):
        value = response_json.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    return candidates[0] if candidates else ""


def generate_profile_avatar_url(profile: Dict[str, Any], api_key: str, timeout_s: int = 12) -> str:
    if not api_key:
        return ""

    prompt = _build_avatar_prompt(profile if isinstance(profile, dict) else {})
    payload = {
        "prompt": prompt,
        "model": IMAGEROUTER_MODEL,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            IMAGEROUTER_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=timeout_s,
        )
        if resp.status_code >= 400:
            log.warning("ImageRouter avatar generation failed status=%s body=%s", resp.status_code, resp.text[:300])
            return ""

        obj = resp.json() if resp.content else {}
        url = _extract_image_url(obj)
        if not url:
            log.warning("ImageRouter avatar response had no URL keys")
        return url
    except Exception:
        log.exception("ImageRouter avatar generation call failed")
        return ""
