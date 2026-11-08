from __future__ import annotations

import json
import io
import math
import os
import secrets
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from .common import (
    AI_CONFIG,
    AI_CONFIG_LOCK,
    COHORTS,
    DEMO_QUESTIONS,
    DIMENSION_IDS,
    DIMENSIONS,
    POPULATION_SHARES,
    RequestError,
    SITES,
    _now_iso,
    _survey_dict,
    connect,
    validate_generator_history,
    validate_questions,
)

VOSK_MODEL_PATH = Path(
    os.environ.get(
        "VOSK_MODEL_PATH",
        Path(__file__).resolve().parent.parent
        / "ui"
        / "assets"
        / "vosk-model-small-es-0.42"
        / "vosk-model-small-es-0.42",
    )
)
VOSK_MAX_AUDIO_BYTES = 5 * 1024 * 1024
VOSK_MAX_AUDIO_SECONDS = 45
VOSK_MAX_CHUNK_BYTES = 64 * 1024
VOSK_SESSION_TIMEOUT_SECONDS = 120
_VOSK_MODEL = None
_VOSK_MODEL_LOCK = threading.Lock()
_VOSK_SESSIONS = {}
_VOSK_SESSIONS_LOCK = threading.Lock()


def _get_vosk() -> tuple[object, object]:
    try:
        import vosk
    except ImportError as exc:
        raise RequestError(
            "Vosk no está instalado. Instala las dependencias de requirements.txt.",
            503,
        ) from exc

    global _VOSK_MODEL
    if _VOSK_MODEL is None:
        with _VOSK_MODEL_LOCK:
            if _VOSK_MODEL is None:
                if not VOSK_MODEL_PATH.is_dir():
                    raise RequestError(
                        "No se encuentra el modelo Vosk de español. Configura VOSK_MODEL_PATH.",
                        503,
                    )
                try:
                    vosk.SetLogLevel(-1)
                    _VOSK_MODEL = vosk.Model(str(VOSK_MODEL_PATH))
                except (OSError, RuntimeError) as exc:
                    raise RequestError("No se pudo cargar el modelo Vosk de español.", 503) from exc
    return vosk, _VOSK_MODEL


def start_voice_transcription(sample_rate: object) -> dict:
    if (
        not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or not 8_000 <= sample_rate <= 48_000
    ):
        raise RequestError("La frecuencia de muestreo debe estar entre 8 y 48 kHz.")

    vosk, model = _get_vosk()
    try:
        recognizer = vosk.KaldiRecognizer(model, sample_rate)
        recognizer.SetWords(False)
    except (RuntimeError, ValueError) as exc:
        raise RequestError("No se pudo iniciar el reconocimiento con Vosk.", 422) from exc

    now = time.monotonic()
    session_id = secrets.token_urlsafe(24)
    with _VOSK_SESSIONS_LOCK:
        expired = [
            key for key, session in _VOSK_SESSIONS.items()
            if now - session["last_activity"] > VOSK_SESSION_TIMEOUT_SECONDS
        ]
        for key in expired:
            del _VOSK_SESSIONS[key]
        _VOSK_SESSIONS[session_id] = {
            "recognizer": recognizer,
            "sample_rate": sample_rate,
            "audio_bytes": 0,
            "last_activity": now,
        }
    return {"session_id": session_id, "source": "vosk", "language": "es"}


