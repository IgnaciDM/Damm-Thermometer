"""
Módulo de Gestión de Consultas, Historial y Acciones de RRHH (AdminPanel).
Gestiona el ciclo de vida de encuestas, validación humana, Typeform,
generación de QR vectorial y acciones recomendadas.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .common import (
    AI_CONFIG,
    AI_CONFIG_LOCK,
    COHORTS,
    COHORT_LABELS,
    POPULATION_SHARES,
    TYPEFORM_CONFIG,
    TYPEFORM_LOCK,
    RequestError,
    _now_iso,
    _survey_dict,
    connect,
    sanitize_comment,
    validate_generator_history,
    validate_questions,
)
from .dashboard import get_dashboard


def get_survey(survey_id: str, public_only: bool = False) -> dict:
    """Obtiene los datos de una encuesta por su identificador."""
    with connect() as db:
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    if row is None or (public_only and row["status"] != "published"):
        raise RequestError("No se ha encontrado esta consulta publicada.", 404)
    return _survey_dict(row, include_private=not public_only)


def list_surveys() -> list[dict]:
    """Lista las últimas consultas creadas con metadatos completos."""
    with connect() as db:
        rows = db.execute("SELECT * FROM surveys ORDER BY created_at DESC LIMIT 50").fetchall()
    return [_survey_dict(row, include_private=True) for row in rows]


def update_survey(survey_id: str, payload: object) -> dict:
    """Actualiza el título, introducción y preguntas de una encuesta en borrador."""
    if not isinstance(payload, dict):
        raise RequestError("El contenido de la consulta no es válido.")
    with connect() as db:
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if row is None:
            raise RequestError("No se ha encontrado esta consulta.", 404)
        if row["status"] != "draft":
            raise RequestError("Solo se pueden editar consultas en borrador.")
        title = payload.get("title", row["title"])
        intro = payload.get("intro", row["intro"])
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise RequestError("El título debe tener entre 1 y 160 caracteres.")
        if not isinstance(intro, str) or not intro.strip() or len(intro) > 500:
            raise RequestError("La introducción debe tener entre 1 y 500 caracteres.")
        cohort = payload.get("cohort", row["cohort"])
        valid_cohorts = set(COHORTS) | {"Todos", "Fábrica", "Oficinas"}
        cohort_parts = [
            value.strip() for value in cohort.split(",") if value.strip()
        ] if isinstance(cohort, str) else []
        if not cohort_parts or not all(value in valid_cohorts for value in cohort_parts):
            raise RequestError("Selecciona un colectivo válido.")
        questions = validate_questions(payload.get("questions", json.loads(row["questions_json"])))
        generator_history = validate_generator_history(
            payload.get("generator_history", json.loads(row["generator_history_json"]))
        )
        db.execute(
            """UPDATE surveys SET title = ?, intro = ?, cohort = ?, questions_json = ?,
               generator_history_json = ?, updated_at = ? WHERE id = ?""",
            (
                title.strip(),
                intro.strip(),
                cohort.strip(),
                json.dumps(questions, ensure_ascii=False),
                json.dumps(generator_history, ensure_ascii=False),
                _now_iso(),
                survey_id,
            ),
        )
        updated = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    return _survey_dict(updated, include_private=True)


def delete_survey(survey_id: str) -> dict:
    """Elimina una encuesta y sus datos locales asociados, sin tocar Typeform."""
    with connect() as db:
        row = db.execute("SELECT id FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if row is None:
            raise RequestError("No se ha encontrado esta consulta.", 404)
        db.execute("DELETE FROM responses WHERE survey_id = ?", (survey_id,))
        db.execute("DELETE FROM typeform_submissions WHERE survey_id = ?", (survey_id,))
        db.execute(
            "UPDATE actions SET followup_survey_id = '' WHERE followup_survey_id = ?",
            (survey_id,),
        )
        db.execute("DELETE FROM surveys WHERE id = ?", (survey_id,))
    return {"id": survey_id, "deleted": True}


def approve_survey(survey_id: str) -> dict:
    """Valida y aprueba formalmente una encuesta por parte de RRHH."""
    with connect() as db:
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if row is None:
            raise RequestError("No se ha encontrado esta consulta.", 404)
        if row["status"] != "draft":
            raise RequestError("Solo se puede aprobar una consulta en borrador.")
        validate_questions(json.loads(row["questions_json"]))
        now = _now_iso()
        db.execute(
            "UPDATE surveys SET status = 'approved', approved_at = ?, updated_at = ? WHERE id = ?",
            (now, now, survey_id),
        )
        updated = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    return _survey_dict(updated, include_private=True)


def _typeform_request(method: str, url: str, payload: dict | None = None) -> dict:
    """Ejecuta una petición autenticada a la API de Typeform."""
    with TYPEFORM_LOCK:
        token = TYPEFORM_CONFIG["token"]
    if not token:
        raise RequestError("Configura el token de Typeform antes de sincronizar.", 400)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8")) if response.status != 204 else {}
    except HTTPError as exc:
        raise RequestError(
            f"Typeform ha respondido con un error ({exc.code}). Revisa el token y los permisos.",
            502,
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RequestError("No se pudo conectar con Typeform.", 502) from exc


def _typeform_form_payload(survey: dict) -> dict:
    """Construye el payload oficial para Typeform Create API."""
    fields = []
    logic_by_ref = {}
    if survey["cohort"] == "Todos":
        fields.append(
            {
                "title": "¿En qué colectivo trabajas?",
                "ref": "cohort",
                "type": "multiple_choice",
                "properties": {"choices": [{"label": cohort} for cohort in COHORTS]},
                "validations": {"required": True},
            }
        )
    for index, question in enumerate(survey["questions"]):
        kind = question["type"]
        field_type = {
            "rating": "opinion_scale",
            "multiple_choice": "multiple_choice",
            "open_text": "long_text",
            "yes_no": "yes_no",
        }[kind]
        properties: dict = {}
        if kind == "rating":
            properties = {
                "steps": 5,
                "start_at_one": True,
                "labels": {"left": "Podría mejorar", "right": "Me siento muy bien"},
            }
        elif kind == "multiple_choice":
            properties = {"choices": [{"label": label} for label in question["choices"]]}
        field = {
            "title": question["text"],
            "ref": question["id"],
            "type": field_type,
            "properties": properties,
            "validations": {"required": question["required"]},
        }
        fields.append(field)
        if question.get("logic"):
            rule = question["logic"]["show_when"]
            trigger_index = index - 1
            trigger_ref = survey["questions"][trigger_index]["id"]
            target_index = index + 1
            target = (
                {"type": "field", "value": survey["questions"][target_index]["id"]}
                if target_index < len(survey["questions"])
                else {"type": "thankyou", "value": "default"}
            )
            logic_by_ref.setdefault(trigger_ref, []).append(
                {
                    "action": "jump",
                    "details": {"to": target},
                    "condition": {
                        "op": "greater_equal_than",
                        "vars": [
                            {
                                "type": "field",
                                "value": survey["questions"][rule["question_index"]]["id"],
                            },
                            {"type": "constant", "value": str(rule["value"])},
                        ],
                    },
                }
            )
    form = {
        "title": survey["title"],
        "fields": fields,
        "settings": {"language": {"es": "es", "ca": "ca", "en": "en"}[survey["language"]]},
        "welcome_screens": [{"title": survey["intro"]}],
        "thankyou_screens": [{"title": "Gracias por compartir tu pulso."}],
    }
    if logic_by_ref:
        form["logic"] = [
            {"type": "field", "ref": ref, "actions": actions}
            for ref, actions in logic_by_ref.items()
        ]
    return form


def _call_typeform_request(method: str, url: str, payload: dict | None = None) -> dict:
    """Invoca la petición a Typeform respetando posibles mocks en tests."""
    import sys
    app_mod = sys.modules.get("backend.app")
    if app_mod and hasattr(app_mod, "_typeform_request") and getattr(app_mod, "_typeform_request") is not _typeform_request:
        return getattr(app_mod, "_typeform_request")(method, url, payload)
    return _typeform_request(method, url, payload)


def publish_survey(survey_id: str, public_base_url: str = "", force: bool = False) -> dict:
    """Publica la encuesta en Typeform o genera URL local."""
    with connect() as db:
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if row is None:
            raise RequestError("No se ha encontrado esta consulta.", 404)
        if row["status"] != "approved" or not row["approved_at"]:
            raise RequestError("La consulta necesita aprobación humana antes de publicarse.")
        if not force:
            too_recent = db.execute(
                """SELECT 1 FROM surveys WHERE status = 'published'
                   AND published_at >= ? AND id != ?
                   AND (cohort = ? OR cohort = 'Todos' OR ? = 'Todos') LIMIT 1""",
                (
                    (datetime.now(timezone.utc) - timedelta(days=row["frequency_days"])).isoformat(),
                    survey_id,
                    row["cohort"],
                    row["cohort"],
                ),
            ).fetchone()
            if too_recent:
                raise RequestError("Este colectivo ya recibió una consulta recientemente. Espera al menos el intervalo configurado.")
        survey = _survey_dict(row, include_private=True)
    with TYPEFORM_LOCK:
        has_typeform = bool(TYPEFORM_CONFIG["token"])
    typeform_id = ""
    if has_typeform:
        response = _call_typeform_request(
            "POST", "https://api.typeform.com/forms", _typeform_form_payload(survey)
        )
        typeform_id = response.get("id", "")
        url = response.get("_links", {}).get("display", "")
        if not typeform_id or not url:
            raise RequestError("Typeform no devolvió un enlace de publicación válido.", 502)
    else:
        url = f"{public_base_url.rstrip('/')}/respond/{survey_id}" if public_base_url else f"/respond/{survey_id}"
    now = _now_iso()
    with connect() as db:
        db.execute(
            """UPDATE surveys SET status = 'published', published_at = ?,
               publish_url = ?, typeform_id = ?, updated_at = ? WHERE id = ?""",
            (now, url, typeform_id, now, survey_id),
        )
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    return _survey_dict(row, include_private=True)


def import_typeform_responses(survey_id: str) -> dict:
    """Importa y anonimiza las respuestas completas de Typeform en SQLite."""
    survey = get_survey(survey_id)
    with connect() as db:
        row = db.execute("SELECT typeform_id FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    if not row["typeform_id"]:
        raise RequestError("Esta consulta aún no está conectada a un formulario de Typeform.")

    imported = 0
    skipped = 0
    imported_ratings = 0
    next_url = f"https://api.typeform.com/forms/{row['typeform_id']}/responses?page_size=1000"
    while next_url:
        if urlparse(next_url).scheme != "https" or urlparse(next_url).hostname != "api.typeform.com":
            raise RequestError("Typeform devolvió un enlace de paginación no válido.", 502)
        result = _call_typeform_request("GET", next_url)
        for item in result.get("items", []):
            response_id = item.get("token")
            answers = item.get("answers", [])
            if not isinstance(response_id, str) or not response_id or not isinstance(answers, list):
                skipped += 1
                continue

            by_ref = {
                answer.get("field", {}).get("ref"): answer
                for answer in answers
                if isinstance(answer, dict) and isinstance(answer.get("field"), dict)
            }
            cohort = survey["cohort"] if survey["cohort"] in COHORTS else "Sin especificar"
            if survey["cohort"] == "Todos":
                cohort_answer = by_ref.get("cohort", {}).get("choice", {}).get("label")
                cohort = cohort_answer if cohort_answer in COHORTS else "Sin especificar"

            stored_answers = []
            ratings = []
            comments = []
            for question in survey["questions"]:
                typeform_answer = by_ref.get(question["id"], {})
                answer_value = None
                if question["type"] == "rating":
                    raw_value = typeform_answer.get("number")
                    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                        answer_value = int(raw_value) if int(raw_value) == raw_value else None
                    if isinstance(answer_value, int) and 1 <= answer_value <= 5:
                        ratings.append((question, answer_value))
                elif question["type"] == "multiple_choice":
                    choice = typeform_answer.get("choice")
                    if isinstance(choice, dict) and isinstance(choice.get("label"), str):
                        answer_value = choice["label"][:200]
                    else:
                        choices = typeform_answer.get("choices")
                        if isinstance(choices, dict) and isinstance(choices.get("labels"), list):
                            answer_value = [
                                label[:200] for label in choices["labels"]
                                if isinstance(label, str)
                            ]
                elif question["type"] == "yes_no":
                    raw_value = typeform_answer.get("boolean")
                    if isinstance(raw_value, bool):
                        answer_value = raw_value
                elif question["type"] == "open_text":
                    raw_text = typeform_answer.get("text")
                    if isinstance(raw_text, str):
                        answer_value = sanitize_comment(raw_text)
                        if answer_value:
                            comments.append(answer_value)

                stored_answers.append(
                    {
                        "question_id": question["id"],
                        "question": question["text"],
                        "type": question["type"],
                        "dimension": question.get("dimension", "bienestar"),
                        "answer": answer_value,
                    }
                )

            submitted_at = item.get("submitted_at")
            if isinstance(submitted_at, str):
                try:
                    parsed_date = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
                except ValueError:
                    submitted_at = _now_iso()
                    parsed_date = datetime.now(timezone.utc)
            else:
                submitted_at = _now_iso()
                parsed_date = datetime.now(timezone.utc)

            response_key = hashlib.sha256(response_id.encode("utf-8")).hexdigest()
            comment = " ".join(comments)[:2000]
            with connect() as db:
                submission_cursor = db.execute(
                    """INSERT OR IGNORE INTO typeform_submissions
                       (survey_id, response_key, submitted_at, cohort, answers_json, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        survey_id,
                        response_key,
                        submitted_at,
                        cohort,
                        json.dumps(stored_answers, ensure_ascii=False),
                        _now_iso(),
                    ),
                )
                if not submission_cursor.rowcount:
                    continue

                db.execute(
                    "DELETE FROM responses WHERE survey_id = ? AND typeform_response_id = ?",
                    (survey_id, response_id),
                )
                for index, (question, score) in enumerate(ratings):
                    cursor = db.execute(
                        """INSERT OR IGNORE INTO responses
                           (day, dimension, site, score, cohort, channel, comment,
                            survey_id, period, typeform_response_id, typeform_submission_key, is_demo)
                           VALUES (?, ?, ?, ?, ?, 'Typeform', ?, ?, 'actual', ?, ?, 0)""",
                        (
                            parsed_date.date().isoformat(),
                            question.get("dimension", "bienestar"),
                            COHORT_LABELS.get(cohort, cohort),
                            score,
                            cohort,
                            comment if index == 0 else "",
                            survey_id,
                            response_key if index == 0 else "",
                            response_key,
                        ),
                    )
                    imported_ratings += cursor.rowcount
            imported += 1
        next_url = result.get("_links", {}).get("next", "")

    return {"imported": imported, "ratings_imported": imported_ratings, "skipped": skipped}


