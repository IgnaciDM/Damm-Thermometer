# Pulso — Estrella Damm Workplace Analytics & Survey Engine
*UAB THE HACK 2026 - Reto Estrella Damm*

**Pulso** es una plataforma integral de escucha activa y analítica continua diseñada específicamente para los cuatro colectivos de **Estrella Damm** (Fábrica/Planta, Logística, Oficinas y Comercial). 

Combina un diseño editorial cervecero (**Mediterranean Brew & Analytics**), un **motor estadístico 100% determinista** (para evitar alucinaciones con números), un generador asistido de microconsultas con publicación directa a **Typeform** / QR de planta, y gobernanza con aprobación humana obligatoria (*Human-in-the-Loop*).

---

## Características Principales

1. **Diseño "Mediterranean Brew & Analytics":**
   - Paleta cervecera corporativa: Rojo Estrella Damm (`#c4161c`), Dorado Malta (`#fed65b` / `#d4af37`), Blanco Espuma (`#fbf9f7`) y Tostado Espresso (`#1e1611`).
   - Tipografía dual: *Epilogue* (titulares y jerarquía editorial) + *Inter* (datos y analítica).
   - Barra lateral retráctil (260px $\leftrightarrow$ 68px) con persistencia en `localStorage`.
   - Navegación SPA por paneles con botón activo resaltado en Rojo Damm.

2. **Arquitectura Modular por Clases (`backend/app.py`):**
   - `DashboardPanel`: Vista principal con KPIs, filtros por colectivo y k-anonimidad ($k \ge 5$).
   - `InsightsPanel`: Conexión directa con el motor estadístico determinista y síntesis RRHH con LLM sin alucinaciones.
   - `GeneratorPanel`: Generador asistido de microconsultas cortas con vista previa interactiva.
   - `AdminPanel`: Historial de encuestas, trazabilidad y aprobación humana de acciones sugeridas.
   - `SettingsPanel`: Configuración de credenciales seguras (Typeform y LLM).

3. **Motor Estadístico Determinista (`statistical_engine.py`):**
   - Separación estricta entre cálculo numérico (NumPy/Pandas/SciPy) y redacción del LLM.
   - Tasa de cobertura cruzada por Rol y Turno (Mañana, Tarde, Noche).
   - Estadísticos descriptivos (media, mediana, desviación típica, varianza, asimetría).
   - Detección automática de sesgos de representatividad ($<15\%$) y alertas de polarización ($\sigma \ge 1.25$).
   - Garantía de Cero Fugas de PII (los hashes de empleados y textos individuales se descartan antes del output).

4. **Conexión Directa con Typeform & QR de Planta:**
   - Publicación en 1-clic a la API de Typeform con lógica condicional integrada.
   - Generación de códigos QR para cartelería en plantas cerveceras y centros logísticos.

---

## Cómo Iniciar

Requiere Python 3.10 o superior:

```powershell
# 1. Instalar dependencias
python -m pip install -r requirements.txt

# 2. Iniciar el servidor
python -m backend.app
```

Abre en tu navegador:
- **Dashboard Principal:** [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Generador Asistido:** [http://127.0.0.1:8000/generator](http://127.0.0.1:8000/generator)
- **Gestión y Acciones:** [http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin)

### Dictado de instrucciones con Vosk

En el generador, pulsa **Dictar con Vosk**, concede permiso para usar el micrófono y dicta una instrucción de hasta 45 segundos. El texto reconocido aparece progresivamente en el cuadro de instrucciones mientras hablas y se finaliza al detener el dictado. Los fragmentos de audio se transcriben con Vosk en el servidor local; el audio no se guarda. Revisa el texto antes de generar preguntas.

El modelo español `vosk-model-small-es-0.42` no se incluye en Git por su tamaño. Descárgalo y descomprímelo; configura `VOSK_MODEL_PATH` en `.env` con la ruta al directorio del modelo. La ruta predeterminada es `ui/assets/vosk-model-small-es-0.42/vosk-model-small-es-0.42`. El dictado requiere un navegador con soporte de micrófono y permiso concedido; `localhost` funciona sin HTTPS.

```text
backend/
  app.py                 Python server, API, and SQLite persistence
  scripts/
    qrgenerator.py       Optional QR image generator
ui/
  index.html             Aggregate dashboard and check-in interface
  generator.html         AI-assisted survey studio and live Typeform preview
  app.js                 Dashboard and respondent interactions
  admin.html             HR survey design and insights
  admin.js               Survey editor and admin API client
  admin.css              Responsive admin design
  styles.css             Responsive visual design
  assets/
    codigo_qr.png        Optional static QR image
statistical_engine.py    Deterministic statistical engine (bias & polarization detection)
llm_insights.py          Prompt engineering & LLM synthesis without hallucinations
tests/
  test_app.py            Backend and API tests
  test_statistical_engine.py Unit tests for math & privacy
data/
  pulso.sqlite3          Local response database (created at runtime)
```

The server serves the UI directly; no separate frontend build is required. The respondent experience is a Typeform-style, one-question-at-a-time conversation. Existing root-level database files are moved to `data/pulso.sqlite3` on the next launch, without overwriting an existing database there.

## Generación con Inteligencia Artificial (Servidor LLM DCC - UAB The Hack!)

Pulso está integrado directamente con el cluster de GPUs del **Departament de Ciències de la Computació (DCC) de la UAB**, utilizando el modelo **Ornith 1.5** (35B MoE, 262k tokens de contexto).

Para arrancar el servidor con la clave oficial de la UAB:

```powershell
# Definir la clave UTH_KEY proporcionada a tu equipo (o introducirla directamente en la pestaña de Configuración)
$env:UTH_KEY = "tu_clave_api_uth_aqui"
$env:AI_MODEL = "ornith"
$env:AI_BASE_URL = "https://dcc-llm-gw.uab.cat/uabthehack/v1/chat/completions"

# Iniciar servidor
python -m backend.app
```

*(También puedes introducir la clave directamente en la sección **Configuración** de la plataforma web o guardarla en el archivo `.env`).*

`PULSO_PUBLIC_URL` is needed when local surveys/QR codes must be reachable outside the machine running Pulso. Without it, local links use the request host. Typeform-hosted surveys use Typeform's public link.

The server binds to `127.0.0.1` only. Do not expose this prototype's HR endpoints to a network without adding authentication and deployment hardening. Responses are stored locally for up to 90 days; names, email addresses, phone numbers, URLs, and common self-identifying phrases are removed from free-text comments before storage. Only aggregated results are shown, and groups under five responses are hidden. Topic labels are keyword-based signals, not causal findings.

## Verify

```powershell
python -m unittest discover -s tests -v
```
