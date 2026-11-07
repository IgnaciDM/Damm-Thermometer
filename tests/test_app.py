import json
import unittest
from datetime import date
from unittest.mock import patch

from backend import app


def response(day, dimension="bienestar", site="Fábrica", score=4, is_demo=0):
    return {
        "day": day,
        "dimension": dimension,
        "site": site,
        "score": score,
        "is_demo": is_demo,
    }


class AggregateTests(unittest.TestCase):
    def test_hides_groups_below_privacy_threshold(self):
        today = date(2026, 11, 7)
        rows = [response(today.isoformat(), score=5) for _ in range(4)]
        result = app.aggregate_responses(rows, today)

        self.assertIsNone(result["overall"])
        self.assertIsNone(result["responses_7d"])
        self.assertEqual(result["locations"], [])
        self.assertTrue(all(item["score"] is None for item in result["dimensions"]))

    def test_shows_aggregates_when_threshold_is_met(self):
        today = date(2026, 11, 7)
        rows = [response(today.isoformat(), score=4) for _ in range(5)]
        result = app.aggregate_responses(rows, today)

        self.assertEqual(result["overall"], 4.0)
        self.assertEqual(result["responses_7d"], 5)
        self.assertEqual(result["dimensions"][0]["score"], 4.0)
        self.assertEqual(result["locations"][0]["label"], "Fábrica")

    def test_demo_seed_is_repeatable_and_anonymous(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "DATABASE", Path(directory) / "test.sqlite3"):
                dashboard = app.get_dashboard()
                self.assertTrue(dashboard["is_demo"])
                self.assertEqual(dashboard["total_responses"], 84)
                with app.connect() as db:
                    columns = {
                        row["name"] for row in db.execute("PRAGMA table_info(responses)")
                    }
                self.assertEqual(columns, {"id", "day", "dimension", "site", "score", "is_demo"})

    def test_legacy_database_moves_without_overwriting_existing_data(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "pulso.sqlite3"
            target = root / "data" / "pulso.sqlite3"
            legacy.write_bytes(b"existing responses")

            app.migrate_legacy_database(legacy, target)

            self.assertFalse(legacy.exists())
            self.assertEqual(target.read_bytes(), b"existing responses")

    def test_legacy_database_is_not_overwritten(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "pulso.sqlite3"
            target = root / "data" / "pulso.sqlite3"
            legacy.write_bytes(b"legacy responses")
            target.parent.mkdir()
            target.write_bytes(b"current responses")

            app.migrate_legacy_database(legacy, target)

            self.assertEqual(legacy.read_bytes(), b"legacy responses")
            self.assertEqual(target.read_bytes(), b"current responses")


class QuestionTests(unittest.TestCase):
    def test_question_generation_has_explicit_demo_mode_without_key(self):
        with patch.dict(app.AI_CONFIG, {"api_key": ""}):
            result = app.generate_question({"dimension": "bienestar", "site": "Fábrica"})

        self.assertEqual(result["source"], "demo")
        self.assertIn("?", result["question"])

    def test_question_output_is_trimmed_to_one_question(self):
        question = app.clean_question('"¿Te has sentido escuchada esta semana?"\nOtra frase')
        self.assertEqual(question, "¿Te has sentido escuchada esta semana?")

    def test_invalid_dimension_is_rejected(self):
        with self.assertRaises(app.RequestError):
            app.generate_question({"dimension": "unknown", "site": "Fábrica"})

    def test_unhashable_dimension_is_rejected_cleanly(self):
        with self.assertRaises(app.RequestError):
            app.generate_question({"dimension": [], "site": "Fábrica"})

    def test_api_question_uses_compatible_chat_completions_request(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "¿Te has sentido escuchada?"}}]}
                ).encode("utf-8")

        with patch.dict(
            app.AI_CONFIG,
            {
                "api_key": "test-key",
                "base_url": "https://example.test/v1/chat/completions",
                "model": "test-model",
            },
        ), patch("backend.app.urlopen", return_value=FakeResponse()) as urlopen_mock:
            result = app.generate_question(
                {"dimension": "bienestar", "site": "Oficina"}
            )

        api_request = urlopen_mock.call_args.args[0]
        self.assertEqual(result["source"], "api")
        self.assertEqual(result["question"], "¿Te has sentido escuchada?")
        self.assertEqual(json.loads(api_request.data)["model"], "test-model")
        self.assertEqual(api_request.get_header("Authorization"), "Bearer test-key")


class SubmissionTests(unittest.TestCase):
    def test_submission_rejects_personal_or_invalid_payload(self):
        with self.assertRaises(app.RequestError):
            app.validate_response({"dimension": "bienestar", "site": "Fábrica", "score": True})

    def test_submission_requires_known_work_context(self):
        with self.assertRaises(app.RequestError):
            app.validate_response({"dimension": "bienestar", "site": "Unknown", "score": 4})

    def test_api_settings_reject_non_text_endpoint(self):
        with self.assertRaises(app.RequestError):
            app.update_ai_config({"api_key": "test-key", "base_url": [], "model": "test"})


if __name__ == "__main__":
    unittest.main()
