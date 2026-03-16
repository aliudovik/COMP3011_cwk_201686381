# app/services/drvibey_chat.py
"""
drVibey chat service.
Manages the music-psychologist conversation flow, Cerebras API calls,
and final listener-profile synthesis.

Flow: 10 structured questions. Screenshots first, then deep taste profiling.
No LLM calls for question transitions (all questions are fixed/short).
LLM used only for: OCR cleanup + richer evidence extraction, profile synthesis,
and diagnosis.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from cerebras.cloud.sdk import Cerebras
from app.services.profile_image import generate_profile_avatar_url, normalize_avatar_identity
from app.services.type_catalog import TYPE_CATALOG

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
        "text": "Nice library! Pick your top 3 songs and 3 artists — these will be your strongest style anchors.",
        # text is dynamically enriched with OCR examples by build_q2()
        "input_type": "chip_select",  # tap-to-select from OCR results
        "skippable": False,
        "dimension": "identity_picks",
    },
    {
        "number": 3,
        "text": (
            "Name one song that makes you ultra-emotional. "
            "What exact emotion do you feel, and which moment in the song causes it?"
        ),
        "input_type": "text",
        "skippable": True,
        "dimension": "emotional_trigger_depth",
    },
    {
        "number": 4,
        "text": "What grabs you first in a new song?",
        "input_type": "buttons",
        "button_options": ["Lyrics", "Beat", "Vibe", "Voice"],
        "skippable": False,
        "dimension": "listening_orientation",
    },
    {
        "number": 5,
        "text": "Which energy curve feels best to you?",
        "input_type": "buttons",
        "button_options": ["Slow build", "Steady groove", "Big switches"],
        "skippable": False,
        "dimension": "energy_curve_preference",
    },
    {
        "number": 6,
        "text": "What's your ideal vocal presence?",
        "input_type": "buttons",
        "button_options": ["Intimate", "Powerful", "Melodic rap/spoken", "Mostly instrumental"],
        "skippable": False,
        "dimension": "vocal_identity_preference",
    },
    {
        "number": 7,
        "text": "What production texture do you prefer?",
        "input_type": "buttons",
        "button_options": ["Raw/Lo-fi", "Balanced", "Clean/Hi-fi"],
        "skippable": False,
        "dimension": "production_aesthetic_polarity",
    },
    {
        "number": 8,
        "text": "For your avatar style, choose one:",
        "input_type": "buttons",
        "button_options": ["👨", "👩", "⭐"],
        "skippable": False,
        "dimension": "avatar_identity",
    },
    {
        "number": 9,
        "text": "For generated songs, what lyric style should dominate?",
        "input_type": "buttons",
        "button_options": ["Simple & catchy", "Balanced", "Poetic & layered"],
        "skippable": False,
        "dimension": "lyric_depth",
    },
    {
        "number": 10,
        "text": "Pick your listening personality in 3 taps:",
        "input_type": "multi_buttons",
        "button_groups": [
            {
                "label": "Emotional mode",
                "options": ["Comfort", "Challenge", "Both"],
                "dimension": "emotion_regulation_strategy",
            },
            {
                "label": "Discovery style",
                "options": ["Explorer", "Loyalist", "Hybrid"],
                "dimension": "discovery_drive",
            },
            {
                "label": "Inner world",
                "options": ["Introspective", "Outward", "Balanced"],
                "dimension": "introspection_bias",
            },
        ],
        "skippable": False,
        "dimension": "personality_triptych",
    },
]

TOTAL_QUESTIONS = len(DRVIBEY_QUESTIONS)  # 10

SKIP_TOKEN = "[skipped]"


# ── system prompts ───────────────────────────────────────────────────

PROFILE_SYNTHESIS_PROMPT = """\
You are a senior music-taste analyst and prompt strategist for Suno.
You have data from a conversation between drVibey (a music psychologist) and a listener,
PLUS tracks extracted from screenshots, PLUS explicit top picks selected by the user.

Analyze ALL of this data and produce a deeply structured listener profile that is directly useful
for high-fidelity music prompt generation.

CONVERSATION DATA:
{conversation_json}

QUESTION ANSWERS (dimension keyed):
{question_answers_json}

EXTRACTED TRACKS FROM SCREENSHOTS:
{tracks_json}

IDENTITY ANCHORS SELECTED BY USER (Q2):
{favorites_json}

