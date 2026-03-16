"""
Psychoacoustic Personality Assessment
--------------------------------------
17 audio A/B comparison questions + 13 behavioral text questions.
4 independent axes, 6-point preference slider, weighted scoring.
"""
from __future__ import annotations

import csv
import os
import random
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Axis definitions
# ---------------------------------------------------------------------------
AXES = {
    1: {"name": "Process",  "pole_a": "Mozart",  "pole_b": "Beethoven", "letter_a": "M", "letter_b": "B"},
    2: {"name": "Energy",   "pole_a": "Elvis",   "pole_b": "Kurt",      "letter_a": "E", "letter_b": "K"},
    3: {"name": "Focus",    "pole_a": "Lamar",   "pole_b": "Drake",     "letter_a": "L", "letter_b": "D"},
    4: {"name": "Vision",   "pole_a": "Taylor",  "pole_b": "Gaga",      "letter_a": "T", "letter_b": "G"},
}

# ---------------------------------------------------------------------------
# Audio questions  (17 total: 4+4+4+5)
# Each pair matches file index: AXIS-{x}-{PoleA}-{i}.mp3 vs AXIS-{x}-{PoleB}-{i}.mp3
# ---------------------------------------------------------------------------
AUDIO_QUESTIONS: List[Dict[str, Any]] = []

_PAIR_COUNTS = {1: 4, 2: 4, 3: 4, 4: 5}

for _axis, _count in _PAIR_COUNTS.items():
    _ax = AXES[_axis]
    for _i in range(1, _count + 1):
        AUDIO_QUESTIONS.append({
            "id": f"audio_axis{_axis}_pair{_i}",
            "axis": _axis,
            "pair_index": _i,
            "file_a": f"AXIS-{_axis}/AXIS-{_axis}-{_ax['pole_a']}-{_i}.mp3",
            "file_b": f"AXIS-{_axis}/AXIS-{_axis}-{_ax['pole_b']}-{_i}.mp3",
            "prompt": "Listen to both clips. Which sound resonates with you more?",
        })

assert len(AUDIO_QUESTIONS) == 17

# ---------------------------------------------------------------------------
# Text (behavioral) questions  (13 total: 3+3+3+4 per axis)
# ---------------------------------------------------------------------------
TEXT_QUESTIONS: List[Dict[str, Any]] = [
    # --- Axis 1: Process (Mozart vs Beethoven) ---
    {
        "id": "text_axis1_q1", "axis": 1,
        "text": "When beginning a complex project, what feels more natural?",
        "option_a": "I prefer to define a clear structure and plan before starting",
        "option_b": "I prefer to explore freely and let structure appear during the process",
    },
    {
        "id": "text_axis1_q2", "axis": 1,
        "text": "Which work style feels more comfortable?",
        "option_a": "Working steadily and consistently toward completion",
        "option_b": "Working in intense bursts of focus with varied intensity",
    },
    {
        "id": "text_axis1_q3", "axis": 1,
        "text": "How do you react to unexpected disruption in your workflow?",
        "option_a": "I prefer restoring order and returning to a clear structure",
        "option_b": "Creative chaos is where I belong... ",
    },
    # --- Axis 2: Energy (Elvis vs Kurt) ---
    {
        "id": "text_axis2_q1", "axis": 2,
        "text": "On your regular night-out, you are usually:",
        "option_a": "Talking to everyone, trying to get the party going",
        "option_b": "Remain authentic to your internal emotions - even if you are sad",
    },
    {
        "id": "text_axis2_q2", "axis": 2,
        "text": "What do you think is more important?",
        "option_a": "Trying to light yourself up even when it feels rough",
        "option_b": "Soak in deep emotional rawness, because it is real",
    },
    {
        "id": "text_axis2_q3", "axis": 2,
        "text": "How do you usually relate to others emotionally?",
        "option_a": "I naturally project warmth outward",
        "option_b": "I am always honest and rarely trying to please everybody",
    },
    # --- Axis 3: Focus (Lamar vs Drake) ---
    {
        "id": "text_axis3_q1", "axis": 3,
        "text": "You have to make an important decision, having little-to-no time, you would:",
        "option_a": "BTry to actually analytically prove it, up until the last moment",
        "option_b": "Trust your gut and choose what feels most adequate in the moment",
    },
    {
        "id": "text_axis3_q2", "axis": 3,
        "text": "When observing the world, you:",
        "option_a": "Trying to analyze the patterns and underlying processes",
        "option_b": "Appreciate the beauty around you, without caring how things work",
    },
    {
        "id": "text_axis3_q3", "axis": 3,
        "text": "For you personally, what’s more important?",
        "option_a": "Personal wellness, close relationships, enjoyment",
        "option_b": "Big ideas, changing the world, own purpose",
    },
    # --- Axis 4: Vision (Taylor vs Gaga) ---
    {
        "id": "text_axis4_q1", "axis": 4,
        "text": "When expressing ideas, you would:",
        "option_a": "Use concrete examples and real situations",
        "option_b": "Use metaphors and concepts - the meaning should uncover itself",
    },
    {
        "id": "text_axis4_q2", "axis": 4,
        "text": "What type of thinking feels more comfortable?",
        "option_a": "Grounded, practical, and reality-based",
        "option_b": "Imaginative, conceptual, and symbolic",
    },
    {
        "id": "text_axis4_q3", "axis": 4,
        "text": "If you would write a diary, it would be filled with:",
        "option_a": "Your day-to-day life, clearly noted so you can remember everything and relive the moments",
        "option_b": "Chaotic mix of emotional texts, tear-soaked pages and Renaissance-stickman drawings",
    },
    {
        "id": "text_axis4_q4", "axis": 4,
        "text": "You'd rather be remembered as:",
        "option_a": "Deeply relatable and universally loved",
        "option_b": "Wildly original and ahead of your time, even if there are haters",
    },
]

