"""
Generador Asistido Omnicanal de Microconsultas - Estrella Damm People Analytics
Reto Hackathon 2026: Adaptación contextual hiperpersonalizada por Colectivo (Fábrica, Logística, Comercial, Oficinas)

Ejecución:
    streamlit run generador_omnicanal_app.py
"""

import os
import json
import streamlit as st
from openai import OpenAI

# -----------------------------------------------------------------------------
# CONFIGURACIÓN GENERAL Y ESTILO STREAMLIT (ESTRELLA DAMM BRANDING)
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Damm | Generador Asistido Omnicanal",
    page_icon="⭐",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Inyección de estilos Damm Red (#C41224) y diseño People Analytics
st.markdown("""
<style>
    .main-header {
        color: #C41224;
        font-weight: 800;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        color: #555555;
        font-size: 0.95rem;
        margin-bottom: 1.5rem;
    }
    .channel-badge-qr {
        background-color: #FEE2E2;
        color: #991B1B;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 0.8rem;
        display: inline-flex;
        align-items: center;
        gap: 5px;
    }
    .channel-badge-sms {
        background-color: #FEF3C7;
        color: #92400E;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 0.8rem;
        display: inline-flex;
        align-items: center;
        gap: 5px;
    }
    .channel-badge-teams {
        background-color: #E0E7FF;
        color: #3730A3;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 0.8rem;
        display: inline-flex;
        align-items: center;
        gap: 5px;
    }
    .survey-card {
        background-color: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 12px;
        padding: 1.25rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.04);
        margin-bottom: 1rem;
    }
    .question-item {
        background-color: #F9FAFB;
        border-left: 4px solid #C41224;
        padding: 10px 14px;
        border-radius: 4px;
        margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# SIDEBAR: CONFIGURACIÓN DE CONEXIÓN AL GATEWAY LLM
# -----------------------------------------------------------------------------
st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/2/25/Estrella_Damm_logo.png", width=140) if False else None
st.sidebar.markdown("### ⭐ Panel de Control LLM")

default_api_key = os.environ.get("UTH_KEY", "")
api_key_input = st.sidebar.text_input(
    "API Key (UTH_KEY)",
    value=default_api_key,
    type="password",
    help="Clave del Gateway LLM de la UAB (variable de entorno UTH_KEY)."
)

base_url = "https://dcc-llm-gw.uab.cat/uabthehack/v1"
model_name = "ornith"

st.sidebar.info(
    f"**Endpoint:** `{base_url}`\n\n"
    f"**Modelo:** `{model_name}` (35B MoE)\n\n"
    f"**Modo:** `json_object` estricto"
)

# -----------------------------------------------------------------------------
# FUNCIÓN GENERADORA DEL SYSTEM PROMPT DINÁMICO POR COLECTIVO
# -----------------------------------------------------------------------------
def build_system_prompt_for_cohort(colectivo: str) -> str:
    """
    Construye la directriz especializada para el modelo según el perfil de puesto
    y las condiciones operativas de cada colectivo de Estrella Damm.
    """
    base_instructions = (
        "Eres un consultor experto en People Analytics y Clima Laboral de Estrella Damm. "
        "Debes generar una encuesta hiperpersonalizada adaptada a la realidad del colectivo especificado. "
        "Devuelve EXCLUSIVAMENTE un objeto JSON válido con la siguiente estructura:\n"
        "{\n"
        '  "colectivo": "Nombre del colectivo",\n'
        '  "titulo_encuesta": "Título corto y motivador",\n'
        '  "canal_recomendado": "Canal recomendado",\n'
        '  "preguntas": [\n'
        '    {"enunciado": "Texto de la pregunta", "tipo": "escala_1_5 | texto_abierto"}\n'
        "  ]\n"
        "}\n\n"
    )

    if colectivo == "Fábrica":
        rules = (
            "REGLAS OBLIGATORIAS PARA FÁBRICA (Operarios de planta El Prat y embotellado):\n"
            "- Formato: Microconsulta ultrarrápida. Máximo 3 preguntas cerradas (escala 1-5).\n"
            "- Tono y lenguaje: Muy directo, sencillo y cotidiano. Enfocado en ritmo de trabajo, descansos, temperatura y turnos.\n"
            "- Canal recomendado: Debes establecer 'canal_recomendado' estrictamente con el valor: 'QR en Vestuario'.\n"
            "- Restricción crítica: NO incluyas ninguna pregunta abierta que requiera escribir texto, no tienen ordenadores en línea."
        )
    elif colectivo in ["Logística", "Comercial"]:
        rules = (
            f"REGLAS OBLIGATORIAS PARA {colectivo.upper()} (Conductores, distribución y fuerza de ventas en ruta):\n"
            "- Formato: Microconsulta ultrarrápida. Máximo 3 preguntas cerradas (escala 1-5).\n"
            "- Tono y lenguaje: Muy directo, ágil y accionable. Enfocado en rutas, herramientas móviles y coordinación.\n"
            "- Canal recomendado: Debes establecer 'canal_recomendado' estrictamente con el valor: 'Enlace SMS/WhatsApp'.\n"
            "- Restricción crítica: NO incluyas preguntas abiertas que requieran escribir mucho texto; responderán desde el teléfono móvil en paradas breves."
        )
    elif colectivo == "Oficinas":
        rules = (
            "REGLAS OBLIGATORIAS PARA OFICINAS (Servicios centrales, finanzas, marketing y RRHH):\n"
            "- Formato: Encuesta completa. Puedes incluir hasta 4 preguntas de escala 1-5 y exactamente 1 pregunta de texto abierto.\n"
            "- Tono y lenguaje: Corporativo, reflexivo y profesional. Enfocado en flexibilidad, conciliación, liderazgo y alineación estratégica.\n"
            "- Canal recomendado: Debes establecer 'canal_recomendado' estrictamente con el valor: 'Tarjeta en Microsoft Teams / Enlace Web'.\n"
            "- La pregunta abierta debe invitar a sugerencias constructivas de mejora organizativa."
        )
    else:
        rules = (
            "Genera una consulta breve de 3 preguntas de escala 1-5 adaptada al colectivo. "
            "canal_recomendado: 'Portal Web'."
        )

    return base_instructions + rules

# -----------------------------------------------------------------------------
# FALLBACK DETERMINISTA DE RESPALDO (HACKATHON-SAFE MOCK)
# -----------------------------------------------------------------------------
def get_mock_survey_for_cohort(colectivo: str, prompt: str) -> dict:
    """Mecanismo de tolerancia a fallos por si la red del hackathon o el gateway caen."""
    clean_p = prompt.strip()
    if colectivo == "Fábrica":
        return {
            "colectivo": "Fábrica",
            "titulo_encuesta": f"Pulso de Planta: {clean_p[:35]}",
            "canal_recomendado": "QR en Vestuario",
            "preguntas": [
                {"enunciado": f"¿Cómo valoras el impacto en tu turno respecto a: {clean_p}?", "tipo": "escala_1_5"},
                {"enunciado": "¿Los tiempos de descanso en relevo te han permitido recuperarte adecuadamente?", "tipo": "escala_1_5"},
                {"enunciado": "¿Sientes que las medidas acordadas resuelven la carga física en tu puesto?", "tipo": "escala_1_5"}
            ]
        }
    elif colectivo == "Logística":
        return {
            "colectivo": "Logística",
            "titulo_encuesta": f"Pulso de Reparto: {clean_p[:35]}",
            "canal_recomendado": "Enlace SMS/WhatsApp",
            "preguntas": [
                {"enunciado": f"¿Cómo de eficiente ha sido la operativa hoy frente a: {clean_p}?", "tipo": "escala_1_5"},
                {"enunciado": "¿La comunicación sobre cambios en ruta o entregas llegó a tiempo a tu terminal?", "tipo": "escala_1_5"},
                {"enunciado": "¿Cuentas con las condiciones necesarias para completar tus franjas sin sobrecarga?", "tipo": "escala_1_5"}
            ]
        }
    elif colectivo == "Comercial":
        return {
            "colectivo": "Comercial",
            "titulo_encuesta": f"Pulso Comercial: {clean_p[:35]}",
            "canal_recomendado": "Enlace SMS/WhatsApp",
            "preguntas": [
                {"enunciado": f"¿Cómo calificarías el apoyo del área operativa respecto a: {clean_p}?", "tipo": "escala_1_5"},
                {"enunciado": "¿La información comercial y objetivos de campaña están claros para tus clientes?", "tipo": "escala_1_5"},
                {"enunciado": "¿Dispones de la agilidad necesaria en campo para gestionar incidencias con hostelería?", "tipo": "escala_1_5"}
            ]
        }
    else:  # Oficinas
        return {
            "colectivo": "Oficinas",
            "titulo_encuesta": f"Diagnóstico Corporativo: {clean_p[:35]}",
            "canal_recomendado": "Tarjeta en Microsoft Teams / Enlace Web",
            "preguntas": [
                {"enunciado": f"¿En qué medida consideras que {clean_p} favorece tu bienestar y conciliación?", "tipo": "escala_1_5"},
                {"enunciado": "¿Consideras que las decisiones tomadas sobre este aspecto son transparentes y equitativas?", "tipo": "escala_1_5"},
                {"enunciado": "¿Tu responsable directo te ha facilitado la flexibilidad necesaria para adaptarte?", "tipo": "escala_1_5"},
                {"enunciado": "¿Sientes que tus objetivos profesionales siguen alineados con el ritmo actual del equipo?", "tipo": "escala_1_5"},
                {"enunciado": "¿Qué medida específica propondrías a RRHH para mejorar este aspecto en tu día a día?", "tipo": "texto_abierto"}
            ]
        }

# -----------------------------------------------------------------------------
# FUNCIÓN DE LLAMADA INDEPENDIENTE AL LLM POR COLECTIVO
# -----------------------------------------------------------------------------
def generate_survey_for_cohort(client: OpenAI, colectivo: str, user_prompt: str) -> dict:
    """Invoca al LLM 'ornith' con formato JSON estructurado."""
    system_prompt = build_system_prompt_for_cohort(colectivo)
    
    try:
        response = client.chat.completions.create(
            model=model_name,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Necesidad de RRHH: '{user_prompt}'. Colectivo destinatario: {colectivo}."}
            ],
            temperature=0.3
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        
        # Validar consistencia de claves
        if not isinstance(data.get("preguntas"), list):
            raise ValueError("El JSON no incluye una lista en la clave 'preguntas'.")
        data.setdefault("colectivo", colectivo)
        data.setdefault("canal_recomendado", "Portal Web")
        return data

    except Exception as e:
        st.warning(f"Aviso en colectivo {colectivo}: Se utilizó motor de contingencia ({str(e)[:90]}).")
        return get_mock_survey_for_cohort(colectivo, user_prompt)

# -----------------------------------------------------------------------------
# INTERFAZ PRINCIPAL (UI)
# -----------------------------------------------------------------------------
st.markdown("<h1 class='main-header'>⭐ Generador Asistido Omnicanal</h1>", unsafe_allow_html=True)
st.markdown(
    "<p class='sub-header'>Generación hiperpersonalizada con IA (UAB Ornith 35B). Una única necesidad de RRHH "
    "se transforma en encuestas optimizadas para la realidad operativa, lenguaje y canal de cada puesto de trabajo.</p>",
    unsafe_allow_html=True
)

col_input1, col_input2 = st.columns([7, 5])

with col_input1:
    user_prompt = st.text_area(
        label="Describe qué quieres medir en esta nueva encuesta:",
        placeholder="Ej: Cómo llevan los trabajadores el nuevo sistema de descansos y turnicidad...",
        height=130,
        help="Escribe la necesidad de Recursos Humanos en lenguaje natural."
    )

with col_input2:
    colectivos_seleccionados = st.multiselect(
        label="Colectivos Objetivo:",
        options=["Fábrica", "Logística", "Comercial", "Oficinas"],
        default=["Fábrica", "Logística", "Oficinas"],
        help="El sistema generará una experiencia de encuesta distinta e independiente para cada colectivo seleccionado."
    )
    
    st.markdown("<br>", unsafe_allow_html=True)
    btn_generar = st.button("🚀 Generar Encuestas con IA", type="primary", use_container_width=True)

# -----------------------------------------------------------------------------
# EJECUCIÓN DEL BUCLE OMNICANAL Y RENDERIZADO DE RESULTADOS
# -----------------------------------------------------------------------------
if btn_generar:
    if not user_prompt.strip():
        st.error("⚠️ Por favor, introduce una necesidad o temática antes de generar las encuestas.")
    elif not colectivos_seleccionados:
        st.error("⚠️ Debes seleccionar al menos un Colectivo Objetivo.")
    else:
        effective_key = api_key_input.strip() or os.environ.get("UTH_KEY", "dummy-key")
        
        # Inicialización del cliente OpenAI según especificación
        client = OpenAI(
            base_url=base_url,
            api_key=effective_key
        )

        resultados = {}
        progress_bar = st.progress(0.0)
        status_text = st.empty()

        for idx, col in enumerate(colectivos_seleccionados):
            status_text.markdown(f"⏳ **Adaptando encuesta con IA para:** `{col}`...")
            survey_data = generate_survey_for_cohort(client, col, user_prompt)
            resultados[col] = survey_data
            progress_bar.progress((idx + 1) / len(colectivos_seleccionados))

        status_text.success("✅ ¡Encuestas omnicanal generadas exitosamente!")
        st.markdown("---")

        st.subheader("📋 Encuestas Hiperpersonalizadas por Colectivo")
        
        # Renderizado en Pestañas (Tabs)
        tabs = st.tabs([f"🏭 {c}" if c=="Fábrica" else f"🚛 {c}" if c=="Logística" else f"💼 {c}" if c=="Comercial" else f"🏢 {c}" for c in colectivos_seleccionados])

        for idx, col in enumerate(colectivos_seleccionados):
            data = resultados[col]
            with tabs[idx]:
                canal = data.get("canal_recomendado", "Portal Web")
                
                # Badge según el canal
                if "QR" in canal:
                    badge_html = f"<span class='channel-badge-qr'>🔲 Canal: {canal}</span>"
                elif "SMS" in canal or "WhatsApp" in canal:
                    badge_html = f"<span class='channel-badge-sms'>📱 Canal: {canal}</span>"
                else:
                    badge_html = f"<span class='channel-badge-teams'>💻 Canal: {canal}</span>"

                st.markdown(f"### {data.get('titulo_encuesta', 'Encuesta de Clima')} &nbsp; {badge_html}", unsafe_allow_html=True)
                
                # Métricas informativas de la encuesta generada
                num_q = len(data.get("preguntas", []))
                has_open = any(q.get("tipo") == "texto_abierto" for q in data.get("preguntas", []))
                
                m1, m2, m3 = st.columns(3)
                m1.metric("Preguntas Totales", f"{num_q} preguntas")
                m2.metric("Tiempo Estimado", "1 minuto" if num_q <= 3 else "2-3 minutos")
                m3.metric("Tipo de Respuesta", "100% Cerrada (Escala 1-5)" if not has_open else "Cerradas + 1 Abierta")

                st.markdown("#### Preguntas Diseñadas:")
                for p_idx, q in enumerate(data.get("preguntas", []), 1):
                    tipo_str = "⭐ Escala 1 al 5" if q.get("tipo") == "escala_1_5" else "📝 Texto abierto"
                    st.markdown(
                        f"""
                        <div class='question-item'>
                            <strong>{p_idx}. {q.get('enunciado')}</strong><br>
                            <span style='font-size:0.75rem; color:#6B7280; font-family:monospace;'>Formato: {tipo_str}</span>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                # Exportar JSON estructurado
                with st.expander(f"🔍 Ver JSON estructurado ({col})"):
                    st.json(data)
