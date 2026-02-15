# app/services/drvibey_chat.py
"""
drVibey chat service.
Manages the music-psychologist conversation flow, Cerebras API calls,
and final listener-profile synthesis.

Flow: 10 short questions. Screenshots first, then quick-fire profiling.
No LLM calls for question transitions (all questions are fixed/short).
LLM used only for: OCR cleanup, profile synthesis, diagnosis.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from cerebras.cloud.sdk import Cerebras

log = logging.getLogger("drvibey.chat")


# ── intro message (shown before Q1) ──────────────────────────────────

INTRO_MESSAGE = (
    "Hey, I'm drVibey \u2014 your personal music psychologist. "
    "I'm here to heal you through the power of music... "
    "but first, I need to understand your diagnosis."
)

# ── question bank (10 total) ─────────────────────────────────────────

DRVIBEY_QUESTIONS = [
    {
        "number": 1,
        "text": (
            "Drop me 3-10 screenshots of your favorite playlists, "
            "most-played songs, or your Year Wrapped from Spotify, "
            "Apple Music, YouTube Music \u2014 whatever you use."
        ),
        "input_type": "screenshot",   # screenshot upload
        "skippable": False,
        "dimension": "screenshot_evidence",
    },
    {
        "number": 2,
        "text": "Nice library! Pick your top 3 songs and 3 artists.",
        # text is dynamically enriched with OCR examples by build_q2()
        "input_type": "chip_select",  # tap-to-select from OCR results
        "skippable": False,
        "dimension": "identity_picks",
    },
    {
        "number": 3,
        "text": "A song that makes you happy?",
        "input_type": "text",
        "skippable": True,
        "dimension": "mood_happy",
    },
    {
        "number": 4,
        "text": "One for when you're feeling sad?",
        "input_type": "text",
        "skippable": True,
        "dimension": "mood_sad",
    },
    {
        "number": 5,
        "text": "And for a romantic mood?",
        "input_type": "text",
        "skippable": True,
        "dimension": "mood_romantic",
    },
    {
        "number": 6,
        "text": "What do you blast while driving?",
        "input_type": "text",
        "skippable": True,
        "dimension": "activity_driving",
    },
    {
        "number": 7,
        "text": "Go-to party tracks?",
        "input_type": "text",
        "skippable": True,
        "dimension": "activity_party",
    },
    {
        "number": 8,
        "text": "Workout playlist staples?",
        "input_type": "text",
        "skippable": True,
        "dimension": "activity_workout",
    },
    {
        "number": 9,
        "text": "What grabs you first in a new song?",
        "input_type": "buttons",      # 3 buttons: Lyrics / Beat / Vibe
        "button_options": ["Lyrics", "Beat", "Vibe"],
        "skippable": False,
        "dimension": "listening_orientation",
    },
    {
        "number": 10,
        "text": "Always chasing new music or riding with what you know?",
        "input_type": "buttons",
        "button_options": ["The Explorer", "The Loyalist"],
        "skippable": False,
        "dimension": "discovery_drive",
    },
]

TOTAL_QUESTIONS = len(DRVIBEY_QUESTIONS)  # 10

SKIP_TOKEN = "[skipped]"


# ── system prompts ───────────────────────────────────────────────────

PROFILE_SYNTHESIS_PROMPT = """\
You are a music taste analyst. You have data from a conversation between drVibey (a music \
psychologist) and a listener, PLUS a list of tracks extracted from their playlist screenshots.

Analyze ALL of this data and produce a comprehensive listener taste profile.

CONVERSATION DATA:
{conversation_json}

EXTRACTED TRACKS FROM SCREENSHOTS:
{tracks_json}

OUTPUT RULES:
- Return ONLY valid JSON matching the schema below. No markdown, no explanation.
- Be specific and detailed. Use actual genre names, not vague terms.
- Base your analysis on BOTH the conversation answers AND the track list.
- If conversation and track data conflict, favor the track data (actions over words).
- Answers marked "[skipped]" should be ignored -- the user chose not to answer.
- The summary should be 2-3 vivid sentences describing this listener's musical identity.
- confidence should be "high" if you have good data, "medium" if sparse.

