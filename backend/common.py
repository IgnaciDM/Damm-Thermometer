from __future__ import annotations

import json
import math
import os
import random
import re
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Iterator, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = ROOT
env_path = ROOT / ".env"
if env_path.exists():
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, v = stripped.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

UI_DIR = ROOT / "ui"
DATA_DIR = ROOT / "data"
LEGACY_DATABASE = ROOT / "pulso.sqlite3"
DATABASE = Path(os.environ.get("PULSO_DATABASE", DATA_DIR / "pulso.sqlite3"))
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
MIN_GROUP_SIZE = 5

DIMENSIONS = [
    {"id": "bienestar", "label": "Bienestar", "short": "Bienestar"},
    {"id": "liderazgo", "label": "Liderazgo", "short": "Liderazgo"},
    {"id": "desarrollo", "label": "Desarrollo", "short": "Desarrollo"},
    {"id": "cultura", "label": "Cultura y pertenencia", "short": "Cultura"},
    {"id": "relaciones", "label": "Entorno y relaciones", "short": "Relaciones"},
    {"id": "condiciones", "label": "Condiciones y organización", "short": "Organización"},
]
DIMENSION_IDS = {item["id"] for item in DIMENSIONS}
SITES = ["Fábrica", "Logística", "Comercial", "Oficina"]
DEMO_QUESTIONS = {
    "bienestar": "¿Qué te ha ayudado a sentirte bien en tu jornada esta semana?",
    "liderazgo": "¿Has recibido el acompañamiento que necesitabas para hacer bien tu trabajo?",
    "desarrollo": "¿Has tenido una oportunidad para aprender algo nuevo esta semana?",
    "cultura": "¿Has sentido que tu equipo comparte y vive los valores de la organización?",
    "relaciones": "¿Cómo ha sido la colaboración con las personas con las que trabajas?",
    "condiciones": "¿Has contado con los recursos y el tiempo necesarios para hacer tu trabajo?",
}
AI_CONFIG = {
    "api_key": (
        os.environ.get("UTH_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("AI_API_KEY")
        or "uth-ba946ba792cd29e62479fefbdf737003a1080cc0"
    ),
    "base_url": os.environ.get(
        "AI_BASE_URL", "https://dcc-llm-gw.uab.cat/uabthehack/v1/chat/completions"
    ),
    "model": os.environ.get("AI_MODEL", "ornith"),
}
TYPEFORM_CONFIG = {
    "token": os.environ.get("TYPEFORM_API_TOKEN", ""),
}
AI_CONFIG_LOCK = threading.Lock()
TYPEFORM_LOCK = threading.Lock()
RETENTION_DAYS = 90
COHORTS = ["Planta", "Logística", "Comercial", "Oficina", "Manager"]
COHORT_LABELS = {
    "Planta": "Fábrica",
    "Logística": "Logística",
    "Comercial": "Comercial",
    "Oficina": "Oficina",
    "Manager": "Manager",
}
POPULATION_SHARES = {
    "Planta": 0.46,
    "Logística": 0.22,
    "Comercial": 0.17,
    "Oficina": 0.10,
    "Manager": 0.05,
}

QUESTION_TYPES = {"rating", "multiple_choice", "open_text", "yes_no"}


class RequestError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def get_database_path() -> Path:
    """Devuelve la ruta actual a la base de datos, respetando patches de tests."""
    import sys
    app_mod = sys.modules.get("backend.app")
    if app_mod and hasattr(app_mod, "DATABASE"):
        return Path(getattr(app_mod, "DATABASE"))
    return DATABASE


def migrate_legacy_database(
    legacy_database: Path = LEGACY_DATABASE, database: Path | None = None
) -> None:
    """Migra de forma segura la base de datos heredada al directorio data/."""
    target = database or get_database_path()
    if "PULSO_DATABASE" in os.environ or target.exists() or not legacy_database.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy_database.replace(target)


def enforce_data_retention_policy(db: sqlite3.Connection, retention_days: int = RETENTION_DAYS) -> dict[str, Any]:
    """
    Aplica la política de caducidad y retención de 90 días exigida por RGPD y gobernanza Damm.
    Elimina registros con fecha anterior al límite temporal tanto en responses como en typeform_submissions.
    """
    cutoff_date = (date.today() - timedelta(days=retention_days)).isoformat()
    deleted_resp = 0
    deleted_tf = 0
    try:
        c_resp = db.execute(
            "DELETE FROM responses WHERE day < ?",
            (cutoff_date,),
        )
        deleted_resp = c_resp.rowcount
    except sqlite3.OperationalError:
        pass

    try:
        c_typeform = db.execute(
            "DELETE FROM typeform_submissions WHERE substr(submitted_at, 1, 10) < ?",
            (cutoff_date,),
        )
        deleted_tf = c_typeform.rowcount
    except sqlite3.OperationalError:
        pass

    return {
        "cutoff_date": cutoff_date,
        "responses_deleted": deleted_resp,
        "typeform_submissions_deleted": deleted_tf,
        "retention_days": retention_days,
    }


def init_db(db: sqlite3.Connection) -> None:
    """Inicializa el esquema de tablas e índices en SQLite y ejecuta la política de caducidad."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY,
            day TEXT NOT NULL,
            dimension TEXT NOT NULL,
            site TEXT NOT NULL,
            score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
            is_demo INTEGER NOT NULL DEFAULT 0
        )"""
    )
    response_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(responses)")
    }
    additions = {
        "cohort": "TEXT NOT NULL DEFAULT 'Oficina'",
        "center_type": "TEXT NOT NULL DEFAULT ''",
        "channel": "TEXT NOT NULL DEFAULT 'Móvil-QR'",
        "comment": "TEXT NOT NULL DEFAULT ''",
        "survey_id": "TEXT NOT NULL DEFAULT ''",
        "period": "TEXT NOT NULL DEFAULT ''",
        "typeform_response_id": "TEXT NOT NULL DEFAULT ''",
        "typeform_submission_key": "TEXT NOT NULL DEFAULT ''",
    }
    for column, definition in additions.items():
        if column not in response_columns:
            db.execute(f"ALTER TABLE responses ADD COLUMN {column} {definition}")
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_responses_typeform_response
           ON responses(typeform_response_id) WHERE typeform_response_id != ''"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS typeform_submissions (
            id INTEGER PRIMARY KEY,
            survey_id TEXT NOT NULL,
            response_key TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            cohort TEXT NOT NULL,
            answers_json TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            UNIQUE(survey_id, response_key)
        )"""
    )
    # Ejecutar política de retención y caducidad de datos (90 días)
    try:
        enforce_data_retention_policy(db, RETENTION_DAYS)
    except Exception:
        pass
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_typeform_submissions_survey_date
           ON typeform_submissions(survey_id, submitted_at)"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS surveys (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            objective TEXT NOT NULL,
            cohort TEXT NOT NULL,
            context TEXT NOT NULL,
            max_duration INTEGER NOT NULL,
            exclusions TEXT NOT NULL,
            language TEXT NOT NULL,
            channel TEXT NOT NULL,
            title TEXT NOT NULL,
            intro TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            status TEXT NOT NULL,
            approved_at TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            publish_url TEXT NOT NULL DEFAULT '',
            typeform_id TEXT NOT NULL DEFAULT '',
            frequency_days INTEGER NOT NULL DEFAULT 7,
            population_json TEXT NOT NULL DEFAULT '{}',
            followup_of TEXT NOT NULL DEFAULT '',
            generator_history_json TEXT NOT NULL DEFAULT '[]'
        )"""
    )
    survey_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(surveys)")
    }
    if "generator_history_json" not in survey_columns:
        db.execute(
            "ALTER TABLE surveys ADD COLUMN generator_history_json TEXT NOT NULL DEFAULT '[]'"
        )
    db.execute(
        """CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            topic TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            rationale TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            reviewed_at TEXT NOT NULL DEFAULT '',
            followup_survey_id TEXT NOT NULL DEFAULT ''
        )"""
    )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        init_db(db)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ENHANCED_PII_PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", re.IGNORECASE), "[correo oculto]"),
    (re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE), "[enlace oculto]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?:[ \t]?[0-9]{2,4}){4,10}\b", re.IGNORECASE), "[cuenta IBAN oculta]"),
    (re.compile(r"\b(?:\d{8}[A-HJ-NP-TV-Z]|[XYZ]\d{7}[A-HJ-NP-TV-Z]|[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J])\b", re.IGNORECASE), "[documento DNI/NIE oculto]"),
    (re.compile(r"(?i)\b(?:el\s+[úu]nico|la\s+[úu]nica|el\s+primer|la\s+primera)\s+(?:hombre|mujer|chico|chica|emplead[oa]|operari[oa]|trabajador[a]|compañer[oa]|persona)\s+(?:del?|en)\s+[^.,;\n]{3,35}"), "[identificador singular oculto]"),
    (re.compile(r"(?i)\b(?:me llamo|mi nombre es|soy)\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ-]+(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ-]+)?"), "[identidad oculta]"),
    (re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){8,15}(?!\w)"), "[teléfono oculto]"),
    (re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,}\b"), "[usuario oculto]"),
    (re.compile(r"(?i)\b(?:preg[úu]ntale\s+a|dile\s+a|habla\s+con|avisa\s+a|comenta\s+con|gracias\s+a|jefe|jefa|encargad[oa]|responsable|mando|coordinador[a]|delegad[oa])\s+([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ-]+)"), "[referencia personal oculta]"),
    (re.compile(r"\b(?:Marta|Carlos|Juan|Pedro|Laura|Ignacio|Victor|Ana|David|Javier|Elena|Pablo|Manuel|Carmen|Antonio|Maria|José|Jordi|Marc|Pol|Montserrat|Nuria)\b", re.IGNORECASE), "[nombre oculto]"),
]

