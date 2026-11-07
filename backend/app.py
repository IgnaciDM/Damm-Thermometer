from __future__ import annotations

import json
import math
import os
import random
import re
import sqlite3
import statistics
import threading
import uuid
import sys
from collections import Counter
from io import BytesIO
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

try:
    from statistical_engine import StatisticalEngine, generate_llm_statistical_payload
except ImportError:
    StatisticalEngine = None
    generate_llm_statistical_payload = None

try:
    from llm_insights import request_llm_insights
except ImportError:
    request_llm_insights = None
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
        or os.environ.get("AI_API_KEY", "")
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
    "Planta": 0.42,
    "Logística": 0.20,
    "Comercial": 0.16,
    "Oficina": 0.16,
    "Manager": 0.06,
}
TOPICS = {
    "comunicacion": ["comunicación", "información", "cambios", "avisos"],
    "turnos": ["turnos", "horario", "descanso", "cobertura"],
    "herramientas": ["herramientas", "sistema", "material", "recursos"],
    "liderazgo": ["liderazgo", "acompañamiento", "feedback", "responsables"],
    "reconocimiento": ["reconocimiento", "valoración", "agradecimiento"],
    "desarrollo": ["aprendizaje", "formación", "crecer", "desarrollo"],
    "bienestar": ["bienestar", "carga", "energía", "pausa"],
}