def feed_voice_transcription(session_id: str, pcm_audio: bytes) -> dict:
    if not pcm_audio or len(pcm_audio) > VOSK_MAX_CHUNK_BYTES or len(pcm_audio) % 2:
        raise RequestError("El fragmento de audio no es válido.", 413)

    with _VOSK_SESSIONS_LOCK:
        session = _VOSK_SESSIONS.get(session_id)
        if session is None:
            raise RequestError("La sesión de dictado ha caducado. Inicia otra grabación.", 410)
        now = time.monotonic()
        if now - session["last_activity"] > VOSK_SESSION_TIMEOUT_SECONDS:
            del _VOSK_SESSIONS[session_id]
            raise RequestError("La sesión de dictado ha caducado. Inicia otra grabación.", 410)
        session["audio_bytes"] += len(pcm_audio)
        if session["audio_bytes"] > session["sample_rate"] * 2 * VOSK_MAX_AUDIO_SECONDS:
            del _VOSK_SESSIONS[session_id]
            raise RequestError("La grabación supera el límite de 45 segundos.", 413)
        session["last_activity"] = now
        recognizer = session["recognizer"]
        try:
            complete = recognizer.AcceptWaveform(pcm_audio)
            result = recognizer.Result() if complete else recognizer.PartialResult()
            text = json.loads(result).get("text" if complete else "partial", "").strip()
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            del _VOSK_SESSIONS[session_id]
            raise RequestError("No se pudo procesar el fragmento con Vosk.", 422) from exc

    return {"text": text, "partial": not complete}


def finish_voice_transcription(session_id: str) -> dict:
    with _VOSK_SESSIONS_LOCK:
        session = _VOSK_SESSIONS.pop(session_id, None)
        if session is None:
            raise RequestError("La sesión de dictado ha caducado. Inicia otra grabación.", 410)
        try:
            text = json.loads(session["recognizer"].FinalResult()).get("text", "").strip()
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            raise RequestError("No se pudo finalizar la transcripción con Vosk.", 422) from exc
    return {"text": text, "partial": False}


def extract_ai_content(result: dict) -> str:
    """Extrae el texto de respuesta de Google Gemini o endpoints compatibles."""
    if "candidates" in result and result["candidates"]:
        cand = result["candidates"][0]
        if "content" in cand and "parts" in cand["content"] and cand["content"]["parts"]:
            return cand["content"]["parts"][0].get("text", "")
    if "choices" in result and result["choices"]:
        choice = result["choices"][0]
        if "message" in choice and "content" in choice["message"]:
            return choice["message"]["content"] or ""
    raise ValueError("Formato de respuesta de IA no reconocido.")