PII_PATTERNS = [p for p, _ in ENHANCED_PII_PATTERNS]


def anonymize_comment_ner_and_pii(value: object) -> str:
    """
    Pasada exhaustiva de desidentificación Zero-PII y NER:
    Elimina emails, teléfonos, URLs, identificadores indirectos ("única mujer del turno"),
    nombres propios ("pregúntale a Marta", "Carlos"), DNI/NIE e IBAN.
    """
    if not isinstance(value, str):
        return ""
    cleaned = value[:2000]
    for pattern, repl in ENHANCED_PII_PATTERNS:
        cleaned = pattern.sub(repl, cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def sanitize_comment(value: object) -> str:
    """Sanitiza y anonimiza comentarios antes de persistir o procesar con LLMs."""
    return anonymize_comment_ner_and_pii(value)


def classify_comment(comment: str) -> str:
    """
    Clasifica comentarios según su afinidad semántica multi-etiqueta ponderada.
    Evita sesgos de 'primer match gana' ('equipo' desambigua entre personas y herramientas;
    'tiempo' desambigua entre descansos y lentitud).
    """
    if not isinstance(comment, str) or len(comment.strip()) < 3:
        return "Organización general"

    normalized = unicodedata.normalize("NFKD", comment.casefold())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))

    topics = [
        (
            "Carga y turnicidad",
            ("turno", "turnos", "ritmo", "carga", "horario", "horas", "relevo", "pausa", "descanso", "cobertura", "cuadrilla", "conciliar", "fatiga"),
        ),
        (
            "Comunicación y liderazgo",
            ("comunic", "inform", "aviso", "mandos", "lider", "jefe", "cambio", "escuch", "mensaje", "feedback", "acompan"),
        ),
        (
            "Herramientas y soporte",
            ("herramienta", "sistema", "recurso", "material", "lento", "ordenador", "maquina", "pantalla", "software", "incidencia", "parada", "repuesto"),
        ),
        (
            "Reconocimiento y ambiente",
            ("reconoc", "ambiente", "compan", "apoyo", "valor", "gracias", "clima", "agradec", "esfuerzo", "merito"),
        ),
        (
            "Desarrollo y formación",
            ("aprendiz", "formac", "crecer", "desarroll", "carrera", "promoc", "capacit", "oportun"),
        ),
        (
            "Bienestar y condiciones",
            ("bienestar", "estres", "ergonom", "salud", "cansancio", "temperatura", "ruido", "calor", "aire"),
        ),
    ]

    scores = {}
    for topic, keywords in topics:
        score = sum(1 for kw in keywords if kw in normalized)
        # Desambiguación 'equipo':
        if "equip" in normalized:
            if any(x in normalized for x in ("compan", "apoyo", "junt", "trabaj en equip")):
                if topic == "Reconocimiento y ambiente":
                    score += 2
                elif topic == "Herramientas y soporte":
                    score -= 1
            elif any(x in normalized for x in ("maquin", "pantall", "ordenad", "lento")):
                if topic == "Herramientas y soporte":
                    score += 2
        # Desambiguación 'tiempo':
        if "tiemp" in normalized:
            if any(x in normalized for x in ("pausa", "descans", "recuper", "jornad")):
                if topic == "Carga y turnicidad":
                    score += 2
            elif any(x in normalized for x in ("esper", "tard", "lento", "cierre")):
                if topic == "Herramientas y soporte":
                    score += 1
        scores[topic] = score

    best_topic, best_score = max(scores.items(), key=lambda item: item[1])
    return best_topic if best_score > 0 else "Organización general"


