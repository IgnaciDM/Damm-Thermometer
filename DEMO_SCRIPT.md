# Guion Oficial de Demostración (Pitch & Live Demo)
## Reto Estrella Damm — UAB The Hack! 2026
**Proyecto:** Pulso Damm — HR Climate & Intelligence Suite

---

## 1. Pitch de Apertura (30 segundos)
> *"En Estrella Damm, una plantilla de más de 5.000 personas no puede medirse con encuestas genéricas de 40 preguntas: el personal de fábrica en El Prat no tiene tiempo ni ordenadores para responder lo mismo que un analista en oficinas centrales. Al mismo tiempo, los promedios brutos ocultan la realidad: un 3.5 en clima puede esconder turnos de noche al límite y una fábrica polarizada.*
>
> *Nuestra solución resuelve esto en dos direcciones:*
> 1. *Un **Generador Asistido Omnicanal** que a partir de una sola necesidad de RRHH crea microconsultas hiperpersonalizadas por colectivo (QR ultrarrápido para planta, tarjeta Teams interactiva para oficinas).*
> 2. *Un **Motor Estadístico Determinista con Zero-PII** que no alucina números, desenmascara la polarización bimodal, calcula la media ponderada censal y descubre temas emergentes con rigor estadístico."*

---

## 2. Recorrido de la Demo en Vivo (3 minutos)

```
[Inicio: Dashboard] ──> [Generador Asistido] ──> [Insights: Escenarios A, B y C] ──> [Síntesis IA & Exportación PDF]
```

### Paso 1: Generador Asistido con IA (El Bucle Omnicanal)
1. **Acceder a la pestaña "Generador de Estudio"**:
   - En el área de texto escribir:  
     `"Evaluar la adaptación al nuevo sistema de turnos rotativos y descansos de verano"`
   - Seleccionar colectivos en el selector múltiple: **Fábrica**, **Logística** y **Oficinas**.
   - Hacer clic en **"Generar / Actualizar"**.
2. **Explicar la hiperpersonalización (Bucle Omnicanal)**:
   - Mostrar cómo el sistema invoca dinámicamente el modelo `ornith` del DCC UAB adaptando el prompt a cada colectivo:
     - **Pestaña Fábrica:** Microconsulta de 3 preguntas cerradas (escala 1 a 5), lenguaje directo, canal asignado: `QR en Vestuario`.
     - **Pestaña Oficinas:** Encuesta completa de 4 preguntas escala 1-5 + 1 pregunta abierta constructiva, canal asignado: `Tarjeta en Microsoft Teams / Enlace Web`.
   - Mostrar el **Preview en Vivo (Typeform Engine)** interactivo:
     - Escala Likert de 1 a 5 con botones de opción etiquetados correctamente de **A a E**.
     - Barra de progreso interactiva sincronizada.
   - Enseñar el **desplegable superior de colectivos**: permite modificar los departamentos tras el primer prompt y el sistema recalcula inmediatamente las consultas.
   - Enseñar los botones de salida:
     - **"Difundir en Teams"**: Muestra el modal con la **Adaptive Card (JSON)** lista para Microsoft 365.
     - **"Lanzar a Plantas / Typeform"**: Publica la encuesta y genera el código QR descargable (SVG/PNG) y enlace anónimo.

---

### Paso 2: Escenario A — Polarización Oculta en Planta
1. **Ir a la pestaña "Insights"**:
   - En el selector de datasets de prueba, seleccionar **"Escenario A: Polarización en Planta"** (o cargar el CSV de prueba correspondiente).
2. **Demostrar el valor para Dirección de Personas**:
   - Enseñar la tarjeta de alerta roja: **"Polarización Bimodal Detectada (σ = 1.38 ≥ 1.25)"** en la dimensión *Liderazgo*.
   - Explicar por qué es crítico: *"Si RRHH solo mira la media aritmética (3.2★), parece un colectivo 'regular'. Pero el boxplot y la distribución bimodal demuestran que la plantilla de envasado está dividida entre calificaciones extremas de 1 y 5. La IA no recomienda 'hacer un curso general', sino abrir mesas de diálogo cualitativo en los relevos de cuadrilla."*

---

### Paso 3: Escenario B — Representatividad y "¿Quién Falta?" (10% de la nota)
1. **Seleccionar en Insights "Escenario B: Infrarrepresentación y Turnos Desatendidos"**:
   - Enseñar la comparativa censal:
     - **Media Bruta:** $3.78\bigstar$ (engañosa por sobrerrepresentación de Oficinas).
     - **Media Ponderada Censal (Damm):** $3.12\bigstar$ (ponderada por la realidad de plantilla: 46% Planta, 22% Logística, 17% Comercial, 10% Oficinas, 5% Managers).
   - Mostrar la tarjeta **"¿Quién falta?"**:
     - Indica con exactitud el déficit numérico de respuestas (ej: *"Faltan 48 respuestas en Logística y 26 en Turno de Noche"*).
     - Alerta de sesgo de autoselección por baja participación ($< 15\%$).
     - Prescripción de RRHH: Reforzar el quiosco físico y los códigos QR en muelles de carga.

---