def transcribe_voice_prompt(audio_data: bytes) -> dict:
    if not isinstance(audio_data, bytes) or not audio_data:
        raise RequestError("No se recibió audio para transcribir.")
    if len(audio_data) > VOSK_MAX_AUDIO_BYTES:
        raise RequestError("La grabación supera el límite de 5 MB.", 413)

    try:
        with wave.open(io.BytesIO(audio_data), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            if (
                audio.getcomptype() != "NONE"
                or channels != 1
                or sample_width != 2
                or not 8_000 <= sample_rate <= 48_000
                or frame_count <= 0
                or frame_count > sample_rate * VOSK_MAX_AUDIO_SECONDS
            ):
                raise RequestError(
                    "El audio debe ser WAV PCM mono de 16 bits y durar como máximo 45 segundos."
                )
            pcm_audio = audio.readframes(frame_count)
    except (wave.Error, EOFError) as exc:
        raise RequestError("El audio no es un archivo WAV válido.") from exc

    vosk, model = _get_vosk()

    try:
        recognizer = vosk.KaldiRecognizer(model, sample_rate)
        recognizer.SetWords(False)
        recognized = []
        for offset in range(0, len(pcm_audio), 8_000):
            if recognizer.AcceptWaveform(pcm_audio[offset : offset + 8_000]):
                segment = json.loads(recognizer.Result()).get("text", "").strip()
                if segment:
                    recognized.append(segment)
        final_segment = json.loads(recognizer.FinalResult()).get("text", "").strip()
        if final_segment:
            recognized.append(final_segment)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise RequestError("No se pudo procesar la grabación con Vosk.", 422) from exc

    return {"text": " ".join(recognized), "source": "vosk", "language": "es"}


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
    valid_cohorts = set(COHORTS) | {"Todos", "Fábrica", "Oficinas"}
    cohort_input = str(payload.get("cohort", "Planta")).strip()
    cohort_parts = [c.strip() for c in cohort_input.split(",") if c.strip()]
    if not cohort_parts or not all(c in valid_cohorts for c in cohort_parts):
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
            title = generated.get("title", "")
            intro = generated.get("intro", "")
            source = "gemini"
        except Exception:
            questions = default_survey_questions(payload["objective"], payload["cohort"], language)
            source = "plantilla"
    if not title:
        title = f"Pulso de equipo: {payload['objective'][:60]}"
    if not intro:
        intro = "Tus respuestas son anónimas y se agregan para mejorar la organización del trabajo."
    return {"title": title, "intro": intro, "questions": questions, "source": source}


def parse_cohort_list(cohort_val: object) -> list[str]:
    """Normaliza y divide la lista de colectivos seleccionados."""
    if isinstance(cohort_val, list):
        items = [str(x).strip() for x in cohort_val if str(x).strip()]
    elif isinstance(cohort_val, str):
        trimmed = cohort_val.strip()
        if not trimmed or trimmed.lower() == "todos":
            return ["Fábrica", "Logística", "Comercial", "Oficinas"]
        items = [p.strip() for p in trimmed.split(",") if p.strip()]
    else:
        return ["Planta"]

    canonical = []
    for item in items:
        lower = item.lower()
        if lower in ["planta", "fabrica", "fábrica"]:
            name = "Planta" if item == "Planta" else "Fábrica"
        elif "logíst" in lower or "logist" in lower:
            name = "Logística"
        elif "comercial" in lower:
            name = "Comercial"
        elif "oficina" in lower:
            name = "Oficinas"
        else:
            name = item
        if name not in canonical:
            canonical.append(name)
    return canonical or ["Fábrica"]


def generate_cohort_survey(
    cohort_name: str,
    user_prompt: str,
    prompt_history: list,
    current_questions: list,
    ai_config: dict,
    language: str = "es",
) -> dict:
    """Genera una microconsulta adaptada a las características operativas de un colectivo concreto."""
    is_factory = cohort_name in ["Fábrica", "Planta"]
    is_logistics_or_commercial = cohort_name in ["Logística", "Comercial"]
    is_office = cohort_name == "Oficinas"

    if is_factory:
        recommended_channel = "QR en Vestuario"
        specific_rules = (
            "- Formato: Microconsulta ultrarrápida. Máximo 3 preguntas cerradas (escala 1-5).\n"
            "- Tono y lenguaje: Muy directo, sencillo y cotidiano. Enfocado en descansos, turnicidad y condiciones físicas.\n"
            "- Canal recomendado obligatorio: 'QR en Vestuario'.\n"
            "- Restricción crítica: CERO preguntas abiertas que requieran escribir mucho texto."
        )
    elif is_logistics_or_commercial:
        recommended_channel = "Enlace SMS/WhatsApp"
        specific_rules = (
            f"- Formato: Microconsulta ultrarrápida para {cohort_name}. Máximo 3 preguntas cerradas (escala 1-5).\n"
            "- Tono y lenguaje: Ágil, directo y accionable para responder desde dispositivos móviles en ruta.\n"
            "- Canal recomendado obligatorio: 'Enlace SMS/WhatsApp'.\n"
            "- Restricción crítica: CERO preguntas abiertas que requieran escribir mucho texto."
        )
    else:
        recommended_channel = "Tarjeta en Microsoft Teams / Enlace Web"
        specific_rules = (
            "- Formato: Encuesta completa para Oficinas.\n"
            "- Preguntas: Hasta 4 preguntas cerradas (escala 1-5) y exactamente 1 pregunta de texto abierto ('open_text').\n"
            "- Tono y lenguaje: Corporativo, reflexivo y profesional sobre conciliación, liderazgo y procesos.\n"
            "- Canal recomendado obligatorio: 'Tarjeta en Microsoft Teams / Enlace Web'."
        )

    # 2. Pools hiperpersonalizados según colectivo para garantizar variedad y evitar duplicados
    low_prompt = user_prompt.lower()
    clean_p = user_prompt.strip()

    factory_pool = [
        {
            "title": f"¿Cómo valoras la seguridad y el ritmo de trabajo en tu línea frente a: {clean_p[:35]}?",
            "dimension": "condiciones",
            "type": "rating",
            "choices": ["Muy seguro y adecuado", "Adecuado", "Excesivo", "Peligroso"],
        },
        {
            "title": "¿Los descansos y las pausas entre turnos son suficientes para recuperarte físicamente?",
            "dimension": "bienestar",
            "type": "multiple_choice",
            "choices": ["Totalmente suficientes", "Aceptables", "Insuficientes", "Muy insuficientes"],
        },
        {
            "title": "¿Cuentas con las herramientas de mantenimiento y EPIs necesarios para tu labor diaria?",
            "dimension": "condiciones",
            "type": "multiple_choice",
            "choices": ["Siempre disponibles", "La mayoría de veces", "Raras veces", "Casi nunca"],
        },
    ]

    logistics_pool = [
        {
            "title": f"¿Cómo de ágil y segura ha sido tu operativa hoy respecto a: {clean_p[:35]}?",
            "dimension": "condiciones",
            "type": "rating",
            "choices": ["Muy ágil", "Normal", "Lenta con fricciones", "Muy complicada"],
        },
        {
            "title": "¿La información sobre cambios en rutas y pedidos llegó con suficiente antelación a tu terminal?",
            "dimension": "liderazgo",
            "type": "multiple_choice",
            "choices": ["Siempre a tiempo", "Casi siempre", "A veces tarde", "Casi nunca"],
        },
        {
            "title": "¿Dispones de las condiciones adecuadas para completar las franjas de entrega sin sobrecarga?",
            "dimension": "bienestar",
            "type": "multiple_choice",
            "choices": ["Completamente", "Suficiente", "Insuficiente"],
        },
    ]

    commercial_pool = [
        {
            "title": f"¿Cómo calificarías el apoyo de operaciones y distribución respecto a: {clean_p[:35]}?",
            "dimension": "relaciones",
            "type": "rating",
            "choices": ["Excelente apoyo", "Bueno", "Mejorable", "Deficiente"],
        },
        {
            "title": "¿La información comercial y objetivos de campaña están claros para transmitir a clientes?",
            "dimension": "cultura",
            "type": "multiple_choice",
            "choices": ["Muy claros", "Claros", "Poco claros", "Confusos"],
        },
        {
            "title": "¿Dispones de la agilidad necesaria en campo para resolver incidencias de producto en hostelería?",
            "dimension": "condiciones",
            "type": "multiple_choice",
            "choices": ["Siempre", "Casi siempre", "Raras veces", "Casi nunca"],
        },
    ]

    office_pool = [
        {
            "title": f"¿En qué medida consideras que {clean_p[:35]} favorece tu conciliación y bienestar?",
            "dimension": "bienestar",
            "type": "rating",
            "choices": ["Muy favorable", "Favorable", "Neutro", "Desfavorable"],
        },
        {
            "title": "¿Consideras que las decisiones adoptadas en tu área son comunicadas con claridad y transparencia?",
            "dimension": "liderazgo",
            "type": "rating",
            "choices": ["Totalmente de acuerdo", "De acuerdo", "En desacuerdo", "Totalmente en desacuerdo"],
        },
        {
            "title": "¿Tu responsable directo te facilita la flexibilidad y autonomía necesaria para alcanzar tus metas?",
            "dimension": "liderazgo",
            "type": "multiple_choice",
            "choices": ["Siempre", "Casi siempre", "A veces", "Raras veces"],
        },
        {
            "title": "¿Sientes que tus objetivos profesionales trimestrales siguen alineados con el ritmo actual del equipo?",
            "dimension": "desarrollo",
            "type": "multiple_choice",
            "choices": ["Completamente alineados", "Alineados", "Poco claros", "Desalineados"],
        },
        {
            "title": "¿Qué propuesta o medida constructiva sugerirías a Dirección de Personas para optimizar este punto?",
            "dimension": "cultura",
            "type": "open_text",
            "choices": [],
        },
    ]

    if is_factory:
        cohort_pool = factory_pool
        synthesis = "Microconsulta ultrarrápida adaptada a operarios de planta y vestuarios."
    elif cohort_name == "Logística":
        cohort_pool = logistics_pool
        synthesis = "Microconsulta móvil adaptada a conductores y centros logísticos."
    elif cohort_name == "Comercial":
        cohort_pool = commercial_pool
        synthesis = "Microconsulta ágil adaptada a la red comercial y ventas en ruta."
    else:
        cohort_pool = office_pool
        synthesis = "Encuesta corporativa completa con 4 preguntas cerradas y 1 abierta para Oficinas."

    # 1. Intentar con LLM si hay API Key configurada
    if ai_config.get("api_key"):
        system_instruction = (
            f"Eres el consultor senior de People & Culture de Estrella Damm especializado en {cohort_name}.\n"
            f"DEPARTAMENTO DESTINATARIO: '{cohort_name}'.\n"
            f"{specific_rules}\n\n"
            "El usuario te da una instrucción en lenguaje natural.\n"
            "Devuelve UN ÚNICO OBJETO JSON con este esquema exacto:\n"
            "{\n"
            f'  "colectivo": "{cohort_name}",\n'
            f'  "canal_recomendado": "{recommended_channel}",\n'
            '  "titulo": "Título motivador y corto",\n'
            '  "synthesis": "Breve frase explicando los cambios",\n'
            '  "updated_indices": [0, 1],\n'
            '  "questions": [\n'
            "    {\n"
            '      "title": "¿Texto claro de la pregunta?",\n'
            '      "dimension": "bienestar" | "liderazgo" | "condiciones" | "desarrollo" | "cultura" | "relaciones",\n'
            '      "type": "rating" | "multiple_choice" | "open_text",\n'
            '      "choices": ["Opción 1", "Opción 2"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )

        user_content = {
            "instruction": user_prompt,
            "previous_instructions": prompt_history,
            "cohort": cohort_name,
            "language": language,
            "existing_questions": current_questions,
        }

        request_body = {
            "model": ai_config.get("model", "ornith"),
            "temperature": 0.3,
            "max_tokens": 4096,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
            ],
        }

        try:
            req = Request(
                ai_config["base_url"],
                data=json.dumps(request_body).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {ai_config['api_key']}"},
                method="POST",
            )
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = extract_ai_content(data)
            parsed = parse_ai_json(text)
            if "questions" in parsed and isinstance(parsed["questions"], list) and len(parsed["questions"]) > 0:
                raw_qs = parsed["questions"]
                sanitized_qs = []
                for q in raw_qs:
                    q_type = q.get("type", "multiple_choice")
                    if not is_office and q_type == "open_text":
                        q_type = "multiple_choice"
                    sanitized_qs.append({
                        "title": q.get("title") or q.get("enunciado") or "¿Pregunta de consulta?",
                        "dimension": q.get("dimension") or "bienestar",
                        "type": q_type,
                        "choices": q.get("choices") if isinstance(q.get("choices"), list) and len(q.get("choices")) > 0 else (
                            ["Muy de acuerdo", "De acuerdo", "En desacuerdo", "Muy en desacuerdo"] if q_type != "open_text" else []
                        )
                    })

                if is_office:
                    # Estricto Oficinas: exactamente 4 cerradas + 1 abierta (total 5)
                    closed_qs = [q for q in sanitized_qs if q["type"] != "open_text"][:4]
                    existing_titles = {q["title"].strip().lower() for q in closed_qs}
                    for cand in cohort_pool:
                        if len(closed_qs) >= 4:
                            break
                        if cand["type"] != "open_text" and cand["title"].strip().lower() not in existing_titles:
                            closed_qs.append(cand)
                            existing_titles.add(cand["title"].strip().lower())
                    open_qs = [q for q in sanitized_qs if q["type"] == "open_text"]
                    if not open_qs:
                        open_qs = [cohort_pool[-1]]
                    final_qs = closed_qs[:4] + [open_qs[0]]
                else:
                    # Estricto Fábrica/Logística/Comercial: exactamente 3 preguntas cerradas
                    closed_qs = [q for q in sanitized_qs if q["type"] != "open_text"][:3]
                    existing_titles = {q["title"].strip().lower() for q in closed_qs}
                    for cand in cohort_pool:
                        if len(closed_qs) >= 3:
                            break
                        if cand["title"].strip().lower() not in existing_titles:
                            closed_qs.append(cand)
                            existing_titles.add(cand["title"].strip().lower())
                    final_qs = closed_qs[:3]

                return {
                    "source": "gemini",
                    "colectivo": cohort_name,
                    "titulo": parsed.get("titulo", f"Pulso {cohort_name}: {user_prompt[:35]}"),
                    "canal_recomendado": parsed.get("canal_recomendado", recommended_channel),
                    "synthesis": parsed.get("synthesis", f"Preguntas actualizadas para {cohort_name}."),
                    "questions": final_qs,
                    "updated_indices": list(range(len(final_qs))),
                }
        except Exception as e:
            print(f"[LLM Refine {cohort_name} Warning] {e}. Usando fallback local.")

    # Si se pide modificar una pregunta concreta (ej: "cambia la 2")
    if current_questions and len(current_questions) > 1 and "2" in low_prompt:
        q_copy = list(current_questions)
        q_copy[1] = {
            "title": "¿Los descansos y las pausas entre turnos son suficientes para recuperarte?",
            "dimension": "bienestar",
            "type": "multiple_choice",
            "choices": ["Totalmente suficientes", "Aceptables", "Insuficientes", "Muy insuficientes"],
        }
        final_mod_qs = q_copy[:5] if is_office else q_copy[:3]
        return {
            "source": "plantilla_adaptada",
            "colectivo": cohort_name,
            "titulo": f"Pulso {cohort_name}: {clean_p[:30]}",
            "canal_recomendado": recommended_channel,
            "synthesis": "He actualizado la pregunta 2 con una valoración sobre descansos.",
            "questions": final_mod_qs,
            "updated_indices": [1],
        }

    # Garantizar estrictamente 3 preguntas para no-oficinas y 5 para oficinas
    final_fallback_qs = cohort_pool[:5] if is_office else cohort_pool[:3]

    return {
        "source": "plantilla_adaptada",
        "colectivo": cohort_name,
        "titulo": f"Pulso {cohort_name}: {clean_p[:30]}",
        "canal_recomendado": recommended_channel,
        "synthesis": synthesis,
        "questions": final_fallback_qs,
        "updated_indices": list(range(len(final_fallback_qs))),
    }


def refine_survey_with_ai(payload: object) -> dict:
    """Genera o actualiza encuestas hiperpersonalizadas para uno o múltiples colectivos."""
    if not isinstance(payload, dict):
        raise RequestError("El formato de la solicitud no es válido.")
    user_prompt = payload.get("user_prompt", "").strip()
    if not user_prompt:
        raise RequestError("Escribe una instrucción o tema para la IA.")

    current_questions = payload.get("current_questions", [])
    if not isinstance(current_questions, list):
        current_questions = []
    prompt_history = validate_generator_history(payload.get("prompt_history", []))

    raw_cohort = payload.get("cohort", "Planta")
    language = payload.get("language", "es")
    if language not in {"es", "ca", "en"}:
        language = "es"

    surveys_input = payload.get("surveys_by_cohort", {})
    if not isinstance(surveys_input, dict):
        surveys_input = {}

    with AI_CONFIG_LOCK:
        ai_config = dict(AI_CONFIG)

    # Si se especificó una lista explícita de colectivos o se separan por comas
    cohorts_list = payload.get("cohorts")
    if not isinstance(cohorts_list, list) or not cohorts_list:
        cohorts_list = parse_cohort_list(raw_cohort)

    # Si se pasó explícitamente "Planta" (ej: en tests unitarios simples), preservar exactitud
    if len(cohorts_list) == 1 and raw_cohort == "Planta":
        cohorts_list = ["Planta"]

    surveys_by_cohort = {}
    for col in cohorts_list:
        cohort_q = []
        if col in surveys_input and isinstance(surveys_input[col], dict):
            cohort_q = surveys_input[col].get("questions", [])
        elif len(cohorts_list) == 1:
            cohort_q = current_questions

        surveys_by_cohort[col] = generate_cohort_survey(
            col, user_prompt, prompt_history, cohort_q, ai_config, language
        )

    active_col = cohorts_list[0]
    active_survey = surveys_by_cohort[active_col]

    if len(cohorts_list) > 1:
        synthesis = f"Se han generado {len(cohorts_list)} encuestas personalizadas para: {', '.join(cohorts_list)}."
    else:
        synthesis = active_survey["synthesis"]

    return {
        "source": active_survey["source"],
        "synthesis": synthesis,
        "questions": active_survey["questions"],
        "updated_indices": active_survey["updated_indices"],
        "cohort": raw_cohort,
        "cohorts": cohorts_list,
        "surveys_by_cohort": surveys_by_cohort,
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

    custom_title = str(payload.get("title", "")).strip()
    custom_intro = str(payload.get("intro", "")).strip()
    generator_history = validate_generator_history(payload.get("generator_history", []))
    final_questions = generated["questions"]
    if "questions" in payload and isinstance(payload["questions"], list) and len(payload["questions"]) >= 1:
        try:
            final_questions = validate_questions(payload["questions"])
        except Exception:
            final_questions = payload["questions"]

    with connect() as db:
        db.execute(
            """INSERT INTO surveys
               (id, created_at, updated_at, objective, cohort, context, max_duration,
                exclusions, language, channel, title, intro, questions_json, status,
                frequency_days, population_json, followup_of, generator_history_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)""",
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
                custom_title or generated["title"],
                custom_intro or generated["intro"],
                json.dumps(final_questions, ensure_ascii=False),
                frequency_days,
                json.dumps(population),
                str(payload.get("followup_of", "")),
                json.dumps(generator_history, ensure_ascii=False),
            ),
        )
        row = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
    return {**_survey_dict(row, include_private=True), "source": generated["source"]}


def publish_survey_to_typeform_direct(payload: object, public_base_url: str = "") -> dict:
    from .admin import approve_survey, publish_survey

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
    return publish_survey(survey_id, public_base_url=public_base_url, force=True)


def clean_question(text: str) -> str:
    """Limpia y valida que la salida sea una única pregunta concisa."""
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
    try:
        request = Request(
            config["base_url"],
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config['api_key']}",
            },
            method="POST",
        )
        import sys
        app_mod = sys.modules.get("backend.app")
        requester = getattr(app_mod, "urlopen") if (app_mod and hasattr(app_mod, "urlopen")) else urlopen
        with requester(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        raw_text = extract_ai_content(result)
        question = clean_question(raw_text)
        return {
            "question": question,
            "source": "api",
            "model": config["model"],
        }
    except Exception:
        return {
            "question": DEMO_QUESTIONS[dimension],
            "source": "demo",
            "model": None,
        }


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
                            "title": "Participar en la consulta (Anónimo)",
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
    from .admin import get_survey

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
        "message": "Notificación enviada al canal de Teams" if sent else "Adaptive Card generada para M365 (Modo simulación / Copia JSON)",
        "survey_url": survey.get("publish_url", f"{base_url}/respond/{survey.get('id', '')}")
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
    def transcribe_voice_prompt(audio_data: bytes) -> dict:
        return transcribe_voice_prompt(audio_data)

    @staticmethod
    def start_voice_transcription(sample_rate: object) -> dict:
        return start_voice_transcription(sample_rate)

    @staticmethod
    def feed_voice_transcription(session_id: str, pcm_audio: bytes) -> dict:
        return feed_voice_transcription(session_id, pcm_audio)

    @staticmethod
    def finish_voice_transcription(session_id: str) -> dict:
        return finish_voice_transcription(session_id)

    @staticmethod
    def generate_teams_card(payload: object, base_url: str = "") -> dict:
        return send_teams_notification(payload, base_url=base_url)
