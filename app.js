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

async function loadQuestion() {
  const button = $("#refresh-question");
  const questionText = $("#question-text");
  const source = $("#question-source");
  const teamsShare = $("#teams-share");
  button.disabled = true;
  questionText.textContent = "Buscando una pregunta que encaje contigo…";
  source.textContent = "";
  teamsShare.hidden = true;
  try {
    const result = await request("/api/question", {
      method: "POST",
      body: JSON.stringify({
        dimension: $("#dimension-select").value,
        site: $("#site-select").value,
      }),
    });
    questionText.textContent = result.question;
    source.textContent = result.source === "api" ? `Generada con ${result.model}` : "Pregunta de demostración · conecta tu API para personalizarla";
    teamsShare.href = `https://teams.microsoft.com/l/chat/0/0?message=${encodeURIComponent(result.question)}`;
    teamsShare.hidden = false;
  } catch (error) {
    questionText.textContent = "No se ha podido generar la pregunta.";
    source.textContent = error.message;
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

function openSettings() {
  $("#settings-dialog").showModal();
}

async function init() {
  const select = $("#dimension-select");
  for (const [id, label] of Object.entries(dimensionLabels)) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = label;
    select.append(option);
  }
  $("#open-settings").addEventListener("click", openSettings);
  $("#open-settings-mobile").addEventListener("click", openSettings);
  $("#dismiss-demo").addEventListener("click", () => { $("#demo-banner").hidden = true; });
  $("#refresh-question").addEventListener("click", loadQuestion);
  select.addEventListener("change", loadQuestion);
  $("#site-select").addEventListener("change", loadQuestion);

  $("#pulse-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.currentTarget.querySelector(".submit-pulse");
    submit.disabled = true;
    try {
      await request("/api/pulse", {
        method: "POST",
        body: JSON.stringify({
          dimension: select.value,
          site: $("#site-select").value,
          score: Number(new FormData(event.currentTarget).get("score")),
        }),
      });
      event.currentTarget.reset();
      $("#site-select").value = "Fábrica";
      select.value = "bienestar";
      await loadDashboard();
      await loadQuestion();
      showToast("Gracias. Tu pulso se ha compartido de forma anónima.");
    } catch (error) {
      showToast(error.message);
    } finally {
      submit.disabled = false;
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
      await loadQuestion();
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
  await loadQuestion();
}

document.addEventListener("DOMContentLoaded", init);
