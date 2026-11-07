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
            "El usuario te da una instrucción en lenguaje natural (ej: 'cambia la pregunta 2 por una sobre descansos', "
            "'adapta todas las preguntas para operarios de fábrica', 'haz una pregunta sobre ergonomía').\n"
            "Tu tarea:\n"
            "1. Analiza qué preguntas deben actualizarse (una, dos o todas). Si la lista actual está vacía, genera 3 o 4 preguntas completas.\n"
            "2. Devuelve UN ÚNICO OBJETO JSON con este esquema exacto:\n"
            "{\n"
            '  "synthesis": "Explicación breve de 1 frase de qué cambios has realizado",\n'
            '  "updated_indices": [0, 1], // Índices base-0 de las preguntas modificadas (o [0, 1, 2] si son todas)\n'
            '  "questions": [\n'
            "    {\n"
            '      "title": "¿Texto claro de la pregunta?",\n'
            '      "dimension": "bienestar" | "liderazgo" | "condiciones" | "desarrollo" | "cultura" | "relaciones",\n'
            '      "type": "rating" | "multiple_choice" | "open_text",\n'
            '      "choices": ["Opción 1", "Opción 2"] // Obligatorio solo si type es multiple_choice\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Reglas críticas de RRHH Damm:\n"
            "- Cero preguntas que comprometan la privacidad (nada de nombres, DNI, correos, turnos específicos de 1 persona).\n"
            "- Lenguaje cercano, respetuoso y adaptado al entorno del trabajador.\n"
            "- La primera pregunta debe ser de tipo 'rating' (escala 1 a 5).\n"
            "- Devuelve ÚNICAMENTE el JSON sin bloques de texto fuera."
        )

        user_content = {
            "instruction": user_prompt,
            "cohort": cohort,
            "language": language,
            "existing_questions": current_questions,
        }

        request_body = {
            "model": ai_config["model"],
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
                return {
                    "source": "gemini",
                    "synthesis": parsed.get("synthesis", "Preguntas actualizadas con el asistente LLM."),
                    "questions": parsed["questions"],
                    "updated_indices": parsed.get("updated_indices", list(range(len(parsed["questions"])))),
                }
        except Exception as e:
            print(f"[LLM Refine Survey Warning] {e}. Usando fallback local inteligente.")

    # Fallback inteligente local
    low_prompt = user_prompt.lower()
    new_questions = list(current_questions) if current_questions else [
        {"title": "¿Qué te ha ayudado a sentirte bien en tu jornada esta semana?", "dimension": "bienestar", "type": "rating", "choices": ["Muy de acuerdo", "De acuerdo", "En desacuerdo", "Muy en desacuerdo"]},
        {"title": "¿Has recibido el acompañamiento que necesitabas para hacer bien tu trabajo?", "dimension": "liderazgo", "type": "multiple_choice", "choices": ["Siempre", "Casi siempre", "A veces", "Casi nunca"]},
        {"title": "¿Has contado con los recursos y el tiempo necesarios para tus tareas?", "dimension": "condiciones", "type": "multiple_choice", "choices": ["Completamente", "Suficiente", "Insuficiente"]},
    ]

    updated_indices = []
    synthesis = "He adaptado la encuesta según tus indicaciones."

    if any(k in low_prompt for k in ["descanso", "pausa", "comedor", "ergonom", "carga"]):
        q_text = "¿Los descansos y las pausas entre turnos son suficientes para recuperarte?"
        if len(new_questions) > 1 and "2" in low_prompt:
            new_questions[1] = {"title": q_text, "dimension": "bienestar", "type": "multiple_choice", "choices": ["Totalmente suficientes", "Aceptables", "Insuficientes", "Muy insuficientes"]}
            updated_indices = [1]
            synthesis = "He cambiado la pregunta 2 por una valoración sobre descansos y pausas en turnos."
        else:
            new_questions.append({"title": q_text, "dimension": "bienestar", "type": "multiple_choice", "choices": ["Totalmente suficientes", "Aceptables", "Insuficientes"]})
            updated_indices = [len(new_questions) - 1]
            synthesis = "He añadido una pregunta enfocada en descansos y condiciones físicas."
    elif any(k in low_prompt for k in ["planta", "fabrica", "fábrica", "operari", "linea"]):
        new_questions = [
            {"title": "¿Cómo valoras la seguridad y el ritmo de trabajo en tu línea o puesto?", "dimension": "condiciones", "type": "rating", "choices": ["Muy seguro y adecuado", "Adecuado", "Excesivo", "Peligroso"]},
            {"title": "¿La confirmación de tus turnos rotativos llega con suficiente antelación?", "dimension": "bienestar", "type": "multiple_choice", "choices": ["Con más de 2 semanas", "Con 1 semana", "Con pocos días", "Con menos de 48h"]},
            {"title": "¿Cuentas con las herramientas de mantenimiento y EPIs necesarios para tu labor?", "dimension": "condiciones", "type": "multiple_choice", "choices": ["Siempre disponibles", "La mayoría de veces", "Raras veces", "Casi nunca"]},
        ]
        updated_indices = [0, 1, 2]
        synthesis = "He reformulado todas las preguntas orientándolas específicamente a la realidad operativa de fábrica y planta."
    elif "1" in low_prompt and "3" in low_prompt:
        new_questions[0]["title"] = f"¿En qué medida el ritmo de esta semana ha permitido realizar tu trabajo con calidad? ({user_prompt[:30]}...)"
        if len(new_questions) > 2:
            new_questions[2]["title"] = "¿Qué apoyo organizativo o de material necesitas con mayor urgencia?"
        updated_indices = [0, 2]
        synthesis = "He actualizado las preguntas 1 y 3 con el nuevo enfoque solicitado."
    elif "2" in low_prompt:
        target_idx = 1 if len(new_questions) > 1 else 0
        new_questions[target_idx]["title"] = f"¿Cómo calificarías la comunicación sobre cambios y tareas en tu jornada? ({user_prompt[:35]})"
        updated_indices = [target_idx]
        synthesis = f"He actualizado la pregunta {target_idx + 1} para reflejar tu indicación."
    else:
        new_questions[0]["title"] = f"¿Cómo calificarías tu experiencia respecto a: {user_prompt[:60]}?"
        updated_indices = [0]
        synthesis = "He adaptado la consulta para enfocarla en el tema que has indicado."

    return {
        "source": "plantilla_adaptada",
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

    custom_title = str(payload.get("title", "")).strip()
    custom_intro = str(payload.get("intro", "")).strip()
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
                custom_title or generated["title"],
                custom_intro or generated["intro"],
                json.dumps(final_questions, ensure_ascii=False),
                frequency_days,
                json.dumps(population),
                str(payload.get("followup_of", "")),
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
