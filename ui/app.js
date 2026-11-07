const dimensionLabels = {
  bienestar: "Bienestar",
  liderazgo: "Liderazgo",
  desarrollo: "Desarrollo",
  cultura: "Cultura y pertenencia",
  relaciones: "Entorno y relaciones",
  condiciones: "Condiciones y organización",
};
const recommendations = {
  bienestar: "Probad una pausa breve por turno y revisad en una semana si os ayuda.",
  liderazgo: "Acordad 10 minutos de escucha al inicio del próximo turno.",
  desarrollo: "Reservad 15 minutos para compartir un aprendizaje del equipo.",
  cultura: "Cerrad la semana con un reconocimiento concreto entre compañeros.",
  relaciones: "Haced una ronda de apoyo: ¿quién necesita ayuda esta semana?",
  condiciones: "Elegid un obstáculo del turno y probad un cambio pequeño para resolverlo.",
};

const $ = (selector) => document.querySelector(selector);
let toastTimer;
const survey = {
  step: 0,
  site: "",
  dimension: "",
  score: 0,
  question: "",
  source: "",
  model: "",
  error: "",
  loading: false,
  campaign: null,
  answers: [],
};
let questionGeneration = 0;

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 3600);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "No se pudo completar la solicitud.");
  return payload;
}

function safeNumber(value) {
  return typeof value === "number" ? value.toFixed(1) : "—";
}