def save_survey_response(payload: object) -> dict:
    """Guarda una respuesta del formulario de respuesta web."""
    if not isinstance(payload, dict):
        raise RequestError("El formato de respuesta no es válido.")
    survey_id = payload.get("survey_id")
    if not isinstance(survey_id, str):
        raise RequestError("No se ha indicado una consulta válida.")
    survey = get_survey(survey_id, public_only=True)
    cohort = payload.get("cohort")
    if not isinstance(cohort, str) or cohort not in COHORTS:
        raise RequestError("Selecciona un colectivo válido.")
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != len(survey["questions"]):
        raise RequestError("Completa las preguntas requeridas.")
    score = None
    comment = ""
    first_score = None
    for question, answer in zip(survey["questions"], answers):
        if question["logic"] and first_score is not None:
            rule = question["logic"]["show_when"]
            if rule["operator"] == "less_than" and answers[rule["question_index"]] is not None and answers[rule["question_index"]] >= rule["value"]:
                continue
        if question["required"] and (answer is None or answer == ""):
            raise RequestError("Responde todas las preguntas obligatorias.")
        if question["type"] == "rating" and answer is not None:
            if isinstance(answer, bool) or not isinstance(answer, int) or not 1 <= answer <= 5:
                raise RequestError("La valoración debe estar entre 1 y 5.")
            if score is None:
                score = answer
            if first_score is None:
                first_score = answer
        if question["type"] == "multiple_choice" and answer is not None and answer not in question["choices"]:
            raise RequestError("Selecciona una de las opciones disponibles.")
        if question["type"] == "yes_no" and answer is not None and not isinstance(answer, bool):
            raise RequestError("La respuesta sí/no no es válida.")
        if question["type"] == "open_text" and isinstance(answer, str):
            if len(answer) > 2000:
                raise RequestError("La respuesta abierta supera el límite de 2.000 caracteres.")
            comment = sanitize_comment(answer)
        if question["type"] == "open_text" and answer is not None and not isinstance(answer, str):
            raise RequestError("La respuesta abierta no es válida.")
    if not comment and isinstance(payload.get("comment"), str):
        comment = sanitize_comment(payload["comment"])
    if score is None:
        score = 3
    turno = payload.get("turno", "")
    center_type = turno if turno in ("Mañana", "Tarde", "Noche") else ""
    submission_key = f"sub_{uuid.uuid4().hex[:16]}"
    with connect() as db:
        db.execute(
            """INSERT INTO responses (day, dimension, site, score, cohort, channel,
               comment, survey_id, period, center_type, typeform_submission_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'actual', ?, ?)""",
            (
                date.today().isoformat(),
                survey["questions"][0]["dimension"],
                COHORT_LABELS[cohort],
                score,
                cohort,
                survey["channel"],
                comment,
                survey_id,
                center_type,
                submission_key,
            ),
        )
        for q, a in zip(survey["questions"][1:], answers[1:]):
            if q.get("type") == "rating" and isinstance(a, int) and not isinstance(a, bool) and 1 <= a <= 5:
                db.execute(
                    """INSERT INTO responses (day, dimension, site, score, cohort, channel,
                       comment, survey_id, period, center_type, typeform_submission_key) VALUES (?, ?, ?, ?, ?, ?, '', ?, 'actual', ?, ?)""",
                    (
                        date.today().isoformat(),
                        q.get("dimension", "bienestar"),
                        COHORT_LABELS[cohort],
                        a,
                        cohort,
                        survey["channel"],
                        survey_id,
                        center_type,
                        submission_key,
                    ),
                )
    return {"received": True, "message": "Gracias por compartir tu pulso."}


