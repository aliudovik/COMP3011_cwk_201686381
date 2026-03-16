# app/services/suno_client.py
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class SunoClient:
    """
    Official Suno API client (sunoapi.org).

    - POST /api/v1/generate
    - GET  /api/v1/generate/record-info?taskId=...
    - POST /api/v1/lyrics
    - GET  /api/v1/lyrics/record-info?taskId=...
    Docs: https://docs.sunoapi.org
    """

    def __init__(self, base_url: str, api_key: str, timeout_s: int = 180):
        if not base_url:
            raise ValueError("SunoClient base_url is empty.")
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout_s = int(timeout_s)

        if not self.api_key:
            raise ValueError("SunoClient api_key is empty. Set SUNO_API_KEY.")

        logger.info(
            f"SunoClient initialized: base_url={self.base_url}, api_key={self.api_key[:8]}..., timeout={self.timeout_s}s")

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def generate(
            self,
            prompt: str,
            is_instrumental: bool,
            custom_mode: bool,
            style: str = "",
            title: str = "",
            model: str = "V4_5ALL",
            callback_url: str = "",
            negative_tags: str = "",
            vocal_gender: str = "",
            style_weight: Optional[float] = None,
            weirdness_constraint: Optional[float] = None,
            audio_weight: Optional[float] = None,
            persona_id: str = "",
    ) -> Dict[str, Any]:
        # The API REQUIRES callBackUrl - use a dummy public URL if localhost
        # The callback won't work for localhost anyway, but we poll instead
        if not callback_url or any(x in callback_url for x in ["localhost", "127.0.0.1", "0.0.0.0"]):
            # Use a dummy URL that satisfies the API requirement
            # We rely on polling (poll_until_first_or_complete) instead of callbacks
            callback_url = "http://127.0.0.1:7777/callback/"

        payload: Dict[str, Any] = {
            "customMode": bool(custom_mode),
            "instrumental": bool(is_instrumental),
            "model": model,
            "callBackUrl": callback_url,
        }

        # Custom mode rules
        if custom_mode:
            payload["title"] = (title or "Untitled")[:100]
            payload["style"] = (style or "")[:1000]
            payload["prompt"] = (prompt or "")[:5000]
        else:
            # Non-custom mode: only prompt
            payload["prompt"] = (prompt or "")[:500]

        # Optional knobs
        if negative_tags:
            payload["negativeTags"] = negative_tags
        if vocal_gender in ("m", "f"):
            payload["vocalGender"] = vocal_gender
        if style_weight is not None:
            payload["styleWeight"] = float(style_weight)
        if weirdness_constraint is not None:
            payload["weirdnessConstraint"] = float(weirdness_constraint)
        if audio_weight is not None:
            payload["audioWeight"] = float(audio_weight)
        if persona_id:
            payload["personaId"] = persona_id

        url = f"{self.base_url}/api/v1/generate"

        # Log the request details (without full prompt for brevity)
        logger.info(f"Suno API POST to {url}")
        logger.info(
            f"Payload keys: {list(payload.keys())}, model={payload.get('model')}, customMode={payload.get('customMode')}, instrumental={payload.get('instrumental')}")
        logger.debug(f"Full payload: {payload}")

        try:
            r = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout_s)
            logger.info(f"Suno API response status: {r.status_code}")
            logger.debug(f"Suno API response headers: {dict(r.headers)}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to Suno API: {e}")
            raise RuntimeError(f"Failed to connect to Suno API at {url}: {e}")
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout connecting to Suno API: {e}")
            raise RuntimeError(f"Timeout connecting to Suno API: {e}")

        # Try to get response body even on error
        try:
            data = r.json()
            logger.info(f"Suno API response: code={data.get('code')}, msg={data.get('msg')}")
        except Exception as json_err:
            logger.error(f"Failed to parse JSON response: {json_err}, raw text: {r.text[:500]}")
            r.raise_for_status()  # Will raise appropriate HTTP error
            raise RuntimeError(f"Invalid JSON response from Suno API: {r.text[:200]}")

        r.raise_for_status()

        # API uses {"code":200,...}
        if isinstance(data, dict) and data.get("code") != 200:
            raise RuntimeError(f"Suno generate failed: {data.get('code')} {data.get('msg')}")

        return data

    # -----------------------------------------------------------------
    # Lyrics generation (POST /api/v1/lyrics + polling)
    # -----------------------------------------------------------------

    def generate_lyrics(self, prompt: str, callback_url: str = "") -> Dict[str, Any]:
        if not callback_url or any(x in callback_url for x in ["localhost", "127.0.0.1", "0.0.0.0"]):
            callback_url = "http://127.0.0.1:7777/callback/"

        if len(prompt) > 200:
            prompt = prompt[:200].rstrip()

        payload = {
            "prompt": prompt,
            "callBackUrl": callback_url,
        }

        url = f"{self.base_url}/api/v1/lyrics"
        logger.info("Suno Lyrics API POST to %s (prompt_chars=%d)", url, len(prompt))

        try:
            r = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout_s)
            logger.info("Suno Lyrics API response status: %s", r.status_code)
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"Failed to connect to Suno Lyrics API at {url}: {e}")
        except requests.exceptions.Timeout as e:
            raise RuntimeError(f"Timeout connecting to Suno Lyrics API: {e}")

        try:
            data = r.json()
            logger.info("Suno Lyrics API response: code=%s, msg=%s", data.get("code"), data.get("msg"))
        except Exception as json_err:
            logger.error("Failed to parse Lyrics JSON response: %s, raw: %s", json_err, r.text[:500])
            r.raise_for_status()
            raise RuntimeError(f"Invalid JSON response from Suno Lyrics API: {r.text[:200]}")

        r.raise_for_status()

        if isinstance(data, dict) and data.get("code") != 200:
            raise RuntimeError(f"Suno lyrics generation failed: {data.get('code')} {data.get('msg')}")

        return data

    def get_lyrics_details(self, task_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/api/v1/lyrics/record-info"
        r = requests.get(
            url,
            params={"taskId": str(task_id)},
            headers=self._headers(),
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("code") != 200:
            raise RuntimeError(f"Suno lyrics record-info failed: {data.get('code')} {data.get('msg')}")
        return data

    def poll_until_lyrics_ready(
            self,
            task_id: str,
            attempts: int = 60,
            sleep_s: float = 1,
    ) -> Dict[str, Any]:
        """Poll /api/v1/lyrics/record-info until SUCCESS or a terminal failure."""
        _LYRICS_FAIL = (
            "CREATE_TASK_FAILED",
            "GENERATE_LYRICS_FAILED",
            "CALLBACK_EXCEPTION",
            "SENSITIVE_WORD_ERROR",
        )
        last: Dict[str, Any] = {}
        for _ in range(int(attempts)):
            last = self.get_lyrics_details(task_id)
            status = ((last.get("data") or {}).get("status") or "").upper()

            if status == "SUCCESS":
                return last
            if status in _LYRICS_FAIL:
                return last

            time.sleep(float(sleep_s))

        return last

    # -----------------------------------------------------------------
    # Music generation details + polling
    # -----------------------------------------------------------------

    def get_generation_details(self, task_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/api/v1/generate/record-info"
        r = requests.get(
            url,
            params={"taskId": str(task_id)},
            headers=self._headers(),
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("code") != 200:
            raise RuntimeError(f"Suno record-info failed: {data.get('code')} {data.get('msg')}")
        return data

    def poll_until_first_or_complete(
            self,
            task_id: str,
            attempts: int = 60,
            sleep_s: int = 10,
    ) -> Dict[str, Any]:
        last: Dict[str, Any] = {}
        for _ in range(int(attempts)):
            last = self.get_generation_details(task_id)
            status = ((last.get("data") or {}).get("status") or "").upper()

            if status in ("FIRST_SUCCESS", "SUCCESS"):
                return last
            if status in (
                    "CREATE_TASK_FAILED",
                    "GENERATE_AUDIO_FAILED",
                    "CALLBACK_EXCEPTION",
                    "SENSITIVE_WORD_ERROR",
            ):
                return last

            time.sleep(int(sleep_s))

        return last

    def poll_until_stream_ready(
            self,
            task_id: str,
            attempts: int = 60,
            sleep_s: int = 5,
    ) -> Dict[str, Any]:
        """Poll until streamAudioUrl is available (faster than waiting for full SUCCESS)."""
        last: Dict[str, Any] = {}
        for _ in range(int(attempts)):
            last = self.get_generation_details(task_id)
            data = last.get("data") or {}
            status = (data.get("status") or "").upper()

            # Check for failure statuses
            if status in (
                    "CREATE_TASK_FAILED",
                    "GENERATE_AUDIO_FAILED",
                    "CALLBACK_EXCEPTION",
                    "SENSITIVE_WORD_ERROR",
            ):
                return last

            # Check if streamAudioUrl exists in response
            response = data.get("response") or {}
            tracks = response.get("sunoData") or response.get("data") or []
            if tracks and len(tracks) > 0:
                first_track = tracks[0]
                stream_url = first_track.get("streamAudioUrl") or first_track.get("sourceStreamAudioUrl")
                if stream_url:
                    return last

            # Also return on SUCCESS/FIRST_SUCCESS as fallback
            if status in ("FIRST_SUCCESS", "SUCCESS"):
                return last

            time.sleep(int(sleep_s))

        return last
