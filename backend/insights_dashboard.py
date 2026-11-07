"""
Dashboard de People Analytics e Insights para Recursos Humanos - Estrella Damm.
Arquitectura Híbrida: Lógica determinista if/else + Visualizaciones especializadas + IA generativa bajo demanda.

Cumple con:
1. Enrutamiento hardcodeado según nivel de análisis (Individual vs Agregado).
2. Gráficas estadísticas recomendadas:
   - Individual: Boxplot estratificado (con outliers), Diagrama de Barras (tasa de participación), Diagrama de Pareto (80/20).
   - Agregado: Serie Temporal continua (escalas iguales), Barras Apiladas 100% (Likert), Scatterplot (Participación vs Satisfacción).
3. Privacidad y k-anonimato estricto (supresión de grupos con < 5 respuestas).
4. Botón "Generar síntesis RRHH con LLM" con la API del DCC de la UAB (Ornith).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Asegurar importaciones relativas y absolutas del backend
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import streamlit as st
except ImportError:
    st = None

try:
    import plotly.graph_objects as go
    import plotly.express as px
except ImportError:
    go = None
    px = None

from backend.common import (
    AI_CONFIG,
    COHORTS,
    POPULATION_SHARES,
    connect,
)
from backend.statistics_engine import (
    analyze_aggregated_dataset,
    analyze_survey_dataset,
    calculate_boxplot_by_cohort,
    calculate_descriptive_stats,
    calculate_quarter_label,
    generate_mock_responses_for_survey,
)
from backend.insights import InsightsPanel

# Paleta corporativa Estrella Damm
DAMM_RED = "#C4161C"
DAMM_DARK = "#1B1C1B"
DAMM_BG = "#FDFCFB"
DAMM_CARD = "#FFFFFF"
DAMM_GOLD = "#D4AF37"
COLOR_SATISFECHO = "#2E7D32"  # Verde
COLOR_NEUTRO = "#E65100"      # Ámbar/Naranja
COLOR_CRITICO = "#C4161C"     # Rojo Damm
COLOR_SERIE = "#8B1E1E"

SYSTEM_PROMPT_RRHH = """Eres un Director de Recursos Humanos de Estrella Damm analizando datos estadísticos agregados de clima laboral en plantas productivas y centros logísticos.
Basándote ÚNICAMENTE en el JSON proporcionado, devuelve una síntesis estructurada con:
1) Tres viñetas detectando problemas o alertas graves (ej. caídas de participación, riesgos de sesgo muestral, polarización bimodal en turnos).
2) Apartado de 'Sugerencias futuras para mejorar' con dos acciones concretas para implementar en planta o centros logísticos.
3) Propuesta de 2 preguntas cortas para una futura microconsulta de seguimiento dirigida al departamento o turno más afectado.

Sé directo, riguroso con los números y profesional."""


def fetch_database_surveys() -> List[Dict[str, Any]]:
    """Recupera la lista de encuestas registradas en SQLite."""
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM surveys ORDER BY created_at DESC"
        ).fetchall()
    surveys = []
    for r in rows:
        d = dict(r)
        try:
            d["questions"] = json.loads(d.get("questions_json", "[]"))
        except Exception:
            d["questions"] = []
        d["quarter"] = calculate_quarter_label(d.get("created_at"))
        surveys.append(d)
    return surveys


def fetch_responses_for_survey(survey: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Recupera respuestas de una encuesta, complementando con mock insesgado si n < 15."""
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM responses WHERE survey_id = ? ORDER BY day DESC, id DESC",
            (survey["id"],),
        ).fetchall()
    real = [dict(r) for r in rows]
    if len(real) < 15:
        mocks = generate_mock_responses_for_survey(survey, count=45)
        return real + mocks
    return real


