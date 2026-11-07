"""
Punto de Entrada y Router HTTP de Pulso Estrella Damm.
Arquitectura orientada a objetos y separación limpia de módulos:
- backend/dashboard.py: Métricas de clima, agregación y preservación de privacidad (k-anonimato).
- backend/insights.py: Motor estadístico, tendencias, cohorte vulnerable y síntesis con IA.
- backend/generator.py: Generador asistido con DCC LLM / Gemini, Typeform Studio y Microsoft Teams.
- backend/admin.py: Historial de consultas, validación humana, seguimiento y códigos QR.
- backend/settings.py: Conectores a modelos de lenguaje y APIs corporativas.
- backend/common.py: Base de datos SQLite, esquemas, sanitización y constantes de dominio.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Asegurar rutas y resolución de paquete para ejecución directa: `python backend/app.py`
_CURRENT_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _CURRENT_DIR.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))
if str(_CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CURRENT_DIR))

if __package__ in (None, ""):
    __package__ = "backend"

# Re-exportaciones para compatibilidad y acceso centralizado
from .common import (  # noqa: F401
    AI_CONFIG,
    AI_CONFIG_LOCK,
    BASE_DIR,
    COHORTS,
    COHORT_LABELS,
    DATA_DIR,
    DATABASE,
    DEMO_QUESTIONS,
    DIMENSIONS,
    DIMENSION_IDS,
    HOST,
    LEGACY_DATABASE,
    MIN_GROUP_SIZE,
    POPULATION_SHARES,
    PORT,
    RETENTION_DAYS,
    SITES,
    TYPEFORM_CONFIG,
    TYPEFORM_LOCK,
    UI_DIR,
    RequestError,
    _now_iso,
    _survey_dict,
    connect,
    init_db,
    migrate_legacy_database,
    sanitize_comment,
    validate_questions,
    validate_response,
)
from .dashboard import (  # noqa: F401
    DashboardPanel,
    _visible_average,
    aggregate_responses,
    get_dashboard,
    save_response,
    seed_demo_data,
)
from .insights import (  # noqa: F401
    InsightsPanel,
    analyze_responses,
    confidence_label,
    get_statistical_engine_payload,
)
from .generator import (  # noqa: F401
    GeneratorPanel,
    clean_question,
    create_survey_draft,
    default_survey_questions,
    extract_ai_content,
    generate_question,
    generate_survey_draft,
    generate_teams_adaptive_card,
    parse_ai_json,
    publish_survey_to_typeform_direct,
    refine_survey_with_ai,
    send_teams_notification,
)
from .admin import (  # noqa: F401
    AdminPanel,
    _typeform_form_payload,
    _typeform_request,
    approve_recommendation,
    approve_survey,
    create_followup_survey,
    create_recommendations,
    generate_qr_svg,
    get_admin_data,
    get_survey,
    import_typeform_responses,
    list_surveys,
    publish_survey,
    review_action,
    save_survey_response,
    update_survey,
)
from .settings import (  # noqa: F401
    SettingsPanel,
    update_ai_config,
)


class PulsoHandler(BaseHTTPRequestHandler):
    """Enrutador de peticiones HTTP para la plataforma Pulso."""

    server_version = "Pulso/2.0"

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
        if length <= 0 or length > 65_536:
            raise RequestError("El contenido de la solicitud no es válido.", 413)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError("El contenido JSON no es válido.") from exc

    def send_file(self, filename: str, content_type: str) -> None:
        file_path = UI_DIR / filename
        if not file_path.exists():
            self.send_json(404, {"error": "Archivo no encontrado."})
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _public_base_url(self) -> str:
        configured_url = os.environ.get("PULSO_PUBLIC_URL", "").strip()
        if configured_url:
            return configured_url.rstrip("/")
        host = self.headers.get("Host", "")
        if not re.fullmatch(r"[A-Za-z0-9.:[\]-]+(?::\d{1,5})?", host):
            return f"http://{HOST}:{PORT}"
        return f"http://{host}"

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        # Endpoints de API REST
        if path == "/api/dashboard":
            self.send_json(200, DashboardPanel.get_data())
            return
        if path == "/api/admin":
            self.send_json(200, AdminPanel.get_admin_data())
            return
        if path in {"/api/settings", "/api/status"}:
            self.send_json(200, SettingsPanel.get_status())
            return
        if path == "/api/stats/engine":
            self.send_json(200, InsightsPanel.get_metrics())
            return

        # Código QR vectorial
        qr_match = re.fullmatch(r"/api/surveys/([a-zA-Z0-9_-]{8,64})/qr\.svg", path)
        if qr_match:
            survey_id = qr_match.group(1)
            target_url = f"{self._public_base_url()}/respond/{survey_id}"
            try:
                survey = get_survey(survey_id, public_only=False)
                if survey.get("publish_url"):
                    target_url = survey["publish_url"]
            except Exception:
                pass
            body = generate_qr_svg(target_url)
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
            return

        # Consulta pública por ID
        survey_match = re.fullmatch(r"/api/surveys/([a-f0-9]{32})", path)
        if survey_match:
            try:
                self.send_json(200, get_survey(survey_match.group(1), public_only=True))
            except RequestError as exc:
                self.send_json(exc.status, {"error": str(exc)})
            return

        # Pantalla de respuesta de microconsulta
        if re.fullmatch(r"/respond/[a-f0-9]{32}", path):
            self.send_file("index.html", "text/html; charset=utf-8")
            return

        # Archivos estáticos de interfaz
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/assets/codigo_qr.png": ("assets/codigo_qr.png", "image/png"),
            "/admin": ("index.html", "text/html; charset=utf-8"),
            "/admin/": ("index.html", "text/html; charset=utf-8"),
            "/generator": ("index.html", "text/html; charset=utf-8"),
            "/generator/": ("index.html", "text/html; charset=utf-8"),
            "/insights": ("index.html", "text/html; charset=utf-8"),
            "/clima": ("index.html", "text/html; charset=utf-8"),
            "/settings": ("index.html", "text/html; charset=utf-8"),
        }

        if path in files:
            filename, content_type = files[path]
            self.send_file(filename, content_type)
            return

        self.send_json(404, {"error": "No se ha encontrado esta página."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()

            # Rutas de generación de preguntas y encuestas
            if path == "/api/question":
                self.send_json(200, GeneratorPanel.generate_question(payload))
            elif path == "/api/generator/refine":
                self.send_json(200, GeneratorPanel.refine_survey(payload))
            elif path == "/api/surveys":
                self.send_json(201, GeneratorPanel.create_draft(payload))
            elif path == "/api/surveys/publish-direct":
                self.send_json(201, GeneratorPanel.publish_direct(payload, self._public_base_url()))
            elif path == "/api/integrations/teams":
                self.send_json(200, GeneratorPanel.generate_teams_card(payload, self._public_base_url()))

            # Rutas de respuestas y pulso diario
            elif path == "/api/pulse":
                self.send_json(201, DashboardPanel.submit_pulse(payload))
            elif path == "/api/responses":
                self.send_json(201, save_survey_response(payload))

            # Rutas de insights y motor estadístico
            elif path == "/api/insights/llm":
                self.send_json(200, InsightsPanel.generate_llm_synthesis())

            # Rutas de configuración
            elif path == "/api/settings":
                self.send_json(200, SettingsPanel.update_settings(payload))

            # Rutas de acciones de RRHH
            elif path == "/api/actions":
                self.send_json(201, approve_recommendation(payload))

            else:
                # Rutas parametrizadas
                survey_route = re.fullmatch(r"/api/surveys/([a-f0-9]{32})/(approve|publish|import)", path)
                action_route = re.fullmatch(r"/api/actions/(\d+)(?:/(followup))?", path)
                update_route = re.fullmatch(r"/api/surveys/([a-f0-9]{32})/edit", path)

                if survey_route:
                    survey_id, action = survey_route.groups()
                    if action == "approve":
                        self.send_json(200, AdminPanel.approve_survey(survey_id))
                    elif action == "publish":
                        force = False
                        if isinstance(payload, dict):
                            force = bool(payload.get("force", False))
                        self.send_json(200, AdminPanel.publish_survey(survey_id, self._public_base_url(), force=force))
                    else:
                        self.send_json(200, AdminPanel.import_responses(survey_id))
                elif action_route:
                    action_id, followup = action_route.groups()
                    if followup:
                        self.send_json(201, create_followup_survey(int(action_id)))
                    else:
                        self.send_json(200, AdminPanel.review_action(int(action_id), payload))
                elif update_route:
                    self.send_json(200, update_survey(update_route.group(1), payload))
                else:
                    self.send_json(404, {"error": "No se ha encontrado esta acción."})

        except RequestError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            self.send_json(500, {"error": f"Error interno en el servidor: {exc}"})


def main() -> None:
    migrate_legacy_database()
    with connect() as db:
        seed_demo_data(db)
    server = ThreadingHTTPServer((HOST, PORT), PulsoHandler)
    print(f"\n=======================================================", flush=True)
    print(f" ★ Pulso Estrella Damm está listo y operativo:", flush=True)
    print(f" -> http://{HOST}:{PORT}", flush=True)
    print(f"=======================================================\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando Pulso.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
