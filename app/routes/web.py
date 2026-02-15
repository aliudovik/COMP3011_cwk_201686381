import uuid
from flask import Blueprint, render_template, redirect, url_for, session, request
from app.extensions import db
from app.models import User, ProviderAccount, ListenerProfile, Generation
from app.services.moods import MOODS

web_bp = Blueprint("web", __name__)

DEMO_NAME = "Demo User"


def get_or_create_demo_user():
    """Return a per-IP demo user; new IP => new user."""
    current_ip = request.remote_addr
    user_id = session.get("user_id")
    stored_ip = session.get("client_ip")

    if user_id and stored_ip == current_ip:
        u = db.session.get(User, user_id)
        if u:
            return u

    # Either first visit, or IP changed: create a fresh anonymous user
    anon_email = f"anon-{uuid.uuid4()}@genify.local"
    u = User(email=anon_email, display_name=DEMO_NAME)
    db.session.add(u)
    db.session.commit()
    session["user_id"] = u.id
    session["client_ip"] = current_ip
    return u


@web_bp.get("/")
def index():
    user = get_or_create_demo_user()
    accounts = ProviderAccount.query.filter_by(user_id=user.id).all()
    latest_profile = ListenerProfile.query.filter_by(user_id=user.id).order_by(ListenerProfile.created_at.desc()).first()
    recent_generations = Generation.query.filter_by(user_id=user.id).order_by(Generation.created_at.desc()).limit(10).all()
    return render_template(
        "index.html",
        user=user,
        accounts=accounts,
        latest_profile=latest_profile,
        recent_generations=recent_generations,
    )


@web_bp.get("/profile")
def profile_page():
    user = get_or_create_demo_user()
    latest_profile = ListenerProfile.query.filter_by(user_id=user.id).order_by(ListenerProfile.created_at.desc()).first()
    return render_template("profile.html", user=user, profile=latest_profile)


@web_bp.get("/generate")
def generate_page():
    user = get_or_create_demo_user()
    latest_profile = ListenerProfile.query.filter_by(user_id=user.id).order_by(ListenerProfile.created_at.desc()).first()
    return render_template("generate.html", user=user, moods=MOODS, profile=latest_profile)


@web_bp.get("/generation/<int:gen_id>")
def generation_page(gen_id: int):
    user = get_or_create_demo_user()
    gen = Generation.query.filter_by(id=gen_id, user_id=user.id).first_or_404()
    return render_template("generation.html", user=user, gen=gen)


@web_bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.index"))

@web_bp.get("/demo/reset")
def demo_reset():
    user_id = session.get("user_id")
    if user_id:
        # delete child rows for the current session user
        Generation.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        ListenerProfile.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        ProviderAccount.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        db.session.commit()

    session.clear()
    return redirect(url_for("web.index"))

