const $ = (selector) => document.querySelector(selector);
const appState = { data: null, survey: null, timer: null };
const questionTypes = {
  rating: "Escala 1–5",
  multiple_choice: "Opción múltiple",
  yes_no: "Sí / No",
  open_text: "Respuesta abierta",
};

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "No se pudo completar la operación.");
  return data;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(appState.timer);
  appState.timer = setTimeout(() => toast.classList.remove("show"), 4000);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatConfidence(size) {
  if (size >= 100) return "Alta";
  if (size >= 30) return "Media";
  if (size >= 5) return "Limitada";
  return "Insuficiente";
}

function renderAnalytics(dashboard) {
  const insights = dashboard.insights;
  $("#synthetic-notice").hidden = !dashboard.is_demo;
  $("#admin-sample").textContent = dashboard.total_responses ?? "Menos de 5";
  $("#admin-confidence").textContent = insights.confidence;
  $("#admin-retention").textContent = `Datos eliminados tras ${dashboard.retention_days} días`;
  const alerts = insights.representation.filter((item) => item.status === "Baja").length;
  $("#admin-alert-count").textContent = alerts ? `${alerts} alertas` : "Alineada";
  $("#admin-alert-count").classList.toggle("attention", alerts > 0);
  $("#admin-channel-count").textContent = insights.channels.length || "—";

  const representation = $("#representation-list");
  representation.replaceChildren();
  for (const item of insights.representation) {
    const row = element("div", `representation-row ${item.status === "Baja" ? "underrepresented" : ""}`);
    const heading = element("div", "representation-heading");
    heading.append(
      element("strong", "", item.cohort),
      element("span", `representation-status ${item.status.toLowerCase()}`, item.status === "Protegida" ? "Protegido" : item.status),
    );
    const track = element("div", "representation-track");
    const bar = element("i");
    bar.style.width = `${Math.max(3, Math.min(100, item.observed_pct ?? 0))}%`;
    track.append(bar);
    const detail = element(
      "small",
      "",
      item.observed_pct === null
        ? `Muestra protegida · objetivo ${item.expected_pct}%`
        : `${item.observed_pct}% de respuestas · objetivo ${item.expected_pct}%`,
    );
    row.append(heading, track, detail);
    representation.append(row);
  }

  const topicList = $("#topic-list");
  topicList.replaceChildren();
  if (!insights.topics.length) {
    topicList.append(element("p", "empty-note", "No hay suficientes respuestas abiertas para mostrar temas."));
  }
  for (const topic of insights.topics) {
    const row = element("div", "topic-row");
    const marker = element("span", topic.emerging ? "topic-marker emerging" : "topic-marker", topic.emerging ? "↑" : "•");
    const label = element("strong", "", topic.label);
    const count = element("span", "topic-count", `${topic.count} menciones`);
    const change = element("span", topic.change_pct > 0 ? "topic-change positive" : "topic-change", `${topic.change_pct > 0 ? "+" : ""}${topic.change_pct}%`);
    row.append(marker, label, count, change);
    topicList.append(row);
  }

  const signals = insights.emerging_topics;
  $("#weak-signal").textContent = signals.length
    ? `Señal emergente: ${signals.join(", ")}. Contrasta este cambio con el contexto antes de interpretarlo.`
    : "Sin señales emergentes claras. El volumen y la clasificación temática tienen límites.";

  const recommendations = $("#recommendation-list");
  recommendations.replaceChildren();
  for (const action of appState.data.actions) {
    const card = element("article", "recommendation-card");
    const label = element("span", "recommendation-kicker", action.status === "approved" ? "ACCIÓN APROBADA" : "PROPUESTA · REQUIERE VALIDACIÓN");
    const title = element("strong", "", action.title);
    const description = element("p", "", action.description);
    const rationale = element("small", "", action.rationale || "");
    card.append(label, title, description, rationale);
    if (action.status === "proposed" && !action.id) {
      const approve = element("button", "small-approve", "Aprobar acción");
      const dismiss = element("button", "small-approve dismiss-button", "Descartar");
      approve.type = "button";
      dismiss.type = "button";
      const review = async (status) => {
        try {
          await request("/api/actions", {
            method: "POST",
            body: JSON.stringify({ ...action, status }),
          });
          await loadAdmin();
          showToast(status === "approved" ? "Acción aprobada. Puedes preparar una consulta de seguimiento." : "Propuesta descartada.");
        } catch (error) {
          showToast(error.message);
        }
      };
      approve.addEventListener("click", () => review("approved"));
      dismiss.addEventListener("click", () => review("dismissed"));
      card.append(approve, dismiss);
    } else if (action.status === "approved") {
      const followup = element("button", "small-approve followup-button", action.followup_survey_id ? "Seguimiento en preparación" : "Crear microconsulta de seguimiento ↗");
      followup.type = "button";
      followup.disabled = Boolean(action.followup_survey_id);
      followup.addEventListener("click", async () => {
        try {
          const draft = await request(`/api/actions/${action.id}/followup`, { method: "POST", body: "{}" });
          appState.survey = draft;
          renderSurveyEditor();
          $("#editor-panel").hidden = false;
          $("#editor-panel").scrollIntoView({ behavior: "smooth", block: "start" });
          showToast("Borrador de seguimiento listo para revisar y aprobar.");
        } catch (error) {
          showToast(error.message);
        }
      });
      card.append(followup);
    }
    recommendations.append(card);
  }
  $("#analysis-caveat").textContent =
    `Muestra: ${insights.sample_size ?? "protegida"} · Confianza: ${insights.confidence}. Los temas se clasifican con palabras clave; las tendencias no explican causas.`;
}