class RequestError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    try:
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
        }
        for column, definition in additions.items():
            if column not in response_columns:
                db.execute(f"ALTER TABLE responses ADD COLUMN {column} {definition}")
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_responses_typeform_response
               ON responses(typeform_response_id) WHERE typeform_response_id != ''"""
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
                followup_of TEXT NOT NULL DEFAULT ''
            )"""
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
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def seed_demo_data(db: sqlite3.Connection) -> None:
    if db.execute("SELECT COUNT(*) FROM responses WHERE is_demo = 1").fetchone()[0]:
        return
    rng = random.Random(2026)
    baselines = [3.7, 3.8, 3.5, 4.0, 3.8, 3.4]
    distributions = [
        {"Planta": 160, "Logística": 70, "Comercial": 28, "Oficina": 30, "Manager": 12},
        {"Planta": 100, "Logística": 50, "Comercial": 40, "Oficina": 65, "Manager": 45},
    ]
    comments = {
        "comunicacion": [
            "Los cambios de turno podrían llegar con más tiempo.",
            "A veces la comunicación de los cambios se pierde entre mensajes.",
            "Nos iría bien tener la información clara antes de empezar.",
        ],
        "turnos": [
            "La planificación de turnos deja poco margen para organizarse.",
            "Un poco más de previsión en los horarios ayudaría mucho.",
            "En algunos turnos cuesta encontrar tiempo para descansar.",
        ],
        "herramientas": [
            "La herramienta nueva funciona, pero hace falta más formación.",
            "Nos faltan algunos recursos para resolver incidencias rápido.",
            "Sería útil tener instrucciones más sencillas del sistema.",
        ],
        "liderazgo": [
            "Agradezco cuando la persona responsable escucha al equipo.",
            "Vendría bien recibir feedback más a menudo.",
            "Tenemos buena disposición, aunque no siempre encontramos tiempo para hablar.",
        ],
        "reconocimiento": [
            "Un agradecimiento por el trabajo bien hecho marca la diferencia.",
            "El equipo se esfuerza y estaría bien visibilizarlo más.",
            "A veces los pequeños logros pasan desapercibidos.",
        ],
        "desarrollo": [
            "Me gustaría tener más oportunidades para aprender en el puesto.",
            "La formación práctica ayuda a sentir más confianza.",
            "Sería bueno compartir lo que aprendemos con el resto del equipo.",
        ],
        "bienestar": [
            "Una pausa a tiempo ayuda a recuperar energía.",
            "En los días de más carga es difícil parar un momento.",
            "El ambiente del equipo contribuye mucho al bienestar.",
        ],
    }
    previous_topics = ["bienestar", "liderazgo", "herramientas", "reconocimiento", "desarrollo"]
    current_topics = ["comunicacion", "comunicacion", "turnos", "herramientas", "bienestar"]
    demo_rows = []
    for period_index, distribution in enumerate(distributions):
        days_ago = 18 if period_index == 0 else 4
        topics = previous_topics if period_index == 0 else current_topics
        period_name = "anterior" if period_index == 0 else "actual"
        index = 0
        for cohort, count in distribution.items():
            for _ in range(count):
                day = date.today() - timedelta(days=max(0, days_ago - rng.randrange(7)))
                topic = topics[index % len(topics)]
                dimension_index = index % len(DIMENSIONS)
                score = max(
                    1,
                    min(5, round(rng.gauss(baselines[dimension_index], 0.8))),
                )
                comment = rng.choice(comments[topic])
                channel = rng.choices(
                    ["Móvil-QR", "Teams", "Correo"],
                    weights=[65 if cohort in {"Planta", "Logística"} else 35, 25, 10 if cohort in {"Planta", "Logística"} else 40],
                )[0]
                demo_rows.append(
                    (
                        day.isoformat(),
                        DIMENSIONS[dimension_index]["id"],
                        COHORT_LABELS[cohort],
                        score,
                        1,
                        cohort,
                        "Centro amplio",
                        channel,
                        comment,
                        "",
                        period_name,
                    )
                )
                index += 1
    db.executemany(
        """INSERT INTO responses
           (day, dimension, site, score, is_demo, cohort, center_type, channel, comment, survey_id, period)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        demo_rows,
    )
    db.commit()


def _visible_average(rows: list[sqlite3.Row]) -> float | None:
    if len(rows) < MIN_GROUP_SIZE:
        return None
    return round(sum(row["score"] for row in rows) / len(rows), 1)


def aggregate_responses(
    rows: list[sqlite3.Row], today: date | None = None
) -> dict:
    today = today or date.today()
    cutoff = today - timedelta(days=6)
    recent = [row for row in rows if date.fromisoformat(row["day"]) >= cutoff]
    visible_rows = rows if len(rows) >= MIN_GROUP_SIZE else []
    overall = _visible_average(visible_rows)

    dimension_stats = []
    for item in DIMENSIONS:
        subset = [row for row in recent if row["dimension"] == item["id"]]
        previous = [
            row
            for row in rows
            if item["id"] == row["dimension"]
            and cutoff - timedelta(days=7) <= date.fromisoformat(row["day"]) < cutoff
        ]
        dimension_stats.append(
            {
                **item,
                "score": _visible_average(subset),
                "responses": len(subset) if len(subset) >= MIN_GROUP_SIZE else None,
                "trend": (
                    round(_visible_average(subset) - _visible_average(previous), 1)
                    if _visible_average(subset) is not None
                    and _visible_average(previous) is not None
                    else None
                ),
            }
        )

    trend = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        day_rows = [row for row in rows if row["day"] == day.isoformat()]
        trend.append(
            {
                "label": ["L", "M", "X", "J", "V", "S", "D"][day.weekday()],
                "score": _visible_average(day_rows),
            }
        )

    location_stats = []
    for site in SITES:
        site_rows = [row for row in recent if row["site"] == site]
        score = _visible_average(site_rows)
        if score is not None:
            location_stats.append(
                {"label": site, "score": score, "responses": len(site_rows)}
            )

    demo_count = sum(row["is_demo"] for row in rows)
    return {
        "overall": overall,
        "responses_7d": len(recent) if len(recent) >= MIN_GROUP_SIZE else None,
        "total_responses": len(rows) if len(rows) >= MIN_GROUP_SIZE else None,
        "dimensions": dimension_stats,
        "trend": trend,
        "locations": location_stats,
        "is_demo": demo_count > 0,
        "privacy_threshold": MIN_GROUP_SIZE,
    }


def get_dashboard() -> dict:
    with connect() as db:
        seed_demo_data(db)
        db.execute(
            "DELETE FROM responses WHERE day < ?",
            ((date.today() - timedelta(days=RETENTION_DAYS)).isoformat(),),
        )
        live_rows = db.execute(
            "SELECT * FROM responses WHERE is_demo = 0 ORDER BY day"
        ).fetchall()
        demo_rows = db.execute(
            "SELECT * FROM responses WHERE is_demo = 1 ORDER BY day"
        ).fetchall()
        population_row = db.execute(
            """SELECT population_json FROM surveys WHERE status = 'published'
               ORDER BY published_at DESC LIMIT 1"""
        ).fetchone()
    rows = live_rows if len(live_rows) >= MIN_GROUP_SIZE else demo_rows or live_rows
    result = aggregate_responses(rows)
    population = None
    if population_row:
        try:
            population = json.loads(population_row["population_json"])
        except (json.JSONDecodeError, TypeError):
            population = None
    result["insights"] = analyze_responses(rows, population)
    result["is_demo"] = bool(rows and rows[0]["is_demo"])
    result["synthetic_responses"] = len(demo_rows)
    result["live_responses"] = len(live_rows)
    result["retention_days"] = RETENTION_DAYS
    return result


def get_statistical_engine_payload() -> dict:
    """
    Ejecuta el Motor Estadístico Determinista sobre las respuestas de la base de datos.
    Calcula tasas de cobertura (rol x turno), estadísticas descriptivas insesgadas
    (media, mediana, std, varianza) y alertas de sesgo/polarización/k-anonimato.
    """
    with connect() as db:
        rows = db.execute("SELECT * FROM responses ORDER BY day DESC").fetchall()

    records = []
    for idx, row in enumerate(rows):
        site_val = row["cohort"] or row["site"] or "Planta"
        rol = "Planta" if site_val in ("Fábrica", "Planta") else site_val
        if rol not in ("Planta", "Logística", "Oficinas", "Comercial"):
            rol = "Planta"

        turno = "Mañana" if idx % 3 == 0 else "Tarde" if idx % 3 == 1 else "Noche"
        if rol in ("Oficinas", "Comercial"):
            turno = "Mañana"

        dim = row["dimension"]
        score = int(row["score"])
        q1 = score if dim == "bienestar" else 3
        q2 = score if dim == "liderazgo" else 3
        q3 = score if dim in ("condiciones", "desarrollo", "recursos", "relaciones") else 3

        records.append({
            "employee_id": f"anon_{row['id']}",
            "rol": rol,
            "turno": turno,
            "q1_bienestar": q1,
            "q2_liderazgo": q2,
            "q3_recursos": q3,
            "texto_abierto": row["comment"] if row["comment"] else None,
        })

    if generate_llm_statistical_payload:
        json_str = generate_llm_statistical_payload(records)
        return json.loads(json_str)
    return {"total_respuestas": len(records), "status": "motor_no_disponible"}


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


def save_response(payload: object) -> dict:
    dimension, site, score = validate_response(payload)
    with connect() as db:
        db.execute(
            """INSERT INTO responses
               (day, dimension, site, score, is_demo, cohort, channel)
               VALUES (?, ?, ?, ?, 0, ?, ?)""",
            (date.today().isoformat(), dimension, site, score, site, "Móvil-QR"),
        )
        db.commit()
    return get_dashboard()


PII_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", re.IGNORECASE),
    re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){8,15}(?!\w)"),
    re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE),
    re.compile(r"(?i)\b(?:me llamo|mi nombre es|soy)\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ-]+(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ-]+)?"),
    re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,}\b"),
]


def sanitize_comment(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value[:2000]
    for pattern in PII_PATTERNS:
        cleaned = pattern.sub("[dato oculto]", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _row_value(row: object, key: str, default: object = "") -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return default


def classify_comment(comment: str) -> str:
    normalized = comment.casefold()
    ranked = [
        (sum(term in normalized for term in terms), topic)
        for topic, terms in TOPICS.items()
    ]
    count, topic = max(ranked, default=(0, ""))
    return topic if count else "otros"


def confidence_label(sample_size: int) -> str:
    if sample_size >= 100:
        return "Alta"
    if sample_size >= 30:
        return "Media"
    if sample_size >= MIN_GROUP_SIZE:
        return "Limitada"
    return "Insuficiente"


def analyze_responses(
    rows: list[object], population_shares: dict | None = None
) -> dict:
    if (
        not isinstance(population_shares, dict)
        or set(population_shares) != set(COHORTS)
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in population_shares.values()
        )
        or sum(population_shares.values()) <= 0
    ):
        population_shares = POPULATION_SHARES
    total_population = sum(population_shares.values())
    normalized_population = {
        cohort: share / total_population
        for cohort, share in population_shares.items()
    }
    current_periods = [str(_row_value(row, "period")) for row in rows]
    current_period = "actual" if "actual" in current_periods else ""
    previous_period = "anterior" if "anterior" in current_periods else ""
    if current_period:
        current_rows = [row for row in rows if _row_value(row, "period") == current_period]
        previous_rows = [row for row in rows if _row_value(row, "period") == previous_period]
    else:
        current_rows = [
            row for row in rows
            if str(_row_value(row, "day")) >= (date.today() - timedelta(days=6)).isoformat()
        ]
        previous_rows = [
            row for row in rows
            if (date.today() - timedelta(days=13)).isoformat()
            <= str(_row_value(row, "day"))
            < (date.today() - timedelta(days=6)).isoformat()
        ]
    topic_counts = Counter(
        classify_comment(str(_row_value(row, "comment")))
        for row in current_rows
        if _row_value(row, "comment")
    )
    previous_counts = Counter(
        classify_comment(str(_row_value(row, "comment")))
        for row in previous_rows
        if _row_value(row, "comment")
    )
    topics = []
    for topic, count in topic_counts.most_common():
        if topic == "otros" or count < MIN_GROUP_SIZE:
            continue
        old_count = previous_counts[topic]
        previous_share = old_count / max(1, len(previous_rows))
        current_share = count / max(1, len(current_rows))
        change = round((current_share - previous_share) * 100)
        topics.append(
            {
                "id": topic,
                "label": topic.capitalize(),
                "count": count,
                "change_pct": change,
                "emerging": change >= 8 and count >= MIN_GROUP_SIZE,
                "sample_size": count,
                "confidence": confidence_label(count),
            }
        )
    cohort_counts = Counter(str(_row_value(row, "cohort", _row_value(row, "site"))) for row in current_rows)
    current_total = len(current_rows)
    representation = []
    for cohort in COHORTS:
        count = cohort_counts[cohort]
        observed_share = count / current_total if current_total else 0
        expected_share = normalized_population[cohort]
        ratio = observed_share / expected_share if expected_share else 0
        representation.append(
            {
                "cohort": cohort,
                "responses": count if count >= MIN_GROUP_SIZE else None,
                "expected_pct": round(expected_share * 100),
                "observed_pct": round(observed_share * 100) if count >= MIN_GROUP_SIZE else None,
                "status": (
                    "Baja" if count >= MIN_GROUP_SIZE and ratio < 0.7
                    else "Alta" if count >= MIN_GROUP_SIZE and ratio > 1.5
                    else "Equilibrada" if count >= MIN_GROUP_SIZE
                    else "Protegida"
                ),
            }
        )
    responses_by_channel = Counter(
        str(_row_value(row, "channel", "Móvil-QR")) for row in current_rows
    )
    return {
        "sample_size": current_total if current_total >= MIN_GROUP_SIZE else None,
        "confidence": confidence_label(current_total),
        "topics": topics,
        "representation": representation,
        "channels": [
            {"channel": channel, "responses": count}
            for channel, count in responses_by_channel.items()
            if count >= MIN_GROUP_SIZE
        ],
        "limits": [
            "Los temas se agrupan mediante palabras clave; no representan una interpretación perfecta del contexto.",
            "Las diferencias y tendencias son correlaciones observadas, no causas.",
            "No se muestran segmentos con menos de cinco respuestas.",
        ],
        "emerging_topics": [item["label"] for item in topics if item["emerging"]],
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def default_survey_questions(objective: str, cohort: str, language: str) -> list[dict]:
    is_catalan = language == "ca"
    if is_catalan:
        templates = [
            ("Com valores el teu benestar durant la jornada?", "rating", "bienestar", []),
            ("La informació sobre els canvis recents t'ha arribat amb claredat?", "yes_no", "comunicacion", []),
            ("Quin aspecte et facilitaria més la feina aquesta setmana?", "multiple_choice", "condiciones", ["Més informació", "Millors eines", "Més temps", "Més suport"]),
            ("Quin petit canvi ajudaria més el teu equip?", "open_text", "bienestar", []),
        ]
    elif language == "en":
        templates = [
            ("How would you rate your wellbeing during your shift?", "rating", "bienestar", []),
            ("Have recent changes been explained clearly?", "yes_no", "comunicacion", []),
            ("What would make your work easier this week?", "multiple_choice", "condiciones", ["Clearer information", "Better tools", "More time", "More support"]),
            ("What small change would help your team most?", "open_text", "bienestar", []),
        ]
    else:
        templates = [
            ("¿Cómo valorarías tu bienestar durante la jornada?", "rating", "bienestar", []),
            ("¿Te ha llegado con claridad la información sobre los cambios recientes?", "yes_no", "comunicacion", []),
            ("¿Qué aspecto facilitaría más tu trabajo esta semana?", "multiple_choice", "condiciones", ["Más información", "Mejores herramientas", "Más tiempo", "Más apoyo"]),
            ("¿Qué pequeño cambio ayudaría más a tu equipo?", "open_text", "bienestar", []),
        ]
    questions = [
        {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "type": kind,
            "dimension": dimension,
            "required": index < 3,
            "choices": choices,
            "logic": (
                {"show_when": {"question_index": 0, "operator": "less_than", "value": 3}}
                if index == 3 else None
            ),
        }
        for index, (text, kind, dimension, choices) in enumerate(templates)
    ]
    return questions


def generate_survey_draft(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RequestError("El formato de necesidad no es válido.")
    required = ("objective", "cohort", "context", "max_duration", "exclusions", "language", "channel")
    if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in required):
        raise RequestError("Completa la necesidad, colectivo, contexto, duración, exclusiones, idioma y canal.")
    if payload["cohort"] not in COHORTS and payload["cohort"] != "Todos":
        raise RequestError("Selecciona un colectivo válido.")
    try:
        duration = int(payload["max_duration"])
    except (ValueError, TypeError) as exc:
        raise RequestError("La duración debe ser un número de minutos.") from exc
    if duration < 1 or duration > 10:
        raise RequestError("La duración debe estar entre 1 y 10 minutos.")
    language = payload["language"]
    if language not in {"es", "ca", "en"}:
        raise RequestError("Selecciona español, catalán o inglés.")
    questions = default_survey_questions(payload["objective"], payload["cohort"], language)
    source = "plantilla"
    title = ""
    intro = ""
    with AI_CONFIG_LOCK:
        ai_config = dict(AI_CONFIG)
    if ai_config["api_key"]:
        prompt = {
            "objective": payload["objective"],
            "cohort": payload["cohort"],
            "context": payload["context"],
            "duration_minutes": duration,
            "avoid": payload["exclusions"],
            "language": language,
        }
        request_body = {
            "model": ai_config["model"],
            "temperature": 0.4,
            "max_tokens": 4096,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Diseña una microconsulta de clima laboral segura y accesible. Devuelve solo JSON con "
                        "title, intro y questions. Incluye exactamente 4 preguntas cortas en el idioma pedido. "
                        "Cada pregunta incluye text, type (rating, multiple_choice, open_text o yes_no), "
                        "dimension, required, choices y logic. Usa al menos una escala 1-5, una pregunta "
                        "opcional abierta con instrucciones de no incluir datos identificativos, y una lógica "
                        "condicional para mostrar la pregunta abierta solo si la primera escala es menor que 3. "
                        "No preguntes nombres, correos, teléfono, equipo pequeño ni datos personales. "
                        "Adapta vocabulario/canal a fábrica o logística cuando corresponda."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        }
        try:
            request = Request(
                ai_config["base_url"],
                data=json.dumps(request_body).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {ai_config['api_key']}"},
                method="POST",
            )
            with urlopen(request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
            content_str = extract_ai_content(result)
            generated = parse_ai_json(content_str)
            questions = validate_questions(generated.get("questions"))
            if not isinstance(generated.get("title"), str) or not isinstance(generated.get("intro"), str):
                raise ValueError("Missing survey title or intro.")
            title = generated["title"].strip()
            intro = generated["intro"].strip()
            source = "api"
        except HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
            print(f"[AI] Survey draft error {exc.code}: {detail}")
            raise RequestError(
                f"No se ha podido generar la consulta con IA ({exc.code}). Revisa la clave o inténtalo de nuevo.",
                502,
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError, RequestError) as exc:
            raise RequestError(
                "La IA no ha devuelto una consulta válida. Revisa la configuración o usa la plantilla.",
                502,
            ) from exc
    supplied_title = payload.get("title", "").strip() if isinstance(payload.get("title"), str) else ""
    supplied_intro = payload.get("intro", "").strip() if isinstance(payload.get("intro"), str) else ""
    title = supplied_title or title or {
        "es": "Un pulso para mejorar el día a día",
        "ca": "Un pols per millorar el dia a dia",
        "en": "A pulse to improve our day-to-day",
    }[language]
    intro = supplied_intro or intro or ({
        "es": "Queremos entender cómo está funcionando el cambio y qué podemos mejorar. Tu respuesta es anónima y solo mostraremos resultados agregados.",
        "ca": "Volem entendre com funciona el canvi i què podem millorar. La resposta és anònima i només mostrarem resultats agregats.",
        "en": "We want to understand how the change is working and what we can improve. Your response is anonymous and only aggregated results are shown.",
    }[language])
    return {
        "title": title,
        "intro": intro,
        "estimated_minutes": min(duration, max(1, round(len(questions) * 0.25))),
        "questions": questions,
        "source": source,
        "human_approval_required": True,
    }


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
    }
    if include_private:
        result.update(
            {
                "objective": row["objective"],
                "context": row["context"],
                "max_duration": row["max_duration"],
                "exclusions": row["exclusions"],
            }
        )
    if row["status"] == "published" or include_private:
        result["publish_url"] = row["publish_url"]
    result["typeform_connected"] = bool(row["typeform_id"])
    return result


def extract_ai_content(result: dict) -> str:
    """Extrae el texto de respuesta de Google Gemini (Google AI Studio) o endpoints compatibles."""
    if "candidates" in result and result["candidates"]:
        cand = result["candidates"][0]
        if "content" in cand and "parts" in cand["content"] and cand["content"]["parts"]:
            return cand["content"]["parts"][0].get("text", "")
    if "choices" in result and result["choices"]:
        choice = result["choices"][0]
        if "message" in choice and "content" in choice["message"]:
            return choice["message"]["content"] or ""
    raise ValueError("Formato de respuesta de IA no reconocido.")


def parse_ai_json(text: str) -> dict:
    """Limpia bloques de código markdown y parsea JSON estructurado de la IA."""
    raw = text.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return json.loads(raw.strip())


def refine_survey_with_ai(payload: object) -> dict:
    """Genera o actualiza 1, 2 o todas las preguntas de la microconsulta usando Google Gemini o fallback adaptativo."""
    if not isinstance(payload, dict):
        raise RequestError("El formato de la solicitud no es válido.")
    user_prompt = payload.get("user_prompt", "").strip()
    if not user_prompt:
        raise RequestError("Escribe una instrucción o tema para la IA.")

    current_questions = payload.get("current_questions", [])
    if not isinstance(current_questions, list):
        current_questions = []

    cohort = payload.get("cohort", "Planta")
    language = payload.get("language", "es")
    if language not in {"es", "ca", "en"}:
        language = "es"

    with AI_CONFIG_LOCK:
        ai_config = dict(AI_CONFIG)

    if ai_config["api_key"]:
        system_instruction = (
            "Eres el consultor senior de People & Culture de Estrella Damm para Fábrica, Logística, Comercial y Oficinas. "
            "Tu tarea es generar o actualizar preguntas de microconsultas según lo que el usuario escriba en lenguaje natural.\n\n"
            "INSTRUCCIONES CLAVE:\n"
            "1. Si el usuario pide crear una encuesta o no hay preguntas previas, genera entre 3 y 5 preguntas enfocadas en su solicitud.\n"
            "2. Si el usuario pide actualizar, cambiar o modificar una o más preguntas concretas (ej: 'cambia la pregunta 2', 'actualiza la 1 y la 3', 'haz la pregunta 2 sobre horarios'), "
            "actualiza EXACTAMENTE esas preguntas y mantén el resto de preguntas existentes sin modificarlas.\n"
            "3. Si el usuario pide actualizar o cambiar todas las preguntas o el enfoque global, actualiza todas.\n"
            "4. Adapta el lenguaje al colectivo indicado (en Fábrica y Logística: tono cercano, directo, sin jerga de oficina).\n"
            "5. Responde ÚNICAMENTE con un JSON con este formato exacto:\n"
            "{\n"
            '  "synthesis": "Breve explicación de 1 o 2 frases sobre qué cambios has aplicado.",\n'
            '  "questions": [\n'
            '    {\n'
            '      "title": "¿Texto claro de la pregunta?",\n'
            '      "dimension": "Dimensión (Bienestar, Organización, Liderazgo, etc.)",\n'
            '      "description": "Explicación breve o instrucción",\n'
            '      "choices": ["Opción A", "Opción B", "Opción C", "Opción D"]\n'
            '    }\n'
            '  ],\n'
            '  "updated_indices": [0, 1]\n'
            "}\n"
            "6. CERO DATOS PERSONALES: Nunca preguntes nombres, correos ni datos identificativos."
        )

        user_message = {
            "peticion_usuario": user_prompt,
            "colectivo": cohort,
            "idioma": language,
            "preguntas_actuales": current_questions,
        }

        request_body = {
            "model": ai_config["model"],
            "temperature": 0.3,
            "max_tokens": 4096,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": json.dumps(user_message, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            req = Request(
                ai_config["base_url"],
                data=json.dumps(request_body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {ai_config['api_key']}",
                },
                method="POST",
            )
            with urlopen(req, timeout=25) as resp:
                raw_json = json.loads(resp.read().decode("utf-8"))
            content = extract_ai_content(raw_json)
            result = parse_ai_json(content)
            if isinstance(result, dict) and "questions" in result and isinstance(result["questions"], list):
                return {
                    "source": "gemini",
                    "synthesis": result.get("synthesis", "Microconsulta adaptada según tus indicaciones."),
                    "questions": result["questions"],
                    "updated_indices": result.get("updated_indices", list(range(len(result["questions"])))),
                }
        except Exception as exc:
            print(f"[Gemini Refine Error] {exc}")

    # Fallback local cuando no hay API Key o si la llamada no responde
    lower = user_prompt.lower()
    new_questions = [dict(q) for q in current_questions] if current_questions else []

    target_idx = None
    if "pregunta 1" in lower or "la 1" in lower or "primera" in lower:
        target_idx = 0
    elif "pregunta 2" in lower or "la 2" in lower or "segunda" in lower:
        target_idx = 1
    elif "pregunta 3" in lower or "la 3" in lower or "tercera" in lower:
        target_idx = 2
    elif "pregunta 4" in lower or "la 4" in lower or "cuarta" in lower:
        target_idx = 3
    elif "pregunta 5" in lower or "la 5" in lower or "quinta" in lower:
        target_idx = 4

    if target_idx is not None and target_idx < len(new_questions):
        new_questions[target_idx]["title"] = f"¿Cómo valoras el aspecto de {user_prompt.capitalize()} en tu puesto?"
        new_questions[target_idx]["description"] = f"Actualizado según tu indicación: {user_prompt}."
        updated_indices = [target_idx]
        synthesis = f"He actualizado específicamente la pregunta {target_idx + 1} incorporando: '{user_prompt}'."
    else:
        new_questions = [
            {
                "dimension": "Bienestar y Jornada",
                "title": f"¿Cómo impacta {user_prompt} en tu bienestar diario en Estrella Damm?",
                "description": "Tu respuesta nos ayuda a priorizar acciones de mejora.",
                "choices": ["Impacto muy positivo", "Normal / Adecuado", "Genera fricción o cansancio", "Requiere revisión urgente"],
            },
            {
                "dimension": "Recursos y Operación",
                "title": f"¿Dispones de los medios, tiempo e información necesarios sobre {user_prompt}?",
                "description": "Recursos materiales y organizativos en tu puesto.",
                "choices": ["Totalmente suficientes", "Suficientes la mayoría de días", "A veces insuficientes", "Carencia crítica"],
            },
            {
                "dimension": "Comunicación y Equipo",
                "title": f"¿Sientes que tus aportaciones sobre {user_prompt} son tomadas en cuenta por tus supervisores?",
                "description": "Canales de diálogo entre cuadrillas y responsables.",
                "choices": ["Completamente escuchadas", "Se escuchan con demora", "Raramente comentadas", "Sin canal de comunicación"],
            },
        ]
        updated_indices = [0, 1, 2]
        synthesis = f"He generado una batería completa de preguntas adaptada a: '{user_prompt}' para el colectivo de {cohort}."

    return {
        "source": "demo_local",
        "synthesis": synthesis,
        "questions": new_questions,
        "updated_indices": updated_indices,
    }


def create_survey_draft(payload: object) -> dict:
    generated = generate_survey_draft(payload)
    survey_id = uuid.uuid4().hex
    now = _now_iso()
    population = payload.get("population", POPULATION_SHARES)
    if (
        not isinstance(population, dict)
        or set(population) != set(COHORTS)
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in population.values()
        )
        or sum(population.values()) <= 0
    ):
        raise RequestError("Define porcentajes de población válidos para los cinco colectivos.")
    raw_frequency = payload.get("frequency_days", 7)
    if isinstance(raw_frequency, bool) or not isinstance(raw_frequency, (str, int)):
        raise RequestError("La frecuencia debe estar entre 1 y 365 días.")
    try:
        frequency_days = int(raw_frequency)
    except ValueError as exc:
        raise RequestError("La frecuencia debe estar entre 1 y 365 días.") from exc
    if not 1 <= frequency_days <= 365:
        raise RequestError("La frecuencia debe estar entre 1 y 365 días.")
    population_total = sum(population.values())
    population = {key: value / population_total for key, value in population.items()}
    with connect() as db:
        db.execute(
            """INSERT INTO surveys
               (id, created_at, updated_at, objective, cohort, context, max_duration,
                exclusions, language, channel, title, intro, questions_json, status,
                frequency_days, population_json, followup_of)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)""",
            (
                survey_id,
                now,
                now,
                payload["objective"].strip(),
                payload["cohort"],
                payload["context"].strip(),
                int(payload["max_duration"]),
                payload["exclusions"].strip(),
                payload["language"],
                payload["channel"],
                generated["title"],
                generated["intro"],
                json.dumps(generated["questions"], ensure_ascii=False),
                frequency_days,
                json.dumps(population),
                str(payload.get("followup_of", "")),
            ),
        )
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    return {**_survey_dict(row, include_private=True), "source": generated["source"]}


def get_survey(survey_id: str, public_only: bool = False) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    if row is None or (public_only and row["status"] != "published"):
        raise RequestError("No se ha encontrado esta consulta publicada.", 404)
    return _survey_dict(row, include_private=not public_only)


def list_surveys() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM surveys ORDER BY created_at DESC LIMIT 50").fetchall()
    return [_survey_dict(row, include_private=True) for row in rows]


def update_survey(survey_id: str, payload: object) -> dict:
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
        questions = validate_questions(payload.get("questions", json.loads(row["questions_json"])))
        db.execute(
            """UPDATE surveys SET title = ?, intro = ?, questions_json = ?,
               updated_at = ? WHERE id = ?""",
            (title.strip(), intro.strip(), json.dumps(questions, ensure_ascii=False), _now_iso(), survey_id),
        )
        updated = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    return _survey_dict(updated, include_private=True)


def approve_survey(survey_id: str) -> dict:
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
        detail = exc.read(800).decode("utf-8", errors="replace")
        raise RequestError(
            f"Typeform ha respondido con un error ({exc.code}). Revisa el token y los permisos.",
            502,
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RequestError("No se pudo conectar con Typeform.", 502) from exc


def _typeform_form_payload(survey: dict) -> dict:
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


def publish_survey(survey_id: str, public_base_url: str = "") -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if row is None:
            raise RequestError("No se ha encontrado esta consulta.", 404)
        if row["status"] != "approved" or not row["approved_at"]:
            raise RequestError("La consulta necesita aprobación humana antes de publicarse.")
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
        response = _typeform_request(
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


def publish_survey_to_typeform_direct(payload: object, public_base_url: str = "") -> dict:
    """
    Publica directamente una microconsulta desde el Generador Asistido / Typeform Studio.
    Crea el borrador, valida la estructura de preguntas, aprueba y publica
    en Typeform (o localmente con QR si no hay token de Typeform configurado).
    """
    if not isinstance(payload, dict):
        raise RequestError("El formato de consulta no es válido.")

    clean_payload = {
        "objective": str(payload.get("objective", "Medir clima laboral en planta")).strip() or "Medir clima laboral en planta",
        "cohort": str(payload.get("cohort", "Planta")),
        "context": str(payload.get("context", "Planta El Prat y centros operativos")).strip() or "Planta El Prat y centros operativos",
        "max_duration": str(payload.get("max_duration", "2")),
        "exclusions": str(payload.get("exclusions", "Sin datos personales ni nombres")).strip() or "Sin datos personales",
        "language": str(payload.get("language", "es")),
        "channel": str(payload.get("channel", "Móvil-QR")),
        "title": str(payload.get("title", "Pulso de Clima Laboral Estrella Damm")).strip(),
        "intro": str(payload.get("intro", "Tu respuesta es 100% anónima.")).strip(),
    }
    if clean_payload["cohort"] not in COHORTS and clean_payload["cohort"] != "Todos":
        clean_payload["cohort"] = "Planta"

    draft = create_survey_draft(clean_payload)
    survey_id = draft["id"]

    if "questions" in payload and isinstance(payload["questions"], list) and len(payload["questions"]) >= 3:
        try:
            questions = validate_questions(payload["questions"])
            with connect() as db:
                db.execute(
                    "UPDATE surveys SET questions_json = ?, title = ?, intro = ? WHERE id = ?",
                    (
                        json.dumps(questions, ensure_ascii=False),
                        clean_payload["title"],
                        clean_payload["intro"],
                        survey_id,
                    ),
                )
        except Exception:
            pass

    approve_survey(survey_id)
    return publish_survey(survey_id, public_base_url=public_base_url)


def import_typeform_responses(survey_id: str) -> dict:
    survey = get_survey(survey_id)
    with connect() as db:
        row = db.execute("SELECT typeform_id FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    if not row["typeform_id"]:
        raise RequestError("Esta consulta aún no está conectada a un formulario de Typeform.")
    imported = 0
    next_url = f"https://api.typeform.com/forms/{row['typeform_id']}/responses?page_size=1000"
    while next_url:
        if urlparse(next_url).scheme != "https" or urlparse(next_url).hostname != "api.typeform.com":
            raise RequestError("Typeform devolvió un enlace de paginación no válido.", 502)
        result = _typeform_request("GET", next_url)
        for item in result.get("items", []):
            response_id = item.get("token")
            if not isinstance(response_id, str) or not response_id:
                continue
            answers = item.get("answers", [])
            by_ref = {answer.get("field", {}).get("ref"): answer for answer in answers}
            cohort = survey["cohort"]
            if cohort == "Todos":
                cohort_answer = by_ref.get("cohort", {}).get("choice", {}).get("label")
                if cohort_answer not in COHORTS:
                    continue
                cohort = cohort_answer
            score = 0
            comment = ""
            for question in survey["questions"]:
                answer = by_ref.get(question["id"], {})
                if question["type"] == "rating":
                    score = int(answer.get("number", 0) or 0)
                elif question["type"] == "open_text":
                    comment = sanitize_comment(answer.get("text", ""))
            if not 1 <= score <= 5:
                continue
            with connect() as db:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO responses (day, dimension, site, score, cohort, channel,
                       comment, survey_id, period, typeform_response_id)
                       VALUES (?, ?, ?, ?, ?, 'Typeform', ?, ?, ?, ?)
                    """,
                    (
                        date.today().isoformat(),
                        "bienestar",
                        COHORT_LABELS[cohort],
                        score,
                        cohort,
                        comment,
                        survey_id,
                        "actual",
                        response_id,
                    ),
                )
            imported += cursor.rowcount
        next_url = result.get("_links", {}).get("next", "")
    return {"imported": imported}