def create_recommendations(insights: dict) -> list[dict]:
    """Genera recomendaciones operativas accionables para RRHH."""
    candidates = []
    for topic in insights["topics"]:
        if topic["emerging"]:
            candidates.append(
                {
                    "topic": topic["id"],
                    "title": f"Escuchar al equipo sobre {topic['label'].lower()}",
                    "description": f"Propuesta: abrir una conversación breve sobre {topic['label'].lower()} y acordar un cambio pequeño con el equipo.",
                    "rationale": f"El tema aparece en {topic['count']} respuestas y sube {topic['change_pct']} puntos porcentuales respecto al periodo anterior. Es una señal, no una causa demostrada.",
                }
            )
    return candidates[:3]


def get_admin_data() -> dict:
    """Devuelve el estado consolidado de la plataforma para RRHH."""
    dashboard = get_dashboard()
    with connect() as db:
        actions = db.execute(
            "SELECT id, created_at, topic, title, description, rationale, status, followup_survey_id FROM actions ORDER BY created_at DESC"
        ).fetchall()
    all_stored = [dict(row) for row in actions]
    seen = {action["topic"] for action in all_stored}
    stored = [action for action in all_stored if action["status"] != "dismissed"]
    for candidate in create_recommendations(dashboard["insights"]):
        if candidate["topic"] not in seen:
            candidate.update({"status": "proposed", "id": None, "followup_survey_id": ""})
            stored.append(candidate)
    return {
        "dashboard": dashboard,
        "surveys": list_surveys(),
        "actions": stored,
        "typeform_connected": bool(TYPEFORM_CONFIG["token"]),
        "ai_connected": bool(AI_CONFIG["api_key"]),
    }


