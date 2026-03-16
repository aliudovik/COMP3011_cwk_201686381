# app/services/openai_prompt.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

# from openai import OpenAI
from cerebras.cloud.sdk import Cerebras

from app.services.moods import MOOD_SHIFTS, MOODS
from app.services.activities import ACTIVITIES, ACTIVITY_SHIFTS


MOOD_ARTIST_AFFINITY = {
    "aggressive": ["metal", "hard rock", "punk", "trap", "drill", "industrial", "heavy"],
    "romantic": ["r&b", "soul", "pop", "ballad", "indie pop", "synthpop"],
    "energetic": ["edm", "dance", "electro", "house", "techno", "drum and bass", "hyperpop"],
    "sad": ["indie", "alt pop", "sad", "melancholic", "ambient", "lo-fi", "ballad"],
    "chill": ["lo-fi", "ambient", "downtempo", "chill", "neo soul", "trip-hop"],
    "focus": ["minimal", "ambient", "instrumental", "post-rock", "classical", "jazz"],
    "happy": ["pop", "funk", "disco", "dance", "afrobeats", "latin pop"],
}


GENRE_ENHANCER_PALETTES = {
    "pop_indie": ["Ethereal", "Shimmering", "Glossy", "Euphoric", "Anthemic", "Crystalline", "Lush", "Atmospheric"],
    "rock_metal_alt": ["Soaring", "Resonant", "Dynamic", "Cathartic", "Driving", "Gritty", "Stadium-sized", "Electrifying"],
    "hiphop_rnb_soul": ["Silky", "Sultry", "Groovy", "Velvety", "Punchy", "Laid-back", "Soulful", "Deep-pocket"],
    "electronic_edm": ["Hypnotic", "Immersive", "Pulsating", "Kinetic", "Astral", "Spacious", "Futuristic", "Panoramic"],
    "jazz_blues_classical": ["Smoky", "Intimate", "Sophisticated", "Virtuosic", "Timeless", "Majestic", "Nuanced", "Cinematic"],
    "acoustic_folk_country": ["Organic", "Earthy", "Stripped-back", "Heartfelt", "Gentle", "Introspective", "Storytelling", "Serene"],
}


STRUCTURE_ARCHETYPES = {
    "radio_hit": "[Intro] [Verse 1] [Pre-Chorus] [Chorus] [Verse 2] [Pre-Chorus] [Chorus] [Bridge] [Final Chorus] [Outro]",
    "club_electronic": "[Intro] [Verse] [Build Up] [Drop] [Verse 2] [Build Up] [Drop] [Breakdown] [Final Drop] [Outro]",
    "flow_bars": "[Intro] [Chorus] [Verse 1] [Chorus] [Verse 2] [Bridge] [Chorus] [Outro]",
    "journey": "[Part 1: Beginning] [Part 2: Ascent] [Instrumental Interlude] [Part 3: Climax] [Part 4: Resolution] [Finale] [End]",
}


VIBRANT_IMAGE_DOMAINS = [
    "neon city rain",
    "2AM coastal highway",
    "golden-hour skyline",
    "foggy afterparty room",
    "empty dance floor before sunrise",
    "late-night train window reflections",
]