function renderCampaigns(data) {
  const list = $("#campaign-list");
  list.replaceChildren();
  if (!data.surveys.length) {
    list.append(element("p", "empty-note", "Aún no hay consultas. Define una necesidad para crear la primera."));
    return;
  }
  for (const survey of data.surveys) {
    const row = element("article", "campaign-row");
    const icon = element("span", "campaign-icon", survey.status === "published" ? "↗" : survey.status === "approved" ? "✓" : "✎");
    const detail = element("div", "campaign-details");
    detail.append(
      element("strong", "", survey.title),
      element("small", "", `${survey.cohort} · ${survey.questions.length} preguntas · ${survey.channel}`),
    );
    const status = element("span", `campaign-status ${survey.status}`, {
      draft: "Borrador",
      approved: "Aprobada",
      published: survey.typeform_connected ? "Typeform" : "Publicada",
    }[survey.status] || survey.status);
    row.append(icon, detail, status);
    if (survey.status === "published") {
      const open = element("a", "campaign-open", "Abrir ↗");
      open.href = survey.publish_url;
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      row.append(open);
    } else {
      const review = element("button", "campaign-open", survey.status === "draft" ? "Continuar" : "Publicar");
      review.type = "button";
      review.addEventListener("click", () => {
        appState.survey = survey;
        $("#editor-panel").hidden = false;
        renderSurveyEditor();
        $("#editor-panel").scrollIntoView({ behavior: "smooth", block: "start" });
      });
      row.append(review);
    }
    list.append(row);
  }
}

function syncQuestionFromCard(card, question) {
  question.text = card.querySelector(".question-text-input").value.trim();
  question.type = card.querySelector(".question-type-input").value;
  question.required = card.querySelector(".question-required-input").checked;
  if (question.type === "multiple_choice") {
    question.choices = card.querySelector(".question-choices-input").value
      .split("\n").map((choice) => choice.trim()).filter(Boolean);
  } else {
    question.choices = [];
  }
}

