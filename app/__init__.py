import logging
import os
import sys

from flask import Flask
from .config import Config
from .extensions import db


def _setup_logging():
    """Configure drvibey loggers to print to stdout at INFO level."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    for name in ("drvibey.chat", "drvibey.api", "drvibey.ocr", "drvibey.auth"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False


def _init_firebase(app):
    """Initialize Firebase Admin SDK for token verification."""
    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            # Try service account JSON file first, then fall back to default credentials
            sa_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "firebase-service-account.json")
            if os.path.exists(sa_path):
                cred = credentials.Certificate(sa_path)
                firebase_admin.initialize_app(cred)
                app.logger.info("Firebase Admin initialized with service account: %s", sa_path)
            elif app.config.get("FIREBASE_PROJECT_ID"):
                # Use Application Default Credentials (works in GCP environments)
                firebase_admin.initialize_app(options={
                    "projectId": app.config["FIREBASE_PROJECT_ID"],
                })
                app.logger.info("Firebase Admin initialized with project ID: %s",
                                app.config["FIREBASE_PROJECT_ID"])
            else:
                app.logger.warning("Firebase not configured -- auth endpoints will fail. "
                                   "Set FIREBASE_PROJECT_ID or provide firebase-service-account.json")
    except ImportError:
        app.logger.warning("firebase-admin not installed -- auth endpoints will be unavailable")
    except Exception as e:
        app.logger.warning("Firebase Admin init failed: %s", e)


def create_app():
    _setup_logging()

    app = Flask(__name__)
    app.config.from_object(Config())

    db.init_app(app)
    _init_firebase(app)

    # Import blueprints only after app/extensions are ready to avoid circular imports
    from .routes.web import web_bp
    from .routes.auth import auth_bp
    from .routes.api import api_bp
    from .routes.public import public_bp, demo_bp
    from .routes.firebase_auth import firebase_auth_bp

    app.register_blueprint(web_bp, url_prefix="/dev")
    app.register_blueprint(auth_bp)

    app.register_blueprint(public_bp)
    app.register_blueprint(demo_bp)

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(firebase_auth_bp, url_prefix="/api/auth")

    return app