### Paso 4: Escenario C — Temas Emergentes y Rigor Estadístico
1. **Demostrar el análisis cualitativo avanzado**:
   - Mostrar el **Ranking de Temas Emergentes** con su cambio porcentual ($\Delta\%$) respecto al trimestre anterior:
     - *Climatización y Ergonomía*: $+18.4\%$ de menciones (tema emergente tras la ola de calor en fábrica).
     - *Herramientas y Carretillas*: $-12.1\%$ (problema en vías de resolución).
   - **Evaluación de Precisión (`tema_real`)**:
     - Mostrar el cálculo automático contra el ground-truth del reto: *"Precisión del clasificador: 94.6% sobre 150 comentarios etiquetados"*.
   - **Rigor Estadístico**:
     - Intervalos de confianza calculados mediante **Bootstrap empírico al 95%** ($B = 1000$ iteraciones).
     - Si la variación entre periodos no supera el test de hipótesis de Welch/permutaciones ($p \ge 0.05$), la interfaz muestra el distintivo:  
       `[Aviso: Variación de +0.08 no estadísticamente significativa (p = 0.24)]`. Evita que RRHH tome decisiones precipitadas basadas en ruido muestral.
2. **Privacidad Zero-PII**:
   - Enseñar cómo comentarios tipo *"pregúntale a Marta de noche"* o *"la única mujer del turno"* son anonimizados a `[NOMBRE_INTERNO]` y `[ROL_SINGULAR]` con NER antes de guardar en SQLite y antes de enviarse al LLM.
   - Demostrar que $k$-anonimato ($k \ge 5$) cuenta **empleados únicos** (`submission_key`), nunca filas de preguntas.
   - Política de retención automática de 90 días conforme al protocolo Damm.

---

### Paso 5: Síntesis Estratégica con IA y Exportación a PDF (Dirección de Personas)
1. **Generación de la Síntesis con IA**:
   - En **Insights**, hacer clic en el botón rojo **"Generar Síntesis con IA"**.
   - Se abre la ventana flotante (popup modal) con diseño ejecutivo y visual:
     - **Ribbon Superior de KPIs Deterministas:** Muestra ($n$), Clima Global (con IC 95%), Diagnóstico de Polarización y Dispersión ($\sigma$).
     - **Resumen Estadístico Breve:** Síntesis concisa para la dirección.
     - **Diagnóstico de Alertas y Sesgos:** Infrarrepresentación crítica desglosada por colectivos (Logística, Comercial, Oficinas).
     - **Plan de Acción Operativo de RRHH (Prioritario):** Tarjetas estructuradas con nivel de prioridad (*Inmediata*, *Alta*, *Seguimiento*) y pasos operativos concretos.
     - **Propuesta de Microconsulta de Seguimiento:** Preguntas recomendadas con escala Likert 1-5.
2. **Exportación a PDF**:
   - Hacer clic en **"Exportar PDF"** (disponible en la cabecera y pie del modal, así como en la tarjeta guardada al cerrar).
   - Genera automáticamente un documento PDF maquetado con la identidad corporativa de Estrella Damm, métricas consolidadas, alertas y plan de acción de People Analytics, listo para imprimir o enviar al comité de dirección.
3. **Persistencia y Control**:
   - Al cerrar el popup, la síntesis se mantiene en una tarjeta persistente al final del panel de Insights con 4 opciones rápidas: **Abrir Síntesis**, **Exportar PDF**, **Copiar** y **Eliminar**.

---

## 3. Plan del Vídeo de Respaldo (Backup Video)

> [!IMPORTANT]
> El checklist del hackathon exige un vídeo de respaldo por si falla la conexión en directo.

### Parámetros de Grabación
- **Herramienta:** OBS Studio o Loom (1080p, 60fps o 30fps).
- **Duración estricta:** Entre 2:30 y 3:00 minutos.
- **Audio:** Micrófono verificado con cancelación de ruido.
- **Resolución:** 1920x1080 (pantalla completa sin barras de marcadores ni pestañas innecesarias).

### Cronograma de Grabación
| Minuto | Escena | Qué mostrar en pantalla |
|---|---|---|
| **0:00 - 0:30** | Portada + Dashboard | Dashboard ejecutivo Damm, KPI cards, serie temporal con banda de confianza 95%. |
| **0:30 - 1:15** | Generador Omnicanal | Escribir prompt, seleccionar colectivos, mostrar la diferenciación Fábrica (3 QR) vs Oficinas (5 Teams), Typeform preview en vivo. |
| **1:15 - 1:55** | Escenario A (Planta) | Activar dataset de polarización, destacar $\sigma=1.38$ y la recomendación accionable de diálogo. |
| **1:55 - 2:30** | Escenario B (¿Quién falta?) | Media ponderada censal vs bruta, métrica de deficit "¿Quién falta?" y alerta de turno de noche. |
| **2:30 - 3:00** | Escenario C + Cierre | Temas emergentes con $\Delta\%$, precisión con `tema_real`, Bootstrap 95%, Zero-PII sanitization y conclusión. |

### Plan de Contingencia Offline
Si el jurado no dispone de conexión a internet o el gateway UAB DCC está saturado:
- El sistema cuenta con **Modo Local Determinista** automático: todos los cálculos estadísticos, bootstrap, ponderación censal y clasificación con fallback funcionan al 100% sin depender de servicios externos.
