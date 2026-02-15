import base64
import hashlib
import os
import secrets
import string
import requests
from urllib.parse import urlencode
from .base import ProviderAdapter

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

def _base64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")

def generate_code_verifier(length: int = 64) -> str:
    alphabet = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))

def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return _base64url(digest)

class SpotifyProvider(ProviderAdapter):
    provider_name = "spotify"

    def __init__(self, client_id: str, redirect_uri: str):
        self.client_id = client_id
        self.redirect_uri = redirect_uri

    def build_authorize_url(self):
        if not self.client_id:
            raise RuntimeError("SPOTIFY_CLIENT_ID not set")

        state = secrets.token_urlsafe(16)
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)

        scope = "user-top-read"
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "scope": scope,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}", state, verifier

    def exchange_code_for_token(self, code: str, code_verifier: str):
        data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }
        r = requests.post(SPOTIFY_TOKEN_URL, data=data, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_me(self, access_token: str):
        r = requests.get(
            f"{SPOTIFY_API_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30
        )
        r.raise_for_status()
        return r.json()

    def ingest_top_tracks(self, access_token: str, limit: int = 50, time_range: str = "medium_term"):
        r = requests.get(
            f"{SPOTIFY_API_BASE}/me/top/tracks",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"limit": limit, "time_range": time_range},
            timeout=30
        )
        r.raise_for_status()
        return r.json().get("items", [])
