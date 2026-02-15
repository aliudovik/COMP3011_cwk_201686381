# app/routes/firebase_auth.py
"""
Firebase Authentication endpoints.
Handles token verification, user migration (anon -> authenticated),
login state, and logout.
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request, session

from app.extensions import db
from app.models import User

log = logging.getLogger("drvibey.auth")

firebase_auth_bp = Blueprint("firebase_auth", __name__)


def _get_firebase_user_info(id_token: str) -> dict:
    """Verify a Firebase ID token and return decoded user info."""
    from firebase_admin import auth as fb_auth
    decoded = fb_auth.verify_id_token(id_token)
    return {
        "uid": decoded["uid"],
        "email": decoded.get("email", ""),
        "name": decoded.get("name", ""),
        "picture": decoded.get("picture", ""),
        "provider": _extract_provider(decoded),
    }


def _extract_provider(decoded: dict) -> str:
    """Extract the sign-in provider from the Firebase token."""
    sign_in_provider = decoded.get("firebase", {}).get("sign_in_provider", "")
    if "google" in sign_in_provider:
        return "google"
    if "apple" in sign_in_provider:
        return "apple"
    if "password" in sign_in_provider:
        return "email"
    return sign_in_provider or "unknown"


# -----------------------------------------------------------------
# POST /api/auth/verify-token
# body: { id_token: str }
# -----------------------------------------------------------------
@firebase_auth_bp.post("/verify-token")
def verify_token():
    body = request.get_json(silent=True) or {}
    id_token = body.get("id_token", "").strip()

    if not id_token:
        return jsonify({"ok": False, "error": "Missing id_token"}), 400

    try:
        fb_info = _get_firebase_user_info(id_token)
    except Exception as e:
        log.warning("Firebase token verification failed: %s", e)
        return jsonify({"ok": False, "error": "Invalid or expired token"}), 401

    firebase_uid = fb_info["uid"]
    email = fb_info["email"]
    name = fb_info["name"]
    photo = fb_info["picture"]
    provider = fb_info["provider"]

    log.info("[verify-token] uid=%s email=%s provider=%s", firebase_uid, email, provider)

    # 1) Check if this Firebase UID already exists (returning user)
    existing = User.query.filter_by(auth_provider_id=firebase_uid).first()
    if existing:
        log.info("[verify-token] existing user id=%s, logging in", existing.id)
        # Update fields that may have changed
        if email and existing.email != email:
            # Check email isn't taken by another user
            email_user = User.query.filter_by(email=email).first()
            if email_user and email_user.id != existing.id:
                existing.email = f"{email}_{existing.id}"
            else:
                existing.email = email
        if name:
            existing.display_name = name
        if photo:
            existing.photo_url = photo
        db.session.commit()

        session["user_id"] = existing.id
        session["is_authenticated"] = True
        return jsonify({
            "ok": True,
            "user": _user_dict(existing),
        })

    # 2) Check if current session has an anonymous user -> migrate
    anon_id = session.get("user_id")
    if anon_id:
        anon_user = db.session.get(User, anon_id)
        if anon_user and not anon_user.auth_provider_id:
            log.info("[verify-token] migrating anon user id=%s -> firebase uid=%s",
                     anon_user.id, firebase_uid)
            # Update the anonymous user to become authenticated
            if email:
                # Check email isn't taken by another user
                email_user = User.query.filter_by(email=email).first()
                if email_user and email_user.id != anon_user.id:
                    anon_user.email = f"{email}_{anon_user.id}"
                else:
                    anon_user.email = email
            if name:
                anon_user.display_name = name
            anon_user.auth_provider = provider
            anon_user.auth_provider_id = firebase_uid
            anon_user.photo_url = photo or None
            db.session.commit()

            session["is_authenticated"] = True
            return jsonify({
                "ok": True,
                "user": _user_dict(anon_user),
            })

    # 3) No existing user, no anon session -> create new
    log.info("[verify-token] creating new user for firebase uid=%s", firebase_uid)
    new_user = User(
        email=email or f"firebase-{firebase_uid}@genify.local",
        display_name=name or "Music Lover",
        auth_provider=provider,
        auth_provider_id=firebase_uid,
        photo_url=photo or None,
    )
    db.session.add(new_user)
    db.session.commit()

    session["user_id"] = new_user.id
    session["is_authenticated"] = True
    return jsonify({
        "ok": True,
        "user": _user_dict(new_user),
    })


# -----------------------------------------------------------------
# POST /api/auth/logout
# -----------------------------------------------------------------
@firebase_auth_bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


# -----------------------------------------------------------------
# GET /api/auth/me
# -----------------------------------------------------------------
@firebase_auth_bp.get("/me")
def me():
    user_id = session.get("user_id")
    is_auth = session.get("is_authenticated", False)

    if not user_id:
        return jsonify({
            "ok": True,
            "is_authenticated": False,
            "user": None,
        })

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({
            "ok": True,
            "is_authenticated": False,
            "user": None,
        })

    return jsonify({
        "ok": True,
        "is_authenticated": bool(is_auth and user.auth_provider_id),
        "user": _user_dict(user),
    })


def _user_dict(user: User) -> dict:
    """Serialize a User to a frontend-friendly dict."""
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "photo_url": user.photo_url,
        "auth_provider": user.auth_provider,
        "is_authenticated": bool(user.auth_provider_id),
    }