def score_comment(comment: str) -> str:
    normalized = comment.casefold()
    positive = ("buen", "bien", "genial", "ágil", "correcto", "apoyo", "gracias", "cómodo", "mejora")
    negative = ("mal", "peor", "falta", "bloqueo", "lento", "ruido", "estrés", "sobrecarga", "difícil", "tarde")
    pos_score = sum(1 for word in positive if word in normalized)
    neg_score = sum(1 for word in negative if word in normalized)
    if pos_score > neg_score:
        return "positive"
    if neg_score > pos_score:
        return "critical"
    return "neutral"


def validate_response(payload: object) -> tuple[str, str, int]:
    if not isinstance(payload, dict):
        raise RequestError("El formato de respuesta no es válido.")
    dimension = payload.get("dimension")
    site = payload.get("site")
    score = payload.get("score")
    if not isinstance(dimension, str) or dimension not in DIMENSION_IDS:
        raise RequestError("Selecciona una dimensión válida.")
    if not isinstance(site, str) or site not in SITES:
        raise RequestError("Selecciona un entorno de trabajo válido.")
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise RequestError("La valoración debe estar entre 1 y 5.")
    return dimension, site, score


def validate_generator_history(history: object) -> list[str]:
    if history is None:
        return []
    if not isinstance(history, list) or len(history) > 50:
        raise RequestError("El historial del generador no es válido.")
    if any(
        not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 1000
        for prompt in history
    ):
        raise RequestError("Cada instrucción del generador debe tener entre 1 y 1000 caracteres.")
    return [prompt.strip() for prompt in history]


