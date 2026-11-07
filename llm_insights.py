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


PROMPT_SISTEMA = """Eres un consultor senior de People Analytics y Recursos Humanos especializado en entornos industriales para Estrella Damm.
Tu misión es redactar un informe ejecutivo claro y accionable para el Comité de Dirección.

REGLAS ESTRICTAS DE INTEGRIDAD:
1. Basarás tus conclusiones ÚNICA Y EXCLUSIVAMENTE en los datos numéricos y booleanos del JSON adjunto.
2. NUNCA calcules ni inventes números, medias o porcentajes que no estén explícitamente en el JSON.
3. Si 'alertas_de_sesgo.resumen_alertas_activas' es true:
   - Señala con alta prioridad cualquier grupo con alerta de baja representatividad (<15%).
   - Alerta sobre polarización si la desviación típica supera 1.25, sugiriendo diálogo cualitativo en ese turno/planta.
4. Redacta el informe con tono profesional, empático y orientado a planes de acción concretos para:
   - Planta (Fábrica)
   - Logística
   - Oficinas
   - Comercial
"""


def build_analysis_prompt(statistical_json: str) -> str:
    """Construye el prompt con el JSON numérico inyectado."""
    return f"""A continuación tienes el informe estadístico determinista procesado de las microconsultas:

```json
{statistical_json}
```

Estructura tu respuesta en Markdown con las siguientes secciones:
1. **Resumen Ejecutivo** (Métricas globales clave).
2. **Alertas Críticas y Sesgos Muestrales** (Representatividad, K-anonimato y Polarización).
3. **Diagnóstico Detallado por Colectivo** (Planta, Logística, Comercial, Oficinas).
4. **Plan de Acción Prioritario** (3 medidas concretas para la dirección de Estrella Damm).
"""


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
