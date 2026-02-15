# app/services/ocr.py
"""
Local OCR service using pytesseract (Tesseract OCR engine).
Extracts text from Spotify/Apple Music/YouTube Music screenshots
and parses out track + artist information.

Works cross-platform (Linux Docker, macOS, etc.).
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Tuple

import pytesseract
from PIL import Image

log = logging.getLogger("drvibey.ocr")


# ── helpers ──────────────────────────────────────────────────────────

def _ocr_image(path: str) -> str:
    """Run Tesseract OCR on a single image file, return raw text."""
    img = Image.open(path)
    # Convert to grayscale for better OCR accuracy on screenshot UIs
    img = img.convert("L")
    text = pytesseract.image_to_string(img, lang="eng+kor+jpn+spa+por+chi_sim+chi_tra+rus+fra")
    log.debug("OCR on %s: %d chars extracted", os.path.basename(path), len(text))
    return text


def _clean_ocr_lines(raw: str) -> List[str]:
    """Normalise OCR output into clean, non-empty lines."""
    lines: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip common Spotify UI chrome
        if line.lower() in (
            "liked songs", "recently played", "made for you",
            "download", "shuffle play", "shuffle", "play",
            "search", "your library", "home", "premium",
            "...", "•••", "see all", "sort", "filter",
            "add to playlist", "share", "queue",
        ):
            continue
        # Skip lines that are just timestamps or durations (e.g. "3:42")
        if re.fullmatch(r"\d{1,2}:\d{2}", line):
            continue
        # Skip pure numbers (play counts, etc.)
        if re.fullmatch(r"[\d,.\s]+", line):
            continue
        # Skip very short lines (likely OCR noise)
        if len(line) < 2:
            continue
        lines.append(line)
    return lines


def _pair_tracks_from_lines(lines: List[str]) -> List[Dict[str, str]]:
    """
    Spotify screenshots typically alternate:
        Track Title
        Artist Name (sometimes with feat. / &)
    Attempt to pair consecutive lines as (title, artist).
    Also handle "Artist • Album" or "Artist · Album" patterns.
    """
    tracks: List[Dict[str, str]] = []
    i = 0
    while i < len(lines):
        title_candidate = lines[i]
        # If the line itself contains a separator like " - " or " — " that
        # suggests "Artist - Title" in a single line:
        if " — " in title_candidate or " - " in title_candidate:
            sep = " — " if " — " in title_candidate else " - "
            parts = title_candidate.split(sep, 1)
            tracks.append({"artist": parts[0].strip(), "title": parts[1].strip()})
            i += 1
            continue

        # Look ahead for an artist line
        if i + 1 < len(lines):
            artist_candidate = lines[i + 1]
            # Strip "• Album" or "· Album" suffix from artist lines
            artist_clean = re.split(r"\s*[•·]\s*", artist_candidate)[0].strip()
            if artist_clean:
                tracks.append({"title": title_candidate, "artist": artist_clean})
                i += 2
                continue

        # Single line – treat as title with unknown artist
        tracks.append({"title": title_candidate, "artist": ""})
        i += 1

    return tracks


# ── public API ───────────────────────────────────────────────────────

def extract_tracks_from_image(image_path: str) -> Tuple[List[Dict[str, str]], str]:
    """
    Process a single screenshot.
    Returns (tracks_list, raw_ocr_text).
    """
    raw = _ocr_image(image_path)
    lines = _clean_ocr_lines(raw)
    tracks = _pair_tracks_from_lines(lines)
    log.info("Extracted %d tracks from %s", len(tracks), os.path.basename(image_path))
    return tracks, raw


def extract_tracks_from_images(image_paths: List[str]) -> Tuple[List[Dict[str, str]], List[str]]:
    """
    Process multiple screenshots.
    Returns (combined_tracks_list, list_of_raw_texts).
    """
    all_tracks: List[Dict[str, str]] = []
    all_raw: List[str] = []

    for path in image_paths:
        tracks, raw = extract_tracks_from_image(path)
        all_tracks.extend(tracks)
        all_raw.append(raw)

    # Deduplicate by (artist_lower, title_lower)
    seen = set()
    unique: List[Dict[str, str]] = []
    for t in all_tracks:
        key = (t["artist"].lower().strip(), t["title"].lower().strip())
        if key not in seen and (t["artist"] or t["title"]):
            seen.add(key)
            unique.append(t)

    log.info("Total: %d raw tracks -> %d unique across %d images",
             len(all_tracks), len(unique), len(image_paths))
    return unique, all_raw


def save_upload_to_temp(file_storage) -> str:
    """
    Save a Werkzeug FileStorage to a temp file and return its path.
    Caller is responsible for cleanup.
    """
    suffix = os.path.splitext(file_storage.filename or ".png")[1] or ".png"
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="drvibey_ocr_")
    os.close(fd)
    file_storage.save(path)
    return path
