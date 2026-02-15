import os
import requests
import secrets
import hashlib
import base64
import logging
from urllib.parse import urlencode
from .base import ProviderAdapter

logger = logging.getLogger(__name__)

# Google / YouTube OAuth Endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Scopes: 'youtube.readonly' is sufficient for fetching liked videos
SCOPES = " ".join([
    "https://www.googleapis.com/auth/youtube.readonly",
    "openid",
    "email",
    "profile"
])


def _base64url(b: bytes) -> str:
    """Helper for PKCE verifier encoding"""
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


def generate_code_verifier(length: int = 64) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return _base64url(digest)


class YouTubeProvider(ProviderAdapter):
    provider_name = "youtube"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def build_authorize_url(self):
        if not self.client_id:
            raise RuntimeError("GOOGLE_CLIENT_ID not set")

        state = secrets.token_urlsafe(16)
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "scope": SCOPES,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "access_type": "offline",  # Request refresh token just in case
            "prompt": "consent",
        }

        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", state, verifier

    def exchange_code_for_token(self, code: str, code_verifier: str):
        if not self.client_secret:
            raise RuntimeError("GOOGLE_CLIENT_SECRET is required for the token exchange")

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }

        r = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_me(self, access_token: str):
        """Fetches basic channel info to verify identity."""
        r = requests.get(
            f"{YOUTUBE_API_BASE}/channels",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"part": "snippet", "mine": "true"},
            timeout=30
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if not items:
            return {"id": "unknown", "display_name": "Unknown YouTube User"}

        snippet = items[0]["snippet"]
        return {
            "id": items[0]["id"],
            "display_name": snippet.get("title"),
            "images": snippet.get("thumbnails", {})
        }

    def ingest_liked_music_videos(self, access_token: str, limit: int = 50):
        print("DEBUG: Starting YouTube Ingest...", flush=True)
        music_tracks = []
        next_page_token = None
        max_items_to_scan = 500
        items_scanned = 0

        while len(music_tracks) < limit and items_scanned < max_items_to_scan:
            params = {
                "myRating": "like",
                "part": "snippet,contentDetails",
                "maxResults": 50,
            }
            if next_page_token:
                params["pageToken"] = next_page_token

            print(f"DEBUG: Requesting page... (scanned {items_scanned} so far)", flush=True)

            try:
                r = requests.get(
                    f"{YOUTUBE_API_BASE}/videos",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                    timeout=30
                )
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"DEBUG: API Request Failed! {e}", flush=True)
                # print(f"DEBUG: Response text: {r.text}", flush=True)
                raise e

            items = data.get("items", [])
            print(f"DEBUG: Google returned {len(items)} items on this page.", flush=True)

            if not items:
                print("DEBUG: No items found in response. Stopping.", flush=True)
                break

            for item in items:
                snippet = item.get("snippet", {})
                title = snippet.get("title", "Unknown")
                category_id = snippet.get("categoryId")

                # PRINT EVERY VIDEO FOUND TO SEE WHAT IS HAPPENING
                print(f"DEBUG: Found video: '{title}' | CategoryID: {category_id}", flush=True)

                if category_id == "10":
                    channel_title = snippet.get("channelTitle", "")
                    music_tracks.append({
                        "name": title,
                        "artists": [{"name": channel_title}],
                        "album": {"name": "YouTube Liked"},
                        "duration_ms": 0,
                        "id": item.get("id"),
                        "popularity": 50
                    })
                else:
                    print(f"DEBUG: Skipped '{title}' (Not Music)", flush=True)

                items_scanned += 1
                if len(music_tracks) >= limit:
                    break

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        print(f"DEBUG: Finished. Total music tracks found: {len(music_tracks)}", flush=True)
        return music_tracks