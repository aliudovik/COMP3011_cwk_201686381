# app/services/profile_builder.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

#from openai import OpenAI
from cerebras.cloud.sdk import Cerebras

from app.models import ProviderAccount, TrackCandidate


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_text(resp: Any) -> str:
    if hasattr(resp, "output_text"):
        try:
            return (resp.output_text or "").strip()
        except Exception:
            pass
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


# Change type hint from client: OpenAI to client: Cerebras
def _call_openai_for_json(client: Cerebras, model: str, system: str, user_obj: Dict[str, Any], temperature: float = 1) -> Tuple[Dict[str, Any], str]:
    raw = ""

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


def _collect_candidate_tracks(session, user_id: int, max_tracks: int) -> List[Dict[str, Any]]:
    q = (
        session.query(TrackCandidate)
        .join(ProviderAccount, TrackCandidate.provider_account_id == ProviderAccount.id)
        .filter(ProviderAccount.user_id == user_id)
        .order_by(TrackCandidate.observed_at.desc())
        .limit(int(max_tracks))
    )
    rows = q.all()

    tracks: List[Dict[str, Any]] = []
    seen = set()

    for c in rows:
        title = (c.title or "").strip()
        artists = (c.artists or "").strip()
        if not title or not artists:
            continue

        key = (artists.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)

        tracks.append(
            {
                "title": title,
                "artists": artists,
                "album": (c.album or "").strip() or None,
                "duration_ms": c.duration_ms,
                "isrc": (c.isrc or "").strip() or None,
                "popularity": c.popularity,
                "source": c.source,
            }
        )

    return tracks


def infer_taste_profile_with_openai(cerebras_api_key: str, tracks: List[Dict[str, Any]], model: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    if not cerebras_api_key:
        profile = {
            "method": "openai_metadata_inference",
            "model": model,
            "generated_at": _utc_now_iso(),
            "input_track_count": len(tracks),
            "confidence": "low",
            "dominant_genres": [],
            "subgenres": [],
            "vibe_keywords": [],
            "instrumentation": [],
            "production_traits": {},
            "summary": "Insufficient data to infer taste profile (CEREBRAS not set).",
        }
        explain = {"notes": "Set CEREBRAS_API_KEY to enable metadata-based taste inference.", "top_influences": []}
        trace = {"note": "CEREBRAS_API_KEY not set; deterministic fallback used."}
        return profile, explain, trace

    compact = [f"{t['artists']} — {t['title']}" for t in tracks[:60]]

    system = (
        "Infer a user's music taste profile from listening history metadata (artist and track title). "
        "Return ONLY valid JSON. Do NOT include lyrics. Do NOT request audio. "
        "Output keys exactly: confidence, dominant_genres, subgenres, vibe_keywords, instrumentation, production_traits, summary, explainability."
    )

    user_obj = {
        "tracks": compact,
        "rules": [
            "Only use metadata (artist/title).",
            "No lyrics.",
            "Keep descriptions genre/production-based.",
        ],
        "output_schema": {
            "confidence": "low|medium|high",
            "dominant_genres": ["string"],
            "subgenres": ["string"],
            "vibe_keywords": ["string"],
            "instrumentation": ["string"],
            "production_traits": {"drums": "string", "bass": "string", "melody": "string", "mixing": "string", "vocals": "string"},
            "summary": "string",
            "explainability": {"notes": "string", "top_influences": [{"track": "Artist — Title", "because": "string"}]},
        },
    }

    client = Cerebras(api_key=cerebras_api_key)
    obj, raw = _call_openai_for_json(client, model=model, system=system, user_obj=user_obj, temperature=1)

    profile: Dict[str, Any] = {
        "method": "openai_metadata_inference",
        "model": model,
        "generated_at": _utc_now_iso(),
        "input_track_count": len(tracks),
        "confidence": obj.get("confidence") or "medium",
        "dominant_genres": obj.get("dominant_genres") or [],
        "subgenres": obj.get("subgenres") or [],
        "vibe_keywords": obj.get("vibe_keywords") or [],
        "instrumentation": obj.get("instrumentation") or [],
        "production_traits": obj.get("production_traits") or {},
        "summary": obj.get("summary") or "Taste profile inferred from track metadata.",
    }

    explain = obj.get("explainability") if isinstance(obj.get("explainability"), dict) else {}
    explain_json: Dict[str, Any] = {"notes": explain.get("notes") or "", "top_influences": explain.get("top_influences") or []}

    prompt_trace = {"system": system, "user": user_obj, "raw": raw, "model": model}

    return profile, explain_json, prompt_trace


def build_profile(user_id: int, session, cerebras_api_key: str, model: str, max_tracks: int = 200) -> Dict[str, Any]:
    candidates = _collect_candidate_tracks(session, user_id=user_id, max_tracks=max_tracks)

    profile_json, explain_json, prompt_trace = infer_taste_profile_with_openai(
        cerebras_api_key=cerebras_api_key,
        tracks=candidates,
        model=model,
    )

    explain_json = dict(explain_json or {})
    explain_json["_openai_trace"] = prompt_trace

    return {"built_from_track_count": len(candidates), "profile_json": profile_json, "explain_json": explain_json}