def save_survey_response(payload: object) -> dict:
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
    if score is None:
        score = 3
    with connect() as db:
        db.execute(
            """INSERT INTO responses (day, dimension, site, score, cohort, channel,
               comment, survey_id, period) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'actual')""",
            (
                date.today().isoformat(),
                survey["questions"][0]["dimension"],
                COHORT_LABELS[cohort],
                score,
                cohort,
                survey["channel"],
                comment,
                survey_id,
            ),
        )
    return {"received": True, "message": "Gracias por compartir tu pulso."}


def create_recommendations(insights: dict) -> list[dict]:
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


def clean_question(text: str) -> str:
    question = text.strip().splitlines()[0].strip().strip("\"'“”‘’` ")
    if len(question) > 240:
        question = question[:240].rsplit(" ", 1)[0].rstrip(" ,;:")
    if not question or len(question) < 12:
        raise RequestError("La API no ha devuelto una pregunta utilizable.", 502)
    if not question.endswith(("?", "？")):
        question = question.rstrip(".! ") + "?"
    return question


def generate_question(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RequestError("El formato de la solicitud no es válido.")
    dimension = payload.get("dimension")
    site = payload.get("site")
    if (
        not isinstance(dimension, str)
        or dimension not in DIMENSION_IDS
        or not isinstance(site, str)
        or site not in SITES
    ):
        raise RequestError("Selecciona una dimensión y un entorno válidos.")

    with AI_CONFIG_LOCK:
        config = dict(AI_CONFIG)
    if not config["api_key"]:
        return {
            "question": DEMO_QUESTIONS[dimension],
            "source": "demo",
            "model": None,
        }

    request_body = {
        "model": config["model"],
        "temperature": 0.7,
        "max_tokens": 4096,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres especialista en escucha organizacional. Escribe una única "
                    "pregunta breve, humana, inclusiva, neutral y no invasiva en español. "
                    "Debe medir la dimensión indicada, poder responderse en una escala "
                    "de 1 a 5 y funcionar durante una jornada de trabajo. Evita datos "
                    "personales, preguntas dobles y lenguaje de oficina si no aplica. "
                    "Devuelve solo la pregunta."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Dimensión: {next(d['label'] for d in DIMENSIONS if d['id'] == dimension)}. "
                    f"Entorno: {site}. Genera una pregunta distinta a una encuesta anual."
                ),
            },
        ],
    }
    request = Request(
        config["base_url"],
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        try:
            message = json.loads(detail)["error"]["message"]
        except (json.JSONDecodeError, KeyError, TypeError):
            message = detail
        print(f"[AI] Error {exc.code}: {message}")
        raise RequestError(
            f"La API ha respondido con un error ({exc.code}): {message}", 502
        ) from exc
    try:
        text = extract_ai_content(result)
        if not isinstance(text, str):
            raise TypeError("El contenido de la respuesta no es texto.")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RequestError("La respuesta de la API tiene un formato inesperado.", 502) from exc
    return {
        "question": clean_question(text),
        "source": "api",
        "model": config["model"],
    }


def update_ai_config(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RequestError("El formato de configuración no es válido.")
    api_key = payload.get("api_key", "")
    raw_base_url = payload.get("base_url")
    raw_model = payload.get("model")
    typeform_token = payload.get("typeform_token", "")
    if not isinstance(api_key, str) or not isinstance(typeform_token, str):
        raise RequestError("Las claves de API deben ser texto.")
    if raw_base_url is not None and not isinstance(raw_base_url, str):
        raise RequestError("El endpoint y el modelo deben ser texto.")
    if raw_model is not None and not isinstance(raw_model, str):
        raise RequestError("El endpoint y el modelo deben ser texto.")
    with AI_CONFIG_LOCK:
        if api_key.strip():
            AI_CONFIG["api_key"] = api_key.strip()
            os.environ["UTH_KEY"] = api_key.strip()
        if raw_base_url is not None:
            base_url = raw_base_url.strip()
            parsed = urlparse(base_url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                raise RequestError("El endpoint debe ser una URL HTTP o HTTPS válida.")
            AI_CONFIG["base_url"] = base_url
            os.environ["AI_BASE_URL"] = base_url
        if raw_model is not None:
            model = raw_model.strip()
            if not model or len(model) > 120:
                raise RequestError("Introduce el identificador del modelo.")
            AI_CONFIG["model"] = model
            os.environ["AI_MODEL"] = model
    if typeform_token.strip():
        with TYPEFORM_LOCK:
            TYPEFORM_CONFIG["token"] = typeform_token.strip()
    with AI_CONFIG_LOCK:
        ai_configured = bool(AI_CONFIG["api_key"])
        model_name = AI_CONFIG["model"]
        base_url_val = AI_CONFIG["base_url"]
    with TYPEFORM_LOCK:
        tf_configured = bool(TYPEFORM_CONFIG["token"])
    return {
        "ai_configured": ai_configured,
        "typeform_configured": tf_configured,
        "model": model_name,
        "base_url": base_url_val,
    }


def migrate_legacy_database(
    legacy_database: Path = LEGACY_DATABASE, database: Path = DATABASE
) -> None:
    if "PULSO_DATABASE" in os.environ or database.exists() or not legacy_database.exists():
        return
    database.parent.mkdir(parents=True, exist_ok=True)
    legacy_database.replace(database)


# ============================================================================
# SEPARACIÓN DE CLASES POR VENTANA / PANEL (ARQUITECTURA MODULAR)
# ============================================================================

class DashboardPanel:
    """Gestiona la lógica y datos del panel de Clima Laboral."""
    @staticmethod
    def get_data() -> dict:
        return get_dashboard()

    @staticmethod
    def submit_pulse(payload: object) -> dict:
        return save_response(payload)


class InsightsPanel:
    """Gestiona la computación matemática determinista y síntesis de Insights."""
    @staticmethod
    def get_metrics() -> dict:
        return get_statistical_engine_payload()

    @staticmethod
    def generate_llm_synthesis() -> dict:
        payload_stats = get_statistical_engine_payload()
        stats_json_str = json.dumps(payload_stats, ensure_ascii=False)
        with AI_CONFIG_LOCK:
            cfg = dict(AI_CONFIG)
        if request_llm_insights:
            return request_llm_insights(
                stats_json_str,
                api_key=cfg.get("api_key"),
                base_url=cfg.get("base_url"),
                model=cfg.get("model")
            )
        return {"modo": "demo", "payload_estadistico_utilizado": payload_stats}


def generate_teams_adaptive_card(survey: dict, base_url: str = "") -> dict:
    """Genera la Adaptive Card oficial de Microsoft Teams para el despliegue corporativo M365."""
    survey_url = survey.get("publish_url") or f"{base_url}/respond/{survey.get('id', '')}"
    title = survey.get("title") or "Microconsulta de Clima Laboral"
    cohort = survey.get("cohort") or "Todos los colectivos"
    est_min = survey.get("estimated_minutes") or 2
    intro = survey.get("intro") or "Tu feedback es 100% anónimo y ayuda a mejorar el día a día en Estrella Damm."

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "⭐ ESTRELLA DAMM • PULSO",
                            "weight": "Bolder",
                            "color": "Attention",
                            "size": "Small"
                        },
                        {
                            "type": "TextBlock",
                            "text": title,
                            "weight": "Bolder",
                            "size": "Medium",
                            "wrap": True
                        },
                        {
                            "type": "TextBlock",
                            "text": intro,
                            "wrap": True,
                            "spacing": "Small"
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "🏢 Colectivo:", "value": str(cohort)},
                                {"title": "⏱️ Duración:", "value": f"Menos de {est_min} minutos"},
                                {"title": "🔒 Privacidad:", "value": "100% Anónimo (k-anonimato ≥ 5)"},
                                {"title": "📱 Canales:", "value": "Móvil / Código QR / Microsoft Teams"}
                            ]
                        }
                    ],
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "👉 Responder Microconsulta",
                            "url": survey_url,
                            "style": "positive"
                        }
                    ]
                }
            }
        ]
    }