LANGUAGE_DIRECTIVES = {
    "english": "English",
    "spanish": "Spanish",
    "french": "French",
    "chinese": "Chinese",
    "korean": "Korean",
    "japanese": "Japanese",
    "russian": "Russian",
}


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
      - psychoacoustic profile schema (psychoacoustic_code/axis_scores/audio_preferences)
      - fallback for older profile schemas (if any)
    """
    if not isinstance(profile_json, dict):
        return {}

    # Psychoacoustic profile schema
    # TODO: Expand this to derive richer generation hints from audio_preferences
    #       (e.g., average tempo from preferred clips, spectral characteristics, etc.)
    if profile_json.get("profile_type") == "psychoacoustic":
        code = profile_json.get("psychoacoustic_code", "")
        axis_scores = profile_json.get("axis_scores", {})
        audio_prefs = profile_json.get("audio_preferences", {})

        # Derive basic generation hints from axis scores
        hints = {"psychoacoustic_code": code}
        for ax_num, ax_data in axis_scores.items():
            if isinstance(ax_data, dict):
                hints[f"axis_{ax_num}_{ax_data.get('axis_name', '')}"] = {
                    "dominant": ax_data.get("dominant_pole"),
                    "pct": ax_data.get("percentage"),
                }

        # Extract tempo hints from preferred audio files
        tempos = []
        for _key, pref in audio_prefs.items():
            if isinstance(pref, dict):
                feats = pref.get("preferred_features", {})
                if isinstance(feats, dict) and "tempo_bpm" in feats:
                    tempos.append(feats["tempo_bpm"])
        if tempos:
            hints["preferred_avg_tempo"] = round(sum(tempos) / len(tempos), 1)

        return hints

    # Newer persona profile schema
    if "dominant_genres" in profile_json or "style_blueprint" in profile_json:
        keep = {
            "summary": profile_json.get("summary"),
            "dominant_identity": {
                "listener_type": (profile_json.get("listener_persona") or {}).get("listener_mbti_like")
                if isinstance(profile_json.get("listener_persona"), dict)
                else None,
                "emotion_regulation_strategy": profile_json.get("emotion_regulation_strategy"),
                "listening_orientation": profile_json.get("listening_orientation"),
            },
            "dominant_genres": profile_json.get("dominant_genres"),
            "subgenres": profile_json.get("subgenres"),
            "vibe_keywords": profile_json.get("vibe_keywords"),
            "instrumentation": profile_json.get("instrumentation"),
            "production_traits": profile_json.get("production_traits"),
            "tempo_preference": profile_json.get("tempo_preference"),
            "energy_range": profile_json.get("energy_range"),
            "style_blueprint": profile_json.get("style_blueprint"),
            "prompt_translation_hints": profile_json.get("prompt_translation_hints"),
            "contextual_preferences": profile_json.get("contextual_preferences"),
            "suggested_artists": profile_json.get("suggested_artists"),
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


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def _compact_text(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip()


def _dedupe_keep_order(items: List[Any]) -> List[Any]:
    out: List[Any] = []
    seen = set()
    for item in items:
        key = str(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(item).strip())
    return out


def _collect_banned_phrases(profile_json: Dict[str, Any], analysis_obj: Dict[str, Any]) -> List[str]:
    phrases: List[str] = []

    anchors = _extract_identity_anchors(profile_json or {})
    for row in anchors.get("selected_artists", []):
        if isinstance(row, dict):
            phrases.append(str(row.get("artist") or "").strip())
    for row in anchors.get("selected_songs", []):
        if isinstance(row, dict):
            phrases.append(str(row.get("title") or "").strip())
            phrases.append(str(row.get("artist") or "").strip())

    artist_blend_plan = analysis_obj.get("artist_blend_plan") if isinstance(analysis_obj, dict) else []
    if isinstance(artist_blend_plan, list):
        for row in artist_blend_plan:
            if isinstance(row, dict):
                phrases.append(str(row.get("artist") or "").strip())

    song_anchor_focus = analysis_obj.get("song_anchor_focus") if isinstance(analysis_obj, dict) else []
    if isinstance(song_anchor_focus, list):
        for row in song_anchor_focus:
            if isinstance(row, dict):
                phrases.append(str(row.get("title") or "").strip())
                phrases.append(str(row.get("artist") or "").strip())

    cleaned = [p for p in _dedupe_keep_order(phrases) if len(p) >= 2]
    cleaned.sort(key=len, reverse=True)
    return cleaned


def _strip_banned_phrases(text: str, banned_phrases: List[str]) -> str:
    out = str(text or "")
    if not out or not banned_phrases:
        return out

    for phrase in banned_phrases:
        pattern = re.compile(rf"(?i)\\b{re.escape(phrase)}\\b")
        out = pattern.sub("", out)

    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r",\s*,+", ", ", out)
    out = re.sub(r"\(\s*\)", "", out)
    return out.strip(" ,;:-")


def _extract_identity_anchors(profile_json: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    identity_weights = profile_json.get("identity_anchor_weights") if isinstance(profile_json, dict) else {}
    if not isinstance(identity_weights, dict):
        identity_weights = {}

    selected_artists = identity_weights.get("selected_artists")
    if not isinstance(selected_artists, list):
        selected_artists = []
    selected_songs = identity_weights.get("selected_songs")
    if not isinstance(selected_songs, list):
        selected_songs = []

    artist_rows: List[Dict[str, Any]] = []
    for row in selected_artists:
        if not isinstance(row, dict):
            continue
        artist = str(row.get("artist") or "").strip()
        if not artist:
            continue
        artist_rows.append({"artist": artist, "weight": float(row.get("weight") or 1.0)})

    if not artist_rows:
        fallback_artists = profile_json.get("identity_artists") if isinstance(profile_json, dict) else []
        if isinstance(fallback_artists, list):
            for artist in fallback_artists[:3]:
                a = str(artist or "").strip()
                if a:
                    artist_rows.append({"artist": a, "weight": 1.0})

    song_rows: List[Dict[str, Any]] = []
    for row in selected_songs:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        artist = str(row.get("artist") or "").strip()
        if not title:
            continue
        song_rows.append({"title": title, "artist": artist, "weight": float(row.get("weight") or 1.0)})

    return {
        "selected_artists": artist_rows[:3],
        "selected_songs": song_rows[:3],
    }


def _listener_novelty_mode(profile_json: Dict[str, Any]) -> Dict[str, Any]:
    discovery_drive = _clamp01((profile_json or {}).get("discovery_drive"), 0.5)

    explicit_mode = ""
    contextual = (profile_json or {}).get("contextual_preferences")
    if isinstance(contextual, dict):
        for v in contextual.values():
            txt = str(v or "").lower()
            if "loyal" in txt:
                explicit_mode = "loyalist"
                break
            if "explorer" in txt:
                explicit_mode = "explorer"
                break

    if explicit_mode == "loyalist" or discovery_drive <= 0.33:
        mode = "loyalist"
    elif explicit_mode == "explorer" or discovery_drive >= 0.66:
        mode = "explorer"
    else:
        mode = "hybrid"

    weirdness = 0.15 if mode == "loyalist" else (0.55 if mode == "explorer" else 0.35)
    return {"mode": mode, "discovery_drive": discovery_drive, "weirdness_constraint": weirdness}


def _resolve_primary_genre(profile_json: Dict[str, Any], genre_override: str = "") -> str:
    if genre_override and str(genre_override).strip():
        return str(genre_override).strip()
    genres = (profile_json or {}).get("dominant_genres")
    if isinstance(genres, list) and genres:
        g = str(genres[0] or "").strip()
        if g:
            return g
    subgenres = (profile_json or {}).get("subgenres")
    if isinstance(subgenres, list) and subgenres:
        g = str(subgenres[0] or "").strip()
        if g:
            return g
    return "Modern Alternative Pop"


def _palette_for_genre(genre_name: str) -> str:
    g = str(genre_name or "").lower()
    if any(x in g for x in ["rock", "metal", "punk", "alt"]):
        return "rock_metal_alt"
    if any(x in g for x in ["hip hop", "rap", "trap", "r&b", "soul"]):
        return "hiphop_rnb_soul"
    if any(x in g for x in ["edm", "house", "techno", "electro", "trance", "dance"]):
        return "electronic_edm"
    if any(x in g for x in ["jazz", "blues", "orchestral", "classical"]):
        return "jazz_blues_classical"
    if any(x in g for x in ["folk", "acoustic", "country"]):
        return "acoustic_folk_country"
    return "pop_indie"


def _normalize_generation_language(language: str) -> str:
    key = str(language or "").strip().lower()
    if key in LANGUAGE_DIRECTIVES:
        return key
    return "english"


def _activity_control_block(activity_label: str, activity_shift: Dict[str, Any], instrumental: bool) -> Tuple[Dict[str, Any], str]:
    """
    Build a high-priority activity control payload and strip activity vocal hints so
    the explicit vocal mode lock remains the single source of truth.
    """
    clean_shift = dict(activity_shift or {}) if isinstance(activity_shift, dict) else {}
    clean_shift.pop("vocals", None)

    if not activity_label:
        return clean_shift, ""

    parts = [
        f"ACTIVITY PRIORITY LOCK: '{activity_label}' is a primary behavior target.",
        "This activity must materially shape groove, arrangement pacing, and section energy.",
    ]
    if clean_shift:
        parts.append(
            "Activity blueprint -> "
            f"tempo={clean_shift.get('tempo')}, bass={clean_shift.get('bass_weight')}, "
            f"rhythm={clean_shift.get('rhythm_pattern')}, arrangement={clean_shift.get('arrangement')}, "
            f"space={clean_shift.get('sonic_space')}, lyric_tone={clean_shift.get('lyric_tone')}."
        )
    if instrumental:
        parts.append("Vocal lock is instrumental-only: activity can never request vocals or lyrics.")
    else:
        parts.append("Vocal lock requires lead vocals: activity can never suppress vocals or lyrics.")
    return clean_shift, " ".join(parts)


def _build_lyrics_brief(language_label: str, structure: str, lyric_dir: Dict[str, Any], mood_label: str) -> str:
    return (
        f"Write metaphorical lyrics in {language_label}. "
        f"Structure: {structure}. "
        f"Theme: {', '.join(lyric_dir.get('themes') or ['self-renewal', 'late-night clarity'])}. "
        "Never state emotions directly - use layered sensory imagery the listener decodes gradually. "
        f"Tone: {lyric_dir.get('tone') or mood_label.lower()}. "
        f"Imagery domain: {lyric_dir.get('imagery') or 'neon city rain'}. "
        f"Hook style: {lyric_dir.get('hook_style') or 'melodic extended'}. "
        f"Avoid: {', '.join(lyric_dir.get('avoid') or ['generic clichés', 'contradictory metaphors'])}."
    )


def _fallback_reference_analysis(song_reference: str) -> Dict[str, Any]:
    ref = str(song_reference or "").strip()
    if not ref:
        return {}
    return {
        "reference_song": ref,
        "core_vibe": "high-fidelity modern production",
        "tempo_feel": "match reference pulse and momentum",
        "drum_profile": "mirror the reference groove contour and drum weight",
        "bass_profile": "retain similar bass presence and movement",
        "harmonic_profile": "borrow tonal color and chordal density",
        "arrangement_profile": "follow comparable section energy curve",
        "vocal_profile": "match vocal intimacy and delivery intensity when vocals are enabled",
        "mix_profile": "preserve comparable width, depth, and transient polish",
        "weight": "very_high",
    }


def _analyze_reference_song_with_cerebras(client: Cerebras, model: str, song_reference: str) -> Dict[str, Any]:
    ref = str(song_reference or "").strip()
    if not ref:
        return {}

    system = (
        "You are a music style analyst. Convert a reference song mention into production traits only.\n"
        "Return ONLY valid JSON with keys: reference_song, core_vibe, tempo_feel, drum_profile, bass_profile, "
        "harmonic_profile, arrangement_profile, vocal_profile, mix_profile, weight.\n"
        "Do not include artist names or song titles outside reference_song.\n"
        "Set weight to very_high."
    )
    user_obj = {"reference_song": ref}
    obj, _raw = _call_openai_for_json(
        client,
        model=model,
        system=system,
        user_obj=user_obj,
        temperature=0.3,
    )
    if obj:
        obj["reference_song"] = ref
        obj["weight"] = "very_high"
        return obj
    return _fallback_reference_analysis(ref)


def _fallback_analysis(
    profile_json: Dict[str, Any],
    mood_id: str,
    mood_label: str,
    mood_intensity: float,
    activity_label: str,
    song_reference: str,
    genre_override: str,
    bpm_target: int,
    instrumental: bool,
    activity_shift: Dict[str, Any],
    language_label: str,
    surprise_me: bool,
    reference_analysis: Dict[str, Any],
) -> Dict[str, Any]:
    anchors = _extract_identity_anchors(profile_json)
    novelty = _listener_novelty_mode(profile_json)
    if surprise_me:
        novelty["mode"] = "explorer"
        novelty["weirdness_constraint"] = max(float(novelty.get("weirdness_constraint") or 0.0), 0.72)
    primary_genre = _resolve_primary_genre(profile_json, genre_override=genre_override)
    palette_key = _palette_for_genre(primary_genre)
    palette = GENRE_ENHANCER_PALETTES.get(palette_key, GENRE_ENHANCER_PALETTES["pop_indie"])

    enhancers = ["High Fidelity", "Warm Analog", palette[0], palette[1], "Atmospheric"]
    enhancers = _dedupe_keep_order(enhancers)[:6]

    vocal_pref = ""
    if isinstance(profile_json.get("production_traits"), dict):
        vocal_pref = str((profile_json.get("production_traits") or {}).get("vocals") or "").strip()

    if instrumental:
        structure = STRUCTURE_ARCHETYPES["journey"]
        vocal_stack = "Instrumental focus, no lead vocal, melodic motifs as emotional narrator"
        vocal_gender = ""
    else:
        if any(x in vocal_pref.lower() for x in ["power", "belt", "strong"]):
            vocal_stack = "Female Mezzo-Soprano, Resonant Tone, Powerful Belting Delivery, Wide Dynamic Range, Light Plate Reverb"
            vocal_gender = "f"
        elif any(x in vocal_pref.lower() for x in ["rap", "spoken", "baritone", "deep"]):
            vocal_stack = "Male Baritone, Raw Textured Tone, Rhythmic Staccato Delivery, Dry Forward Mix, Controlled Distortion Edge"
            vocal_gender = "m"
        else:
            vocal_stack = "Female Alto, Breathy Intimate Tone, Soft Melodic Delivery, Close Mic Dry Mix, Light Plate Reverb"
            vocal_gender = "f"
        structure = STRUCTURE_ARCHETYPES["radio_hit"]

    clean_activity_shift, activity_priority_clause = _activity_control_block(
        activity_label=activity_label,
        activity_shift=activity_shift,
        instrumental=bool(instrumental),
    )
    activity_clause = f"for {activity_label.lower()}" if activity_label else ""
    reference_clause = (
        "Use very high-weight reference traits for groove, bass profile, harmonic color, arrangement arc, and mix depth."
        if song_reference
        else ""
    )
    bpm_clause = f"Target {int(bpm_target)} BPM." if bpm_target else ""
    language_clause = (
        f"All lyrical direction and topline language must be in {language_label}."
        if not instrumental
        else "Instrumental mode lock: no lead vocal and no lyrics."
    )
    activity_style_clause = ""
    if isinstance(clean_activity_shift, dict) and clean_activity_shift:
        activity_style_clause = (
            f"Activity shaping: bass={clean_activity_shift.get('bass_weight')}, rhythm={clean_activity_shift.get('rhythm_pattern')}, "
            f"arrangement={clean_activity_shift.get('arrangement')}, space={clean_activity_shift.get('sonic_space')}, "
            f"lyric tone={clean_activity_shift.get('lyric_tone')}."
        )
    novelty_clause = (
        "Stay extremely close to core identity anchors and avoid left turns."
        if novelty["mode"] == "loyalist"
        else (
            "Inject controlled surprises in transitions and harmonic color while preserving identity DNA."
            if novelty["mode"] == "explorer"
            else "Balance familiarity with one notable surprise per section."
        )
    )

    return {
        "analysis_version": "fallback-v1",
        "primary_genre": primary_genre,
        "secondary_genre": str(((profile_json.get("dominant_genres") or [""]) + [""])[1]).strip() or "None",
        "artist_blend_plan": anchors.get("selected_artists", []),
        "song_anchor_focus": anchors.get("selected_songs", []),
        "mood": {"id": mood_id, "label": mood_label, "intensity": mood_intensity},
        "enhancer_plan": {
            "audio_base": ["High Fidelity", "Warm Analog"],
            "genre_palette": enhancers[2:5],
            "x_factor": "Atmospheric" if mood_id in ("sad", "chill", "romantic") else "Driving",
            "all": enhancers,
        },
        "vocal_identity": {
            "stack": vocal_stack,
            "anchor": vocal_stack,
            "gender": vocal_gender,
        },
        "structure_archetype": "journey" if instrumental else "radio_hit",
        "structure_tags": structure,
        "lyric_direction": {
            "pov": "I",
            "tone": f"{mood_label.lower()} and emotionally intentional in {language_label}",
            "imagery": VIBRANT_IMAGE_DOMAINS[0],
            "hook_style": "short repetitive" if mood_id in ("energetic", "happy", "aggressive") else "melodic extended",
            "themes": [
                str((profile_json.get("emotion_regulation_strategy") or "identity")).replace("_", " "),
                "self-renewal",
                "late-night clarity",
            ],
            "avoid": (profile_json.get("prompt_translation_hints") or {}).get("avoid")
            if isinstance(profile_json.get("prompt_translation_hints"), dict)
            else ["generic clichés", "contradictory metaphors"],
        },
        "performance_notes": [
            f"Build a {mood_label.lower()} emotional arc {activity_clause}".strip(),
            activity_priority_clause,
            reference_clause,
            activity_style_clause,
            language_clause,
            bpm_clause,
            novelty_clause,
        ],
        "reference_style_analysis": reference_analysis or {},
    }


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


def _sanitize_generated_payload(
    generated_obj: Dict[str, Any],
    analysis_obj: Dict[str, Any],
    profile_json: Dict[str, Any],
    mood_label: str,
    instrumental: bool,
    title_hint: str,
    style_hint: str,
    custom_mode: bool,
) -> Dict[str, Any]:
    banned_phrases = _collect_banned_phrases(profile_json or {}, analysis_obj or {})

    raw_title = generated_obj.get("title") or title_hint or f"{mood_label} Pulse"
    title = _compact_text(_strip_banned_phrases(raw_title, banned_phrases), 100)
    if not title:
        title = _compact_text(f"{mood_label} Pulse", 100)

    style_fallback_parts = [
        str(analysis_obj.get("primary_genre") or "Modern Alternative Pop").strip(),
        f"{mood_label} focus",
        str(((analysis_obj.get("enhancer_plan") or {}).get("all") or [""])[0]).strip(),
        str((analysis_obj.get("vocal_identity") or {}).get("stack") or "").strip(),
    ]
    style_fallback = ", ".join([p for p in style_fallback_parts if p])
    raw_style = generated_obj.get("style") or style_hint or style_fallback
    style = _compact_text(_strip_banned_phrases(raw_style, banned_phrases), 1000)

    raw_prompt = generated_obj.get("prompt") or ""
    prompt = _compact_text(_strip_banned_phrases(raw_prompt, banned_phrases), 5000)
    if not prompt and instrumental:
        prompt = (
            "Instrumental direction only. No lyrics and no vocal topline. "
            "Translate identity-artist DNA into arrangement: intro texture, motif development, dynamic lift, "
            "peak section, and resolving outro. "
            "Emphasize artist-derived drum language, bass movement, harmonic color, and signature ear-candy transitions."
        )
    elif not prompt:
        structure = str(analysis_obj.get("structure_tags") or STRUCTURE_ARCHETYPES["radio_hit"]).strip()
        prompt = (
            "English lyrics direction only. Do not write final lyrics yet. "
            f"Use this structure: {structure}. "
            "Keep chorus emotionally central with memorable hook language. "
            "Embed concrete sensory imagery and section-level dynamics."
        )
    elif instrumental:
        if "no lyrics" not in prompt.lower():
            prompt = _compact_text(
                "Instrumental lock: no lead vocals and no lyrics. " + prompt,
                5000,
            )
    else:
        if "lead vocal" not in prompt.lower():
            prompt = _compact_text(
                "Vocal lock: include a clear lead vocal presence and lyric-ready topline. " + prompt,
                5000,
            )

    negative_tags = generated_obj.get("negative_tags") or generated_obj.get("negativeTags") or []
    if isinstance(negative_tags, str):
        negative_tags = [x.strip() for x in negative_tags.split(",") if x.strip()]
    if not isinstance(negative_tags, list):
        negative_tags = []
    negative_tags = _dedupe_keep_order([
        _strip_banned_phrases(str(x).strip(), banned_phrases)
        for x in negative_tags
        if str(x).strip()
    ])[:12]

    vocal_gender = str(generated_obj.get("vocal_gender") or generated_obj.get("vocalGender") or "").strip().lower()
    if instrumental:
        vocal_gender = ""
    elif vocal_gender not in ("m", "f"):
        vocal_gender = str((analysis_obj.get("vocal_identity") or {}).get("gender") or "").strip().lower()
    if vocal_gender not in ("m", "f"):
        vocal_gender = ""

    style_weight = generated_obj.get("style_weight")
    weirdness_constraint = generated_obj.get("weirdness_constraint")
    audio_weight = generated_obj.get("audio_weight")

    try:
        style_weight = float(style_weight) if style_weight is not None else None
    except (TypeError, ValueError):
        style_weight = None
    if style_weight is not None:
        style_weight = max(0.0, min(1.0, style_weight))

    try:
        weirdness_constraint = float(weirdness_constraint) if weirdness_constraint is not None else None
    except (TypeError, ValueError):
        weirdness_constraint = None
    if weirdness_constraint is not None:
        weirdness_constraint = max(0.0, min(1.0, weirdness_constraint))

    try:
        audio_weight = float(audio_weight) if audio_weight is not None else None
    except (TypeError, ValueError):
        audio_weight = None
    if audio_weight is not None:
        audio_weight = max(0.0, min(1.0, audio_weight))

    persona_id = _compact_text(generated_obj.get("persona_id") or generated_obj.get("personaId") or "", 120)

    payload = {
        "prompt": prompt,
        "instrumental": bool(instrumental),
        "customMode": True if custom_mode is not None else True,
        "title": title,
        "style": style,
        "model": "V5",
    }
    if negative_tags:
        payload["negative_tags"] = ", ".join(negative_tags)
    if vocal_gender:
        payload["vocal_gender"] = vocal_gender
    if style_weight is not None:
        payload["style_weight"] = style_weight
    if weirdness_constraint is not None:
        payload["weirdness_constraint"] = weirdness_constraint
    if audio_weight is not None:
        payload["audio_weight"] = audio_weight
    if persona_id:
        payload["persona_id"] = persona_id

    return payload


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
    language: str = "english",
    surprise_me: bool = False,
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
            "model": "V5"
        }
      }
    """
    mood_label = _mood_label(mood_id)
    language_key = _normalize_generation_language(language)
    language_label = LANGUAGE_DIRECTIVES[language_key]
    shift = MOOD_SHIFTS.get(mood_id, {})
    ctx = _compact_profile_context(profile_json)

    act_label = _activity_label(activity_id) if activity_id else None
    raw_act_shift = ACTIVITY_SHIFTS.get(activity_id, {}) if activity_id else {}
    act_shift, activity_priority_clause = _activity_control_block(
        activity_label=act_label or "",
        activity_shift=raw_act_shift,
        instrumental=bool(instrumental),
    )
    anchors = _extract_identity_anchors(profile_json)
    novelty = _listener_novelty_mode(profile_json)
    if surprise_me:
        novelty["mode"] = "explorer"
        novelty["weirdness_constraint"] = max(float(novelty.get("weirdness_constraint") or 0.0), 0.72)
    reference_analysis = _fallback_reference_analysis(song_reference)

    if not cerebras_api_key:
        analysis_obj = _fallback_analysis(
            profile_json=profile_json,
            mood_id=mood_id,
            mood_label=mood_label,
            mood_intensity=_clamp01(mood_intensity, 0.5),
            activity_label=act_label or "",
            song_reference=song_reference or "",
            genre_override=genre_override or "",
            bpm_target=bpm_target,
            instrumental=bool(instrumental),
            activity_shift=act_shift,
            language_label=language_label,
            surprise_me=bool(surprise_me),
            reference_analysis=reference_analysis,
        )
        prompt_prefix = (
            "Instrumental direction only. No lead vocal or lyrics. "
            if bool(instrumental)
            else f"{language_label} arrangement direction. Keep a clear section structure with emotional trajectory. "
        )
        fb_lyric_dir = analysis_obj.get("lyric_direction") or {}
        fb_structure = str(analysis_obj.get("structure_tags") or STRUCTURE_ARCHETYPES["radio_hit"]).strip()
        fallback_lyrics_brief = ""
        if not bool(instrumental):
            fallback_lyrics_brief = _build_lyrics_brief(
                language_label=language_label,
                structure=fb_structure,
                lyric_dir=fb_lyric_dir,
                mood_label=mood_label,
            )
        generated_obj = {
            "title": title_hint or f"{mood_label} Echo",
            "style": style_hint or "",
            "prompt": (
                prompt_prefix
                +
                f"Mood={mood_label}, intensity={_clamp01(mood_intensity, 0.5):.2f}. "
                f"Activity={act_label or 'none'}. "
                f"{activity_priority_clause} "
                f"Surprise mode={'on' if surprise_me else 'off'}. "
                "When a reference exists, heavily match its groove contour, bass movement, harmonic density, and mix depth. "
                "Use profile-consistent themes and concrete imagery."
            ),
            "lyrics_brief": fallback_lyrics_brief,
            "negative_tags": ["generic lyrics", "flat dynamics", "stylistic contradiction"],
            "vocal_gender": "",
            "weirdness_constraint": novelty.get("weirdness_constraint"),
            "style_weight": 0.97 if song_reference else 0.85,
            "audio_weight": 0.94 if song_reference else 0.8,
        }
        payload = _sanitize_generated_payload(
            generated_obj=generated_obj,
            analysis_obj=analysis_obj,
            profile_json=profile_json,
            mood_label=mood_label,
            instrumental=bool(instrumental),
            title_hint=title_hint,
            style_hint=style_hint,
            custom_mode=True,
        )
        return {
            "openai_prompt": {
                "note": "CEREBRAS_API_KEY not set; deterministic two-step fallback used",
                "ctx": ctx,
                "analysis": analysis_obj,
                "generated": generated_obj,
            },
            "suno_payload": payload,
            "lyrics_brief": fallback_lyrics_brief,
        }

    client = Cerebras(api_key=cerebras_api_key)
    if song_reference:
        reference_analysis = _analyze_reference_song_with_cerebras(
            client=client,
            model=model,
            song_reference=song_reference,
        )

    step1_system = (
        "You are a senior music taste translator for Suno generation.\n"
        "Task: convert listener profile + controls into a deeply specific STYLE ANALYSIS JSON.\n"
        "Return ONLY valid JSON.\n"
        "You MUST prioritize, in order: selected identity artists, selected identity songs, activity controls, profile traits, mood controls.\n"
        "Use mood-weighted artist blending: raise weight for artists whose stylistic lane best fits mood target.\n"
        "If user mode is loyalist, stay very close to core artist DNA. If explorer, introduce controlled novelty.\n"
        "Song references must be translated into sonic traits only.\n"
        "If a reference style analysis is provided, weight those traits VERY HEAVILY in groove, bass, harmony, arrangement, and mix.\n"
        "Honor vocal_mode_lock strictly: vocals_required means explicit lead vocal profile; instrumental_only means no vocals.\n"
        "Honor language_lock strictly for lyrical direction language.\n"
        "Output schema keys exactly:\n"
        "{\n"
        "  \"primary_genre\": \"string\",\n"
        "  \"secondary_genre\": \"string\",\n"
        "  \"artist_blend_plan\": [{\"artist\": \"string\", \"weight\": 0.0, \"style_fingerprint\": \"string\"}],\n"
        "  \"song_anchor_focus\": [{\"title\": \"string\", \"artist\": \"string\", \"weight\": 0.0, \"sonic_traits\": [\"string\"]}],\n"
        "  \"enhancer_plan\": {\"audio_base\": [\"string\"], \"genre_palette\": [\"string\"], \"x_factor\": \"string\", \"all\": [\"string\"]},\n"
        "  \"vocal_identity\": {\"stack\": \"string\", \"anchor\": \"string\", \"gender\": \"m|f|\"},\n"
        "  \"structure_archetype\": \"radio_hit|club_electronic|flow_bars|journey\",\n"
        "  \"structure_tags\": \"string\",\n"
        "  \"lyric_direction\": {\"pov\": \"I|you|we\", \"tone\": \"string\", \"imagery\": \"string\", \"hook_style\": \"short repetitive|melodic extended\", \"themes\": [\"string\"], \"avoid\": [\"string\"]},\n"
        "  \"performance_notes\": [\"string\"]\n"
        "}\n"
        "Rules:\n"
        "- Activity controls are high-weight constraints, not soft suggestions.\n"
        "- If activity is provided, keep activity influence obvious and section-consistent (groove, arrangement, transitions).\n"
        "- Activity vocal hints must NEVER override vocal_mode_lock.\n"
        "- Keep enhancer total between 4 and 7 items.\n"
        "- Use exactly one x-factor.\n"
        "- Avoid contradictory descriptors.\n"
        "- Keep lyrical direction in the required locked language.\n"
    )

    step1_user = {
        "controls": {
            "target_mood": mood_label,
            "mood_id": mood_id,
            "mood_intensity": _clamp01(mood_intensity, 0.5),
            "mood_shift": shift,
            "activity": act_label,
            "activity_shift": act_shift,
            "activity_priority_clause": activity_priority_clause or None,
            "instrumental": bool(instrumental),
            "vocal_mode_lock": "instrumental_only" if bool(instrumental) else "vocals_required",
            "language": language_label,
            "language_lock": True,
            "surprise_me": bool(surprise_me),
            "style_reference_song": song_reference or None,
            "reference_style_analysis": reference_analysis or None,
            "reference_weight": "very_high" if song_reference else "none",
            "genre_preference": genre_override or None,
            "target_bpm": bpm_target or None,
            "always_custom_mode": True,
        },
        "listener_mode": novelty,
        "mood_artist_affinity_hints": MOOD_ARTIST_AFFINITY.get(mood_id, []),
        "identity_anchors": anchors,
        "listener_profile_compact": ctx,
        "listener_profile_full": profile_json,
        "genre_enhancer_palettes": GENRE_ENHANCER_PALETTES,
        "structure_archetypes": STRUCTURE_ARCHETYPES,
    }

    step1_obj, step1_raw = _call_openai_for_json(
        client,
        model=model,
        system=step1_system,
        user_obj=step1_user,
        temperature=0.6,
    )

    if not step1_obj:
        step1_obj = _fallback_analysis(
            profile_json=profile_json,
            mood_id=mood_id,
            mood_label=mood_label,
            mood_intensity=_clamp01(mood_intensity, 0.5),
            activity_label=act_label or "",
            song_reference=song_reference or "",
            genre_override=genre_override or "",
            bpm_target=bpm_target,
            instrumental=bool(instrumental),
            activity_shift=act_shift,
            language_label=language_label,
            surprise_me=bool(surprise_me),
            reference_analysis=reference_analysis,
        )

    step2_system = (
        "You are building final Suno custom-mode payload fields for maximum profile fidelity.\n"
        "Return ONLY valid JSON with keys: title, style, prompt, lyrics_brief, negative_tags, vocal_gender, style_weight, weirdness_constraint, audio_weight, persona_id.\n"
        "Critical rules:\n"
        "- custom mode is always used.\n"
        "- style must be <=1000 characters, prompt <=5000 characters.\n"
        "- For instrumental tracks: prompt must be arrangement/composition direction (sections, dynamics, instrumentation, transitions) and MUST NOT be empty. lyrics_brief must be empty string.\n"
        f"- For vocal tracks: prompt should be {language_label} arrangement direction and structure guidance (it will be replaced by generated lyrics later).\n"
        "- lyrics_brief (max 200 characters) is a prompt that will be sent to Suno's lyrics generation API to produce actual song lyrics.\n"
        "  It must describe the desired lyrics, NOT be the lyrics themselves.\n"
        f"  It MUST request lyrics written in {language_label}.\n"
        "  It MUST instruct metaphorical, layered language — never state emotions or themes directly.\n"
        "  Transform every feeling into vivid sensory imagery the listener decodes gradually.\n"
        "  Example: 'focused drive' becomes 'Locked in, milky way, starlines fall beneath me'.\n"
        "  Example: 'heartbreak' becomes 'Glass cathedral caving in, echoes where your voice was'.\n"
        "  Include: structure archetype tags, emotional arc per section, imagery domain, thematic direction, and what to avoid.\n"
        "- include structure tags and section-level emotional trajectory.\n"
        "- keep activity influence explicit and high-weight in rhythm, arrangement shape, and transitions.\n"
        "- activity cannot override vocal_mode_lock under any condition.\n"
        "- strongly represent weighted blend of up to 3 identity artists plus selected songs' sonic traits.\n"
        "- if reference style analysis exists, weight it VERY HEAVILY in style/prompt and keep strong sonic similarity.\n"
        "- NEVER include artist names or song titles in title/style/prompt/lyrics_brief. Use abstract sonic descriptors only.\n"
        "- if loyalist mode: minimize novelty; if explorer: allow stronger novelty.\n"
        "- Use profile-consistent keywords and avoid list.\n"
        "- Do not include profanity unless explicitly demanded (not provided here).\n"
    )

    step2_user = {
        "analysis": step1_obj,
        "controls": step1_user.get("controls"),
        "profile": {
            "summary": (profile_json or {}).get("summary"),
            "soul_signature": (profile_json or {}).get("soul_signature"),
            "prompt_translation_hints": (profile_json or {}).get("prompt_translation_hints"),
            "style_blueprint": (profile_json or {}).get("style_blueprint"),
            "production_traits": (profile_json or {}).get("production_traits"),
            "contextual_preferences": (profile_json or {}).get("contextual_preferences"),
        },
        "hints": {
            "title_hint": title_hint or None,
            "style_hint": style_hint or None,
        },
        "reference_style_analysis": reference_analysis or None,
        "surprise_me": bool(surprise_me),
    }

    step2_obj, step2_raw = _call_openai_for_json(
        client,
        model=model,
        system=step2_system,
        user_obj=step2_user,
        temperature=0.9,
    )

    if not step2_obj:
        lyric_dir = step1_obj.get("lyric_direction") or {}
        structure = str(step1_obj.get("structure_tags") or STRUCTURE_ARCHETYPES["radio_hit"]).strip()
        fallback_lyrics_brief = ""
        if not bool(instrumental):
            fallback_lyrics_brief = _build_lyrics_brief(
                language_label=language_label,
                structure=structure,
                lyric_dir=lyric_dir,
                mood_label=mood_label,
            )
        step2_obj = {
            "title": title_hint or f"{mood_label} Signal",
            "style": style_hint or "",
            "prompt": (
                "Instrumental direction only. Build clear sections with evolving motifs, dynamic contour, and impact transitions. "
                "Apply weighted artist DNA blend and selected-song sonic cues for drums, bass, harmony, and FX detail. "
                "If reference style analysis exists, match it very closely in groove contour, bass profile, harmonic color, and mix polish."
                if bool(instrumental)
                else f"{language_label} arrangement direction. Use section tags from chosen archetype. Keep one emotional center in chorus/drop. "
                "Apply weighted artist DNA blend, selected-song sonic cues, and heavy reference-style similarity."
            ),
            "lyrics_brief": fallback_lyrics_brief,
            "negative_tags": ["generic filler", "flat arrangement", "style collision"],
            "vocal_gender": str((step1_obj.get("vocal_identity") or {}).get("gender") or ""),
            "style_weight": 0.97 if song_reference else 0.9,
            "weirdness_constraint": novelty.get("weirdness_constraint"),
            "audio_weight": 0.94 if song_reference else 0.85,
        }

    if song_reference:
        try:
            current_style_weight = float(step2_obj.get("style_weight") or 0.0)
        except (TypeError, ValueError):
            current_style_weight = 0.0
        try:
            current_audio_weight = float(step2_obj.get("audio_weight") or 0.0)
        except (TypeError, ValueError):
            current_audio_weight = 0.0
        step2_obj["style_weight"] = max(current_style_weight, 0.95)
        step2_obj["audio_weight"] = max(current_audio_weight, 0.9)

    if surprise_me:
        try:
            current_weirdness = float(step2_obj.get("weirdness_constraint") or 0.0)
        except (TypeError, ValueError):
            current_weirdness = 0.0
        step2_obj["weirdness_constraint"] = max(current_weirdness, 0.72)

    lyrics_brief = str(step2_obj.get("lyrics_brief") or "").strip()
    if not bool(instrumental) and not lyrics_brief:
        lyric_dir = step1_obj.get("lyric_direction") or {}
        structure = str(step1_obj.get("structure_tags") or STRUCTURE_ARCHETYPES["radio_hit"]).strip()
        lyrics_brief = _build_lyrics_brief(
            language_label=language_label,
            structure=structure,
            lyric_dir=lyric_dir,
            mood_label=mood_label,
        )
    if len(lyrics_brief) > 200:
        lyrics_brief = lyrics_brief[:200].rstrip()

    payload = _sanitize_generated_payload(
        generated_obj=step2_obj,
        analysis_obj=step1_obj,
        profile_json=profile_json,
        mood_label=mood_label,
        instrumental=bool(instrumental),
        title_hint=title_hint,
        style_hint=style_hint,
        custom_mode=True,
    )

    return {
        "openai_prompt": {
            "model": model,
            "step1": {"system": step1_system, "user": step1_user, "raw": step1_raw, "output": step1_obj},
            "step2": {"system": step2_system, "user": step2_user, "raw": step2_raw, "output": step2_obj},
        },
        "suno_payload": payload,
        "lyrics_brief": lyrics_brief,
    }