OUTPUT RULES:
- Return ONLY valid JSON matching the schema below. No markdown, no explanation.
- Be specific and detailed. Use actual genres/subgenres/micro-scenes, not vague labels.
- Base your analysis on BOTH the conversation answers AND the track list.
- Priority order is STRICT:
  1) identity anchors selected by user in Q2,
  2) repeated OCR evidence,
  3) free-text answers.
- Answers marked "[skipped]" should be ignored -- the user chose not to answer.
- The summary should be 2-3 vivid sentences describing this listener's musical identity.
- listener_persona should feel like a music-MBTI profile and be interesting for the user to read.
- prompt_translation_hints must be practical and directly reusable by a prompt generator.
- suggested_artists should include adjacent artists likely to match this listener.
- soul_signature should be vivid, intimate, and profile-grounded: around 60-90 words in second person, flattering but specific to their musical evidence.
- soul_signature must read like a personal emotional portrait, not a technical style summary.
- Avoid BPM numbers, avoid listing genres/subgenres, and avoid production-jargon-heavy wording.
- Good examples of tone:
  - "Your soul is so deep that only dark, atmospheric bass and melancholic R&B can reach it."
  - "You are a spark of energy that charges everyone around you, the way breakbeat charges your own pulse."
- avatar_identity should reflect user's avatar answer: "boy", "girl", or "wonder".
- Use the lyric_depth answer to shape prompt_translation_hints lyric guidance.
- Use the introspection_bias answer to inform the introspection_bias temperament axis.
- listener_persona.listener_mbti_like must be one of these exact codes only:
  FVPD, FVPR, FVCD, FVCR, FIPD, FIPR, FICD, FICR,
  NVPD, NVPR, NVCD, NVCR, NIPD, NIPR, NICD, NICR.
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
  "emotion_regulation_strategy": "comfort|challenge|both|varied",
  "avatar_identity": "boy|girl|wonder",
  "contextual_preferences": {{
    "focus_work": "genre/vibe description",
    "active_energy": "genre/vibe description",
    "emotional_processing": "genre/vibe description",
    "social": "genre/vibe description"
  }},
  "listener_persona": {{
    "archetype_name": "string",
    "listener_mbti_like": "4-letter music type code",
    "temperament_axes": {{
      "intensity_seeking": 0.0,
      "emotional_openness": 0.0,
      "novelty_drive": 0.0,
      "rhythmic_dependence": 0.0,
      "introspection_bias": 0.0
    }},
    "explanation": "2 short sentences explaining this music personality"
  }},
  "style_blueprint": {{
    "style_vectors": ["ranked style vector 1", "ranked style vector 2", "ranked style vector 3"],
    "arrangement_preferences": "description",
    "dynamic_profile": "description",
    "vocal_treatment": "description",
    "mix_character": "description"
  }},
  "prompt_translation_hints": {{
    "must_include": ["string", "string", "string"],
    "avoid": ["string", "string", "string"],
    "tempo_targets": ["string"],
    "energy_targets": ["string"],
    "context_variants": {{
      "focus_work": "string",
      "active_energy": "string",
      "social": "string",
      "emotional_release": "string"
    }}
  }},
  "identity_anchor_weights": {{
    "selected_artists": [{{"artist": "string", "weight": 1.0}}],
    "selected_songs": [{{"title": "string", "artist": "string", "weight": 1.0}}]
  }},
  "extracted_tracks": [
    {{"artist": "...", "title": "..."}}
  ],
  "suggested_artists": ["artist1", "artist2", "artist3"],
  "soul_signature": "around 60-90 words, second-person soul-level portrait grounded in the listener's style evidence",
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
- Do NOT mention any type code/name (including MBTI labels) in the diagnosis text
- Keep it under 60 words total
- Sound like a cool music-obsessed friend, not a clinical report
- Make the wording emotionally intimate, like you're naming what music does to their inner world.
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
4. Estimate `evidence_count` for each track (how often it likely appears)
5. Add optional extra fields when inferable from OCR text only:
   - edition_tags: ["live", "acoustic", "remix", "sped_up", ...]
   - language_hint: short label
   - era_hint: short label
   - source_confidence: number 0..1
6. Return a clean list of tracks