def review_action(action_id: object, payload: object) -> dict:
    """Actualiza el estado de una recomendación de RRHH."""
    if isinstance(action_id, bool) or not isinstance(action_id, int):
        raise RequestError("La acción indicada no es válida.")
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("status"), str)
        or payload["status"] not in {"approved", "dismissed"}
    ):
        raise RequestError("Selecciona aprobar o descartar la recomendación.")
    with connect() as db:
        action = db.execute("SELECT id FROM actions WHERE id = ?", (action_id,)).fetchone()
        if action is None:
            raise RequestError("No se ha encontrado la recomendación.", 404)
        db.execute(
            "UPDATE actions SET status = ?, reviewed_at = ? WHERE id = ?",
            (payload["status"], _now_iso(), action_id),
        )
    return {"id": action_id, "status": payload["status"]}


def approve_recommendation(payload: object) -> dict:
    """Aprueba formalmente una recomendación propuesta por el motor estadístico."""
    if not isinstance(payload, dict):
        raise RequestError("La recomendación no es válida.")
    topic, title, description, rationale = (
        payload.get("topic"), payload.get("title"), payload.get("description"), payload.get("rationale")
    )
    status = payload.get("status", "approved")
    if not isinstance(status, str) or status not in {"approved", "dismissed"}:
        raise RequestError("Selecciona aprobar o descartar la recomendación.")
    if not all(isinstance(value, str) and value.strip() for value in (topic, title, description, rationale)):
        raise RequestError("La recomendación necesita un tema, título, acción y explicación.")
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO actions (created_at, topic, title, description, rationale, status, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_now_iso(), topic, title.strip(), description.strip(), rationale.strip(), status, _now_iso()),
        )
        action_id = cursor.lastrowid
    return {"id": action_id, "status": "approved"}


