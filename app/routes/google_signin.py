from __future__ import annotations

import secrets

import requests
from flask import Blueprint, abort, current_app, redirect, request, session, url_for

from app.extensions import db
from app.models import User

google_signin_bp = Blueprint("google_signin", __name__)


def _safe_next_url(u: str | None) -> str | None:
    """Allow only local, non-protocol-relative redirect targets."""
    if not u:
        return None
    if u.startswith("/") and not u.startswith("//"):
        return u
    return None


def _extract_provider(userinfo: dict) -> str:
    """Map Google userinfo payload to our auth_provider values."""
    return "google" if userinfo.get("sub") else "unknown"


def _upsert_user_from_google(userinfo: dict) -> User:
    """
    Find or create user from Google identity.
    Priority:
    1) exact provider id match
    2) email match (covers older Firebase-UID records)
    3) migrate current anonymous session user
    4) create new user
    """
    google_sub = (userinfo.get("sub") or "").strip()
    email = (userinfo.get("email") or "").strip()
    name = (userinfo.get("name") or "").strip()
    picture = (userinfo.get("picture") or "").strip()
    provider = _extract_provider(userinfo)

    if not google_sub:
        abort(400, "Missing Google subject id")

    # 1) Existing provider id
    existing = User.query.filter_by(auth_provider_id=google_sub).first()
    if existing:
        if email and existing.email != email:
            email_user = User.query.filter_by(email=email).first()
            if email_user and email_user.id != existing.id:
                existing.email = f"{email}_{existing.id}"
            else:
                existing.email = email
        if name:
            existing.display_name = name
        if picture:
            existing.photo_url = picture
        existing.auth_provider = provider
        db.session.commit()
        return existing

    # 2) Match by email (old Firebase UID accounts transition)
    if email:
        email_match = User.query.filter_by(email=email).first()
        if email_match:
            email_match.auth_provider = provider
            email_match.auth_provider_id = google_sub
            if name:
                email_match.display_name = name
            if picture:
                email_match.photo_url = picture
            db.session.commit()
            return email_match

    # 3) Migrate anonymous session user
    anon_id = session.get("user_id")
    if anon_id:
        anon_user = db.session.get(User, anon_id)
        if anon_user and not anon_user.auth_provider_id:
            if email:
                email_user = User.query.filter_by(email=email).first()
                if email_user and email_user.id != anon_user.id:
                    anon_user.email = f"{email}_{anon_user.id}"
                else:
                    anon_user.email = email
            if name:
                anon_user.display_name = name
            anon_user.auth_provider = provider
            anon_user.auth_provider_id = google_sub
            anon_user.photo_url = picture or None
            db.session.commit()
            return anon_user

    # 4) Create new user
    new_user = User(
        email=email or f"google-{google_sub}@genify.local",
        display_name=name or "Music Lover",
        auth_provider=provider,
        auth_provider_id=google_sub,
        photo_url=picture or None,
    )
    db.session.add(new_user)
    db.session.commit()
    return new_user


@google_signin_bp.get("/auth/google-signin")
def google_signin_start():
    """
    Start server-side Google OAuth sign-in flow.
    """
    client_id = current_app.config.get("GOOGLE_CLIENT_ID", "")
    redirect_uri = current_app.config.get("GOOGLE_SIGNIN_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        abort(503, "Google sign-in is not configured")

    state = secrets.token_urlsafe(32)
    session["google_signin_state"] = state

    next_url = _safe_next_url(request.args.get("next"))
    if next_url:
        session["google_signin_next"] = next_url

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    req = requests.Request("GET", auth_url, params=params).prepare()
    return redirect(req.url)


@google_signin_bp.get("/auth/google-signin/callback")
def google_signin_callback():
    """
    Complete server-side Google OAuth sign-in flow.
    """
    state = request.args.get("state", "")
    code = request.args.get("code", "")
    expected_state = session.get("google_signin_state", "")
    if not state or not code:
        abort(400, "Missing state or code")
    if not expected_state or state != expected_state:
        abort(400, "State mismatch")

    client_id = current_app.config.get("GOOGLE_CLIENT_ID", "")
    client_secret = current_app.config.get("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = current_app.config.get("GOOGLE_SIGNIN_REDIRECT_URI", "")
    if not client_id or not client_secret or not redirect_uri:
        abort(503, "Google sign-in is not configured")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if not token_resp.ok:
        abort(400, "Failed to exchange code for token")
    token_data = token_resp.json()
    access_token = token_data.get("access_token", "")
    if not access_token:
        abort(400, "Missing access token")

    userinfo_resp = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if not userinfo_resp.ok:
        abort(400, "Failed to fetch Google profile")
    userinfo = userinfo_resp.json()

    user = _upsert_user_from_google(userinfo)
    session["user_id"] = user.id
    session["is_authenticated"] = True

    session.pop("google_signin_state", None)
    nxt = _safe_next_url(session.pop("google_signin_next", None))
    if nxt:
        return redirect(nxt)
    return redirect(url_for("public.public_home"))
