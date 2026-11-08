<div align="center">

# 🍺 Damn Thermometer

### Escuchar mejor. Entender antes. Actuar con criterio.

Plataforma de escucha activa y analítica de clima laboral para **Estrella Damm**, creada para **UAB THE HACK 2026**.

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![UAB THE HACK 2026](https://img.shields.io/badge/UAB-THE%20HACK%202026-c4161c)
![PolyForm Free Trial](https://img.shields.io/badge/Licencia-PolyForm%20Free%20Trial-d4af37)

</div>

<p align="center">
  <img src="ui/assets/readme-insights.png" alt="Laboratorio de Insights con un escenario de encuesta sintético" width="100%">
</p>

<p align="center"><em>Prueba escenarios y archivos CSV sin mezclarlos con las respuestas reales.</em></p>

**Damn Thermometer** combina encuestas breves, análisis estadístico reproducible y asistencia de IA en una experiencia pensada para Fábrica, Logística, Comercial y Oficinas. Los números los calcula un motor determinista; la IA genera preguntas y redacta síntesis sobre esos números, pero no los inventa ni sustituye la revisión de RR. HH.

> 📋 **¿Eres del jurado?** Lee el [resumen del proyecto](RESUMEN_JURADO.md) (1 minuto) y el [guion de la demo](DEMO_SCRIPT.md) (3 minutos).

**[Características](#-qué-puedes-hacer) · [Capturas](#-damn-thermometer-por-dentro) · [Inicio rápido](#-inicio-rápido) · [Configuración](#-integraciones-opcionales) · [Estructura](#-estructura) · [Privacidad](#-privacidad-y-seguridad) · [Tests](#-verificación)**

---

## ✨ Qué puedes hacer

### Escuchar

- **Diseñar consultas en lenguaje natural**, por texto o por **dictado de voz local** (Vosk), y revisar una vista previa antes de lanzarlas.
- **Adaptar la encuesta a cada colectivo:** 3 preguntas rápidas para Fábrica, Logística y Comercial; encuesta completa con una pregunta abierta para Oficinas.
- **Llegar a todo el mundo:** enlace, código QR (para vestuarios y puestos sin ordenador), tarjeta de Microsoft Teams y publicación en Typeform.
- **Evitar la fatiga de encuestas:** se limita la frecuencia con la que un mismo colectivo recibe una consulta.

### Entender

- **Analizar con rigor:** medias, medianas, dispersión, intervalos de confianza (bootstrap), contraste de significación de los cambios entre periodos y alertas de polarización.
- **Corregir el sesgo de participación:** ponderación por el peso real de cada colectivo en la plantilla y análisis de **«¿Quién falta?»** por colectivo y turno.
- **Detectar temas emergentes** en los comentarios abiertos y medir la precisión del clasificador cuando el CSV incluye la etiqueta real.
- **Consultar por encuesta, cuatrimestre o año**, con comparativas entre periodos.
- **Sintetizar con IA:** un informe ejecutivo redactado sobre el JSON estadístico, con una síntesis determinista de respaldo si el modelo no está disponible.

### Actuar con criterio

- **Mantener control humano:** ninguna consulta se publica sin aprobación, y las acciones recomendadas se revisan antes de aplicarse.
- **Medir el impacto:** cada acción aprobada puede generar una microconsulta de seguimiento.
- **Probar antes de usar:** escenarios sintéticos o un CSV propio en un laboratorio aislado. La prueba no modifica Typeform ni se guarda como una encuesta real.

### Proteger a las personas

- Los resultados de grupos con menos de **5 respuestas** se ocultan (k-anonimato).
- Los comentarios se anonimizan antes de guardarse: correos, teléfonos, DNI/NIE, IBAN, enlaces y nombres propios.
- Las respuestas se eliminan automáticamente a los **90 días**.

## 🖥️ Damn Thermometer por dentro

<table>
  <tr>
    <td align="center" width="50%">
      <strong>Generador asistido</strong><br>
      <img src="ui/assets/readme-generator.png" alt="Generador asistido con vista previa de encuesta" width="100%">
    </td>
    <td align="center" width="50%">
      <strong>Resultados por pregunta</strong><br>
      <img src="ui/assets/readme-question-chart.png" alt="Carrusel de resultados con gráfico circular por pregunta" width="100%">
    </td>
  </tr>
</table>

Las vistas de Insights y del carrusel usan una muestra sintética; no incluyen respuestas reales.

## 🚀 Inicio rápido

Necesitas **Python 3.10 o superior**.

```powershell
git clone https://github.com/IgnaciDM/Damn.git
cd Damn
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m backend.app
```

En macOS o Linux, activa el entorno con:

```bash
source .venv/bin/activate
```

Abre **[http://127.0.0.1:8000](http://127.0.0.1:8000)**. El servidor sirve la interfaz y la API; no hace falta compilar un frontend. Sin credenciales puedes explorar el modo de demostración con datos sintéticos.

> 🎬 **Guion de demostración y pitch:** consulta [DEMO_SCRIPT.md](DEMO_SCRIPT.md) para el recorrido de 3 minutos, los 3 escenarios oficiales del jurado (Polarización en Planta, Representatividad censal y Temas emergentes) y las pautas del vídeo de respaldo.

## 🔌 Integraciones opcionales

Para configurar servicios externos, copia `.env.example` a `.env` y añade tus propias credenciales. También puedes configurarlas desde la sección **Configuración** de la aplicación; en ese caso solo duran mientras el servidor está en marcha.

| Variable | Para qué sirve |
|---|---|
| `UTH_KEY` | Acceso al modelo de lenguaje de la UAB. |
| `AI_MODEL`, `AI_BASE_URL` | Modelo y endpoint del servicio de IA. |
| `GEMINI_API_KEY` | Alternativa de proveedor para generación asistida. |
| `TYPEFORM_API_TOKEN` | Publicar encuestas e importar respuestas de Typeform. |
| `TEAMS_WEBHOOK_URL` | Enviar la tarjeta de la encuesta a un canal de Microsoft Teams. Sin él, se genera la tarjeta en modo simulación. |
| `THERMOMETER_PUBLIC_URL` | Crear enlaces y QR accesibles desde otros dispositivos. |
| `THERMOMETER_DATABASE` | Cambiar la ruta de la base de datos SQLite. |
| `VOSK_MODEL_PATH` | Ruta local al modelo Vosk de voz en español. |
| `PORT` | Puerto del servidor (8000 por defecto). |

Si no hay clave de IA o el servicio falla, la aplicación sigue funcionando con plantillas y síntesis deterministas.

### Dictado por voz

El generador puede transcribir instrucciones con Vosk, localmente y en streaming. Descarga y descomprime el modelo español `vosk-model-small-es-0.42` y configura `VOSK_MODEL_PATH` con su ubicación (la ruta predeterminada está en `.env.example`). El dictado necesita un navegador con permiso de micrófono; en `localhost` no hace falta HTTPS. Las grabaciones se limitan a 45 segundos y el audio no se guarda.

## 🗂️ Estructura

```text
backend/
  app.py                 Servidor HTTP y rutas de la API
  common.py              SQLite, configuración, anonimización y utilidades compartidas
  dashboard.py           Indicadores y agregados del dashboard
  insights.py            Análisis estadístico, laboratorio de datos y síntesis con IA
  statistics_engine.py   Motor estadístico determinista
  generator.py           Generación de encuestas, dictado por voz (Vosk) y tarjetas de Teams
  admin.py               Historial, aprobación, Typeform, QR y acciones de RR. HH.
  settings.py            Configuración de IA y Typeform
  scripts/
    qrgenerator.py       Generación de códigos QR para las encuestas
ui/
  index.html             Aplicación web (single page)
  styles.css             Estilos
  assets/                Logos, imágenes de marca y capturas del README
dataset/
  dataset_generator.py   Generador de datos sintéticos de clima laboral
test_dataset/            Datos de prueba y resultados esperados
  *.csv                      Respuestas sintéticas y entrada del motor estadístico
  expected_checks.json       Valores esperados para validar los cálculos
  invalid_payloads.json      Payloads inválidos para probar la validación
tests/
  test_app.py                    Pruebas de la API y la aplicación
  test_statistical_engine.py     Pruebas del motor estadístico
generador_omnicanal_app.py   Generador de encuestas omnicanal
DEMO_SCRIPT.md           Guion de la demo de 3 minutos
RESUMEN_JURADO.md        Resumen del reto y del proyecto
data/                    Base SQLite local, creada al ejecutar la aplicación
```

## 🔒 Privacidad y seguridad

- Los resultados agregados con menos de **5 respuestas** se suprimen, contando personas únicas y no filas.
- Las respuestas se almacenan localmente en SQLite y se conservan hasta **90 días**.
- Los comentarios libres se anonimizan antes de almacenarse o enviarse al modelo de IA.
- Los escenarios sintéticos y los CSV del laboratorio se analizan temporalmente: no se incorporan a la base de datos ni se publican en Typeform.
- El servidor escucha en `127.0.0.1`. **No expongas este prototipo a una red** sin añadir autenticación y medidas de endurecimiento.
- Nunca subas tu `.env` ni tus claves al repositorio.

## ✅ Verificación

Ejecuta la suite de pruebas desde la raíz del proyecto:

```powershell
python -m unittest discover -s tests -v
```

## 📄 Licencia

El proyecto utiliza **PolyForm Free Trial 1.0.0**. Consulta [LICENSE](LICENSE) antes de usarlo, modificarlo o distribuirlo.