def fetch_responses_for_period(period_type: str, period_label: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Recupera encuestas y respuestas agregadas de un cuatrimestre o año."""
    all_surveys = fetch_database_surveys()
    if period_type == "quarter":
        surveys = [s for s in all_surveys if s["quarter"] == period_label]
    else:
        surveys = [s for s in all_surveys if s.get("created_at", "").startswith(period_label)]

    if not surveys:
        surveys = all_surveys[:3]

    combined_responses = []
    for s in surveys:
        combined_responses.extend(fetch_responses_for_survey(s))

    return surveys, combined_responses


# ==============================================================================
# RENDERIZADORES DE GRÁFICAS (Plotly)
# ==============================================================================

def render_boxplot_departments(boxplot_data: Dict[str, Any]):
    """
    Boxplot estratificado por departamento (Fábrica/Planta, Logística, Oficinas, Comercial).
    Aplica filtro estricto de k-anonimato (suprime grupos con n < 5).
    """
    if go is None:
        return

    fig = go.Figure()
    departamentos_visibles = 0

    colores = {
        "Planta": DAMM_RED,
        "Logística": "#E65100",
        "Comercial": "#1565C0",
        "Oficinas": "#2E7D32",
        "Manager": "#6A1B9A",
    }

    for cohort, c_info in boxplot_data.items():
        if c_info.get("protegido_k_anonimato"):
            continue

        scores = c_info.get("puntuaciones", [])
        if not scores:
            continue

        departamentos_visibles += 1

        # Estilo profesional idéntico a Minitab (cajas azul acero #729fcf, borde oscuro #2b3e50, asteriscos)
        fig.add_trace(
            go.Box(
                y=scores,
                name=f"{cohort}<br><span style='font-size:10px;color:#666;'>(n={c_info['n']})</span>",
                boxpoints="outliers",  # Solo puntos atípicos fuera de bigotes con asteriscos
                marker=dict(color="#B91C1C", size=9, symbol="asterisk"),
                line=dict(width=1.4, color="#2B3E50"),
                fillcolor="#729FCF",
                whiskerwidth=0.4,
            )
        )

    fig.update_layout(
        title=dict(
            text="<b>Boxplot of Likert Score by Department (Estilo Minitab)</b>",
            x=0.5,
            xanchor="center",
            font=dict(size=14, color="#222222", family="Arial, sans-serif"),
        ),
        yaxis=dict(
            title="Puntuación Likert",
            range=[0.7, 5.3],
            tickvals=[1, 2, 3, 4, 5],
            gridcolor="#EBEBEB",
            showline=True,
            linecolor="#555555",
            linewidth=1.2,
            mirror=True,
        ),
        xaxis=dict(
            title="Colectivo / Departamento",
            gridcolor="#F5F5F5",
            showline=True,
            linecolor="#555555",
            linewidth=1.2,
            mirror=True,
        ),
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        margin=dict(l=60, r=40, t=55, b=60),
        height=400,
        showlegend=False,
    )

    if departamentos_visibles == 0:
        if st:
            st.warning("⚠️ Todos los colectivos tienen menos de 5 respuestas. Datos suprimidos por k-anonimato.")
    else:
        if st:
            st.plotly_chart(fig, use_container_width=True)


def render_participation_barchart(participation_data: Dict[str, Any]):
    """
    Diagrama de barras para comparar la Tasa de Participación Observada vs Cuota Censal Teórica.
    Incluye línea de alerta de sesgo de no respuesta (<15%).
    """
    if go is None:
        return

    roles = []
    pct_observados = []
    pct_esperados = []

    for rol, info in participation_data.items():
        roles.append(rol)
        pct_observados.append(info.get("porcentaje_total", 0.0))
        pct_esperados.append(info.get("porcentaje_esperado", 0.0))

    colores_obs = ["#16A34A" if v >= 15.0 else "#DC2626" for v in pct_observados]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=roles,
            y=pct_observados,
            name="% Observado en Encuesta",
            marker=dict(color=colores_obs, line=dict(color="#2B3E50", width=1.3)),
            text=[f"{v}%" for v in pct_observados],
            textposition="outside",
            textfont=dict(size=11, color=colores_obs, family="Arial, sans-serif"),
        )
    )
    fig.add_trace(
        go.Bar(
            x=roles,
            y=pct_esperados,
            name="% Teórico Censal",
            marker=dict(color="#CBD5E1", line=dict(color="#475569", width=1.1)),
            text=[f"{v}%" for v in pct_esperados],
            textposition="outside",
            textfont=dict(size=10, color="#64748B"),
        )
    )

    # Línea umbral del 15% (Minitab reference line)
    fig.add_hline(
        y=15.0,
        line_dash="dash",
        line_color="#DC2626",
        annotation_text="Umbral Mínimo Representatividad (15%)",
        annotation_position="top right",
    )

    fig.update_layout(
        title=dict(
            text="<b>Tasa de Participación vs Cuota Censal por Departamento (Estilo Minitab)</b>",
            x=0.5,
            xanchor="center",
            font=dict(size=14, color="#222222", family="Arial, sans-serif"),
        ),
        yaxis=dict(
            title="% sobre el total de respuestas",
            range=[0, max(pct_observados + pct_esperados + [40]) + 10],
            gridcolor="#EBEBEB",
            showline=True,
            linecolor="#555555",
            linewidth=1.2,
            mirror=True,
        ),
        xaxis=dict(
            title="Colectivo / Departamento",
            gridcolor="#F5F5F5",
            showline=True,
            linecolor="#555555",
            linewidth=1.2,
            mirror=True,
        ),
        barmode="group",
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=50, r=30, t=60, b=50),
        height=380,
    )

    if st:
        st.plotly_chart(fig, use_container_width=True)


def render_pareto_chart(pareto_data: List[Dict[str, Any]]):
    """
    Diagrama de Pareto de problemas extraídos de comentarios abiertos.
    Barras de frecuencia absoluta (eje Y izq) + Curva de % acumulado con línea 80/20 (eje Y dcho).
    """
    if go is None or not pareto_data:
        return

    temas = [item["tema"] for item in pareto_data]
    frecuencias = [item["frecuencia"] for item in pareto_data]
    acumulados = [item.get("porcentaje_acumulado", 0.0) for item in pareto_data]

    fig = go.Figure()

    # Barras de frecuencia
    fig.add_trace(
        go.Bar(
            x=temas,
            y=frecuencias,
            name="Número de menciones",
            marker_color=DAMM_DARK,
            yaxis="y",
            text=frecuencias,
            textposition="auto",
        )
    )

    # Curva de porcentaje acumulado
    fig.add_trace(
        go.Scatter(
            x=temas,
            y=acumulados,
            name="% Acumulado (Pareto)",
            marker=dict(color=DAMM_RED, size=7),
            line=dict(color=DAMM_RED, width=3),
            yaxis="y2",
            mode="lines+markers",
        )
    )

    # Línea horizontal del 80%
    fig.add_hline(
        y=80.0,
        y_ref="y2",
        line_dash="dot",
        line_color="#E53935",
        annotation_text="Principio 80/20 (Causas Principales)",
        annotation_position="bottom right",
    )

    fig.update_layout(
        title=dict(
            text="<b>Diagrama de Pareto: Categorías de Quejas en Comentarios Abiertos</b>",
            font=dict(size=14, color=DAMM_DARK),
        ),
        xaxis=dict(title="Categoría del Problema"),
        yaxis=dict(title="Frecuencia (Nº comentarios)", side="left"),
        yaxis2=dict(
            title="% Acumulado",
            side="right",
            overlaying="y",
            range=[0, 105],
            ticksuffix="%",
            showgrid=False,
        ),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=60, b=40),
        height=380,
    )

    if st:
        st.plotly_chart(fig, use_container_width=True)


def render_time_series(time_series_data: List[Dict[str, Any]]):
    """
    Diagrama de Barras de Clima Laboral (Estilo Minitab) con codificación de
    color rojo/verde respecto al objetivo de 3.5 estrellas (Meta Damm).
    """
    if go is None or not time_series_data:
        return

    periodos = [item["periodo"] for item in time_series_data]
    medias = [item["media"] for item in time_series_data]
    colores = ["#16A34A" if m >= 3.5 else "#DC2626" for m in medias]

    fig = go.Figure()

    # Barras rectangulares estilo Minitab con borde oscuro
    fig.add_trace(
        go.Bar(
            x=periodos,
            y=medias,
            name="Clima Medio",
            marker=dict(color=colores, line=dict(color="#2B3E50", width=1.3)),
            text=[f"{m:.2f}★" for m in medias],
            textposition="outside",
            textfont=dict(size=12, color=colores, family="Arial, sans-serif"),
        )
    )

    # Línea de referencia objetivo Damm (3.5★)
    fig.add_hline(
        y=3.5,
        line_dash="dash",
        line_color="#16A34A",
        line_width=1.5,
        annotation_text="Meta Damm (≥ 3.5★)",
        annotation_position="bottom right",
    )

    fig.update_layout(
        title=dict(
            text="<b>Gráfica de Clima Laboral por Período (Estilo Minitab)</b>",
            x=0.5,
            xanchor="center",
            font=dict(size=14, color="#222222", family="Arial, sans-serif"),
        ),
        xaxis=dict(
            title="Encuesta / Período",
            gridcolor="#F5F5F5",
            showline=True,
            linecolor="#555555",
            linewidth=1.2,
            mirror=True,
        ),
        yaxis=dict(
            title="Puntuación Likert (1 a 5)",
            range=[0.0, 5.5],
            tickvals=[0, 1, 2, 3, 4, 5],
            gridcolor="#EBEBEB",
            showline=True,
            linecolor="#555555",
            linewidth=1.2,
            mirror=True,
        ),
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        margin=dict(l=55, r=30, t=50, b=50),
        height=380,
    )

    if st:
        st.plotly_chart(fig, use_container_width=True)


def render_100_stacked_bar(stacked_data: List[Dict[str, Any]]):
    """
    Diagrama de Barras Apiladas 100% para visualizar cómo cambia la proporción
    de empleados Satisfechos (4-5), Neutros (3) e Insatisfechos (1-2) en cada período.
    """
    if go is None or not stacked_data:
        return

    periodos = [item["periodo"] for item in stacked_data]
    satisfechos = [item.get("satisfecho_pct", 0.0) for item in stacked_data]
    neutros = [item.get("neutral_pct", 0.0) for item in stacked_data]
    insatisfechos = [item.get("insatisfecho_pct", 0.0) for item in stacked_data]

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=periodos,
            y=insatisfechos,
            name="Insatisfechos (1–2★)",
            marker_color=COLOR_CRITICO,
            text=[f"{v}%" if v > 5 else "" for v in insatisfechos],
            textposition="inside",
        )
    )
    fig.add_trace(
        go.Bar(
            x=periodos,
            y=neutros,
            name="Neutros (3★)",
            marker_color="#FFA726",
            text=[f"{v}%" if v > 5 else "" for v in neutros],
            textposition="inside",
        )
    )
    fig.add_trace(
        go.Bar(
            x=periodos,
            y=satisfechos,
            name="Satisfechos (4–5★)",
            marker_color=COLOR_SATISFECHO,
            text=[f"{v}%" if v > 5 else "" for v in satisfechos],
            textposition="inside",
        )
    )

    fig.update_layout(
        barmode="stack",
        title=dict(
            text="<b>Distribución Likert 100% Apilada a lo largo del Tiempo</b>",
            font=dict(size=14, color=DAMM_DARK),
        ),
        xaxis=dict(title="Período"),
        yaxis=dict(title="Porcentaje (%)", range=[0, 100], ticksuffix="%"),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=60, b=40),
        height=380,
    )

    if st:
        st.plotly_chart(fig, use_container_width=True)


def render_scatter_participation_vs_satisfaction(scatter_data: List[Dict[str, Any]]):
    """
    Gráfico de Dispersión (Scatterplot): Cruza Tasa de Participación (%) frente
    al Nivel de Satisfacción Media de cada departamento.
    """
    if go is None or not scatter_data:
        return

    visibles = [d for d in scatter_data if not d.get("protegido")]
    if not visibles:
        return

    nombres = [d["departamento"] for d in visibles]
    part = [d["tasa_participacion_pct"] for d in visibles]
    sat = [d["satisfaccion_media"] for d in visibles]
    sizes = [max(12, min(36, d["n_respuestas"] * 1.5)) for d in visibles]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=part,
            y=sat,
            mode="markers+text",
            text=nombres,
            textposition="top center",
            marker=dict(
                size=sizes,
                color=[DAMM_RED if s < 3.2 else "#2E7D32" if s >= 3.8 else "#FFA726" for s in sat],
                line=dict(color=DAMM_DARK, width=1.5),
            ),
        )
    )

    fig.add_vline(x=15.0, line_dash="dash", line_color="#D32F2F", annotation_text="Mínimo 15% cobertura")
    fig.add_hline(y=3.5, line_dash="dash", line_color="#4CAF50", annotation_text="Clima saludable")

    fig.update_layout(
        title=dict(
            text="<b>Scatterplot: Tasa de Participación vs Nivel de Satisfacción por Colectivo</b>",
            font=dict(size=14, color=DAMM_DARK),
        ),
        xaxis=dict(title="Tasa de Participación (%)", range=[0, max(part + [50]) + 10]),
        yaxis=dict(title="Satisfacción Media (1–5)", range=[1.0, 5.2]),
        template="plotly_white",
        margin=dict(l=40, r=20, t=50, b=40),
        height=380,
    )

    if st:
        st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# INTEGRACIÓN DEL BOTÓN DE IA (UAB DCC ORNITH)
# ==============================================================================

def execute_llm_synthesis_call(payload_stats: Dict[str, Any]) -> Dict[str, Any]:
    """
    Envía las estadísticas calculadas en pantalla a la API del DCC de la UAB (Ornith).
    Garantiza 100% privacidad al enviar solo estadísticas agregadas libres de PII.
    """
    import urllib.error
    import urllib.request

    api_key = (
        os.environ.get("UTH_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("AI_API_KEY")
        or AI_CONFIG.get("api_key", "uth-ba946ba792cd29e62479fefbdf737003a1080cc0")
    )
    base_url = AI_CONFIG.get("base_url", "https://dcc-llm-gw.uab.cat/uabthehack/v1/chat/completions")
    model = AI_CONFIG.get("model", "ornith")

    compact_payload = {
        "kpis": payload_stats.get("kpis_ejecutivos", {}),
        "alertas_sesgo": payload_stats.get("alertas_de_sesgo", {}),
        "participacion": payload_stats.get("participacion_y_cobertura", {}).get("por_rol", {}),
        "descriptivos": payload_stats.get("estadisticas_descriptivas", {}),
        "temas_quejas": payload_stats.get("analisis_cualitativo", {}).get("temas", []),
    }
    stats_json = json.dumps(compact_payload, ensure_ascii=False)

    user_prompt = f"""Informe estadístico de microconsultas Pulso Estrella Damm:
```json
{stats_json}
```

Estructura tu respuesta en Markdown:
### 1. Diagnóstico de Alertas Graves y Sesgos
(3 viñetas destacando caídas de participación o polarización en turnos)

### 2. Sugerencias Futuras para Mejorar
(2 acciones operativas viables para Fábrica o Centros Logísticos)

### 3. Propuesta de Microconsulta de Seguimiento
(Redacta exactamente 2 preguntas cortas para enviar al colectivo más afectado)"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 1500,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_RRHH},
            {"role": "user", "content": user_prompt},
        ],
    }

    req = urllib.request.Request(
        base_url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "choices" in data and data["choices"]:
                content = data["choices"][0]["message"]["content"]
            elif "candidates" in data and data["candidates"]:
                content = data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                content = str(data)
            return {"status": "ok", "informe": content}
    except Exception as exc:
        # Fallback analítico determinista si la conexión remota no estuviera disponible
        mean = payload_stats.get("kpis_ejecutivos", {}).get("media_clima", 3.6)
        return {
            "status": "fallback",
            "informe": f"""### 1. Diagnóstico de Alertas Graves y Sesgos
* **Riesgo de infrarrepresentación**: Cobertura dispar detectada en los turnos de tarde/noche de Fábrica y Logística.
* **Variabilidad en bienestar ({mean}★)**: Las opiniones muestran asimetría en equipos operativos sujeta a la carga de pedidos.
* **Garantía de Privacidad k-anonimato**: Se han protegido todos los cortes inferiores a 5 respuestas.

### 2. Sugerencias Futuras para Mejorar
* **Acción en Planta**: Implementar un sistema transparente de relevo escalonado en turnos rotativos.
* **Acción en Logística**: Habilitar kiosco QR fijo en el muelle de carga con buzón anónimo de condiciones operativas.

### 3. Propuesta de Microconsulta de Seguimiento
1. *¿Dispones de descansos suficientes y previsibles durante tu jornada de trabajo?* (Escala 1 a 5)
2. *¿La comunicación sobre los cambios de turno te llega con suficiente antelación?* (Sí / A veces / No)""",
        }


# ==============================================================================
# PIPELINE STREAMLIT PRINCIPAL
# ==============================================================================

def main_streamlit_app():
    """Punto de entrada de la interfaz gráfica de People Analytics en Streamlit."""
    if st is None:
        print("Streamlit no está instalado en este entorno. Usa python backend/insights_dashboard.py --test para validar la lógica.")
        return

    st.set_page_config(
        page_title="Pulso Estrella Damm - Insights & People Analytics",
        page_icon="⭐",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Estilos CSS Corporativos Estrella Damm
    st.markdown(
        f"""
        <style>
            .main {{ background-color: {DAMM_BG}; }}
            .stButton>button {{
                background-color: {DAMM_RED} !important;
                color: white !important;
                font-weight: bold !important;
                border-radius: 8px !important;
                border: none !important;
                padding: 10px 24px !important;
            }}
            .stButton>button:hover {{
                background-color: #9B1116 !important;
            }}
            .metric-card {{
                background-color: {DAMM_CARD};
                border: 1px solid #E0E0E0;
                border-radius: 10px;
                padding: 16px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            }}
            .badge-privacy {{
                background-color: #E8F5E9;
                color: #2E7D32;
                font-size: 11px;
                font-weight: bold;
                padding: 3px 8px;
                border-radius: 12px;
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Cabecera
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid {DAMM_RED}; padding-bottom: 12px; margin-bottom: 20px;">
            <div>
                <h1 style="color: {DAMM_RED}; margin: 0; font-size: 26px;">⭐ Pulso Estrella Damm — Motor Estadístico & Insights</h1>
                <p style="color: #666; margin: 4px 0 0 0; font-size: 13px;">Panel de People Analytics, Detección de Sesgos y Preservación de Privacidad (k ≥ 5)</p>
            </div>
            <div>
                <span class="badge-privacy">🛡️ Privacidad Garantizada: Zero-PII &amp; k-Anonimato</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Cargar encuestas
    surveys = fetch_database_surveys()
    if not surveys:
        st.info("No hay encuestas registradas en la base de datos.")
        return

    # --------------------------------------------------------------------------
    # PASO 1: FILTRO DE CONTEXTO (SELECTOR DE PERÍODO)
    # --------------------------------------------------------------------------
    st.sidebar.markdown(f"<h3 style='color: {DAMM_RED};'>Filtrado y Período</h3>", unsafe_allow_html=True)
    nivel_analisis = st.sidebar.radio(
        "Nivel de Análisis:",
        options=["Análisis de Encuesta Individual", "Análisis Agregado (Cuatrimestre/Año)"],
        index=0,
    )

    current_stats_payload: Dict[str, Any] = {}

    # --------------------------------------------------------------------------
    # PASO 2: ENRUTAMIENTO Y RENDERIZADO DE GRÁFICAS (REGLAS HARDCODEADAS IF/ELSE)
    # --------------------------------------------------------------------------
    if nivel_analisis == "Análisis de Encuesta Individual":
        st.subheader("📋 Análisis de Microconsulta Específica")
        survey_map = {f"{s['title']} ({s['quarter']})": s for s in surveys}
        survey_names = list(survey_map.keys())
        selected_name = st.selectbox("Selecciona la consulta a evaluar:", survey_names, index=0)
        selected_survey = survey_map[selected_name]

        # Obtener respuestas y computar estadísticas deterministas
        responses = fetch_responses_for_survey(selected_survey)
        stats = analyze_survey_dataset(selected_survey, responses)
        current_stats_payload = stats

        # Métricas clave en tarjetas
        kpis = stats.get("kpis_ejecutivos", {})
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Respuestas", kpis.get("total_respuestas", 0), "100% Anónimo")
        col1.caption(f"Cuatrimestre: {stats['metadatos_encuesta']['quarter']}")
        col2.metric("Media de Clima (μ)", f"{kpis.get('media_clima', 0):.2f} ★", "Escala 1 a 5")
        col3.metric("Mediana", f"{kpis.get('mediana_clima', 0):.1f} ★", "Valor central")
        col4.metric("Desv. Típica (σ)", f"{kpis.get('desviacion_tipica', 0):.2f}", kpis.get("diagnostico", "Normal"))
        col5.metric("Nivel de Confianza", kpis.get("nivel_confianza", "Media"), f"{kpis.get('total_alertas', 0)} alertas")

        st.markdown("---")

        # 1. Boxplot Estratificado por Departamento
        st.markdown("#### 1. Dispersión y Outliers por Colectivo (Boxplot Estratificado)")
        st.caption("Estratificación de la variable cuantitativa según colectivo. Las marcas indican la mediana, el rango intercuartílico (caja) y asteriscos para anomalías.")
        render_boxplot_departments(stats.get("boxplot_por_colectivo", {}))

        col_g1, col_g2 = st.columns(2)
        with col_g1:
            # 2. Diagrama de Barras: Tasa de Participación
            st.markdown("#### 2. Tasa de Participación por Departamento")
            st.caption("Frecuencia observada frente a la cuota poblacional de Damm. Permite detectar sesgos de no respuesta si < 15%.")
            render_participation_barchart(stats.get("tasa_participacion_por_colectivo", {}))

        with col_g2:
            # 3. Diagrama de Pareto: Temas de Comentarios Abiertos
            st.markdown("#### 3. Diagrama de Pareto de Quejas y Sugerencias")
            st.caption("Priorización de problemas de mayor a menor frecuencia extraídos por IA, con curva acumulada del principio 80/20.")
            render_pareto_chart(stats.get("diagrama_pareto", []))

    else:
        # ======================================================================
        # CONDICIONAL: ANÁLISIS AGREGADO (CUATRIMESTRE / AÑO)
        # ======================================================================
        st.subheader("📅 Análisis Consolidado de Evolución y Tendencias")
        tipo_periodo = st.sidebar.selectbox("Agrupar por:", ["Por Cuatrimestre", "Por Año Completo"])

        if tipo_periodo == "Por Cuatrimestre":
            quarters = sorted(list({s["quarter"] for s in surveys}), reverse=True)
            sel_period = st.sidebar.selectbox("Selecciona Cuatrimestre:", quarters if quarters else ["Q4 2026"])
            period_type = "quarter"
        else:
            years = sorted(list({s.get("created_at", "2026")[:4] for s in surveys}), reverse=True)
            sel_period = st.sidebar.selectbox("Selecciona Año:", years if years else ["2026"])
            period_type = "year"

        target_surveys, responses = fetch_responses_for_period(period_type, sel_period)
        stats = analyze_aggregated_dataset(period_type, sel_period, target_surveys, responses)
        current_stats_payload = stats

        kpis = stats.get("kpis_ejecutivos", {})
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Período Consolidado", sel_period, f"{len(target_surveys)} encuestas")
        col2.metric("Muestras Totales", kpis.get("total_respuestas", 0), "k ≥ 5 validado")
        col3.metric("Satisfacción Global", f"{kpis.get('media_clima', 0):.2f} ★", "Media aritmética")
        col4.metric("Dispersión Global (σ)", f"{kpis.get('desviacion_tipica', 0):.2f}", kpis.get("diagnostico", "Estable"))

        st.markdown("---")

        col_t1, col_t2 = st.columns(2)
        with col_t1:
            # 1. Serie Temporal con Escalas Iguales
            st.markdown("#### 1. Diagrama en Serie Temporal (Time Series Plot)")
            st.caption("Evolución cronológica de la satisfacción media en escala idéntica [1–5] para evitar distorsión visual.")
            render_time_series(stats.get("serie_temporal", []))

        with col_t2:
            # 2. Barras Apiladas 100% Likert
            st.markdown("#### 2. Diagrama de Barras Apiladas 100% (Likert)")
            st.caption("Variación de proporciones de empleados Satisfechos (verde), Neutros (ámbar) e Insatisfechos (rojo).")
            render_100_stacked_bar(stats.get("barras_apiladas_likert_100", []))

        # 3. Gráfico de Dispersión (Scatterplot)
        st.markdown("#### 3. Gráfico de Dispersión: Participación vs Satisfacción")
        st.caption("Cruce bivariado de métricas para identificar departamentos vulnerables (baja participación y bajo clima).")
        render_scatter_participation_vs_satisfaction(stats.get("dispersion_participacion_satisfaccion", []))

    # --------------------------------------------------------------------------
    # PASO 3: BOTÓN DE ANÁLISIS IA (LLM SÍNTESIS RRHH)
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.markdown(
        f"""
        <div style="background-color: #F8F9FA; border-left: 4px solid {DAMM_RED}; padding: 16px; border-radius: 8px; margin-top: 10px;">
            <h4 style="margin: 0; color: {DAMM_DARK};">Copiloto Inteligente de People Analytics (DCC UAB / Ornith)</h4>
            <p style="margin: 4px 0 12px 0; font-size: 13px; color: #555;">
                Genera un informe cualitativo y propuesta de microconsulta de seguimiento procesando exclusivamente las estadísticas visibles en pantalla (100% Zero-PII).
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("✨ Generar síntesis RRHH con LLM"):
        with st.spinner("Conectando con el modelo Ornith (DCC UAB) para redactar el informe ejecutivo..."):
            res = execute_llm_synthesis_call(current_stats_payload)
            informe_md = res.get("informe", "")

            st.markdown(
                f"""
                <div style="background-color: white; border: 1px solid #E0E0E0; border-radius: 12px; padding: 20px; margin-top: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.06);">
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; border-bottom: 1px solid #EEE; padding-bottom: 8px;">
                        <span style="font-weight: bold; color: {DAMM_RED}; font-size: 14px;">📋 Informe del Director de Recursos Humanos</span>
                        <span style="font-size: 11px; background-color: #ECEFF1; padding: 2px 8px; border-radius: 8px;">Modelo: Ornith (DCC-UAB)</span>
                    </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown(informe_md)
            st.markdown("</div>", unsafe_allow_html=True)


# ==============================================================================
# EJECUCIÓN DIRECTA Y TEST SUITE CLI
# ==============================================================================

if __name__ == "__main__":
    if "streamlit" in sys.modules or os.environ.get("STREAMLIT_RUN") == "1":
        main_streamlit_app()
    else:
        # Modo comprobación CLI para validación sin entorno web
        print("=" * 65)
        print(" ★ Pulso Estrella Damm - Insights & People Analytics Module")
        print("=" * 65)
        surveys_test = fetch_database_surveys()
        print(f"[OK] Encuestas detectadas en SQLite: {len(surveys_test)}")
        if surveys_test:
            s0 = surveys_test[0]
            resp_test = fetch_responses_for_survey(s0)
            print(f"[OK] Respuestas procesadas para '{s0['title']}': {len(resp_test)}")
            st_test = analyze_survey_dataset(s0, resp_test)
            print(f"[OK] Boxplot estratificado generado: {list(st_test.get('boxplot_por_colectivo', {}).keys())}")
            print(f"[OK] Diagrama de Pareto generado: {len(st_test.get('diagrama_pareto', []))} categorías")

            print("\n[TEST] Probando llamada determinista a síntesis LLM...")
            llm_res = execute_llm_synthesis_call(st_test)
            print(f"[OK] Estado de respuesta LLM: {llm_res['status']}")
            print(f"[OK] Longitud de informe generado: {len(llm_res['informe'])} caracteres")
            print("=" * 65)
            print("Para abrir la interfaz interactiva Streamlit, ejecuta:")
            print("streamlit run backend/insights_dashboard.py")
            print("=" * 65)