Return ONLY valid JSON: {{"tracks": [{{"artist": "...", "title": "...", "evidence_count": 1, "edition_tags": [], "language_hint": "", "era_hint": "", "source_confidence": 0.8}}]}}
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


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def _extract_identity_picks(history: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Parse the Q2 chip-select payload from chat history.
    Frontend sends text like: "Artists: A, B, C | Songs: T1, T2, T3".
    """
    selected_artists: List[str] = []
    selected_songs: List[Dict[str, str]] = []

    user_msgs = [m.get("content", "") for m in history if m.get("role") == "user"]
    for msg in user_msgs:
        text = (msg or "").strip()
        if "Artists:" not in text and "Songs:" not in text:
            continue

        for part in [p.strip() for p in text.split("|")]:
            if part.startswith("Artists:"):
                names = [x.strip() for x in part.replace("Artists:", "", 1).split(",") if x.strip()]
                for n in names:
                    if n.lower() not in {a.lower() for a in selected_artists}:
                        selected_artists.append(n)
            elif part.startswith("Songs:"):
                songs = [x.strip() for x in part.replace("Songs:", "", 1).split(",") if x.strip()]
                for s in songs:
                    title = s
                    artist = ""
                    if " — " in s:
                        title, artist = [x.strip() for x in s.split(" — ", 1)]
                    elif " -- " in s:
                        title, artist = [x.strip() for x in s.split(" -- ", 1)]
                    elif " - " in s:
                        title, artist = [x.strip() for x in s.split(" - ", 1)]
                    selected_songs.append({"title": title, "artist": artist})

    selected_song_uniq = []
    seen_song = set()
    for s in selected_songs:
        key = (s.get("title", "").lower(), s.get("artist", "").lower())
        if key in seen_song:
            continue
        seen_song.add(key)
        selected_song_uniq.append(s)

    return {
        "selected_artists": selected_artists[:3],
        "selected_songs": selected_song_uniq[:3],
        "weights": {
            "selected_favorites": 1.0,
            "ocr_frequency": 0.7,
            "free_text_answers": 0.45,
        },
    }


def _build_question_answers(history: List[Dict[str, str]]) -> Dict[str, str]:
    """Map user answers to question dimensions for easier synthesis consumption."""
    user_answers = [
        str(m.get("content", "")).strip()
        for m in history
        if m.get("role") == "user"
    ]
    user_answers = [
        ans for ans in user_answers
        if ans and not ans.startswith("[Uploaded ")
    ]

    # Q1 is screenshots (no text answer in history). Q2..Q10 are mapped from user answers.
    dimensions = [q["dimension"] for q in DRVIBEY_QUESTIONS[1:]]
    mapped: Dict[str, str] = {}
    for i, dim in enumerate(dimensions):
        if i < len(user_answers):
            if user_answers[i] != SKIP_TOKEN:
                mapped[dim] = user_answers[i]

    # Expand personality_triptych into its sub-dimensions
    triptych = mapped.pop("personality_triptych", "")
    if triptych:
        parts = [p.strip() for p in triptych.split("|")]
        q10 = DRVIBEY_QUESTIONS[9]  # Q10
        groups = q10.get("button_groups", [])
        for j, group in enumerate(groups):
            if j < len(parts) and parts[j]:
                mapped[group["dimension"]] = parts[j]

    return mapped


def _derive_listener_type(axes: Dict[str, Any]) -> str:
    """Create a 4-letter music personality code from temperament axes."""
    intensity = _clamp01(axes.get("intensity_seeking"), 0.5)
    novelty = _clamp01(axes.get("novelty_drive"), 0.5)
    openness = _clamp01(axes.get("emotional_openness"), 0.5)
    introspection = _clamp01(axes.get("introspection_bias"), 0.5)

    c1 = "F" if novelty >= 0.5 else "N"        # Futuristic vs Nostalgic
    c2 = "V" if openness >= 0.5 else "I"       # Vocal-led vs Instrumental-led
    c3 = "P" if intensity >= 0.5 else "C"      # Powerful vs Chill
    c4 = "D" if introspection >= 0.5 else "R"  # Dreamer vs Realist
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


def build_q2_from_tracks(tracks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build Q2 dynamically from OCR-extracted tracks.
    Returns the Q2 message + chip_options for the frontend.
    """
    q = DRVIBEY_QUESTIONS[1]

    # Collect weighted artist/song candidates for chips
    artist_weights: Dict[str, float] = {}
    artist_labels: Dict[str, str] = {}
    song_weights: Dict[Tuple[str, str], float] = {}
    song_labels: Dict[Tuple[str, str], Dict[str, str]] = {}

    for t in tracks:
        artist = t.get("artist", "").strip()
        title = t.get("title", "").strip()
        evidence = max(1.0, float(t.get("evidence_count") or 1))
        confidence = _clamp01(t.get("source_confidence"), 0.75)
        weight = evidence * confidence

        if artist:
            key_artist = artist.lower()
            artist_weights[key_artist] = artist_weights.get(key_artist, 0.0) + weight
            artist_labels[key_artist] = artist

        if title:
            key_song = (title.lower(), artist.lower())
            song_weights[key_song] = song_weights.get(key_song, 0.0) + weight
            song_label = title if not artist else f"{title} — {artist}"
            song_labels[key_song] = {"title": title, "artist": artist, "label": song_label}

    artists = [
        artist_labels[k]
        for k, _v in sorted(artist_weights.items(), key=lambda kv: kv[1], reverse=True)
    ]
    songs = [
        song_labels[k]
        for k, _v in sorted(song_weights.items(), key=lambda kv: kv[1], reverse=True)
    ]

    # Build a short message with a few examples
    examples = []
    for a in artists[:3]:
        examples.append(a)
    for s in songs[:2]:
        examples.append(s["title"])
    example_str = ", ".join(examples[:4])

    reply = (
        f"Nice library! I see {example_str}... "
        "Pick your top 3 songs and 3 artists — these will be your strongest style anchors."
    )
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

    if q["input_type"] == "multi_buttons" and "button_groups" in q:
        result["button_groups"] = q["button_groups"]

    return result


def cleanup_ocr_tracks(
    cerebras_api_key: str,
    model: str,
    parsed_tracks: List[Dict[str, str]],
    raw_texts: List[str],
) -> List[Dict[str, Any]]:
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

    clean: List[Dict[str, Any]] = []
    for t in tracks:
        if isinstance(t, dict) and ("artist" in t or "title" in t):
            edition_tags = t.get("edition_tags")
            if not isinstance(edition_tags, list):
                edition_tags = []
            clean.append({
                "artist": str(t.get("artist", "")).strip(),
                "title": str(t.get("title", "")).strip(),
                "evidence_count": int(t.get("evidence_count") or 1),
                "edition_tags": [str(x).strip() for x in edition_tags if str(x).strip()][:5],
                "language_hint": str(t.get("language_hint", "")).strip(),
                "era_hint": str(t.get("era_hint", "")).strip(),
                "source_confidence": _clamp01(t.get("source_confidence"), 0.75),
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
    identity_picks = _extract_identity_picks(history)
    question_answers = _build_question_answers(history)

    log.info("Step 1: Calling Cerebras for profile synthesis...")
    system = PROFILE_SYNTHESIS_PROMPT.format(
        conversation_json=json.dumps(conversation_data, ensure_ascii=False),
        question_answers_json=json.dumps(question_answers, ensure_ascii=False),
        tracks_json=json.dumps(extracted_tracks[:60], ensure_ascii=False),
        favorites_json=json.dumps(identity_picks, ensure_ascii=False),
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
    profile.setdefault("soul_signature", "")
    profile.setdefault("identity_artists", [])
    profile.setdefault("suggested_artists", [])
    profile.setdefault("emotion_regulation_strategy", "varied")
    profile.setdefault("avatar_identity", normalize_avatar_identity(question_answers.get("avatar_identity", "wonder")))
    profile.setdefault("profile_avatar_url", "")
    profile.setdefault("listener_persona", {})
    profile.setdefault("style_blueprint", {})
    profile.setdefault("prompt_translation_hints", {})
    profile.setdefault("identity_anchor_weights", {
        "selected_artists": [{"artist": a, "weight": 1.0} for a in identity_picks.get("selected_artists", [])],
        "selected_songs": [
            {
                "title": s.get("title", ""),
                "artist": s.get("artist", ""),
                "weight": 1.0,
            }
            for s in identity_picks.get("selected_songs", [])
        ],
    })
    profile.setdefault("extracted_tracks", extracted_tracks[:60])

    profile["avatar_identity"] = normalize_avatar_identity(profile.get("avatar_identity", question_answers.get("avatar_identity", "wonder")))

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
        "You seek emotionally honest songs with a recognizable sonic fingerprint and strong repeat value.",
    )
    profile["listener_persona"] = listener_persona

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

    imagerouter_api_key = os.getenv("IMAGEROUTER_API_KEY", "").strip()
    profile["profile_avatar_url"] = profile.get("profile_avatar_url") or generate_profile_avatar_url(
        profile=profile,
        api_key=imagerouter_api_key,
    )

    log.info("DIAGNOSIS:")
    log.info("  Archetype: %s", diagnosis.get("archetype"))
    log.info("  Text:      %s", diagnosis.get("diagnosis_text"))
    log.info("=" * 60)
    log.info("PROFILE SYNTHESIS COMPLETE")
    log.info("=" * 60)

    return profile, diagnosis