assert len(TEXT_QUESTIONS) == 13

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------
AUDIO_WEIGHT = 0.7
TEXT_WEIGHT = 0.3

# Slider maps: value index (1-6) → numeric score
# Left = pole_a, Right = pole_b
# Positions: 1=Strong A, 2=Moderate A, 3=Slight A, 4=Slight B, 5=Moderate B, 6=Strong B
SLIDER_AUDIO_MAP = {1: -3.0, 2: -2.0, 3: -1.0, 4: 1.0, 5: 2.0, 6: 3.0}
SLIDER_TEXT_MAP  = {1: -2.0, 2: -1.33, 3: -0.67, 4: 0.67, 5: 1.33, 6: 2.0}

# Max possible raw contribution per axis
# Audio: each question contributes max 3 * 0.7 = 2.1
# Text: each question contributes max 2 * 0.3 = 0.6
_AUDIO_PER_AXIS = {1: 4, 2: 4, 3: 4, 4: 5}
_TEXT_PER_AXIS   = {1: 3, 2: 3, 3: 3, 4: 4}

MAX_RAW_PER_AXIS = {
    ax: (_AUDIO_PER_AXIS[ax] * 3.0 * AUDIO_WEIGHT) + (_TEXT_PER_AXIS[ax] * 2.0 * TEXT_WEIGHT)
    for ax in range(1, 5)
}

# ---------------------------------------------------------------------------
# Audio features CSV cache
# ---------------------------------------------------------------------------
_features_cache: Dict[str, Dict[str, Any]] | None = None


def _load_features() -> Dict[str, Dict[str, Any]]:
    global _features_cache
    if _features_cache is not None:
        return _features_cache

    csv_path = os.path.join(
        os.path.dirname(__file__), os.pardir, "static", "test-audio", "test-audio-features.csv"
    )
    csv_path = os.path.normpath(csv_path)

    _features_cache = {}
    if not os.path.exists(csv_path):
        return _features_cache

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fname = row.get("filename", "").strip()
            if not fname:
                continue
            features: Dict[str, Any] = {}
            for k, v in row.items():
                if k in ("axis", "filename", "filepath", "status", "error"):
                    continue
                try:
                    features[k] = float(v)
                except (ValueError, TypeError):
                    features[k] = v
            _features_cache[fname] = features

    return _features_cache


def get_features_for_file(filename: str) -> Dict[str, Any]:
    """Return the audio feature dict for a given filename."""
    feats = _load_features()
    return feats.get(filename, {})


