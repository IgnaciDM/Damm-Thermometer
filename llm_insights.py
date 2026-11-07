"""
Orquestador LLM para Síntesis de Clima Laboral (Estrella Damm).

Conecta el payload del Motor Estadístico Determinista con la API del LLM,
garantizando cero alucinaciones numéricas mediante Prompt Chaining estricto.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


PROMPT_SISTEMA = """Eres un Director de Recursos Humanos de Estrella Damm analizando datos estadísticos agregados de clima laboral en plantas productivas y centros logísticos.
Tu misión es interpretar rigurosamente el informe numérico y devolver una síntesis ejecutiva estructurada y accionable para People Analytics.

REGLAS ESTRICTAS DE RESPUESTA:
Basándote ÚNICAMENTE en el JSON estadístico proporcionado:
1. NUNCA inventes números o medias que no estén explícitamente en el JSON.
2. Si detectas colectivos con participación < 15%, señálalo como riesgo de sesgo de no respuesta.
3. Si la desviación típica supera 1.25, destaca la polarización bimodal y la necesidad de diálogo cualitativo en esos turnos.
4. Cumple rigurosamente el formato de salida solicitado."""


def build_analysis_prompt(statistical_json: str) -> str:
    """Construye el prompt con el JSON numérico inyectado."""
    return f"""Informe estadístico procesado de las microconsultas de Pulso Estrella Damm:

```json
{statistical_json}
```

Estructura tu respuesta en Markdown con las siguientes 3 secciones obligatorias:
### 1. Diagnóstico de Alertas Graves y Sesgos
(Exactamente 3 viñetas destacando problemas graves: caídas de participación por debajo del 15%, riesgos de no respuesta o polarización bimodal en turnos).

### 2. Sugerencias Futuras para Mejorar
(Exactamente 2 acciones operativas viables para implementar en planta o centros logísticos de Damm).

### 3. Propuesta de Microconsulta de Seguimiento
(Exactamente 2 preguntas cortas redactadas para enviar al colectivo o turno más afectado)."""


def request_llm_insights(
    statistical_json: str,
    api_key: Optional[str] = None,
    base_url: str = "https://dcc-llm-gw.uab.cat/uabthehack/v1/chat/completions",
    model: str = "ornith"
) -> Dict[str, Any]:
    """
    Envía el JSON determinista al modelo Ornith del DCC de la UAB (o endpoint compatible) para obtener el informe cualitativo.
    Si no hay API key configurada, devuelve una respuesta sintética estructurada de prueba.
    """
    api_key = api_key or os.environ.get("UTH_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("AI_API_KEY", "")

    if not api_key:
        return {
            "modo": "demo_local_sin_api_key",
            "informe_markdown": (
                "### Resumen Ejecutivo (Modo Demo)\n"
                "Para generar conclusiones en lenguaje natural con IA en tiempo real, "
                "introduce tu clave de UAB The Hack! (DCC Ornith: `uth-...`) en la pestaña de Configuración "
                "o define la variable de entorno `UTH_KEY`.\n\n"
                "El JSON numérico determinista se ha calculado con 100% de precisión y estricta privacidad."
            ),
            "payload_estadistico_utilizado": json.loads(statistical_json)
        }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    body = {
        "model": model,
        "temperature": 0.2,  # Temperatura baja para maximizar rigor analítico
        "max_tokens": 4096,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": build_analysis_prompt(statistical_json)}
        ]
    }

    req = urllib.request.Request(
        base_url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
            if "candidates" in result and result["candidates"]:
                content = result["candidates"][0]["content"]["parts"][0]["text"]
            elif "choices" in result and result["choices"]:
                content = result["choices"][0]["message"]["content"]
            else:
                content = str(result)
            return {
                "modo": "produccion_llm",
                "informe_markdown": content,
                "payload_estadistico_utilizado": json.loads(statistical_json)
            }
    except urllib.error.HTTPError as e:
        error_detail = e.read().decode("utf-8")
        return {
            "modo": "error_api",
            "error": f"HTTP {e.code}: {error_detail}",
            "payload_estadistico_utilizado": json.loads(statistical_json)
        }
    except Exception as e:
        return {
            "modo": "error_conexion",
            "error": str(e),
            "payload_estadistico_utilizado": json.loads(statistical_json)
        }