function renderSurveyEditor() {
  const survey = appState.survey;
  if (!survey) return;
  $("#draft-status").textContent = survey.status === "approved" ? "APROBADA" : survey.status === "published" ? "PUBLICADA" : "BORRADOR";
  $("#question-count").textContent = `${survey.questions.length} preguntas · ${survey.estimated_minutes} min estimados`;
  $("#approve-survey").hidden = survey.status !== "draft";
  $("#save-draft").hidden = survey.status !== "draft";
  $("#add-question").hidden = survey.status !== "draft" || survey.questions.length >= 6;
  $("#publish-survey").hidden = survey.status !== "approved";

  const editor = $("#survey-editor");
  editor.replaceChildren();
  const titleField = element("label", "editor-label", "Título de la consulta");
  const title = element("input", "survey-title-input");
  title.value = survey.title;
  title.maxLength = 160;
  titleField.append(title);
  editor.append(titleField);
  const introField = element("label", "editor-label", "Introducción y transparencia");
  const intro = element("textarea", "survey-intro-input");
  intro.value = survey.intro;
  intro.rows = 2;
  intro.maxLength = 500;
  introField.append(intro);
  editor.append(introField);

  survey.questions.forEach((question, index) => {
    const card = element("article", "question-editor-card");
    const header = element("div", "question-editor-header");
    header.append(element("span", "question-number", `${String(index + 1).padStart(2, "0")}`));
    const controls = element("div", "question-order-controls");
    for (const [label, delta, accessible] of [["↑", -1, "Subir pregunta"], ["↓", 1, "Bajar pregunta"], ["×", 0, "Eliminar pregunta"]]) {
      const button = element("button", delta === 0 ? "remove-question" : "move-question", label);
      button.type = "button";
      button.setAttribute("aria-label", accessible);
      button.disabled = survey.status !== "draft" || (delta === -1 && index === 0) || (delta === 1 && index === survey.questions.length - 1) || (delta === 0 && survey.questions.length <= 3);
      button.addEventListener("click", () => {
        collectSurveyEdits();
        if (delta === 0) survey.questions.splice(index, 1);
        else [survey.questions[index], survey.questions[index + delta]] = [survey.questions[index + delta], survey.questions[index]];
        renderSurveyEditor();
      });
      controls.append(button);
    }
    header.append(controls);
    const prompt = element("textarea", "question-text-input");
    prompt.rows = 2;
    prompt.maxLength = 280;
    prompt.value = question.text;
    prompt.disabled = survey.status !== "draft";
    const settings = element("div", "question-editor-settings");
    const typeLabel = element("label", "", "Tipo de respuesta");
    const type = element("select", "question-type-input");
    for (const [value, label] of Object.entries(questionTypes)) {
      const option = element("option", "", label);
      option.value = value;
      type.append(option);
    }
    type.value = question.type;
    type.disabled = survey.status !== "draft";
    typeLabel.append(type);
    const requiredLabel = element("label", "required-toggle");
    const required = element("input", "question-required-input");
    required.type = "checkbox";
    required.checked = question.required;
    required.disabled = survey.status !== "draft";
    requiredLabel.append(required, element("span", "", "Obligatoria"));
    settings.append(typeLabel, requiredLabel);
    card.append(header, prompt, settings);
    if (question.type === "multiple_choice") {
      const optionsLabel = element("label", "choices-label", "Opciones · una por línea");
      const choices = element("textarea", "question-choices-input");
      choices.value = (question.choices || []).join("\n");
      choices.rows = 3;
      choices.disabled = survey.status !== "draft";
      optionsLabel.append(choices);
      card.append(optionsLabel);
    }
    const liveUpdate = () => {
      syncQuestionFromCard(card, question);
      $("#question-count").textContent = `${survey.questions.length} preguntas · ${survey.estimated_minutes} min estimados`;
    };
    prompt.addEventListener("input", liveUpdate);
    type.addEventListener("change", () => {
      collectSurveyEdits();
      renderSurveyEditor();
    });
    required.addEventListener("change", liveUpdate);
    card.querySelector(".question-choices-input")?.addEventListener("input", liveUpdate);
    editor.append(card);
  });

  title.disabled = survey.status !== "draft";
  intro.disabled = survey.status !== "draft";
  $("#publish-result").hidden = survey.status !== "published";
  $("#import-typeform").hidden = survey.status !== "published" || !survey.typeform_connected;
  if (survey.status === "published") showPublishResult(survey);
}

function collectSurveyEdits() {
  const survey = appState.survey;
  const cards = [...$("#survey-editor").querySelectorAll(".question-editor-card")];
  const questions = cards.map((card, index) => {
    const question = survey.questions[index];
    syncQuestionFromCard(card, question);
    return question;
  });
  return {
    title: $("#survey-editor .survey-title-input").value,
    intro: $("#survey-editor .survey-intro-input").value,
    questions,
  };
}

async function saveSurvey() {
  const survey = appState.survey;
  if (!survey || survey.status !== "draft") throw new Error("Solo se pueden editar borradores.");
  const updated = await request(`/api/surveys/${survey.id}/edit`, {
    method: "POST",
    body: JSON.stringify(collectSurveyEdits()),
  });
  appState.survey = { ...survey, ...updated };
  return appState.survey;
}

function showPublishResult(survey) {
  $("#published-link").value = survey.publish_url;
  $("#open-published-link").href = survey.publish_url;
  $("#published-qr").src = `/api/surveys/${survey.id}/qr.svg`;
  $("#share-published-teams").href = `https://teams.microsoft.com/l/chat/0/0?message=${encodeURIComponent(`${survey.title}\n${survey.publish_url}`)}`;
}