def validate_questions(questions: object) -> list[dict]:
    if not isinstance(questions, list) or not 3 <= len(questions) <= 6:
        raise RequestError("La consulta debe tener entre 3 y 6 preguntas.")
    normalized = []
    allowed_types = {"rating", "multiple_choice", "open_text", "yes_no"}
    seen_ids = set()
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise RequestError(f"La pregunta {index + 1} no tiene un formato válido.")
        text = question.get("text")
        kind = question.get("type")
        if not isinstance(text, str) or not 8 <= len(text.strip()) <= 280:
            raise RequestError(f"Revisa el texto de la pregunta {index + 1}.")
        if not isinstance(kind, str) or kind not in allowed_types:
            raise RequestError(f"El tipo de la pregunta {index + 1} no es válido.")
        required = question.get("required", True)
        if not isinstance(required, bool):
            raise RequestError(f"Indica si la pregunta {index + 1} es obligatoria.")
        question_id = question.get("id")
        if question_id is None or question_id == "":
            question_id = uuid.uuid4().hex[:8]
        if (
            not isinstance(question_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", question_id)
            or question_id in seen_ids
        ):
            raise RequestError(f"El identificador de la pregunta {index + 1} no es válido.")
        seen_ids.add(question_id)
        choices = question.get("choices", [])
        if kind == "multiple_choice":
            if not isinstance(choices, list) or not 2 <= len(choices) <= 8:
                raise RequestError(f"La pregunta {index + 1} necesita entre 2 y 8 opciones.")
            if not all(
                isinstance(choice, str) and 1 <= len(choice.strip()) <= 80
                for choice in choices
            ):
                raise RequestError(f"Las opciones de la pregunta {index + 1} no son válidas.")
            choices = [choice.strip() for choice in choices]
        logic = question.get("logic")
        if logic is not None:
            if not isinstance(logic, dict) or set(logic) != {"show_when"}:
                raise RequestError(f"La lógica de la pregunta {index + 1} no es válida.")
            rule = logic["show_when"]
            if (
                not isinstance(rule, dict)
                or set(rule) != {"question_index", "operator", "value"}
                or isinstance(rule.get("question_index"), bool)
                or not isinstance(rule.get("question_index"), int)
                or not 0 <= rule["question_index"] < index
                or not isinstance(questions[rule["question_index"]], dict)
                or rule.get("operator") != "less_than"
                or isinstance(rule.get("value"), bool)
                or not isinstance(rule.get("value"), int)
                or not 1 <= rule["value"] <= 5
                or questions[rule["question_index"]].get("type") != "rating"
            ):
                raise RequestError(f"La lógica de la pregunta {index + 1} no es válida.")
        normalized.append(
            {
                "id": question_id,
                "text": text.strip(),
                "type": kind,
                "dimension": str(question.get("dimension") or "bienestar"),
                "required": required,
                "choices": choices if kind == "multiple_choice" else [],
                "logic": logic,
            }
        )
    if not any(
        question["type"] == "rating" and question["required"]
        for question in normalized
    ):
        raise RequestError("Incluye al menos una pregunta obligatoria con escala de 1 a 5.")
    return normalized


def calculate_quarter_label(dt_or_iso: datetime | str | None) -> str:
    """Calcula el cuatrimestre (Q1, Q2, Q3, Q4) y el año usando la librería estándar datetime."""
    if not dt_or_iso:
        dt = datetime.now()
    elif isinstance(dt_or_iso, str):
        try:
            dt = datetime.fromisoformat(dt_or_iso.replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(dt_or_iso[:10], "%Y-%m-%d")
            except Exception:
                dt = datetime.now()
    elif isinstance(dt_or_iso, datetime):
        dt = dt_or_iso
    elif isinstance(dt_or_iso, date):
        dt = datetime.combine(dt_or_iso, datetime.min.time())
    else:
        dt = datetime.now()

    quarter = (dt.month - 1) // 3 + 1
    return f"Q{quarter} {dt.year}"


def _survey_dict(row: sqlite3.Row, include_private: bool = False) -> dict:
    result = {
        "id": row["id"],
        "title": row["title"],
        "intro": row["intro"],
        "cohort": row["cohort"],
        "language": row["language"],
        "channel": row["channel"],
        "questions": json.loads(row["questions_json"]),
        "status": row["status"],
        "estimated_minutes": min(row["max_duration"], max(1, round(len(json.loads(row["questions_json"])) * 0.25))),
        "frequency_days": row["frequency_days"],
        "followup_of": row["followup_of"],
        "quarter": calculate_quarter_label(row["created_at"]),
        "created_at": row["created_at"],
    }
    if include_private:
        result.update(
            {
                "objective": row["objective"],
                "context": row["context"],
                "max_duration": row["max_duration"],
                "exclusions": row["exclusions"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "approved_at": row["approved_at"],
                "published_at": row["published_at"],
                "typeform_id": row["typeform_id"],
                "population": json.loads(row["population_json"]),
                "generator_history": json.loads(row["generator_history_json"]),
            }
        )
    if row["status"] == "published" or include_private:
        result["publish_url"] = row["publish_url"]
    result["typeform_connected"] = bool(row["typeform_id"])
    return result
