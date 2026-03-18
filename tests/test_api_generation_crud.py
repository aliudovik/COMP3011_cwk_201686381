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
        self.assertEqual(set(patch_json["updated_fields"]), {"genre", "bpm", "mood_intensity"})

        delete_res = self.client.delete(f"/api/generation/{generation_id}")
        self.assertEqual(delete_res.status_code, 200)
        delete_json = delete_res.get_json()
        self.assertTrue(delete_json["ok"])
        self.assertFalse(delete_json["already_deleted"])
        self.assertIsNotNone(delete_json["deleted_at"])

        read_deleted_res = self.client.get(f"/api/generation/{generation_id}")
        self.assertEqual(read_deleted_res.status_code, 410)
        read_deleted_json = read_deleted_res.get_json()
        self.assertIn("request_id", read_deleted_json)
        self.assertIn("server_time", read_deleted_json)
        self.assertEqual(read_deleted_json["error"]["code"], "generation_deleted")

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

    def test_update_generation_rejects_unknown_fields(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post("/api/generate", json={"user_id": self.user_id, "mood": "focus"})
        generation_id = create_res.get_json()["generation_id"]

        patch_res = self.client.patch(
            f"/api/generation/{generation_id}",
            json={"unexpected_key": "value"},
        )
        self.assertEqual(patch_res.status_code, 400)
        patch_json = patch_res.get_json()
        self.assertEqual(patch_json["error"]["code"], "validation_error")

    def test_update_generation_rejects_no_changes(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post("/api/generate", json={"user_id": self.user_id, "mood": "focus"})
        generation_id = create_res.get_json()["generation_id"]

        patch_res = self.client.patch(
            f"/api/generation/{generation_id}",
            json={"mood": "focus"},
        )
        self.assertEqual(patch_res.status_code, 400)
        patch_json = patch_res.get_json()
        self.assertEqual(patch_json["error"]["code"], "validation_error")

    def test_generations_list_pagination(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            self.client.post("/api/generate", json={"user_id": self.user_id, "mood": "focus"})
            self.client.post("/api/generate", json={"user_id": self.user_id, "mood": "chill"})
            self.client.post("/api/generate", json={"user_id": self.user_id, "mood": "happy"})

        page_1 = self.client.get("/api/generations?limit=2&offset=0")
        self.assertEqual(page_1.status_code, 200)
        page_1_json = page_1.get_json()
        self.assertTrue(page_1_json["ok"])
        self.assertEqual(page_1_json["pagination"]["limit"], 2)
        self.assertEqual(page_1_json["pagination"]["offset"], 0)
        self.assertEqual(page_1_json["pagination"]["returned"], 2)
        self.assertEqual(len(page_1_json["generations"]), 2)

        page_2 = self.client.get("/api/generations?limit=2&offset=2")
        self.assertEqual(page_2.status_code, 200)
        page_2_json = page_2.get_json()
        self.assertEqual(page_2_json["pagination"]["limit"], 2)
        self.assertEqual(page_2_json["pagination"]["offset"], 2)
        self.assertEqual(page_2_json["pagination"]["returned"], 1)
        self.assertEqual(len(page_2_json["generations"]), 1)

    def test_generations_list_pagination_validation(self):
        bad_limit = self.client.get("/api/generations?limit=0&offset=0")
        self.assertEqual(bad_limit.status_code, 400)
        bad_limit_json = bad_limit.get_json()
        self.assertEqual(bad_limit_json["error"]["code"], "validation_error")

        bad_offset = self.client.get("/api/generations?limit=10&offset=-1")
        self.assertEqual(bad_offset.status_code, 400)
        bad_offset_json = bad_offset.get_json()
        self.assertEqual(bad_offset_json["error"]["code"], "validation_error")

    def test_generations_list_filtering(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            first = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "focus", "activity": "studying"},
            )
            second = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "chill", "activity": "driving"},
            )

        first_id = first.get_json()["generation_id"]
        second_id = second.get_json()["generation_id"]

        with self.app.app_context():
            second_gen = Generation.query.get(second_id)
            second_gen.status = "failed"
            db.session.commit()

        mood_filtered = self.client.get("/api/generations?mood=focus")
        self.assertEqual(mood_filtered.status_code, 200)
        mood_filtered_json = mood_filtered.get_json()
        self.assertEqual(len(mood_filtered_json["generations"]), 1)
        self.assertEqual(mood_filtered_json["generations"][0]["id"], first_id)
        self.assertEqual(mood_filtered_json["filters"]["mood"], "focus")

        combined_filtered = self.client.get("/api/generations?status=failed&activity=driving")
        self.assertEqual(combined_filtered.status_code, 200)
        combined_filtered_json = combined_filtered.get_json()
        self.assertEqual(len(combined_filtered_json["generations"]), 1)
        self.assertEqual(combined_filtered_json["generations"][0]["id"], second_id)
        self.assertEqual(combined_filtered_json["filters"]["status"], "failed")
        self.assertEqual(combined_filtered_json["filters"]["activity"], "driving")

    def test_generations_list_filtering_validation(self):
        bad_status = self.client.get("/api/generations?status=done")
        self.assertEqual(bad_status.status_code, 400)
        bad_status_json = bad_status.get_json()
        self.assertEqual(bad_status_json["error"]["code"], "validation_error")

    def test_soft_delete_visibility_and_guards(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "focus", "activity": "studying"},
            )
        generation_id = create_res.get_json()["generation_id"]

        first_delete = self.client.delete(f"/api/generation/{generation_id}")
        self.assertEqual(first_delete.status_code, 200)
        first_delete_json = first_delete.get_json()
        self.assertFalse(first_delete_json["already_deleted"])

        second_delete = self.client.delete(f"/api/generation/{generation_id}")
        self.assertEqual(second_delete.status_code, 200)
        second_delete_json = second_delete.get_json()
        self.assertTrue(second_delete_json["already_deleted"])

        normal_list = self.client.get("/api/generations")
        self.assertEqual(normal_list.status_code, 200)
        normal_list_json = normal_list.get_json()
        self.assertEqual(len(normal_list_json["generations"]), 0)

        include_deleted_list = self.client.get("/api/generations?include_deleted=true")
        self.assertEqual(include_deleted_list.status_code, 200)
        include_deleted_json = include_deleted_list.get_json()
        self.assertEqual(len(include_deleted_json["generations"]), 1)
        self.assertEqual(include_deleted_json["generations"][0]["status"], "deleted")
        self.assertIsNotNone(include_deleted_json["generations"][0]["deleted_at"])

        only_deleted_list = self.client.get("/api/generations?status=deleted")
        self.assertEqual(only_deleted_list.status_code, 200)
        only_deleted_json = only_deleted_list.get_json()
        self.assertEqual(len(only_deleted_json["generations"]), 1)

        patch_deleted = self.client.patch(
            f"/api/generation/{generation_id}",
            json={"genre": "ambient"},
        )
        self.assertEqual(patch_deleted.status_code, 410)
        patch_deleted_json = patch_deleted.get_json()
        self.assertEqual(patch_deleted_json["error"]["code"], "generation_deleted")

        fav_deleted = self.client.patch(
            f"/api/generation/{generation_id}/favourite",
            json={"is_favourite": True},
        )
        self.assertEqual(fav_deleted.status_code, 410)
        fav_deleted_json = fav_deleted.get_json()
        self.assertEqual(fav_deleted_json["error"]["code"], "generation_deleted")

        like_deleted = self.client.patch(
            f"/api/generation/{generation_id}/like",
            json={"like_status": "liked"},
        )
        self.assertEqual(like_deleted.status_code, 410)
        like_deleted_json = like_deleted.get_json()
        self.assertEqual(like_deleted_json["error"]["code"], "generation_deleted")

    def test_generation_analytics_summary_expanded_fields(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            first = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "focus", "activity": "studying"},
            )
            second = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "chill", "activity": "driving"},
            )
            third = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "happy", "activity": "partying"},
            )

        first_id = first.get_json()["generation_id"]
        second_id = second.get_json()["generation_id"]
        third_id = third.get_json()["generation_id"]

        with self.app.app_context():
            first_gen = Generation.query.get(first_id)
            second_gen = Generation.query.get(second_id)
            third_gen = Generation.query.get(third_id)

            first_gen.status = "succeeded"
            first_gen.is_favourite = True
            first_gen.like_status = "liked"

            second_gen.status = "failed"
            second_gen.like_status = "disliked"

            third_gen.status = "queued"

            db.session.commit()

        delete_res = self.client.delete(f"/api/generation/{third_id}")
        self.assertEqual(delete_res.status_code, 200)

        summary_res = self.client.get("/api/analytics/generations/summary?days=30")
        self.assertEqual(summary_res.status_code, 200)
        summary_json = summary_res.get_json()
        self.assertTrue(summary_json["ok"])

        self.assertEqual(summary_json["window_days"], 30)
        self.assertEqual(summary_json["total_generations"], 3)
        self.assertEqual(summary_json["active_generations"], 2)
        self.assertEqual(summary_json["deleted_count"], 1)
        self.assertEqual(summary_json["favourite_count"], 1)
        self.assertEqual(summary_json["favourite_rate"], 0.3333)
        self.assertEqual(summary_json["succeeded_count"], 1)
        self.assertEqual(summary_json["failed_count"], 1)
        self.assertEqual(summary_json["success_rate"], 0.5)
        self.assertEqual(summary_json["like_breakdown"]["liked"], 1)
        self.assertEqual(summary_json["like_breakdown"]["disliked"], 1)
        self.assertEqual(summary_json["like_breakdown"]["neutral"], 0)

        self.assertIsInstance(summary_json["daily_counts"], list)
        self.assertGreaterEqual(len(summary_json["daily_counts"]), 1)
        self.assertIsNotNone(summary_json["recent_generation_at"])

    def test_profile_share_requires_auth(self):
        with self.client.session_transaction() as sess:
            sess.pop("user_id", None)

        res = self.client.post("/api/profile/share", json={"listener_profile_id": self.listener_profile_id})
        self.assertEqual(res.status_code, 401)
        body = res.get_json()
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_profile_share_token_rotation_flow(self):
        first = self.client.post("/api/profile/share", json={"listener_profile_id": self.listener_profile_id})
        self.assertEqual(first.status_code, 200)
        first_json = first.get_json()
        self.assertTrue(first_json["ok"])
        self.assertTrue(first_json["token_rotated"])
        self.assertIn("/vibe/", first_json["share_url"])

        second = self.client.post("/api/profile/share", json={"listener_profile_id": self.listener_profile_id})
        self.assertEqual(second.status_code, 200)
        second_json = second.get_json()
        self.assertFalse(second_json["token_rotated"])
        self.assertEqual(second_json["share_url"], first_json["share_url"])

        rotated = self.client.post(
            "/api/profile/share",
            json={"listener_profile_id": self.listener_profile_id, "rotate_token": True},
        )
        self.assertEqual(rotated.status_code, 200)
        rotated_json = rotated.get_json()
        self.assertTrue(rotated_json["token_rotated"])
        self.assertNotEqual(rotated_json["share_url"], first_json["share_url"])

    def test_profile_share_listener_profile_validation(self):
        bad_id = self.client.post("/api/profile/share", json={"listener_profile_id": "abc"})
        self.assertEqual(bad_id.status_code, 400)
        bad_id_json = bad_id.get_json()
        self.assertEqual(bad_id_json["error"]["code"], "validation_error")

        missing = self.client.post("/api/profile/share", json={"listener_profile_id": 999999})
        self.assertEqual(missing.status_code, 404)
        missing_json = missing.get_json()
        self.assertEqual(missing_json["error"]["code"], "not_found")

    def test_generation_status_transition_success(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "focus", "activity": "studying"},
            )
        generation_id = create_res.get_json()["generation_id"]

        to_running = self.client.patch(
            f"/api/generation/{generation_id}/status",
            json={"status": "running"},
        )
        self.assertEqual(to_running.status_code, 200)
        to_running_json = to_running.get_json()
        self.assertEqual(to_running_json["status_transition"]["from"], "queued")
        self.assertEqual(to_running_json["status_transition"]["to"], "running")

        to_succeeded = self.client.patch(
            f"/api/generation/{generation_id}/status",
            json={"status": "succeeded"},
        )
        self.assertEqual(to_succeeded.status_code, 200)
        to_succeeded_json = to_succeeded.get_json()
        self.assertEqual(to_succeeded_json["status_transition"]["from"], "running")
        self.assertEqual(to_succeeded_json["status_transition"]["to"], "succeeded")

    def test_generation_status_transition_validation(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "focus", "activity": "studying"},
            )
        generation_id = create_res.get_json()["generation_id"]

        invalid_status = self.client.patch(
            f"/api/generation/{generation_id}/status",
            json={"status": "done"},
        )
        self.assertEqual(invalid_status.status_code, 400)
        invalid_status_json = invalid_status.get_json()
        self.assertEqual(invalid_status_json["error"]["code"], "validation_error")

        invalid_transition = self.client.patch(
            f"/api/generation/{generation_id}/status",
            json={"status": "succeeded"},
        )
        self.assertEqual(invalid_transition.status_code, 409)
        invalid_transition_json = invalid_transition.get_json()
        self.assertEqual(invalid_transition_json["error"]["code"], "transition_not_allowed")

    def test_generation_status_transition_to_deleted_soft_deletes(self):
        with patch("app.routes.api.enqueue", return_value=DummyJob()):
            create_res = self.client.post(
                "/api/generate",
                json={"user_id": self.user_id, "mood": "focus", "activity": "studying"},
            )
        generation_id = create_res.get_json()["generation_id"]

        to_deleted = self.client.patch(
            f"/api/generation/{generation_id}/status",
            json={"status": "deleted"},
        )
        self.assertEqual(to_deleted.status_code, 200)
        to_deleted_json = to_deleted.get_json()
        self.assertEqual(to_deleted_json["generation"]["status"], "deleted")
        self.assertIsNotNone(to_deleted_json["generation"]["deleted_at"])

        read_deleted = self.client.get(f"/api/generation/{generation_id}")
        self.assertEqual(read_deleted.status_code, 410)
        read_deleted_json = read_deleted.get_json()
        self.assertEqual(read_deleted_json["error"]["code"], "generation_deleted")


if __name__ == "__main__":
    unittest.main()
