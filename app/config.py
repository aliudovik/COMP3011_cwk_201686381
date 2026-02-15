import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret")

    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:7777")

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    TOKEN_ENC_KEY = os.getenv("TOKEN_ENC_KEY", "")

    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
    SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", f"{APP_BASE_URL}/callback/spotify")

    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

    CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
    LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b")

    SUNO_API_KEY = os.getenv("SUNO_API_KEY", "")
    SUNO_BASE_URL = os.getenv("SUNO_BASE_URL", "https://api.sunoapi.org")
    SUNO_MODEL = os.getenv("SUNO_MODEL", "V5")

    # Firebase Authentication
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
    FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "")
    FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", "")
    FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET", "")
    FIREBASE_MESSAGING_SENDER_ID = os.getenv("FIREBASE_MESSAGING_SENDER_ID", "")
    FIREBASE_APP_ID = os.getenv("FIREBASE_APP_ID", "")