REQUIRED JSON SCHEMA:
{{
  "method": "drvibey_chat_inference",
  "model": "{model}",
  "generated_at": "{timestamp}",
  "input_source": "chat+screenshots",
  "confidence": "high|medium|low",
  "dominant_genres": ["genre1", "genre2", "genre3"],
  "subgenres": ["subgenre1", "subgenre2", "subgenre3"],
  "vibe_keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "instrumentation": ["instrument1", "instrument2", "instrument3"],
  "production_traits": {{
    "drums": "description of preferred drum style",
    "bass": "description of preferred bass style",
    "melody": "description of preferred melodic style",
    "mixing": "description of preferred mixing/production aesthetic",
    "vocals": "description of preferred vocal style"
  }},
  "tempo_preference": "slow|mid|fast|varied",
  "energy_range": {{"low": 0.0, "high": 1.0}},
  "emotional_profile": {{
    "primary_emotions": ["emotion1", "emotion2"],
    "mood_strategy": "matcher|escapist|varied",
    "emotional_depth": 0.0
  }},
  "identity_artists": ["artist1", "artist2", "artist3"],
  "listening_orientation": "lyrics|production|vibe",
  "discovery_drive": 0.0,
  "contextual_preferences": {{
    "focus_work": "genre/vibe description",
    "active_energy": "genre/vibe description",
    "emotional_processing": "genre/vibe description",
    "social": "genre/vibe description"
  }},
  "extracted_tracks": [
    {{"artist": "...", "title": "..."}}
  ],
  "summary": "2-3 sentence vivid description of this listener's musical identity"
}}
"""

DIAGNOSIS_PROMPT = """\
You are drVibey. You just finished analyzing a listener's musical DNA. Here is their profile:

{profile_json}

Write a SHORT, punchy "Vibe Diagnosis" in drVibey's voice. Rules:
- Start with a listener archetype name (e.g. "The Melancholic Explorer", "The Rhythm Purist", etc.)
- Follow with 2-3 sentences max explaining their preferred style and sonic identity
- Focus on genres, vibes, production style, and listening patterns -- NOT specific artist names
- Do NOT mention any artist names in the diagnosis text (they are shown separately)
- Keep it under 60 words total
- Sound like a cool music-obsessed friend, not a clinical report
- No emojis

Return ONLY valid JSON: {{"archetype": "The Archetype Name", "diagnosis_text": "your diagnosis"}}
"""

OCR_CLEANUP_PROMPT = """\
You are a music data parser. Below is raw OCR text extracted from music app screenshots \
(Spotify, Apple Music, YouTube Music, etc). The OCR may have errors, truncations, or UI noise.

Also provided is a preliminary parse of tracks from the OCR text.

RAW OCR TEXT:
{raw_texts}

PRELIMINARY PARSED TRACKS:
{parsed_tracks}

YOUR TASK:
1. Clean up and correct any obvious OCR errors in track titles and artist names
2. Remove any entries that are clearly UI elements, not actual tracks
3. Deduplicate entries (same song appearing across multiple screenshots)
4. Return a clean list of tracks

Return ONLY valid JSON: {{"tracks": [{{"artist": "...", "title": "..."}}]}}
Keep maximum 60 tracks. If there are more, keep the ones that appear most clearly in the data.
"""


# ── helpers ──────────────────────────────────────────────────────────

def _extract_json(raw: str) -> Dict[str, Any]:
    """Parse JSON from LLM output, handling markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                return {}
    return {}


def _call_cerebras(client: Cerebras, model: str, system: str,
                   user_content: str, temperature: float = 0.7) -> Tuple[Dict[str, Any], str]:
    """Call Cerebras chat completions and return (parsed_json, raw_text)."""
    log.debug("Cerebras call [model=%s, temp=%.1f]", model, temperature)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature,
    )
    raw = (resp.choices[0].message.content or "").strip()
    log.debug("Cerebras raw response (first 500 chars): %s", raw[:500])
    return _extract_json(raw), raw


# ── public API ───────────────────────────────────────────────────────

