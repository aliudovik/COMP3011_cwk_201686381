from __future__ import annotations

from typing import Dict

TYPE_CATALOG: Dict[str, Dict[str, str]] = {
    "FVPD": {"name": "Neon Oracle", "description": "Big future-pop vocals, cinematic drops, emotional but explosive."},
    "FVPR": {"name": "Arena Pulse", "description": "Anthemic hooks, confidence music, modern stadium energy."},
    "FVCD": {"name": "Holo Whisper", "description": "Airy vocals, synth haze, gentle but transportive."},
    "FVCR": {"name": "Chrome Confessional", "description": "Clean production, intimate lyrics, sleek and controlled."},
    "FIPD": {"name": "Circuit Titan", "description": "Aggressive electronic/instrumental force, bass-forward and bold."},
    "FIPR": {"name": "Iron Groove", "description": "Precision beats, rhythm obsession, engineered momentum."},
    "FICD": {"name": "Glass Drift", "description": "Ambient textures, experimental calm, dreamy sonic architecture."},
    "FICR": {"name": "Metro Minimalist", "description": "Sparse, modern, efficient, tasteful and highly curated."},
    "NVPD": {"name": "Velvet Prophet", "description": "Soulful retro vocals with emotional weight and big feeling."},
    "NVPR": {"name": "Anthem Archivist", "description": "Classic-era bangers, sing-alongs, timeless crowd energy."},
    "NVCD": {"name": "Moonlit Memoir", "description": "Warm, sentimental, late-night memory soundtrack."},
    "NVCR": {"name": "Cassette Confessor", "description": "Honest lyric-first songs, grounded and deeply human."},
    "NIPD": {"name": "Analog Storm", "description": "Guitar/drum-driven force, raw intensity, old-school fire."},
    "NIPR": {"name": "Vinyl Vanguard", "description": "Groove purist, craft-focused, timeless instrumental authority."},
    "NICD": {"name": "Lo-Fi Stargazer", "description": "Soft instrumental nostalgia, dreamy and introspective."},
    "NICR": {"name": "Hearth Conductor", "description": "Organic, cozy, grounded arrangements; calm and intentional."},
}

DEFAULT_TYPE_META = {
    "name": "Resonance Seeker",
    "description": "Emotion-led taste with balanced sonic curiosity.",
}


def get_type_meta(code: str) -> Dict[str, str]:
    c = str(code or "").strip().upper()
    return TYPE_CATALOG.get(c, DEFAULT_TYPE_META)
