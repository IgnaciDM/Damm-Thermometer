from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("PULSO_DATABASE", ROOT / "pulso.sqlite3"))
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
    "api_key": os.environ.get("AI_API_KEY", ""),
    "base_url": os.environ.get(
        "AI_BASE_URL", "https://api.openai.com/v1/chat/completions"
    ),
    "model": os.environ.get("AI_MODEL", "gpt-4o-mini"),
}
AI_CONFIG_LOCK = threading.Lock()


class RequestError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
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
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def seed_demo_data(db: sqlite3.Connection) -> None:
    if db.execute("SELECT COUNT(*) FROM responses").fetchone()[0]:
        return
    rng = random.Random(2026)
    baselines = [4.1, 3.7, 3.4, 4.0, 3.8, 3.5]
    start = date.today() - timedelta(days=13)
    demo_rows = []
    for day_offset in range(14):
        day = start + timedelta(days=day_offset)
        for index in range(6):
            dimension = DIMENSIONS[(day_offset + index) % len(DIMENSIONS)]["id"]
            dimension_index = next(
                i for i, item in enumerate(DIMENSIONS) if item["id"] == dimension
            )
            score = max(1, min(5, round(rng.gauss(baselines[dimension_index], 0.75))))
            demo_rows.append(
                (
                    day.isoformat(),
                    dimension,
                    SITES[(day_offset + index) % len(SITES)],
                    score,
                    1,
                )
            )
    db.executemany(
        "INSERT INTO responses (day, dimension, site, score, is_demo) VALUES (?, ?, ?, ?, ?)",
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
        rows = db.execute(
            "SELECT day, dimension, site, score, is_demo FROM responses ORDER BY day"
        ).fetchall()
    return aggregate_responses(rows)


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
        db.execute("DELETE FROM responses WHERE is_demo = 1")
        db.execute(
            "INSERT INTO responses (day, dimension, site, score, is_demo) VALUES (?, ?, ?, ?, 0)",
            (date.today().isoformat(), dimension, site, score),
        )
        db.commit()
    return get_dashboard()


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
        "max_tokens": 90,
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
        raise RequestError(
            f"La API ha respondido con un error ({exc.code}). Revisa la clave y el modelo.",
            502,
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RequestError(
            "No se pudo conectar con la API. Comprueba el endpoint y la conexión.",
            502,
        ) from exc

    try:
        text = result["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("El contenido de la respuesta no es texto.")
    except (KeyError, IndexError, TypeError) as exc:
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
    raw_base_url = payload.get("base_url", "")
    raw_model = payload.get("model", "")
    if not isinstance(api_key, str) or not api_key.strip():
        raise RequestError("Introduce una clave de API válida.")
    if not isinstance(raw_base_url, str) or not isinstance(raw_model, str):
        raise RequestError("El endpoint y el modelo deben ser texto.")
    base_url = raw_base_url.strip()
    model = raw_model.strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise RequestError("El endpoint debe ser una URL HTTP o HTTPS válida.")
    if not isinstance(model, str) or not model or len(model) > 120:
        raise RequestError("Introduce el identificador del modelo.")
    with AI_CONFIG_LOCK:
        AI_CONFIG.update(
            {"api_key": api_key.strip(), "base_url": base_url, "model": model}
        )
    return {"configured": True, "model": model}


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

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            self.send_json(200, get_dashboard())
            return
        if path == "/api/status":
            with AI_CONFIG_LOCK:
                configured = bool(AI_CONFIG["api_key"])
                model = AI_CONFIG["model"] if configured else None
            self.send_json(
                200, {"api_configured": configured, "model": model, "demo": not configured}
            )
            return
        if path == "/":
            path = "/index.html"
        files = {
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if path not in files:
            self.send_json(404, {"error": "No se ha encontrado esta página."})
            return
        filename, content_type = files[path]
        body = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/question":
                self.send_json(200, generate_question(payload))
            elif path == "/api/pulse":
                self.send_json(201, save_response(payload))
            elif path == "/api/settings":
                self.send_json(200, update_ai_config(payload))
            else:
                self.send_json(404, {"error": "No se ha encontrado esta acción."})
        except RequestError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except (OSError, sqlite3.Error):
            self.send_json(500, {"error": "No se pudo guardar la respuesta."})


def main() -> None:
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
