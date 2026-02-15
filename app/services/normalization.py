import re

def _clean(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def canonicalize_track(title: str, artist: str):
    return _clean(title), _clean(artist)

def pick_primary_artist(artists_str: str) -> str:
    if not artists_str:
        return ""
    parts = [p.strip() for p in artists_str.split(",") if p.strip()]
    return parts[0] if parts else artists_str.strip()
