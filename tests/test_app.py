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
                self.assertEqual(dashboard["total_responses"], 600)
                self.assertEqual(len(dashboard["insights"]["representation"]), 5)
                with app.connect() as db:
                    columns = {
                        row["name"] for row in db.execute("PRAGMA table_info(responses)")
                    }
                self.assertTrue(
                    {
                        "id", "day", "dimension", "site", "score", "is_demo",
                        "cohort", "center_type", "channel", "comment", "survey_id",
                        "period", "typeform_response_id",
                    }.issubset(columns)
                )

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


class SurveyTests(unittest.TestCase):
    def survey_payload(self):
        return {
            "objective": "Entender cómo funcionan los turnos.",
            "cohort": "Todos",
            "context": "Se ha cambiado la planificación.",
            "max_duration": "2",
            "exclusions": "Nombres y datos identificativos.",
            "language": "es",
            "channel": "Móvil-QR",
            "frequency_days": "7",
            "population": {
                "Planta": 42,
                "Logística": 20,
                "Comercial": 16,
                "Oficina": 16,
                "Manager": 6,
            },
        }

    def test_conditional_question_rejects_forward_or_non_rating_references(self):
        questions = app.default_survey_questions("Turnos", "Todos", "es")
        questions[-1]["logic"]["show_when"]["question_index"] = 2
        with self.assertRaises(app.RequestError):
            app.validate_questions(questions)

    def test_question_schema_rejects_unhashable_types_and_invalid_flags(self):
        questions = app.default_survey_questions("Turnos", "Todos", "es")
        questions[0]["type"] = []
        with self.assertRaises(app.RequestError):
            app.validate_questions(questions)

    def test_survey_requires_a_required_rating_for_aggregate_scores(self):
        questions = app.default_survey_questions("Turnos", "Todos", "es")
        questions[0]["type"] = "yes_no"
        questions[0]["dimension"] = "bienestar"
        questions[0]["logic"] = None
        questions[-1]["logic"] = None

        with self.assertRaises(app.RequestError):
            app.validate_questions(questions)

        questions = app.default_survey_questions("Turnos", "Todos", "es")
        questions[0]["required"] = "false"
        with self.assertRaises(app.RequestError):
            app.validate_questions(questions)

        questions = app.default_survey_questions("Turnos", "Todos", "es")
        questions[0]["type"] = "open_text"
        with self.assertRaises(app.RequestError):
            app.validate_questions(questions)

    def test_typeform_payload_uses_form_level_numeric_logic_jump(self):
        questions = app.validate_questions(
            app.default_survey_questions("Turnos", "Todos", "es")
        )
        survey = {
            "title": "Consulta de prueba",
            "intro": "Respuesta anónima.",
            "language": "es",
            "cohort": "Todos",
            "questions": questions,
        }

        payload = app._typeform_form_payload(survey)

        self.assertIn("logic", payload)
        logic = payload["logic"][0]
        self.assertEqual(logic["type"], "field")
        self.assertEqual(logic["ref"], questions[2]["id"])
        self.assertEqual(logic["actions"][0]["condition"]["op"], "greater_equal_than")
        self.assertEqual(
            logic["actions"][0]["condition"]["vars"][0]["value"], questions[0]["id"]
        )
        self.assertEqual(logic["actions"][0]["details"]["to"], {"type": "thankyou", "value": "default"})
        self.assertFalse(any("logic" in field for field in payload["fields"]))

    def test_settings_can_configure_only_typeform(self):
        with patch.dict(app.AI_CONFIG, {"api_key": ""}), patch.dict(
            app.TYPEFORM_CONFIG, {"token": ""}
        ):
            result = app.update_ai_config({"typeform_token": "test-token"})

        self.assertTrue(result["typeform_configured"])
        self.assertFalse(result["ai_configured"])

    def test_approved_survey_publishes_to_typeform(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DATABASE", Path(directory) / "test.sqlite3"
        ), patch.dict(app.AI_CONFIG, {"api_key": ""}), patch.dict(
            app.TYPEFORM_CONFIG, {"token": "test-token"}
        ):
            draft = app.create_survey_draft(self.survey_payload())
            app.approve_survey(draft["id"])
            with patch(
                "backend.app._typeform_request",
                return_value={
                    "id": "typeform-id",
                    "_links": {"display": "https://form.typeform.com/to/typeform-id"},
                },
            ) as typeform_request:
                published = app.publish_survey(draft["id"])

        request_payload = typeform_request.call_args.args[2]
        self.assertEqual(published["status"], "published")
        self.assertTrue(published["typeform_connected"])
        self.assertEqual(published["publish_url"], "https://form.typeform.com/to/typeform-id")
        self.assertIn("logic", request_payload)
        self.assertEqual(request_payload["fields"][0]["ref"], "cohort")

    def test_survey_requires_human_approval_before_local_publication(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DATABASE", Path(directory) / "test.sqlite3"
        ), patch.dict(app.AI_CONFIG, {"api_key": ""}), patch.dict(
            app.TYPEFORM_CONFIG, {"token": ""}
        ):
            draft = app.create_survey_draft(self.survey_payload())
            with self.assertRaises(app.RequestError):
                app.publish_survey(draft["id"], "http://localhost:8000")

            app.approve_survey(draft["id"])
            published = app.publish_survey(draft["id"], "http://localhost:8000")

        self.assertEqual(published["status"], "published")
        self.assertEqual(published["publish_url"], f"http://localhost:8000/respond/{draft['id']}")

    def test_survey_rejects_invalid_population_and_frequency(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DATABASE", Path(directory) / "test.sqlite3"
        ), patch.dict(app.AI_CONFIG, {"api_key": ""}):
            payload = self.survey_payload()
            payload["population"]["Planta"] = float("nan")
            with self.assertRaises(app.RequestError):
                app.create_survey_draft(payload)

    def test_frequency_limit_applies_between_all_and_specific_cohort_surveys(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DATABASE", Path(directory) / "test.sqlite3"
        ), patch.dict(app.AI_CONFIG, {"api_key": ""}), patch.dict(
            app.TYPEFORM_CONFIG, {"token": ""}
        ):
            all_cohorts = app.create_survey_draft(self.survey_payload())
            app.approve_survey(all_cohorts["id"])
            app.publish_survey(all_cohorts["id"], "http://localhost:8000")

            specific_payload = self.survey_payload()
            specific_payload["cohort"] = "Planta"
            specific = app.create_survey_draft(specific_payload)
            app.approve_survey(specific["id"])
            with self.assertRaises(app.RequestError):
                app.publish_survey(specific["id"], "http://localhost:8000")

            payload = self.survey_payload()
            payload["frequency_days"] = "1.5"
            with self.assertRaises(app.RequestError):
                app.create_survey_draft(payload)

    def test_typeform_import_is_anonymous_and_idempotent(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DATABASE", Path(directory) / "test.sqlite3"
        ), patch.dict(app.AI_CONFIG, {"api_key": ""}), patch.dict(
            app.TYPEFORM_CONFIG, {"token": "test-token"}
        ):
            survey = app.create_survey_draft(self.survey_payload())
            questions = survey["questions"]
            with app.connect() as db:
                db.execute(
                    """UPDATE surveys SET status = 'published', typeform_id = 'form-id',
                       publish_url = 'https://form.typeform.com/to/form-id' WHERE id = ?""",
                    (survey["id"],),
                )
            typeform_response = {
                "token": "response-token",
                "answers": [
                    {"field": {"ref": questions[0]["id"]}, "number": 4},
                    {"field": {"ref": "cohort"}, "choice": {"label": "Planta"}},
                    {"field": {"ref": questions[-1]["id"]}, "text": "Me llamo Ana, necesitamos más descanso."},
                ],
            }
            with patch(
                "backend.app._typeform_request",
                return_value={"items": [typeform_response], "_links": {}},
            ):
                first = app.import_typeform_responses(survey["id"])
                second = app.import_typeform_responses(survey["id"])

            with app.connect() as db:
                row = db.execute(
                    "SELECT cohort, comment, typeform_response_id FROM responses"
                ).fetchone()

        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(row["cohort"], "Planta")
        self.assertNotIn("Ana", row["comment"])
        self.assertEqual(row["typeform_response_id"], "response-token")


if __name__ == "__main__":
    unittest.main()