# ---------------------------------------------------------------------------
# Config generator (called once per test session)
# ---------------------------------------------------------------------------
def generate_test_config() -> Dict[str, Any]:
    """Return a randomized test configuration for the frontend."""
    audio_qs = []
    for q in AUDIO_QUESTIONS:
        swap = random.choice([True, False])
        audio_qs.append({
            "id": q["id"],
            "axis": q["axis"],
            "pair_index": q["pair_index"],
            "prompt": q["prompt"],
            "left_file": q["file_b"] if swap else q["file_a"],
            "right_file": q["file_a"] if swap else q["file_b"],
            "swapped": swap,  # True means pole_b is on the left
        })
    random.shuffle(audio_qs)

    text_qs = []
    for q in TEXT_QUESTIONS:
        swap = random.choice([True, False])
        text_qs.append({
            "id": q["id"],
            "axis": q["axis"],
            "text": q["text"],
            "left_option": q["option_b"] if swap else q["option_a"],
            "right_option": q["option_a"] if swap else q["option_b"],
            "swapped": swap,
        })
    random.shuffle(text_qs)

    return {
        "audio_questions": audio_qs,
        "text_questions": text_qs,
        "total_questions": len(audio_qs) + len(text_qs),
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_test(
    audio_answers: List[Dict[str, Any]],
    text_answers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Score the complete test.

    Each answer dict must contain:
      - id: question id
      - value: int 1-6 (slider position, 1=strong left, 6=strong right)
      - swapped: bool (whether left/right were swapped from default)

    Returns full result payload.
    """
    # Accumulate raw scores per axis (negative = pole_a, positive = pole_b)
    axis_raw: Dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}

    # --- Score audio answers ---
    for ans in audio_answers:
        q = _find_audio_q(ans["id"])
        if not q:
            continue
        raw = SLIDER_AUDIO_MAP.get(ans["value"], 0.0)
        # If swapped, left was pole_b, so slider-left means pole_b → flip sign
        if ans.get("swapped", False):
            raw = -raw
        axis_raw[q["axis"]] += raw * AUDIO_WEIGHT

    # --- Score text answers ---
    for ans in text_answers:
        q = _find_text_q(ans["id"])
        if not q:
            continue
        raw = SLIDER_TEXT_MAP.get(ans["value"], 0.0)
        if ans.get("swapped", False):
            raw = -raw
        axis_raw[q["axis"]] += raw * TEXT_WEIGHT

    # --- Build code + percentages ---
    code = ""
    axis_details = {}
    for ax_num in range(1, 5):
        ax = AXES[ax_num]
        raw_score = axis_raw[ax_num]
        max_raw = MAX_RAW_PER_AXIS[ax_num]
        pct = min(100.0, abs(raw_score) / max_raw * 100.0) if max_raw else 50.0

        if raw_score < 0:
            letter = ax["letter_a"]
            dominant = ax["pole_a"]
        elif raw_score > 0:
            letter = ax["letter_b"]
            dominant = ax["pole_b"]
        else:
            letter = ax["letter_a"]  # tie-break to pole_a
            dominant = ax["pole_a"]
            pct = 50.0

        code += letter
        axis_details[ax_num] = {
            "axis_name": ax["name"],
            "raw_score": round(raw_score, 3),
            "percentage": round(pct, 1),
            "dominant_pole": dominant,
            "letter": letter,
            "pole_a": ax["pole_a"],
            "pole_b": ax["pole_b"],
        }

    # --- Build per-pair audio preference JSON ---
    audio_preferences = _build_audio_preferences(audio_answers)

    # --- Get type description ---
    type_info = PSYCHOACOUSTIC_TYPE_CATALOG.get(code, DEFAULT_PSYCH_TYPE)

    # --- Assemble result ---
    result = {
        "profile_type": "psychoacoustic",
        "psychoacoustic_code": code,
        "title": type_info["title"],
        "axis_scores": {str(k): v for k, v in axis_details.items()},
        "audio_preferences": audio_preferences,
        "sections": type_info["sections"],
    }

    return result


def _find_audio_q(qid: str) -> Dict[str, Any] | None:
    for q in AUDIO_QUESTIONS:
        if q["id"] == qid:
            return q
    return None


def _find_text_q(qid: str) -> Dict[str, Any] | None:
    for q in TEXT_QUESTIONS:
        if q["id"] == qid:
            return q
    return None


def _build_audio_preferences(audio_answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a clean JSON of all 17 audio pair preferences.
    Each entry maps to the file pair and records which file was preferred + strength.
    """
    preferences: Dict[str, Any] = {}

    for ans in audio_answers:
        q = _find_audio_q(ans["id"])
        if not q:
            continue

        slider_val = ans["value"]   # 1-6
        swapped = ans.get("swapped", False)

        # Determine which actual file was on left/right
        if swapped:
            left_file = q["file_b"]
            right_file = q["file_a"]
        else:
            left_file = q["file_a"]
            right_file = q["file_b"]

        # Determine preference direction + strength
        # Slider 1-3 = prefer left, 4-6 = prefer right
        if slider_val <= 3:
            preferred_file = left_file
            strength_label = {1: "strong", 2: "moderate", 3: "slight"}[slider_val]
            strength_value = {1: 3, 2: 2, 3: 1}[slider_val]
        else:
            preferred_file = right_file
            strength_label = {4: "slight", 5: "moderate", 6: "strong"}[slider_val]
            strength_value = {4: 1, 5: 2, 6: 3}[slider_val]

        # Get audio features for both files
        file_a_name = os.path.basename(q["file_a"])
        file_b_name = os.path.basename(q["file_b"])
        preferred_name = os.path.basename(preferred_file)

        entry = {
            "preferred_file": preferred_file,
            "preferred_filename": preferred_name,
            "preference_strength": strength_value,
            "preference_label": strength_label,
            "slider_value": slider_val,
            "file_a": q["file_a"],
            "file_b": q["file_b"],
            "file_a_features": get_features_for_file(file_a_name),
            "file_b_features": get_features_for_file(file_b_name),
            "preferred_features": get_features_for_file(preferred_name),
        }

        # Key format: axis_1_pair_1, axis_1_pair_2, etc.
        key = f"axis_{q['axis']}_pair_{q['pair_index']}"
        preferences[key] = entry

    return preferences


# ---------------------------------------------------------------------------
# Psychoacoustic Type Catalog  (16 combos)
# ---------------------------------------------------------------------------
# Code letters: M/B (axis1) + E/K (axis2) + L/D (axis3) + T/G (axis4)
PSYCHOACOUSTIC_TYPE_CATALOG: Dict[str, Dict[str, Any]] = {
    "MELT": {
        "title": "The Elegant Architect",
        "sections": {
            "how_you_work": "You approach everything with the precision of a master watchmaker. Your mind naturally organizes chaos into structure, and you find deep satisfaction in systems that are both beautiful and functional. You don't rush — you refine. Every decision is deliberate, every detail intentional. People may see your process as slow, but what they're really witnessing is craftsmanship.",
            "superpower": "You can take any tangled mess — a project, a conversation, a broken system — and untangle it into something elegant. Your ability to hold complexity in your head while keeping the output simple is genuinely rare. You're the person people call when things need to actually work.",
            "achilles_heel": "Perfectionism can become a cage. You sometimes polish things so long they never ship. The fear of releasing something imperfect can cost you momentum, and you may struggle to let others contribute because their standards don't match yours.",
            "perfect_studio": "A sunlit room with tall ceilings and minimal furniture. A single instrument in the center, perfectly tuned. Sheet music organized by composer. The kind of silence that feels intentional. A place where every object earns its space.",
        },
    },
    "MELG": {
        "title": "The Refined Provocateur",
        "sections": {
            "how_you_work": "You combine classical precision with avant-garde vision. Where others see contradiction between order and chaos, you see a spectrum to play with. You plan meticulously, then deliberately break your own rules at the perfect moment. The result is work that feels both inevitable and surprising.",
            "superpower": "You bridge worlds that don't normally talk to each other. You can sit in a boardroom and a mosh pit with equal comfort. Your taste is both refined and fearless, which means you create things that feel premium but never predictable.",
            "achilles_heel": "You can come across as contradictory. People who value consistency may find your shapeshifting unsettling. You also tend to get bored the moment something becomes routine, which can leave a trail of beautifully started but unfinished projects.",
            "perfect_studio": "A converted industrial space with museum-quality lighting. Analog synths next to a grand piano. A mood board that looks like it was curated by a fashion editor who listens to Stockhausen. Controlled chaos, deliberately arranged.",
        },
    },
    "MEDT": {
        "title": "The Velvet Strategist",
        "sections": {
            "how_you_work": "You're a smooth operator with deep emotional intelligence. You process the world through feeling first, then construct elegant frameworks around those feelings. Your approach is atmospheric — you set the tone before you set the agenda. People feel understood in your presence before you've even said much.",
            "superpower": "You read rooms like sheet music. You know exactly when to lead with warmth and when to pull back into mystery. Your emotional radar is calibrated to frequencies most people can't detect, making you an extraordinary collaborator and communicator.",
            "achilles_heel": "You can overthink the emotional dimension of everything. Sometimes a decision is just a decision, but you'll spend hours feeling through every possible reaction. You may also struggle with directness when it risks disrupting the vibe you've carefully built.",
            "perfect_studio": "A cozy room with warm lighting, thick curtains, and a vintage record player. Scented candles. A notebook full of half-written lyrics. The kind of space where time melts and everything feels like a scene from a film.",
        },
    },
    "MEDG": {
        "title": "The Harmonic Visionary",
        "sections": {
            "how_you_work": "You operate at the intersection of beauty and boldness. Your mind craves harmony, but your spirit demands innovation. You're drawn to creating experiences — not just products or art, but entire worlds people can step into. You think in textures, colors, and frequencies.",
            "superpower": "You have an almost synesthetic ability to blend sensory inputs into something cohesive. Where others make songs, you build sonic environments. Where others write plans, you compose experiences. Your work has a signature atmosphere that's instantly recognizable.",
            "achilles_heel": "Your ambition can outpace your bandwidth. The gap between what you envision and what you can execute in a day can be genuinely frustrating. You need collaborators who can match your wavelength, and finding them isn't always easy.",
            "perfect_studio": "A dome-shaped room with surround sound and projection mapping. Instruments from three continents on the walls. A tea ceremony station next to a MIDI controller. A place where ancient wisdom and future technology coexist.",
        },
    },
    "MKLT": {
        "title": "The Quiet Insurgent",
        "sections": {
            "how_you_work": "You carry revolutionary ideas inside a calm exterior. Your approach is methodical but your convictions are fierce. You don't need to be the loudest in the room because your work speaks volumes. You build things that look simple on the surface but contain layers of meaning underneath.",
            "superpower": "Depth. Everything you touch gets deeper. You can take a simple concept and excavate it until it becomes profound. Your patience is weaponized — while others sprint and burn out, you maintain intensity over long distances.",
            "achilles_heel": "You can become so internally focused that you forget to surface. Your ideas deserve audiences, but you sometimes hoard them, convinced they're not ready. Isolation is your comfort zone, but it can also become your limitation.",
            "perfect_studio": "A basement with exposed brick, one window at ceiling height letting in a strip of light. Stacks of books. A guitar with worn frets. A whiteboard covered in diagrams no one else can decipher. The kind of space that has a specific smell and a specific story.",
        },
    },
    "MKLG": {
        "title": "The Structured Rebel",
        "sections": {
            "how_you_work": "You're a paradox: disciplined but defiant. You study the rules harder than anyone, specifically so you know exactly how to break them. Your creative process has rigor behind it, but the output always challenges expectations. You prepare like a scholar and perform like a punk.",
            "superpower": "You make rebellion sustainable. While others burn bright and fade, your structured approach to challenging norms means you can keep pushing boundaries for decades. You're the long game revolutionary.",
            "achilles_heel": "The tension between structure and chaos lives inside you constantly. You can become rigid about being anti-rigid, which is its own trap. Learning to sit with uncertainty — without immediately systemizing it — is your growth edge.",
            "perfect_studio": "A library that doubles as a recording studio. Philosophy books next to distortion pedals. A color-coded calendar on one wall, graffiti on another. Everything contradicts, and somehow it all makes sense.",
        },
    },
    "MKDT": {
        "title": "The Intimate Alchemist",
        "sections": {
            "how_you_work": "You transform personal pain into universal truth. Your process is deeply internal — you need time alone to process before you can create. But when you do create, it comes out with a specificity that makes everyone feel like you're speaking directly to them. Your work is a mirror.",
            "superpower": "Emotional authenticity. In a world of performed emotions, yours are real and people can tell. You have the rare ability to be vulnerable without being weak, specific without being alienating. Your honesty is your art.",
            "achilles_heel": "You can get stuck in your own emotional landscape. Sometimes what you need isn't more introspection — it's sunlight. You may also attract people who want to consume your emotional labor without reciprocating.",
            "perfect_studio": "A cabin in the woods with one instrument and a journal. Rain on the window. A single lamp. No WiFi on purpose. The kind of place where you can hear your own thoughts clearly, maybe for the first time in months.",
        },
    },
    "MKDG": {
        "title": "The Dark Innovator",
        "sections": {
            "how_you_work": "You operate in the shadows of the creative spectrum, mining beauty from places others are afraid to look. Your work is both intimate and experimental — you take personal truths and wrap them in unexpected forms. You're not trying to be weird; you're trying to be real, and reality is weird.",
            "superpower": "You make the uncomfortable beautiful. You can take the darkest, most complex human experiences and give them a form that people can sit with. Your work doesn't just express — it transforms.",
            "achilles_heel": "You can get so deep into your own process that you lose perspective. The line between artistic depth and self-indulgence is one you have to consciously manage. You need trusted people who can tell you when you've gone too far inward.",
            "perfect_studio": "An underground space with no natural light but incredible acoustics. Screens displaying generative art. A collection of field recordings from abandoned places. Equipment that looks more like sculpture than technology.",
        },
    },
    "BELT": {
        "title": "The Passionate Commander",
        "sections": {
            "how_you_work": "You lead with fire and back it up with charisma. Your creative process is explosive — you generate ideas at velocity and have the social energy to rally others around them. You don't just create; you perform. Every presentation is a show, every conversation is a stage.",
            "superpower": "You make people believe. Your combination of raw passion and strategic thinking means you can sell a vision while simultaneously building it. You're the founder energy, the opening-night energy, the 'follow me into battle' energy.",
            "achilles_heel": "Sustainability. You burn so bright that burnout is a real and recurring pattern. You may also confuse volume with value — not every idea deserves maximum intensity. Learning when to whisper is your masterclass.",
            "perfect_studio": "A rooftop space in a city that never sleeps. Speakers that can rattle windows. A whiteboard, a microphone, and a phone full of contacts. The kind of space where you pace while you think and gesture while you talk.",
        },
    },
    "BELG": {
        "title": "The Showrunner",
        "sections": {
            "how_you_work": "You think in spectacles. Your mind naturally gravitates toward the biggest possible version of any idea. You're not content with good — you want iconic. Your process involves gathering diverse influences, throwing them in a blender, and serving something no one's tasted before.",
            "superpower": "Scale and originality. You can envision entire worlds and actually build them. Where others think in songs, you think in albums. Where others think in projects, you think in movements. Your ambition is matched by genuine creative range.",
            "achilles_heel": "You can steamroll subtlety. Not everything needs to be epic, and sometimes the quiet moments are the most powerful. You may also struggle with collaborative dynamics when others don't share your grand vision.",
            "perfect_studio": "A warehouse converted into a multimedia playground. Stage lighting, multiple screens, instruments from every genre. A team of people who get it. The kind of space where a rehearsal feels like a premiere.",
        },
    },
    "BEDT": {
        "title": "The Magnetic Storyteller",
        "sections": {
            "how_you_work": "You live at the intersection of passion and narrative. Everything you do has a storyline, a character arc, an emotional payoff. You're naturally theatrical but grounded in real emotion. People are drawn to you because you make them feel like they're part of something cinematic.",
            "superpower": "You turn ordinary moments into mythology. Your ability to find the epic in the everyday makes your work universally compelling. You're the person who can tell a story about a Tuesday morning and make people cry.",
            "achilles_heel": "You can get addicted to the narrative. Sometimes life doesn't have a clean arc, and forcing one can mean missing what's actually happening. You may also struggle with the parts of creation that aren't glamorous — the admin, the revision, the waiting.",
            "perfect_studio": "A theater with the curtain perpetually half-open. Mood lighting that changes with the time of day. A piano, a camera, and a rack of costumes. The kind of space where reality and performance blur by design.",
        },
    },
    "BEDG": {
        "title": "The Supernova",
        "sections": {
            "how_you_work": "You are pure creative force. Your energy is volcanic and your imagination is limitless. You don't follow trends — you create weather systems. Your process is less about planning and more about channeling whatever's moving through you at maximum intensity.",
            "superpower": "You are unforgettable. In a world of beige, you are neon. Your willingness to go all the way — emotionally, aesthetically, conceptually — means your work has an impact that lingers long after the experience ends. You change the temperature of any room you enter.",
            "achilles_heel": "Everything at maximum intensity is exhausting — for you and everyone around you. You need to build rest into your system, not as laziness but as reloading. Also, not every moment needs to be a moment. Sometimes tea is just tea.",
            "perfect_studio": "An arena. Seriously. Or at minimum, a space with 30-foot ceilings, a fog machine, and absolutely zero acoustic dampening. A place where the reverb matches your personality. Go big or go home, and you're never going home.",
        },
    },
    "BKLT": {
        "title": "The Raw Philosopher",
        "sections": {
            "how_you_work": "You think big thoughts and deliver them with zero polish. Your creative process is almost academic in its rigor but punk in its delivery. You care deeply about meaning — every word, every note, every choice has to earn its place. But you'd rather be raw than refined.",
            "superpower": "Intellectual fire. You can dismantle an argument, a song structure, or a social norm with equal precision. Your combination of analytical depth and emotional intensity makes your work hit different — it's smart and it hurts.",
            "achilles_heel": "You can be intimidating without meaning to be. Your standards are high and your tolerance for superficiality is low, which can push away people who might actually have something to offer. Not everything has to be profound to be worthwhile.",
            "perfect_studio": "A concrete room with a single harsh light. Stacks of vinyl records and philosophy texts. A drum kit that's seen better days. Coffee that's been sitting too long. The kind of space where comfort is irrelevant and truth is the only currency.",
        },
    },
    "BKLG": {
        "title": "The Glitch Architect",
        "sections": {
            "how_you_work": "You build systems specifically to break them in interesting ways. Your mind operates on a frequency that most people can't access — it's not chaos, it's a different kind of order. You're drawn to the edges of every genre, every convention, every expectation.",
            "superpower": "You see possibilities that literally don't exist yet. Your ability to combine unlikely elements into something coherent is a form of genius that often goes unrecognized until the world catches up. You're always about three years ahead.",
            "achilles_heel": "Being ahead of your time is lonely. You may struggle with feeling misunderstood, and the temptation to dumb things down for accessibility can feel like betrayal. Finding your audience requires patience you don't naturally have.",
            "perfect_studio": "A server room repurposed as a creative lab. Custom-built instruments. Screens displaying code alongside visual art. The hum of machines as a constant drone. A space that would confuse a contractor and inspire an engineer.",
        },
    },
    "BKDT": {
        "title": "The Wounded Healer",
        "sections": {
            "how_you_work": "You create from scar tissue. Your process isn't pretty — it's excavation. You dig into the hardest truths and shape them into something others can hold without getting cut. Your work is therapy, both for you and for anyone brave enough to engage with it.",
            "superpower": "You make people feel less alone. In a world that rewards polish and performance, your radical honesty is medicine. You can name the feelings other people are afraid to admit they have, and that naming is a form of liberation.",
            "achilles_heel": "You can confuse suffering with meaning. Not all pain produces art, and not all art requires pain. Learning to create from joy — or even just from Tuesday — is a muscle you need to build. Your darkness is a gift, but it's not the only room in the house.",
            "perfect_studio": "A room that feels lived in. Stained notebooks, broken strings, half-empty mugs. Photos pinned to the wall — real people, real moments. A space that looks like it's been through something, because it has.",
        },
    },
    "BKDG": {
        "title": "The Chaos Oracle",
        "sections": {
            "how_you_work": "You are the purest form of creative destruction. You don't just think outside the box — you set the box on fire and build a spaceship from the ashes. Your process looks insane from the outside but follows a dream-logic that produces things the world has never seen.",
            "superpower": "You are genuinely original. Not 'influenced by original things' original — actually, truly, maddeningly original. Your work doesn't reference; it generates. You are a primary source in a world of citations.",
            "achilles_heel": "Accessibility. Your brilliance can be so far ahead that it becomes isolating. You need translators — people who can bridge your vision to the rest of the world without diluting it. Alone, you're a genius in a vacuum. With the right team, you're a movement.",
            "perfect_studio": "Does not exist yet. You would need to invent it. Probably involves spatial audio, AI collaborators, and materials that haven't been synthesized. Until then, any empty room with electricity will do. The studio is your nervous system.",
        },
    },
}

DEFAULT_PSYCH_TYPE: Dict[str, Any] = {
    "title": "The Resonance Seeker",
    "sections": {
        "how_you_work": "You navigate the creative world with balanced curiosity, drawing from multiple influences without being defined by any single one.",
        "superpower": "Adaptability. You can find something valuable in almost any creative context.",
        "achilles_heel": "You may struggle to commit to a single direction when multiple paths look equally appealing.",
        "perfect_studio": "A flexible space that can transform based on your current mood and project needs.",
    },
}