async function loadAdmin() {
  appState.data = await request("/api/admin");
  renderAnalytics(appState.data.dashboard);
  renderCampaigns(appState.data);
  const connections = [];
  if (appState.data.ai_connected) connections.push("IA conectada");
  if (appState.data.typeform_connected) connections.push("Typeform conectado");
  $("#connection-state").textContent = connections.length
    ? `${connections.join(" · ")} · clave conservada solo en memoria`
    : "Sin configurar · las plantillas locales funcionan sin API.";
}

$("#need-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#generate-draft");
  button.disabled = true;
  button.textContent = "Preparando una consulta segura…";
  const fields = Object.fromEntries(new FormData(event.currentTarget).entries());
  fields.population = {
    Planta: Number(fields.population_Planta),
    Logística: Number(fields.population_Logística),
    Comercial: Number(fields.population_Comercial),
    Oficina: Number(fields.population_Oficina),
    Manager: Number(fields.population_Manager),
  };
  for (const key of Object.keys(fields)) {
    if (key.startsWith("population_")) delete fields[key];
  }
  try {
    const survey = await request("/api/surveys", {
      method: "POST",
      body: JSON.stringify(fields),
    });
    appState.survey = survey;
    renderSurveyEditor();
    $("#editor-panel").hidden = false;
    $("#editor-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    await loadAdmin();
    showToast(`Borrador creado con ${survey.source === "api" ? "IA" : "una plantilla local"}. Revísalo antes de aprobar.`);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.innerHTML = "Generar borrador de consulta <span>✦</span>";
  }
});

$("#add-question").addEventListener("click", () => {
  if (!appState.survey || appState.survey.questions.length >= 6) return;
  collectSurveyEdits();
  appState.survey.questions.push({
    id: crypto.randomUUID().replaceAll("-", "").slice(0, 8),
    text: "¿Qué pequeño cambio ayudaría a tu equipo?",
    type: "open_text",
    dimension: "bienestar",
    required: false,
    choices: [],
    logic: null,
  });
  renderSurveyEditor();
});

$("#save-draft").addEventListener("click", async () => {
  try {
    await saveSurvey();
    renderSurveyEditor();
    await loadAdmin();
    showToast("Cambios guardados como borrador.");
  } catch (error) {
    showToast(error.message);
  }
});

$("#approve-survey").addEventListener("click", async () => {
  try {
    await saveSurvey();
    if (!window.confirm("¿Has revisado el objetivo, el lenguaje, las preguntas y la privacidad? La consulta solo se publicará después de esta aprobación.")) return;
    appState.survey = await request(`/api/surveys/${appState.survey.id}/approve`, { method: "POST", body: "{}" });
    renderSurveyEditor();
    await loadAdmin();
    showToast("Aprobación humana registrada. Ya puedes publicar.");
  } catch (error) {
    showToast(error.message);
  }
});

$("#publish-survey").addEventListener("click", async () => {
  const button = $("#publish-survey");
  button.disabled = true;
  try {
    appState.survey = await request(`/api/surveys/${appState.survey.id}/publish`, { method: "POST", body: "{}" });
    renderSurveyEditor();
    await loadAdmin();
    $("#publish-result").scrollIntoView({ behavior: "smooth", block: "center" });
    showToast(appState.survey.typeform_connected ? "Consulta creada en Typeform." : "Consulta publicada en el entorno local.");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

$("#copy-published-link").addEventListener("click", async () => {
  const field = $("#published-link");
  try {
    await navigator.clipboard.writeText(field.value);
    showToast("Enlace copiado.");
  } catch {
    field.select();
    document.execCommand("copy");
    showToast("Enlace seleccionado para copiar.");
  }
});

$("#import-typeform").addEventListener("click", async () => {
  try {
    const result = await request(`/api/surveys/${appState.survey.id}/import`, { method: "POST", body: "{}" });
    await loadAdmin();
    showToast(`${result.imported} respuestas agregadas importadas y anonimizadas.`);
  } catch (error) {
    showToast(error.message);
  }
});

$("#integration-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  try {
    await request("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        api_key: values.get("api_key"),
        typeform_token: values.get("typeform_token"),
        base_url: values.get("base_url"),
        model: values.get("model"),
      }),
    });
    form.elements.api_key.value = "";
    form.elements.typeform_token.value = "";
    await loadAdmin();
    showToast("Conexiones actualizadas. Los tokens no se guardaron en disco.");
  } catch (error) {
    showToast(error.message);
  }
});

async function init() {
  try {
    await loadAdmin();
  } catch (error) {
    showToast(error.message);
  }
}

document.addEventListener("DOMContentLoaded", init);
