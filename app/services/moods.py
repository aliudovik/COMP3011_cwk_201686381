MOODS = [
    {"id": "chill", "label": "Chill", "hint": "Lower tempo, softer dynamics, smooth textures."},
    {"id": "happy", "label": "Happy", "hint": "Brighter harmony, upbeat groove, optimistic feel."},
    {"id": "energetic", "label": "Energetic", "hint": "Higher tempo, stronger transients, driving rhythm."},
    {"id": "sad", "label": "Sad", "hint": "Slower tempo, minor-leaning, simpler arrangement."},
    {"id": "focus", "label": "Focus", "hint": "Steady tempo, minimal vocals, low variation."},
    {"id": "romantic", "label": "Romantic", "hint": "Warm harmony, lush textures, mid-tempo."},
    {"id": "aggressive", "label": "Aggressive", "hint": "Harder drums, darker timbre, high intensity."},
]

MOOD_SHIFTS = {
    "chill": {"tempo": "down", "brightness": "down", "energy": "down"},
    "happy": {"tempo": "up", "brightness": "up", "energy": "mid"},
    "energetic": {"tempo": "up", "brightness": "mid", "energy": "up"},
    "sad": {"tempo": "down", "brightness": "down", "energy": "down"},
    "focus": {"tempo": "steady", "brightness": "mid", "energy": "down"},
    "romantic": {"tempo": "steady", "brightness": "mid", "energy": "mid"},
    "aggressive": {"tempo": "up", "brightness": "down", "energy": "up"},
}