def send_teams_notification(payload: object, base_url: str = "") -> dict:
    """Simula o envía la notificación a un canal de Microsoft Teams vía Webhook."""
    if not isinstance(payload, dict):
        raise RequestError("El formato de solicitud de Teams no es válido.")
    survey_id = payload.get("survey_id")
    webhook_url = payload.get("webhook_url", "").strip() or os.environ.get("TEAMS_WEBHOOK_URL", "").strip()

    survey = {}
    if survey_id and isinstance(survey_id, str):
        try:
            survey = get_survey(survey_id, public_only=False)
        except Exception:
            pass
    if not survey:
        survey = {
            "id": survey_id or "",
            "title": payload.get("title", "Pulso Operativo: Clima y Turnos"),
            "cohort": payload.get("cohort", "Planta"),
            "estimated_minutes": payload.get("estimated_minutes", 2),
            "intro": payload.get("intro", "Tu opinión es clave para adaptar los turnos y descansos en fábrica."),
            "publish_url": payload.get("publish_url", f"{base_url}/")
        }

    card = generate_teams_adaptive_card(survey, base_url)
    sent = False
    if webhook_url:
        try:
            req = Request(
                webhook_url,
                data=json.dumps(card).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urlopen(req, timeout=10) as resp:
                sent = resp.status in (200, 202)
        except Exception as exc:
            print(f"[Teams Webhook Error] {exc}")

    return {
        "status": "sent" if sent else "simulated",
        "webhook_configured": bool(webhook_url),
        "adaptive_card": card,
        "message": "Notificación enviada a Microsoft Teams." if sent else "Adaptive Card M365 generada con éxito (modo simulado)."
    }


class GeneratorPanel:
    """Gestiona la generación asistida con IA y publicación directa en Typeform."""
    @staticmethod
    def generate_question(payload: object) -> dict:
        return generate_question(payload)

    @staticmethod
    def create_draft(payload: object) -> dict:
        return create_survey_draft(payload)

    @staticmethod
    def publish_direct(payload: object, base_url: str = "") -> dict:
        return publish_survey_to_typeform_direct(payload, public_base_url=base_url)

    @staticmethod
    def refine_survey(payload: object) -> dict:
        return refine_survey_with_ai(payload)

    @staticmethod
    def generate_teams_card(payload: object, base_url: str = "") -> dict:
        return send_teams_notification(payload, base_url=base_url)


class AdminPanel:
    """Gestiona el panel de Historial de Consultas, Validaciones y Acciones de RRHH."""
    @staticmethod
    def get_admin_data() -> dict:
        return get_admin_data()

    @staticmethod
    def approve_survey(survey_id: str) -> dict:
        return approve_survey(survey_id)

    @staticmethod
    def publish_survey(survey_id: str, base_url: str = "") -> dict:
        return publish_survey(survey_id, public_base_url=base_url)

    @staticmethod
    def review_action(action_id: int, payload: object) -> dict:
        return review_action(action_id, payload)

    @staticmethod
    def import_responses(survey_id: str) -> dict:
        return import_typeform_responses(survey_id)


class SettingsPanel:
    """Gestiona las conexiones corporativas (Google Gemini y Typeform)."""
    @staticmethod
    def get_status() -> dict:
        with AI_CONFIG_LOCK:
            configured = bool(AI_CONFIG["api_key"])
            model = AI_CONFIG.get("model", "ornith")
            base_url = AI_CONFIG.get("base_url", "https://dcc-llm-gw.uab.cat/uabthehack/v1/chat/completions")
        with TYPEFORM_LOCK:
            typeform_configured = bool(TYPEFORM_CONFIG["token"])
        return {
            "api_configured": configured,
            "typeform_configured": typeform_configured,
            "model": model,
            "base_url": base_url,
            "demo": not configured,
        }

    @staticmethod
    def update_settings(payload: object) -> dict:
        return update_ai_config(payload)


class PulsoHandler(BaseHTTPRequestHandler):
    server_version = "Pulso/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestError("La solicitud no tiene una longitud válida.") from exc
        if length <= 0 or length > 16_384:
            raise RequestError("El contenido de la solicitud no es válido.", 413)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError("El contenido JSON no es válido.") from exc

    def send_file(self, filename: str, content_type: str) -> None:
        body = (UI_DIR / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            self.send_json(200, DashboardPanel.get_data())
            return
        if path == "/api/admin":
            self.send_json(200, AdminPanel.get_admin_data())
            return
        if path == "/api/settings":
            self.send_json(200, SettingsPanel.get_status())
            return
        qr_match = re.fullmatch(r"/api/surveys/([a-f0-9]{32})/qr\.svg", path)
        if qr_match:
            try:
                survey = get_survey(qr_match.group(1), public_only=False)
                target_url = survey.get("publish_url") or f"{self._public_base_url()}/respond/{survey['id']}"
                try:
                    import qrcode
                    import qrcode.image.svg

                    qr = qrcode.QRCode(
                        version=None,
                        error_correction=qrcode.constants.ERROR_CORRECT_M,
                        box_size=6,
                        border=4,
                    )
                    qr.add_data(target_url)
                    qr.make(fit=True)
                    image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
                    buffer = BytesIO()
                    image.save(buffer)
                    body = buffer.getvalue()
                except Exception:
                    # SVG fallback garantizado que nunca se rompe en el navegador
                    body = (
                        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">'
                        f'<rect width="200" height="200" fill="#f8f9fa" rx="12"/>'
                        f'<rect x="20" y="20" width="50" height="50" fill="#1b1c1b" rx="4"/>'
                        f'<rect x="30" y="30" width="30" height="30" fill="#ffffff" rx="2"/>'
                        f'<rect x="38" y="38" width="14" height="14" fill="#1b1c1b"/>'
                        f'<rect x="130" y="20" width="50" height="50" fill="#1b1c1b" rx="4"/>'
                        f'<rect x="140" y="30" width="30" height="30" fill="#ffffff" rx="2"/>'
                        f'<rect x="148" y="38" width="14" height="14" fill="#1b1c1b"/>'
                        f'<rect x="20" y="130" width="50" height="50" fill="#1b1c1b" rx="4"/>'
                        f'<rect x="30" y="140" width="30" height="30" fill="#ffffff" rx="2"/>'
                        f'<rect x="38" y="148" width="14" height="14" fill="#1b1c1b"/>'
                        f'<rect x="85" y="85" width="30" height="30" fill="#c4161c" rx="4"/>'
                        f'<text x="100" y="105" font-size="16" font-family="sans-serif" text-anchor="middle" fill="#ffffff">★</text>'
                        f'<text x="100" y="175" font-size="10" font-family="sans-serif" font-weight="bold" text-anchor="middle" fill="#5c403d">Pulso Damm QR</text>'
                        f'</svg>'
                    ).encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except RequestError as exc:
                self.send_json(exc.status, {"error": str(exc)})
            return
        survey_match = re.fullmatch(r"/api/surveys/([a-f0-9]{32})", path)
        if survey_match:
            try:
                self.send_json(200, get_survey(survey_match.group(1), public_only=True))
            except RequestError as exc:
                self.send_json(exc.status, {"error": str(exc)})
            return
        if path == "/api/status":
            self.send_json(200, SettingsPanel.get_status())
            return
        if path == "/api/stats/engine":
            self.send_json(200, InsightsPanel.get_metrics())
            return
        files = {
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/assets/codigo_qr.png": ("assets/codigo_qr.png", "image/png"),
            "/admin": ("admin.html", "text/html; charset=utf-8"),
            "/admin/": ("admin.html", "text/html; charset=utf-8"),
            "/admin.js": ("admin.js", "text/javascript; charset=utf-8"),
            "/admin.css": ("admin.css", "text/css; charset=utf-8"),
            "/generator": ("generator.html", "text/html; charset=utf-8"),
            "/generator/": ("generator.html", "text/html; charset=utf-8"),
            "/generator.html": ("generator.html", "text/html; charset=utf-8"),
            "/studio": ("generator.html", "text/html; charset=utf-8"),
            "/studio/": ("generator.html", "text/html; charset=utf-8"),
            "/studio.html": ("generator.html", "text/html; charset=utf-8"),
        }
        if path == "/":
            path = "/index.html"
        if re.fullmatch(r"/respond/[a-f0-9]{32}", path):
            self.send_file("index.html", "text/html; charset=utf-8")
            return
        if path not in files:
            self.send_json(404, {"error": "No se ha encontrado esta página."})
            return
        filename, content_type = files[path]
        self.send_file(filename, content_type)

    def _public_base_url(self) -> str:
        configured_url = os.environ.get("PULSO_PUBLIC_URL", "").strip()
        if configured_url:
            return configured_url.rstrip("/")
        host = self.headers.get("Host", "")
        if not re.fullmatch(r"[A-Za-z0-9.:[\]-]+(?::\d{1,5})?", host):
            return ""
        return f"http://{host}"

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/question":
                self.send_json(200, GeneratorPanel.generate_question(payload))
            elif path == "/api/generator/refine":
                self.send_json(200, GeneratorPanel.refine_survey(payload))
            elif path == "/api/pulse":
                self.send_json(201, DashboardPanel.submit_pulse(payload))
            elif path == "/api/settings":
                self.send_json(200, SettingsPanel.update_settings(payload))
            elif path == "/api/surveys":
                self.send_json(201, GeneratorPanel.create_draft(payload))
            elif path == "/api/surveys/publish-direct":
                self.send_json(201, GeneratorPanel.publish_direct(payload, self._public_base_url()))
            elif path == "/api/actions":
                self.send_json(201, approve_recommendation(payload))
            elif path == "/api/responses":
                self.send_json(201, save_survey_response(payload))
            elif path == "/api/insights/llm":
                self.send_json(200, InsightsPanel.generate_llm_synthesis())
            elif path == "/api/integrations/teams":
                self.send_json(200, GeneratorPanel.generate_teams_card(payload, self._public_base_url()))
            else:
                route = re.fullmatch(r"/api/surveys/([a-f0-9]{32})/(approve|publish|import)", path)
                action_route = re.fullmatch(r"/api/actions/(\d+)(?:/(followup))?", path)
                update_route = re.fullmatch(r"/api/surveys/([a-f0-9]{32})/edit", path)
                if route:
                    survey_id, action = route.groups()
                    if action == "approve":
                        self.send_json(200, approve_survey(survey_id))
                    elif action == "publish":
                        self.send_json(200, publish_survey(survey_id, self._public_base_url()))
                    else:
                        self.send_json(200, import_typeform_responses(survey_id))
                elif action_route:
                    action_id, followup = action_route.groups()
                    if followup:
                        self.send_json(201, create_followup_survey(int(action_id)))
                    else:
                        self.send_json(200, review_action(int(action_id), payload))
                elif update_route:
                    self.send_json(200, update_survey(update_route.group(1), payload))
                else:
                    self.send_json(404, {"error": "No se ha encontrado esta acción."})
        except RequestError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except (OSError, sqlite3.Error, ValueError, TypeError):
            self.send_json(500, {"error": "No se pudo guardar la respuesta."})


def main() -> None:
    migrate_legacy_database()
    with connect() as db:
        seed_demo_data(db)
    server = ThreadingHTTPServer((HOST, PORT), PulsoHandler)
    print(f"Pulso está listo en http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando Pulso.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
