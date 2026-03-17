import os
import tempfile
import unittest
from unittest.mock import patch
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.gettempdir(), 'drvibey_test.db')}"
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User, ListenerProfile, Generation  # noqa: E402


_PK_COUNTERS = {
    User: 0,
    ListenerProfile: 0,
    Generation: 0,
}


def _set_pk_if_missing(model_cls, target):
    if getattr(target, "id", None) is None:
        _PK_COUNTERS[model_cls] += 1
        target.id = _PK_COUNTERS[model_cls]


@event.listens_for(User, "before_insert")
def _user_before_insert(_mapper, _connection, target):
    _set_pk_if_missing(User, target)


@event.listens_for(ListenerProfile, "before_insert")
def _listener_profile_before_insert(_mapper, _connection, target):
    _set_pk_if_missing(ListenerProfile, target)


@event.listens_for(Generation, "before_insert")
def _generation_before_insert(_mapper, _connection, target):
    _set_pk_if_missing(Generation, target)


class DummyJob:
    id = "test-job-1"


class GenerationCrudApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)
        with cls.app.app_context():
            db.drop_all()
            db.create_all()

    def setUp(self):
        self.client = self.app.test_client()
        with self.app.app_context():
            Generation.query.delete()
            ListenerProfile.query.delete()
            User.query.delete()
            db.session.commit()

            user = User(email="test@example.com", display_name="Test User", auth_provider_id="test-uid-1")
            db.session.add(user)
            db.session.commit()
            self.user_id = int(user.id)

            lp = ListenerProfile(
                user_id=self.user_id,
                version=1,
                built_from_track_count=5,
                profile_json={"listener_type": "FVPD"},
                explain_json={"diagnosis": "test"},
            )
            db.session.add(lp)
            db.session.commit()
            self.listener_profile_id = int(lp.id)

        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id

    def test_generation_crud_lifecycle(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "chill", "title": "Focus Flow", "genre": "lofi", "bpm": 90},
            )
        self.assertEqual(create_res.status_code, 200)
        create_json = create_res.get_json()
        self.assertTrue(create_json["ok"])
        generation_id = create_json["generation_id"]

        read_res = self.client.get(f"/api/generation/{generation_id}")
        self.assertEqual(read_res.status_code, 200)
        read_json = read_res.get_json()
        self.assertIn("request_id", read_json)
        self.assertIn("server_time", read_json)

        patch_res = self.client.patch(
            f"/api/generation/{generation_id}",
            json={"genre": "ambient", "bpm": 96, "mood_intensity": 0.7},
        )
        self.assertEqual(patch_res.status_code, 200)
        patch_json = patch_res.get_json()
        self.assertEqual(patch_json["generation"]["genre"], "ambient")
        self.assertEqual(patch_json["generation"]["bpm"], 96)

        delete_res = self.client.delete(f"/api/generation/{generation_id}")
        self.assertEqual(delete_res.status_code, 204)

        read_deleted_res = self.client.get(f"/api/generation/{generation_id}")
        self.assertEqual(read_deleted_res.status_code, 404)
        read_deleted_json = read_deleted_res.get_json()
        self.assertIn("request_id", read_deleted_json)
        self.assertIn("server_time", read_deleted_json)

    def test_update_generation_requires_auth(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post("/api/generate", json={"user_id": self.user_id, "mood": "energetic"})
        generation_id = create_res.get_json()["generation_id"]

        with self.client.session_transaction() as sess:
            sess.pop("user_id", None)

        patch_res = self.client.patch(f"/api/generation/{generation_id}", json={"genre": "house"})
        self.assertEqual(patch_res.status_code, 401)

    def test_update_generation_validation_error(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post("/api/generate", json={"user_id": self.user_id, "mood": "focus"})
        generation_id = create_res.get_json()["generation_id"]

        patch_res = self.client.patch(
            f"/api/generation/{generation_id}",
            json={"mood_intensity": 9},
        )
        self.assertEqual(patch_res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
