from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB, UUID
from .extensions import db

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.BigInteger, primary_key=True)
    email = db.Column(db.Text, unique=True)
    display_name = db.Column(db.Text)
    auth_provider = db.Column(db.Text)        # "google" | "apple" | NULL (anonymous)
    auth_provider_id = db.Column(db.Text, unique=True)  # Firebase UID
    photo_url = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)

class ProviderAccount(db.Model):
    __tablename__ = "provider_accounts"
    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = db.Column(db.Text, nullable=False)  # spotify / apple_music / youtube
    provider_user_id = db.Column(db.Text, nullable=False)
    scopes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("provider", "provider_user_id", name="uq_provider_user"),
        db.UniqueConstraint("user_id", "provider", name="uq_user_provider"),
    )

class OAuthToken(db.Model):
    __tablename__ = "oauth_tokens"
    id = db.Column(db.BigInteger, primary_key=True)
    provider_account_id = db.Column(db.BigInteger, db.ForeignKey("provider_accounts.id", ondelete="CASCADE"), nullable=False, unique=True)
    access_token_enc = db.Column(db.Text, nullable=False)
    refresh_token_enc = db.Column(db.Text)
    expires_at = db.Column(db.DateTime(timezone=True))
    token_type = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)

class Track(db.Model):
    __tablename__ = "tracks"
    id = db.Column(db.BigInteger, primary_key=True)
    canonical_title = db.Column(db.Text, nullable=False)
    canonical_artist = db.Column(db.Text, nullable=False)
    album = db.Column(db.Text)
    duration_ms = db.Column(db.Integer)
    isrc = db.Column(db.Text, unique=True)
    release_year = db.Column(db.Integer)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("canonical_artist", "canonical_title", "duration_ms", name="uq_track_fingerprint"),
    )

class TrackCandidate(db.Model):
    __tablename__ = "track_candidates"
    id = db.Column(db.BigInteger, primary_key=True)
    provider_account_id = db.Column(db.BigInteger, db.ForeignKey("provider_accounts.id", ondelete="CASCADE"), nullable=False)
    provider_track_id = db.Column(db.Text, nullable=False)
    title = db.Column(db.Text)
    artists = db.Column(db.Text)
    album = db.Column(db.Text)
    duration_ms = db.Column(db.Integer)
    isrc = db.Column(db.Text)
    popularity = db.Column(db.Integer)
    source = db.Column(db.Text, nullable=False)  # top/recent/playlist
    source_ref = db.Column(db.Text)
    observed_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    track_id = db.Column(db.BigInteger, db.ForeignKey("tracks.id"))
    __table_args__ = (
        db.UniqueConstraint("provider_account_id", "provider_track_id", name="uq_candidate_provider_track"),
    )

class ListenerProfile(db.Model):
    __tablename__ = "listener_profiles"
    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)

    built_from_track_count = db.Column(db.Integer, nullable=False)
    profile_json = db.Column(JSONB, nullable=False)
    explain_json = db.Column(JSONB, nullable=False)

    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "version", name="uq_profile_version"),
    )

class Generation(db.Model):
    __tablename__ = "generations"
    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    listener_profile_id = db.Column(db.BigInteger, db.ForeignKey("listener_profiles.id"))

    mood = db.Column(db.Text, nullable=False)  # chill/happy/energetic/sad/focus/romantic/aggressive
    mood_intensity = db.Column(db.Float)       # 0.0 (subtle) to 1.0 (extreme)
    activity = db.Column(db.Text)              # studying/working_out/falling_in_love/driving/meditating/partying/winding_down
    song_reference = db.Column(db.Text)        # user-provided song reference for style inspiration
    genre = db.Column(db.Text)                 # optional genre override
    bpm = db.Column(db.Integer)                # optional target BPM

    openai_prompt = db.Column(JSONB, nullable=False)
    suno_request = db.Column(JSONB, nullable=False)

    suno_job_id = db.Column(db.Text)
    status = db.Column(db.Text, nullable=False, default="queued")  # queued/running/succeeded/failed
    result_json = db.Column(JSONB)
    error = db.Column(db.Text)

    is_favourite = db.Column(db.Boolean, default=False, nullable=False, server_default="false")
    like_status = db.Column(db.Text, default=None)  # null | "liked" | "disliked"
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)