def get_initial_message() -> Dict[str, Any]:
    """Return intro + Q1 (screenshot upload prompt). No LLM call needed."""
    q = DRVIBEY_QUESTIONS[0]
    return {
        "intro": INTRO_MESSAGE,
        "reply": q["text"],
        "question_number": 1,
        "input_type": q["input_type"],
        "skippable": q["skippable"],
    }


def build_q2_from_tracks(tracks: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Build Q2 dynamically from OCR-extracted tracks.
    Returns the Q2 message + chip_options for the frontend.
    """
    q = DRVIBEY_QUESTIONS[1]

    # Collect unique artists and songs for chips
    artists = []
    songs = []
    seen_artists = set()
    seen_songs = set()

    for t in tracks:
        artist = t.get("artist", "").strip()
        title = t.get("title", "").strip()

        if artist and artist.lower() not in seen_artists:
            seen_artists.add(artist.lower())
            artists.append(artist)

        if title and title.lower() not in seen_songs:
            seen_songs.add(title.lower())
            song_label = title
            if artist:
                song_label = f"{title} \u2014 {artist}"
            songs.append({"title": title, "artist": artist, "label": song_label})

    # Build a short message with a few examples
    examples = []
    for a in artists[:3]:
        examples.append(a)
    for s in songs[:2]:
        examples.append(s["title"])
    example_str = ", ".join(examples[:4])

    reply = f"Nice library! I see {example_str}... Pick your top 3 songs and 3 artists."
    if not examples:
        reply = q["text"]

    log.info("build_q2: %d artists, %d songs available as chips", len(artists), len(songs))

    return {
        "reply": reply,
        "question_number": 2,
        "input_type": "chip_select",
        "skippable": False,
        "chip_options": {
            "artists": artists[:30],
            "songs": songs[:30],
        },
    }


def get_next_question(current_question: int) -> Dict[str, Any]:
    """
    Return the next question after the user answered current_question.
    No LLM call -- all questions are fixed and short.
    """
    next_idx = current_question  # 0-based index (Q1=0 already answered, next is idx 1, etc.)

    log.info("get_next_question: answered Q%d, next idx=%d / %d",
             current_question, next_idx, TOTAL_QUESTIONS)

    if next_idx >= TOTAL_QUESTIONS:
        return {
            "reply": "Got it! Let me build your musical DNA...",
            "question_number": current_question + 1,
            "input_type": "none",
            "skippable": False,
            "is_complete": True,
        }

    q = DRVIBEY_QUESTIONS[next_idx]
    result = {
        "reply": q["text"],
        "question_number": q["number"],
        "input_type": q["input_type"],
        "skippable": q["skippable"],
    }

    if q["input_type"] == "buttons" and "button_options" in q:
        result["button_options"] = q["button_options"]

    return result


def cleanup_ocr_tracks(
    cerebras_api_key: str,
    model: str,
    parsed_tracks: List[Dict[str, str]],
    raw_texts: List[str],
) -> List[Dict[str, str]]:
    """
    Use Cerebras to clean up OCR-extracted tracks:
    fix errors, remove UI noise, deduplicate.
    """
    log.info("cleanup_ocr_tracks: %d raw tracks from %d screenshots",
             len(parsed_tracks), len(raw_texts))

    if not parsed_tracks:
        return []

    system = OCR_CLEANUP_PROMPT.format(
        raw_texts="\n---\n".join(raw_texts),
        parsed_tracks=json.dumps(parsed_tracks, ensure_ascii=False),
    )

    client = Cerebras(api_key=cerebras_api_key)
    obj, _raw = _call_cerebras(
        client, model=model,
        system=system,
        user_content="Clean up and return the track list as JSON.",
        temperature=0.3,
    )

    tracks = obj.get("tracks", [])
    if not isinstance(tracks, list):
        log.warning("OCR cleanup returned non-list, falling back to raw parse")
        return parsed_tracks

    clean: List[Dict[str, str]] = []
    for t in tracks:
        if isinstance(t, dict) and ("artist" in t or "title" in t):
            clean.append({
                "artist": str(t.get("artist", "")).strip(),
                "title": str(t.get("title", "")).strip(),
            })

    log.info("cleanup_ocr_tracks: %d raw -> %d cleaned tracks", len(parsed_tracks), len(clean))
    return clean if clean else parsed_tracks


def synthesize_profile(
    cerebras_api_key: str,
    model: str,
    history: List[Dict[str, str]],
    extracted_tracks: List[Dict[str, str]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Synthesize the full listener profile from conversation + tracks.
    Returns (profile_json, diagnosis_json).
    """
    log.info("=" * 60)
    log.info("PROFILE SYNTHESIS START")
    log.info("  Chat history: %d messages", len(history))
    log.info("  Extracted tracks: %d", len(extracted_tracks))
    log.info("=" * 60)

    timestamp = datetime.now(timezone.utc).isoformat()

    conversation_data = [{"role": m["role"], "content": m["content"]} for m in history]

    log.info("Step 1: Calling Cerebras for profile synthesis...")
    system = PROFILE_SYNTHESIS_PROMPT.format(
        conversation_json=json.dumps(conversation_data, ensure_ascii=False),
        tracks_json=json.dumps(extracted_tracks[:60], ensure_ascii=False),
        model=model,
        timestamp=timestamp,
    )

    client = Cerebras(api_key=cerebras_api_key)
    profile, _raw = _call_cerebras(
        client, model=model,
        system=system,
        user_content="Analyze and return the listener taste profile as JSON.",
        temperature=0.5,
    )

    # Ensure required fields
    profile.setdefault("method", "drvibey_chat_inference")
    profile.setdefault("model", model)
    profile.setdefault("generated_at", timestamp)
    profile.setdefault("input_source", "chat+screenshots")
    profile.setdefault("confidence", "medium")
    profile.setdefault("dominant_genres", [])
    profile.setdefault("subgenres", [])
    profile.setdefault("vibe_keywords", [])
    profile.setdefault("instrumentation", [])
    profile.setdefault("production_traits", {})
    profile.setdefault("summary", "")
    profile.setdefault("identity_artists", [])
    profile.setdefault("extracted_tracks", extracted_tracks[:60])

    log.info("-" * 60)
    log.info("GENERATED PROFILE:")
    log.info("  Confidence:    %s", profile.get("confidence"))
    log.info("  Genres:        %s", profile.get("dominant_genres"))
    log.info("  Subgenres:     %s", profile.get("subgenres"))
    log.info("  Vibe keywords: %s", profile.get("vibe_keywords"))
    log.info("  Artists:       %s", profile.get("identity_artists"))
    log.info("  Orientation:   %s", profile.get("listening_orientation"))
    log.info("  Discovery:     %s", profile.get("discovery_drive"))
    log.info("  Tempo pref:    %s", profile.get("tempo_preference"))
    log.info("  Energy range:  %s", profile.get("energy_range"))
    log.info("  Emotional:     %s", profile.get("emotional_profile"))
    log.info("  Production:    %s", profile.get("production_traits"))
    log.info("  Contextual:    %s", profile.get("contextual_preferences"))
    log.info("  Tracks count:  %d", len(profile.get("extracted_tracks", [])))
    log.info("  Summary:       %s", profile.get("summary"))
    log.info("-" * 60)
    log.debug("FULL PROFILE JSON:\n%s", json.dumps(profile, indent=2, ensure_ascii=False))

    log.info("Step 2: Calling Cerebras for Vibe Diagnosis...")
    diag_system = DIAGNOSIS_PROMPT.format(
        profile_json=json.dumps(profile, ensure_ascii=False),
    )

    diagnosis, _diag_raw = _call_cerebras(
        client, model=model,
        system=diag_system,
        user_content="Write the Vibe Diagnosis.",
        temperature=0.8,
    )

    diagnosis.setdefault("archetype", "The Music Lover")
    diagnosis.setdefault("diagnosis_text", profile.get("summary", ""))

    log.info("DIAGNOSIS:")
    log.info("  Archetype: %s", diagnosis.get("archetype"))
    log.info("  Text:      %s", diagnosis.get("diagnosis_text"))
    log.info("=" * 60)
    log.info("PROFILE SYNTHESIS COMPLETE")
    log.info("=" * 60)

    return profile, diagnosis
