from __future__ import annotations

import random
import time
import uuid
from flask import Blueprint, render_template, request, session, url_for, redirect

from app.extensions import db
from app.models import User, ProviderAccount
from app.services.moods import MOODS
from app.services.activities import ACTIVITIES

public_bp = Blueprint("public", __name__)
demo_bp = Blueprint("demo", __name__, url_prefix="/demo")

DEMO_NAME = "Demo User"

GENRE_LIST = [
    "Pop", "Hip-Hop", "R&B", "Rock", "Indie",
    "Electronic", "Jazz", "Classical", "Country",
    "Latin", "Metal", "Folk", "Reggae", "Blues", "Funk",
]


def _create_anonymous_user() -> User:
    """Create a unique anonymous user for this session."""
    anon_email = f"anon-{uuid.uuid4()}@genify.local"
    u = User(email=anon_email, display_name=DEMO_NAME)
    db.session.add(u)
    db.session.commit()
    return u


def ensure_demo_session() -> User:
    """Return a per-IP demo user; new IP => new user."""
    current_ip = request.remote_addr
    uid = session.get("user_id")
    stored_ip = session.get("client_ip")

    if uid and stored_ip == current_ip:
        u = db.session.get(User, uid)
        if u:
            return u

    # Either first visit, or IP changed: create a fresh anonymous user
    u = _create_anonymous_user()
    session["user_id"] = u.id
    session["client_ip"] = current_ip
    return u


def is_spotify_connected(user_id: int) -> bool:
    acct = ProviderAccount.query.filter_by(user_id=user_id, provider="spotify").first()
    return acct is not None


@public_bp.get("/")
def public_home():
    """
    Public UI:
      - First screen is always the drVibey chat (no Spotify connection needed)
      - After chat + profile, proceeds to mood/activity/extras/generate flow
    """
    user = ensure_demo_session()

    # Keep mood assignment stable across refresh until reset
    if "public_mood_ids" not in session:
        ids = [m["id"] for m in MOODS]
        random.shuffle(ids)
        session["public_mood_ids"] = ids

    mood_order = session["public_mood_ids"]
    mood_map = {m["id"]: m for m in MOODS}
    ordered_moods = [mood_map[mid] for mid in mood_order if mid in mood_map]

    emoji_map = {
        "chill": "\U0001f60c",
        "happy": "\U0001f604",
        "energetic": "\u26a1",
        "sad": "\U0001f622",
        "focus": "\U0001f3a7",
        "romantic": "\U0001f497",
        "aggressive": "\U0001f525",
    }

    radial_classes = ["red", "orange", "gold", "green", "blue", "indigo", "purple"]

    mood_items = []
    for idx, m in enumerate(ordered_moods[:7]):
        mood_items.append(
            {
                "id": m["id"],
                "label": m["label"],
                "hint": m.get("hint", ""),
                "emoji": emoji_map.get(m["id"], "\U0001f642"),
                "cls": radial_classes[idx],
            }
        )

    # ---- Activity items ----
    if "public_activity_ids" not in session:
        act_ids = [a["id"] for a in ACTIVITIES]
        random.shuffle(act_ids)
        session["public_activity_ids"] = act_ids

    activity_order = session["public_activity_ids"]
    activity_map = {a["id"]: a for a in ACTIVITIES}
    ordered_activities = [activity_map[aid] for aid in activity_order if aid in activity_map]

    activity_emoji_map = {
        "studying":        "\U0001f4d6",
        "working_out":     "\U0001f4aa",
        "falling_in_love": "\U0001f495",
        "driving":         "\U0001f697",
        "meditating":      "\U0001f9d8",
        "partying":        "\U0001f389",
        "winding_down":    "\U0001f319",
    }

    activity_radial_classes = ["red", "orange", "gold", "green", "blue", "indigo", "purple"]

    activity_items = []
    for idx, a in enumerate(ordered_activities[:7]):
        activity_items.append(
            {
                "id": a["id"],
                "label": a["label"],
                "hint": a.get("hint", ""),
                "emoji": activity_emoji_map.get(a["id"], "\U0001f3b5"),
                "cls": activity_radial_classes[idx],
            }
        )

    from flask import current_app
    cfg = current_app.config
    return render_template(
        "public.html",
        user_id=user.id,
        mood_items=mood_items,
        activity_items=activity_items,
        genre_list=GENRE_LIST,
        cache_bust=int(time.time()),
        firebase_config={
            "apiKey": cfg.get("FIREBASE_WEB_API_KEY", ""),
            "authDomain": cfg.get("FIREBASE_AUTH_DOMAIN", ""),
            "projectId": cfg.get("FIREBASE_PROJECT_ID", ""),
            "storageBucket": cfg.get("FIREBASE_STORAGE_BUCKET", ""),
            "messagingSenderId": cfg.get("FIREBASE_MESSAGING_SENDER_ID", ""),
            "appId": cfg.get("FIREBASE_APP_ID", ""),
        },
    )


@demo_bp.get("/reset")
def demo_reset():
    """
    "Start over" for the public mock UI.
    Keeps DB as-is; clears session UI state.
    """
    session.pop("public_mood_ids", None)
    session.pop("public_activity_ids", None)
    # Optional: force showing connect screen again even if already connected
    session["public_force_connect"] = True
    return redirect(url_for("public.public_home"))


@public_bp.get("/public")
def public_legacy_redirect():
    """Backwards-compat: old /public URL redirects to /."""
    return redirect(url_for("public.public_home"))


@public_bp.before_app_request
def _public_force_connect_flag_cleanup():
    """
    If you want "Start over" to show connect first, we can use a soft flag:
    - /demo/reset sets session['public_force_connect']=True
    - /public reads it (via template logic below) and clears it
    """
    pass
