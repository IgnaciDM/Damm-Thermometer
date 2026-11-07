"""
Módulo de Configuración de Conexiones Corporativas (SettingsPanel).
Permite gestionar las credenciales del modelo LLM (API Key, endpoint, modelo)
y el token de Typeform de forma segura y en memoria/entorno.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from .common import (
    AI_CONFIG,
    AI_CONFIG_LOCK,
    TYPEFORM_CONFIG,
    TYPEFORM_LOCK,
    RequestError,
)


def update_ai_config(payload: object) -> dict:
    """Actualiza la configuración de LLM y Typeform en caliente."""
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


class SettingsPanel:
    """Gestiona las conexiones corporativas (Google Gemini, DCC LLM y Typeform)."""

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