def create_followup_survey(action_id: object) -> dict:
    """Crea una microconsulta de seguimiento para medir el impacto de una acción aprobada."""
    from .generator import create_survey_draft

    if isinstance(action_id, bool) or not isinstance(action_id, int):
        raise RequestError("La acción indicada no es válida.")
    with connect() as db:
        action = db.execute(
            "SELECT * FROM actions WHERE id = ? AND status = 'approved'", (action_id,)
        ).fetchone()
    if action is None:
        raise RequestError("Solo se pueden crear seguimientos de acciones aprobadas.")
    draft = create_survey_draft(
        {
            "objective": f"Comprobar si la acción «{action['title']}» ha ayudado.",
            "cohort": "Todos",
            "context": action["description"],
            "max_duration": "2",
            "exclusions": "Nombres, correos, teléfonos y detalles que identifiquen a una persona.",
            "language": "es",
            "channel": "Móvil-QR",
            "followup_of": str(action_id),
        }
    )
    with connect() as db:
        db.execute(
            "UPDATE actions SET followup_survey_id = ? WHERE id = ?",
            (draft["id"], action_id),
        )
    return draft


def generate_qr_svg(url: str) -> bytes:
    """Genera un SVG vectorial nativo sin dependencias pesadas de renderizado."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=3,
        )
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        n = len(matrix)
        paths = []
        for r, row in enumerate(matrix):
            for c, val in enumerate(row):
                if val:
                    paths.append(f"M{c},{r}h1v1h-1z")
        path_data = "".join(paths)
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="280" height="280" shape-rendering="crispEdges">\n'
            f'  <rect width="{n}" height="{n}" fill="#ffffff"/>\n'
            f'  <path d="{path_data}" fill="#1b1c1b"/>\n'
            f'</svg>'
        ).encode("utf-8")
    except Exception:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">\n'
            '  <rect width="200" height="200" fill="#f8f9fa" rx="12"/>\n'
            '  <rect x="20" y="20" width="50" height="50" fill="#1b1c1b" rx="4"/>\n'
            '  <rect x="30" y="30" width="30" height="30" fill="#ffffff" rx="2"/>\n'
            '  <rect x="38" y="38" width="14" height="14" fill="#1b1c1b"/>\n'
            '  <rect x="130" y="20" width="50" height="50" fill="#1b1c1b" rx="4"/>\n'
            '  <rect x="140" y="30" width="30" height="30" fill="#ffffff" rx="2"/>\n'
            '  <rect x="148" y="38" width="14" height="14" fill="#1b1c1b"/>\n'
            '  <rect x="20" y="130" width="50" height="50" fill="#1b1c1b" rx="4"/>\n'
            '  <rect x="30" y="140" width="30" height="30" fill="#ffffff" rx="2"/>\n'
            '  <rect x="38" y="148" width="14" height="14" fill="#1b1c1b"/>\n'
            '  <rect x="85" y="85" width="30" height="30" fill="#c4161c" rx="4"/>\n'
            '  <text x="100" y="105" font-size="16" font-family="sans-serif" text-anchor="middle" fill="#ffffff">★</text>\n'
            '  <text x="100" y="175" font-size="10" font-family="sans-serif" font-weight="bold" text-anchor="middle" fill="#5c403d">Pulso Damm QR</text>\n'
            '</svg>'
        ).encode("utf-8")


def generate_qr_png(url: str) -> bytes:
    """Genera un código QR en formato PNG rasterizado de alta definición."""
    try:
        import io
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=3,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#1b1c1b", back_color="#ffffff")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b""


class AdminPanel:
    """Panel de gestión y administración de RRHH."""

    @staticmethod
    def get_admin_data() -> dict:
        return get_admin_data()

    @staticmethod
    def approve_survey(survey_id: str) -> dict:
        return approve_survey(survey_id)

    @staticmethod
    def publish_survey(survey_id: str, base_url: str = "", force: bool = False) -> dict:
        return publish_survey(survey_id, public_base_url=base_url, force=force)

    @staticmethod
    def review_action(action_id: int, payload: object) -> dict:
        return review_action(action_id, payload)

    @staticmethod
    def import_responses(survey_id: str) -> dict:
        return import_typeform_responses(survey_id)
