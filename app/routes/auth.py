from flask import Blueprint, current_app, redirect, request, session, url_for, abort
from app.extensions import db
from app.models import User, ProviderAccount, OAuthToken
from app.crypto import TokenCipher
from app.services.providers.spotify import SpotifyProvider
from app.services.providers.youtube import YouTubeProvider

auth_bp = Blueprint("auth", __name__)


def require_user() -> User:
    user_id = session.get("user_id")
    if not user_id:
        abort(401)
    u = db.session.get(User, user_id)
    if not u:
        abort(401)
    return u


def _safe_next_url(u: str | None) -> str | None:
    """
    Prevent open redirects:
    allow only local relative paths like "/public?connected=1".
    Disallow "//evil.com" and "https://evil.com".
    """
    if not u:
        return None
    if u.startswith("/") and not u.startswith("//"):
        return u
    return None


@auth_bp.get("/connect/<provider>")
def connect(provider: str):
    user = require_user()

    # NEW: store a safe "next" redirect target in session (optional)
    next_url = _safe_next_url(request.args.get("next"))
    if next_url:
        session["oauth_next"] = next_url

    if provider == "spotify":
        sp = SpotifyProvider(
            client_id=current_app.config["SPOTIFY_CLIENT_ID"],
            redirect_uri=current_app.config["SPOTIFY_REDIRECT_URI"],
        )
        auth_url, state, verifier = sp.build_authorize_url()
        session["oauth_state"] = state
        session["pkce_verifier"] = verifier
        return redirect(auth_url)

    if provider == "youtube":
        yp = YouTubeProvider(
            client_id=current_app.config["GOOGLE_CLIENT_ID"],
            client_secret=current_app.config["GOOGLE_CLIENT_SECRET"],
            redirect_uri=current_app.config["GOOGLE_REDIRECT_URI"],
        )
        auth_url, state, verifier = yp.build_authorize_url()
        session["oauth_state"] = state
        session["pkce_verifier"] = verifier
        return redirect(auth_url)

    if provider not in ("spotify", "youtube"):
        abort(501)

    abort(404)


@auth_bp.get("/callback/<provider>")
def callback(provider: str):
    user = require_user()

    if provider not in ("spotify", "youtube"):
        abort(501)

    state = request.args.get("state")
    code = request.args.get("code")
    if not state or not code:
        abort(400, "Missing state or code")
    if state != session.get("oauth_state"):
        abort(400, "State mismatch")

    verifier = session.get("pkce_verifier")
    if not verifier:
        abort(400, "Missing PKCE verifier")

    # Initialize the correct provider
    provider_service = None
    if provider == "spotify":
        provider_service = SpotifyProvider(
            client_id=current_app.config["SPOTIFY_CLIENT_ID"],
            redirect_uri=current_app.config["SPOTIFY_REDIRECT_URI"],
        )
    elif provider == "youtube":
        provider_service = YouTubeProvider(
            client_id=current_app.config["GOOGLE_CLIENT_ID"],
            client_secret=current_app.config["GOOGLE_CLIENT_SECRET"],
            redirect_uri=current_app.config["GOOGLE_REDIRECT_URI"],
        )

    # Exchange code for token
    token = provider_service.exchange_code_for_token(code=code, code_verifier=verifier)
    me = provider_service.get_me(access_token=token["access_token"])

    provider_user_id = me.get("id")
    display_name = me.get("display_name") or me.get("id")

    if user.display_name == "Demo User" and display_name:
        user.display_name = display_name

    # Save Account
    acct = ProviderAccount.query.filter_by(user_id=user.id, provider=provider).first()
    if not acct:
        acct = ProviderAccount(
            user_id=user.id,
            provider=provider,
            provider_user_id=provider_user_id,
            scopes=token.get("scope", ""),
        )
        db.session.add(acct)
        db.session.flush()
    else:
        acct.provider_user_id = provider_user_id
        acct.scopes = token.get("scope", acct.scopes)

    cipher = TokenCipher(current_app.config["TOKEN_ENC_KEY"])
    tok = OAuthToken.query.filter_by(provider_account_id=acct.id).first()
    if not tok:
        tok = OAuthToken(
            provider_account_id=acct.id,
            access_token_enc=cipher.encrypt(token["access_token"]),
            refresh_token_enc=cipher.encrypt(token.get("refresh_token", "")),
            token_type=token.get("token_type", "Bearer"),
        )
        db.session.add(tok)
    else:
        tok.access_token_enc = cipher.encrypt(token["access_token"])
        if token.get("refresh_token"):
            tok.refresh_token_enc = cipher.encrypt(token.get("refresh_token"))
        tok.token_type = token.get("token_type", tok.token_type)

    db.session.commit()

    session.pop("oauth_state", None)
    session.pop("pkce_verifier", None)

    # NEW: redirect to "next" if present (and safe), else fallback
    nxt = _safe_next_url(session.pop("oauth_next", None))
    if nxt:
        return redirect(nxt)

    return redirect(url_for("web.index"))