function renderTrend(trend) {
  const container = $("#trend-chart");
  const width = 560;
  const height = 100;
  const padX = 12;
  const padY = 12;
  const visible = trend.filter((point) => point.score !== null);
  if (!visible.length) {
    container.innerHTML = `<div class="empty-chart">Necesitamos al menos cinco respuestas para mostrar una tendencia.</div>`;
    return;
  }
  const coords = visible.map((point, index) => {
    const x = visible.length === 1 ? width / 2 : padX + index * (width - 2 * padX) / (visible.length - 1);
    const y = height - padY - (point.score - 1) / 4 * (height - 2 * padY);
    return { x, y, label: point.label, score: point.score };
  });
  const points = coords.map((point) => `${point.x},${point.y}`).join(" ");
  const area = `${coords[0].x},${height} ${points} ${coords.at(-1).x},${height}`;
  const guides = [0, 1, 2].map((index) => {
    const y = padY + index * (height - 2 * padY) / 2;
    return `<line x1="${padX}" y1="${y}" x2="${width - padX}" y2="${y}" stroke="#eee9e2" stroke-dasharray="3 5"/>`;
  }).join("");
  const markers = coords.map((point) => `<circle cx="${point.x}" cy="${point.y}" r="3.3" fill="#df352c" stroke="white" stroke-width="2"><title>${point.label}: ${point.score.toFixed(1)}</title></circle>`).join("");
  const labels = trend.map((point, index) => {
    const x = padX + index * (width - 2 * padX) / Math.max(1, trend.length - 1);
    return `<text x="${x}" y="${height}" text-anchor="middle" fill="#aaa198" font-size="9">${point.label}</text>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Puntuación media de los últimos siete días"><defs><linearGradient id="area-fill" x1="0" x2="0" y1="0" y2="1"><stop stop-color="#df352c" stop-opacity=".14"/><stop offset="1" stop-color="#df352c" stop-opacity="0"/></linearGradient></defs>${guides}<polygon points="${area}" fill="url(#area-fill)"/><polyline points="${points}" fill="none" stroke="#df594c" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>${markers}${labels}</svg>`;
}

function renderDimensions(dimensions) {
  const list = $("#dimension-list");
  list.replaceChildren();
  for (const dimension of dimensions) {
    const row = document.createElement("div");
    row.className = "dimension-row";
    const name = document.createElement("span");
    name.className = "dimension-name";
    name.textContent = dimension.label;
    const track = document.createElement("div");
    track.className = "dimension-track";
    const fill = document.createElement("div");
    fill.className = "dimension-fill";
    fill.style.width = dimension.score === null ? "0%" : `${dimension.score * 20}%`;
    track.append(fill);
    const score = document.createElement("span");
    score.className = "dimension-score";
    score.textContent = dimension.score === null ? "—" : dimension.score.toFixed(1);
    row.append(name, track, score);
    list.append(row);
  }
}

function renderSites(locations) {
  const grid = $("#site-grid");
  grid.replaceChildren();
  for (const site of ["Fábrica", "Logística", "Comercial", "Oficina"]) {
    const match = locations.find((location) => location.label === site);
    const item = document.createElement("div");
    item.className = "site-item";
    const label = document.createElement("span");
    label.textContent = site;
    const score = document.createElement("strong");
    score.textContent = match ? match.score.toFixed(1) : "—";
    const bar = document.createElement("i");
    bar.style.setProperty("--site-score", match ? `${match.score * 20}%` : "0%");
    item.append(label, score, bar);
    grid.append(item);
  }
}

function renderDashboard(data) {
  $("#demo-banner").hidden = !data.is_demo;
  $("#overall-score").textContent = safeNumber(data.overall);
  $("#response-count").textContent = data.responses_7d ?? "—";
  $("#trend-score").textContent = safeNumber(data.overall);
  const ranked = data.dimensions.filter((item) => item.score !== null).sort((a, b) => b.score - a.score);
  $("#top-dimension").textContent = ranked[0]?.short || "En escucha";
  $("#top-dimension-score").textContent = ranked[0] ? `${ranked[0].score.toFixed(1)} / 5` : "";
  const latest = data.trend.filter((point) => point.score !== null);
  const change = latest.length > 1 ? latest.at(-1).score - latest[0].score : null;
  const trend = $("#overall-trend");
  trend.textContent = change === null ? "En construcción" : `${change >= 0 ? "+" : ""}${change.toFixed(1)} pts`;
  trend.classList.toggle("down", change !== null && change < 0);
  $("#chart-insight").textContent = data.is_demo
    ? "Una conversación breve puede anticipar un cambio importante."
    : data.overall === null
      ? "Las tendencias aparecen cuando hay cinco respuestas o más."
      : "Cada voz ayuda a dibujar una imagen más clara.";
  $("#action-copy").textContent = ranked.length && ranked.at(-1).score < 3.8
    ? recommendations[ranked.at(-1).id]
    : "Convierte una señal en un pequeño cambio que se note.";
  renderTrend(data.trend);
  renderDimensions(data.dimensions);
  renderSites(data.locations);
}

async function loadDashboard() {
  const data = await request("/api/dashboard");
  renderDashboard(data);
}

function createSurveyElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function renderSurvey() {
  if (survey.campaign) {
    renderCampaignSurvey();
    return;
  }
  const body = $("#survey-body");
  const navigation = $("#survey-navigation");
  const progress = $("#survey-progress-fill");
  const counter = $("#survey-counter");
  body.replaceChildren();
  navigation.replaceChildren();
  progress.style.width = `${Math.max(0, survey.step) / 3 * 100}%`;
  counter.textContent = survey.step > 0 && survey.step < 4
    ? `${survey.step} DE 3`
    : survey.step === 4 ? "LISTO" : "30 SEG";

  const screen = createSurveyElement("section", "survey-screen");
  screen.setAttribute("aria-live", "polite");
  const addBackButton = (label = "Atrás") => {
    const back = createSurveyElement("button", "survey-back", `← ${label}`);
    back.type = "button";
    back.addEventListener("click", () => {
      survey.step = Math.max(0, survey.step - 1);
      renderSurvey();
    });
    navigation.append(back);
  };
  const addContinueButton = (label, handler, disabled = false) => {
    const next = createSurveyElement("button", "survey-continue", label);
    next.type = "button";
    next.disabled = disabled;
    next.addEventListener("click", handler);
    navigation.append(next);
  };

  if (survey.step === 0) {
    screen.append(createSurveyElement("span", "survey-kicker", "TU PULSO · 30 SEGUNDOS"));
    const title = createSurveyElement("h1", "survey-title", "Empecemos por ti.");
    title.id = "survey-title";
    screen.append(title);
    screen.append(createSurveyElement("p", "survey-description", "Una pregunta breve, hecha para tu día. No pedimos tu nombre ni guardamos datos que te identifiquen."));
    const start = createSurveyElement("button", "survey-start", "Empezar");
    start.type = "button";
    start.addEventListener("click", () => {
      survey.step = 1;
      renderSurvey();
    });
    screen.append(start);
    screen.append(createSurveyElement("span", "survey-hint", "Pulsa ↵ para continuar"));
  } else if (survey.step === 1 || survey.step === 2) {
    const isSite = survey.step === 1;
    screen.append(createSurveyElement("span", "survey-kicker", `PREGUNTA ${survey.step} · 3`));
    const title = createSurveyElement(
      "h1",
      "survey-title",
      isSite ? "¿Dónde trabajas?" : "¿Qué te gustaría contarnos?",
    );
    title.id = "survey-title";
    screen.append(title);
    screen.append(createSurveyElement(
      "p",
      "survey-description",
      isSite ? "Queremos escuchar tu realidad, estés donde estés." : "Elige el tema que más se parece a tu semana.",
    ));
    const choices = isSite
      ? [
          ["Fábrica", "⚙", "En planta y producción"],
          ["Logística", "↗", "En movimiento"],
          ["Comercial", "⌂", "Con clientes y equipos"],
          ["Oficina", "▦", "En equipos corporativos"],
        ]
      : Object.entries(dimensionLabels).map(([id, label], index) => [
          id,
          ["♡", "◎", "✳", "⚑", "◉", "⚙"][index],
          label,
        ]);
    const options = createSurveyElement("div", "survey-choices");
    options.setAttribute("role", "group");
    options.setAttribute("aria-label", isSite ? "Entorno de trabajo" : "Dimensión del clima laboral");
    choices.forEach(([value, icon, description], index) => {
      const option = createSurveyElement("button", "survey-choice");
      option.type = "button";
      option.dataset.index = String(index + 1);
      option.setAttribute("aria-pressed", String(isSite ? survey.site === value : survey.dimension === value));
      const symbol = createSurveyElement("span", "choice-icon", icon);
      symbol.setAttribute("aria-hidden", "true");
      const text = createSurveyElement("span", "choice-copy");
      text.append(
        createSurveyElement("strong", "", isSite ? value : description),
        createSurveyElement("small", "", isSite ? description : "Escuchar esta dimensión"),
      );
      option.append(symbol, text, createSurveyElement("span", "choice-number", String(index + 1)));
      option.addEventListener("click", () => {
        if (isSite) {
          survey.site = value;
          survey.step = 2;
          renderSurvey();
        } else {
          survey.dimension = value;
          survey.score = 0;
          void generateSurveyQuestion();
        }
      });
      options.append(option);
    });
    screen.append(options);
    addBackButton();
    screen.dataset.choiceCount = String(choices.length);
  } else if (survey.step === 3) {
    screen.append(createSurveyElement("span", "survey-kicker", "PREGUNTA 3 · 3"));
    const title = createSurveyElement(
      "h1",
      "survey-title generated-title",
      survey.loading ? "Preparando tu pregunta…" : survey.error ? "No hemos podido conectar." : survey.question,
    );
    title.id = "survey-title";
    screen.append(title);
    if (survey.loading) {
      screen.append(createSurveyElement("div", "survey-loading", "✳"));
      screen.append(createSurveyElement("p", "survey-description", "Estamos pensando en lo que importa en tu equipo."));
    } else if (survey.error) {
      screen.append(createSurveyElement("p", "survey-description", survey.error));
      addBackButton();
      addContinueButton("Volver a intentar ↻", () => void generateSurveyQuestion());
    } else {
      screen.append(createSurveyElement("p", "survey-description", `En ${survey.site.toLowerCase()} · ${dimensionLabels[survey.dimension]}`));
      screen.append(createSurveyElement(
        "span",
        "survey-question-source",
        survey.source === "api" ? `✦ Pregunta generada con ${survey.model}` : "✦ Pregunta de demostración",
      ));
      const scale = createSurveyElement("div", "survey-scale");
      scale.setAttribute("role", "group");
      scale.setAttribute("aria-label", "Valora tu experiencia del 1 al 5");
      for (let score = 1; score <= 5; score += 1) {
        const option = createSurveyElement("button", "scale-option", String(score));
        option.type = "button";
        option.setAttribute("aria-pressed", String(survey.score === score));
        option.setAttribute("aria-label", `${score} de 5`);
        option.addEventListener("click", () => {
          survey.score = score;
          scale.querySelectorAll(".scale-option").forEach((button) => {
            button.setAttribute("aria-pressed", String(Number(button.textContent) === score));
          });
          submit.disabled = false;
        });
        scale.append(option);
      }
      screen.append(scale);
      const labels = createSurveyElement("div", "scale-labels");
      labels.append(
        createSurveyElement("span", "", "Podría mejorar"),
        createSurveyElement("span", "", "Me siento muy bien"),
      );
      screen.append(labels);
      screen.append(createSurveyElement("span", "survey-hint", "Elige del 1 al 5 o usa las teclas de tu teclado"));
      addBackButton();
      const submit = createSurveyElement("button", "survey-continue", "Enviar mi pulso ↗");
      submit.type = "button";
      submit.disabled = !survey.score;
      submit.addEventListener("click", () => void submitSurvey(submit));
      navigation.append(submit);
    }
  } else {
    screen.append(createSurveyElement("span", "survey-success-mark", "✓"));
    const title = createSurveyElement("h1", "survey-title", "Gracias por hacerte escuchar.");
    title.id = "survey-title";
    screen.append(title);
    screen.append(createSurveyElement("p", "survey-description", "Tu respuesta ya forma parte de una conversación que puede mejorar las cosas."));
    const finish = createSurveyElement("button", "survey-start", "Volver al resumen");
    finish.type = "button";
    finish.addEventListener("click", () => $("#survey-dialog").close());
    navigation.append(finish);
  }

  body.append(screen);
  requestAnimationFrame(() => {
    const heading = $("#survey-title");
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  });
}

function visibleCampaignQuestions() {
  return survey.campaign.questions.map((question, index) => {
    const rule = question.logic?.show_when;
    if (!rule) return index;
    const earlierAnswer = survey.answers[rule.question_index];
    if (rule.operator === "less_than" && Number(earlierAnswer) >= Number(rule.value)) return null;
    return index;
  }).filter((index) => index !== null);
}

function renderCampaignSurvey() {
  const campaign = survey.campaign;
  const body = $("#survey-body");
  const navigation = $("#survey-navigation");
  const progress = $("#survey-progress-fill");
  const counter = $("#survey-counter");
  const visible = visibleCampaignQuestions();
  const questionIndex = survey.step - 2;
  const currentQuestion = questionIndex >= 0 ? campaign.questions[questionIndex] : null;
  body.replaceChildren();
  navigation.replaceChildren();
  progress.style.width = `${Math.max(0, survey.step - 1) / Math.max(1, visible.length + 1) * 100}%`;
  counter.textContent = currentQuestion
    ? `${Math.max(1, visible.indexOf(questionIndex) + 1)} DE ${visible.length}`
    : `${Math.max(0, Math.min(visible.length, survey.step - 1))} DE ${visible.length}`;

  const screen = createSurveyElement("section", "survey-screen");
  screen.setAttribute("aria-live", "polite");
  const setHeading = (text) => {
    const heading = createSurveyElement("h1", "survey-title", text);
    heading.id = "survey-title";
    screen.append(heading);
  };
  const addBack = () => {
    const button = createSurveyElement("button", "survey-back", "← Atrás");
    button.type = "button";
    button.addEventListener("click", () => {
      survey.step -= 1;
      if (survey.campaign.cohort !== "Todos" && survey.step === 1) survey.step = 0;
      renderSurvey();
    });
    navigation.append(button);
  };
  const addContinue = (label, handler, disabled = false) => {
    const button = createSurveyElement("button", "survey-continue", label);
    button.type = "button";
    button.disabled = disabled;
    button.addEventListener("click", handler);
    navigation.append(button);
    return button;
  };
  const advance = () => {
    let nextStep = survey.step + 1;
    while (nextStep >= 2 && nextStep - 2 < campaign.questions.length) {
      const question = campaign.questions[nextStep - 2];
      const rule = question.logic?.show_when;
      if (!rule || !(rule.operator === "less_than" && Number(survey.answers[rule.question_index]) >= Number(rule.value))) {
        break;
      }
      survey.answers[nextStep - 2] = null;
      nextStep += 1;
    }
    survey.step = nextStep;
    renderSurvey();
  };

  if (survey.step === 0) {
    screen.append(createSurveyElement("span", "survey-kicker", `PULSO ANÓNIMO · ${campaign.estimated_minutes} MIN`));
    setHeading(campaign.title);
    screen.append(createSurveyElement("p", "survey-description", campaign.intro));
    screen.append(createSurveyElement("span", "survey-hint", "No pedimos tu nombre ni guardamos datos que te identifiquen."));
    addContinue("Empezar ↵", () => {
      survey.step = campaign.cohort === "Todos" ? 1 : 2;
      if (campaign.cohort !== "Todos") survey.cohort = campaign.cohort;
      renderSurvey();
    });
  } else if (survey.step === 1) {
    screen.append(createSurveyElement("span", "survey-kicker", "UNA PREGUNTA PARA CADA REALIDAD"));
    setHeading("¿En qué colectivo trabajas?");
    screen.append(createSurveyElement("p", "survey-description", "Solo usamos esta categoría amplia para comprobar que escuchamos voces diversas."));
    const options = createSurveyElement("div", "survey-choices");
    for (const cohort of ["Planta", "Logística", "Comercial", "Oficina", "Manager"]) {
      const button = createSurveyElement("button", "survey-choice", cohort);
      button.type = "button";
      button.addEventListener("click", () => {
        survey.cohort = cohort;
        survey.step = 2;
        renderSurvey();
      });
      options.append(button);
    }
    screen.append(options);
    addBack();
  } else if (currentQuestion) {
    screen.append(createSurveyElement("span", "survey-kicker", `PREGUNTA ${Math.max(1, visible.indexOf(questionIndex) + 1)} · ${visible.length}`));
    setHeading(currentQuestion.text);
    const labels = {
      rating: "Valora tu experiencia del 1 al 5.",
      multiple_choice: "Elige la opción que mejor representa tu experiencia.",
      yes_no: "Elige la respuesta que mejor te representa.",
      open_text: "Comparte una idea breve. No incluyas nombres, correos ni teléfonos.",
    };
    screen.append(createSurveyElement("p", "survey-description", labels[currentQuestion.type]));
    let answer;
    if (currentQuestion.type === "rating") {
      const scale = createSurveyElement("div", "survey-scale");
      for (let score = 1; score <= 5; score += 1) {
        const option = createSurveyElement("button", "scale-option", String(score));
        option.type = "button";
        option.setAttribute("aria-pressed", String(survey.answers[questionIndex] === score));
        option.addEventListener("click", () => {
          survey.answers[questionIndex] = score;
          scale.querySelectorAll(".scale-option").forEach((item, index) => item.setAttribute("aria-pressed", String(index + 1 === score)));
          next.disabled = false;
        });
        scale.append(option);
      }
      screen.append(scale);
      const captions = createSurveyElement("div", "scale-labels");
      captions.append(createSurveyElement("span", "", "Podría mejorar"), createSurveyElement("span", "", "Me siento muy bien"));
      screen.append(captions);
      answer = survey.answers[questionIndex];
    } else if (currentQuestion.type === "multiple_choice" || currentQuestion.type === "yes_no") {
      const choices = currentQuestion.type === "yes_no" ? [["Sí", true], ["No", false]] : currentQuestion.choices.map((item) => [item, item]);
      const options = createSurveyElement("div", "survey-choices");
      choices.forEach(([label, value], index) => {
        const option = createSurveyElement("button", "survey-choice", label);
        option.type = "button";
        option.dataset.index = String(index + 1);
        option.setAttribute("aria-pressed", String(survey.answers[questionIndex] === value));
        option.addEventListener("click", () => {
          survey.answers[questionIndex] = value;
          options.querySelectorAll(".survey-choice").forEach((item) => item.setAttribute("aria-pressed", String(item === option)));
          next.disabled = false;
        });
        options.append(option);
      });
      screen.append(options);
      answer = survey.answers[questionIndex];
    } else {
      const input = createSurveyElement("textarea", "survey-text-answer");
      input.maxLength = 2000;
      input.rows = 3;
      input.placeholder = "Escribe aquí tu respuesta…";
      input.value = typeof survey.answers[questionIndex] === "string" ? survey.answers[questionIndex] : "";
      input.addEventListener("input", () => {
        survey.answers[questionIndex] = input.value;
        next.disabled = currentQuestion.required && !input.value.trim();
      });
      screen.append(input);
      answer = survey.answers[questionIndex];
    }
    if (!currentQuestion.required) {
      screen.append(createSurveyElement("span", "survey-hint", "Esta pregunta es opcional."));
    }
    addBack();
    const isLast = visible.filter((index) => index >= questionIndex).length === 1;
    const next = addContinue(
      isLast ? "Enviar mi pulso ↗" : "Continuar →",
      () => {
        const currentAnswer = survey.answers[questionIndex];
        if (currentQuestion.required && (currentAnswer === undefined || currentAnswer === null || currentAnswer === "")) return;
        if (isLast) void submitCampaignSurvey(next);
        else advance();
      },
      currentQuestion.required && (answer === undefined || answer === null || answer === ""),
    );
  } else {
    screen.append(createSurveyElement("span", "survey-success-mark", "✓"));
    setHeading("Gracias por hacerte escuchar.");
    screen.append(createSurveyElement("p", "survey-description", "Tu respuesta ya forma parte de una conversación que puede mejorar las cosas."));
    addContinue("Volver al resumen", () => $("#survey-dialog").close());
  }

  body.append(screen);
  requestAnimationFrame(() => {
    const heading = $("#survey-title");
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  });
}

async function openPublishedSurvey(surveyId) {
  try {
    survey.campaign = await request(`/api/surveys/${surveyId}`);
    survey.step = 0;
    survey.answers = Array(survey.campaign.questions.length).fill(null);
    survey.cohort = survey.campaign.cohort === "Todos" ? "" : survey.campaign.cohort;
    renderSurvey();
    if (!$("#survey-dialog").open) $("#survey-dialog").showModal();
  } catch (error) {
    showToast(error.message);
  }
}

async function submitCampaignSurvey(button) {
  button.disabled = true;
  button.textContent = "Enviando tu respuesta…";
  try {
    await request("/api/responses", {
      method: "POST",
      body: JSON.stringify({
        survey_id: survey.campaign.id,
        cohort: survey.cohort,
        answers: survey.answers,
      }),
    });
    survey.step = 2 + survey.campaign.questions.length;
    renderSurvey();
    await loadDashboard();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Enviar mi pulso ↗";
    showToast(error.message);
  }
}

async function generateSurveyQuestion() {
  const generation = ++questionGeneration;
  survey.step = 3;
  survey.loading = true;
  survey.error = "";
  survey.question = "";
  survey.source = "";
  renderSurvey();
  try {
    const result = await request("/api/question", {
      method: "POST",
      body: JSON.stringify({
        dimension: survey.dimension,
        site: survey.site,
      }),
    });
    if (generation !== questionGeneration) return;
    survey.question = result.question;
    survey.source = result.source;
    survey.model = result.model || "";
  } catch (error) {
    if (generation !== questionGeneration) return;
    survey.error = error.message;
  } finally {
    if (generation === questionGeneration) {
      survey.loading = false;
      renderSurvey();
    }
  }
}

async function submitSurvey(button) {
  if (!survey.score || button.disabled) return;
  button.disabled = true;
  button.textContent = "Enviando tu respuesta…";
  try {
    await request("/api/pulse", {
      method: "POST",
      body: JSON.stringify({
        dimension: survey.dimension,
        site: survey.site,
        score: survey.score,
      }),
    });
    survey.step = 4;
    renderSurvey();
    await loadDashboard();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Enviar mi pulso ↗";
    showToast(error.message);
  }
}

function openSettings() {
  $("#settings-dialog").showModal();
}

async function init() {
  $("#open-settings").addEventListener("click", openSettings);
  $("#open-settings-mobile").addEventListener("click", openSettings);
  $("#dismiss-demo").addEventListener("click", () => { $("#demo-banner").hidden = true; });
  $("#open-survey").addEventListener("click", () => {
    Object.assign(survey, {
      step: 0,
      site: "",
      dimension: "",
      score: 0,
      question: "",
      source: "",
      model: "",
      error: "",
      loading: false,
      campaign: null,
      answers: [],
    });
    renderSurvey();
    $("#survey-dialog").showModal();
  });
  $("#close-survey").addEventListener("click", () => $("#survey-dialog").close());
  $("#survey-dialog").addEventListener("click", (event) => {
    if (event.target === $("#survey-dialog")) $("#survey-dialog").close();
  });
  $("#survey-dialog").addEventListener("keydown", (event) => {
    if (event.key === "Escape") return;
    const choices = [...$("#survey-body").querySelectorAll(".survey-choice")];
    const selected = choices.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      if (choices.length) {
        event.preventDefault();
        choices[(selected + 1 + choices.length) % choices.length].focus();
      } else if (survey.step === 3 && !survey.loading && !survey.error) {
        const score = Math.min(5, survey.score ? survey.score + 1 : 1);
        $("#survey-body").querySelectorAll(".scale-option")[score - 1]?.click();
      }
      return;
    }
    if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      if (choices.length) {
        event.preventDefault();
        choices[(selected - 1 + choices.length) % choices.length].focus();
      } else if (survey.step === 3 && !survey.loading && !survey.error) {
        const score = Math.max(1, survey.score - 1 || 1);
        $("#survey-body").querySelectorAll(".scale-option")[score - 1]?.click();
      }
      return;
    }
    if (event.key >= "1" && event.key <= "5" && survey.step === 3 && !survey.loading && !survey.error) {
      $("#survey-body").querySelectorAll(".scale-option")[Number(event.key) - 1]?.click();
      return;
    }
    if (event.key === "Enter" && event.target === $("#survey-title")) {
      event.preventDefault();
      if (survey.step === 0) $("#survey-body .survey-start")?.click();
      else if (survey.step === 3 && survey.score) $("#survey-navigation .survey-continue")?.click();
    }
  });

  $("#settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const button = form.querySelector(".submit-pulse");
    button.disabled = true;
    try {
      await request("/api/settings", {
        method: "POST",
        body: JSON.stringify({
          base_url: values.get("base_url"),
          model: values.get("model"),
          api_key: values.get("api_key"),
        }),
      });
      form.elements.api_key.value = "";
      $("#api-status-dot").classList.add("connected");
      $("#settings-dialog").close();
      showToast("API conectada para esta sesión. La clave no se ha guardado.");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  });

  try {
    const [status] = await Promise.all([request("/api/status"), loadDashboard()]);
    $("#api-status-dot").classList.toggle("connected", status.api_configured);
  } catch (error) {
    showToast(error.message);
  }
  const respondentMatch = window.location.pathname.match(/^\/respond\/([a-f0-9]{32})$/);
  if (respondentMatch) void openPublishedSurvey(respondentMatch[1]);
}

document.addEventListener("DOMContentLoaded", init);
