#!/usr/bin/env python3
"""
Generador determinista de datos de test para Pulso (Estrella Damm, UAB THE HACK 2026).

Produce, en el directorio --out:
  pulso_test_responses.csv  -> filas para la tabla `responses` de SQLite (lo que lee el dashboard)
  pulso_test_engine.csv     -> DataFrame para statistical_engine.py (employee_id, rol, turno, q1..q3, texto)
  invalid_payloads.json     -> payloads validos/invalidos para backend.app.validate_response
  expected_checks.json      -> valores esperados calculados de forma independiente (oraculo de test)

Opcionalmente carga las respuestas en una BBDD SQLite de pruebas (nunca toca data/pulso.sqlite3):
  python generate_test_dataset.py --load --project-root ../Damn

Casos limite sembrados a proposito:
  * Comercial infrarrepresentado en el periodo "actual" (~4% observado vs 16% esperado)  -> status "Baja"
  * Manager con 3 respuestas en "actual" (< k=5)                                         -> grupo "Protegida"
  * Celdas cohorte x dimension con < 5 respuestas (k-anonimato)
  * Planta x liderazgo bimodal (1 y 5) solo en "actual"                                  -> polarizacion
  * Temas emergentes en "actual": comunicacion y turnos
  * Engine CSV: Comercial 8% (<15%), celda Oficinas x Tarde con 3 filas (<k), 4 comentarios con PII ficticia
Todos los datos son sinteticos; los emails/telefonos/URLs de PII son ficticios.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

SEED = 20261107
MIN_K = 5

DIMENSIONS = ["bienestar", "liderazgo", "desarrollo", "cultura", "relaciones", "condiciones"]
COHORTS = ["Planta", "Logística", "Comercial", "Oficina", "Manager"]
SITE_LABEL = {"Planta": "Fábrica", "Logística": "Logística", "Comercial": "Comercial",
              "Oficina": "Oficina", "Manager": "Manager"}
POPULATION = {"Planta": 0.42, "Logística": 0.20, "Comercial": 0.16, "Oficina": 0.16, "Manager": 0.06}
CHANNELS = ["Móvil-QR", "Teams", "Correo"]

# Respuestas por periodo y colectivo (anterior / actual)
COUNTS = {
    "anterior": {"Planta": 150, "Logística": 70, "Comercial": 35, "Oficina": 55, "Manager": 9},
    "actual":   {"Planta": 170, "Logística": 80, "Comercial": 12, "Oficina": 60, "Manager": 3},
}

# Media base por colectivo y dimension
BASE = {
    "Planta":    {"bienestar": 3.3, "liderazgo": 3.4, "desarrollo": 3.1, "cultura": 3.7, "relaciones": 3.9, "condiciones": 3.0},
    "Logística": {"bienestar": 3.1, "liderazgo": 3.4, "desarrollo": 3.0, "cultura": 3.5, "relaciones": 3.6, "condiciones": 2.6},
    "Comercial": {"bienestar": 3.6, "liderazgo": 3.5, "desarrollo": 3.4, "cultura": 3.8, "relaciones": 3.9, "condiciones": 3.3},
    "Oficina":   {"bienestar": 4.0, "liderazgo": 3.9, "desarrollo": 3.8, "cultura": 4.0, "relaciones": 4.2, "condiciones": 3.9},
    "Manager":   {"bienestar": 3.8, "liderazgo": 4.2, "desarrollo": 3.9, "cultura": 4.1, "relaciones": 4.0, "condiciones": 3.5},
}
# Deriva en el periodo "actual" (tendencia a la baja en Planta/bienestar y Logistica/condiciones)
DRIFT = {("Planta", "bienestar"): -0.3, ("Logística", "condiciones"): -0.3, ("Oficina", "relaciones"): 0.1}

# Mismas palabras clave que backend.app.TOPICS (para el oraculo independiente)
TOPICS = {
    "comunicacion": ["comunicación", "información", "cambios", "avisos"],
    "turnos": ["turnos", "horario", "descanso", "cobertura"],
    "herramientas": ["herramientas", "sistema", "material", "recursos"],
    "liderazgo": ["liderazgo", "acompañamiento", "feedback", "responsables"],
    "reconocimiento": ["reconocimiento", "valoración", "agradecimiento"],
    "desarrollo": ["aprendizaje", "formación", "crecer", "desarrollo"],
    "bienestar": ["bienestar", "carga", "energía", "pausa"],
}

COMMENTS = {
    "comunicacion": [
        "Los avisos de cambios llegan tarde o por varios canales.",
        "A veces la información sobre los cambios se pierde entre mensajes.",
        "Nos iría bien tener la comunicación clara antes de empezar.",
        "Faltan avisos con antelación cuando hay cambios en la planificación.",
    ],
    "turnos": [
        "La planificación de turnos deja poco margen para organizarse.",
        "Un horario más previsible ayudaría mucho a conciliar.",
        "En algunos turnos cuesta encontrar tiempo para el descanso.",
        "La cobertura en el turno de noche es justa cuando hay bajas.",
    ],
    "herramientas": [
        "El sistema va lento y cuesta resolver incidencias rápido.",
        "Nos falta material en la línea para trabajar sin paradas.",
        "Las herramientas nuevas funcionan, pero faltan instrucciones más sencillas.",
        "Faltan recursos para cubrir las incidencias en el día a día.",
    ],
    "liderazgo": [
        "Agradezco cuando los responsables escuchan al equipo.",
        "Vendría bien recibir feedback más a menudo.",
        "Echo en falta más acompañamiento en los momentos difíciles.",
        "El liderazgo cercano marca la diferencia en el equipo.",
    ],
    "reconocimiento": [
        "Un agradecimiento por el trabajo bien hecho marca la diferencia.",
        "El equipo se esfuerza y falta más reconocimiento.",
        "A veces la valoración de los pequeños logros pasa desapercibida.",
    ],
    "desarrollo": [
        "Me gustaría tener más oportunidades de formación en el puesto.",
        "El aprendizaje práctico da más confianza.",
        "Veo poco margen para crecer dentro de la empresa.",
        "El desarrollo profesional debería tener más visibilidad.",
    ],
    "bienestar": [
        "Una pausa a tiempo ayuda a recuperar energía.",
        "En los días de más carga es difícil parar un momento.",
        "El ambiente del equipo contribuye mucho al bienestar.",
    ],
    "otros": [
        "Todo bien por ahora.",
        "Sin comentarios por esta vez.",
        "Gracias por preguntar.",
        "Me gusta poder responder rápido.",
    ],
}

TOPIC_WEIGHTS = {
    "anterior": {"bienestar": .20, "liderazgo": .20, "herramientas": .20, "reconocimiento": .15,
                 "desarrollo": .15, "comunicacion": .05, "turnos": .05},
    "actual":   {"comunicacion": .30, "turnos": .28, "herramientas": .15, "bienestar": .12,
                 "liderazgo": .08, "reconocimiento": .04, "desarrollo": .03},
}


def classify_comment(text: str) -> str:
    """Misma regla que backend.app.classify_comment (max por (aciertos, nombre))."""
    norm = text.casefold()
    ranked = [(sum(t in norm for t in terms), topic) for topic, terms in TOPICS.items()]
    count, topic = max(ranked)
    return topic if count else "otros"


# --------------------------------------------------------------------------- responses
def draw_score(rng: random.Random, cohort: str, dim: str, period: str) -> int:
    if cohort == "Planta" and dim == "liderazgo" and period == "actual":
        return rng.choices([1, 2, 4, 5], weights=[.35, .10, .15, .40])[0]  # bimodal
    mean = BASE[cohort][dim] + (DRIFT.get((cohort, dim), 0.0) if period == "actual" else 0.0)
    return max(1, min(5, round(rng.gauss(mean, 0.8))))


def generate_responses(rng: random.Random, today: date) -> list[dict]:
    windows = {"anterior": (today - timedelta(days=28), 14), "actual": (today - timedelta(days=14), 15)}
    rows: list[dict] = []
    tf_seen: set[str] = set()
    for period in ("anterior", "actual"):
        start, span = windows[period]
        weights = TOPIC_WEIGHTS[period]
        for cohort in COHORTS:
            n = COUNTS[period][cohort]
            dims = [DIMENSIONS[i % len(DIMENSIONS)] for i in range(n)]
            rng.shuffle(dims)
            p_comment = 0.38 if cohort in ("Planta", "Logística") else 0.50
            for dim in dims:
                day = start + timedelta(days=rng.randrange(span))
                comment = ""
                tema_real = ""
                if rng.random() < p_comment:
                    if rng.random() < 0.10:
                        comment = rng.choice(COMMENTS["otros"])
                        tema_real = "otros"
                    else:
                        topic = rng.choices(list(weights), weights=list(weights.values()))[0]
                        comment = rng.choice(COMMENTS[topic])
                        tema_real = topic
                channel = rng.choices(
                    CHANNELS,
                    weights=[65, 25, 10] if cohort in ("Planta", "Logística") else [35, 25, 40],
                )[0]
                tf_id = ""
                if rng.random() < 0.25:
                    tf_id = "tf_" + hashlib.sha1(f"{SEED}-{len(rows)}".encode()).hexdigest()[:12]
                    assert tf_id not in tf_seen
                    tf_seen.add(tf_id)
                rows.append({
                    "day": day.isoformat(),
                    "dimension": dim,
                    "site": SITE_LABEL[cohort],
                    "score": draw_score(rng, cohort, dim, period),
                    "is_demo": 1,
                    "cohort": cohort,
                    "center_type": "Centro amplio" if rng.random() < 0.95 else "",
                    "channel": channel,
                    "comment": comment,
                    "tema_real": tema_real,
                    "survey_id": "",
                    "period": period,
                    "typeform_response_id": tf_id,
                })
    rows.sort(key=lambda r: (r["day"], r["cohort"], r["dimension"]))
    return rows


# --------------------------------------------------------------------------- engine csv
ENGINE_COMMENTS = [
    ("Falta agilidad en la reposición de repuestos en línea.", "herramientas"),
    ("Buen ambiente entre compañeros de turno.", "reconocimiento"),
    ("Los relevos de turno requieren mejor coordinación.", "turnos"),
    ("Herramientas informáticas lentas durante el cierre.", "herramientas"),
    ("Más claridad en las instrucciones de cambio de formato.", "comunicacion"),
    ("El equipo agradece las pausas programadas.", "turnos"),
]
# PII FICTICIA: el motor no debe filtrar estos textos en su payload
PII_COMMENTS = [
    ("Escribidme a juan.perez@example.com para lo del turno.", "turnos"),
    ("Llamad al 600 123 456 si hay cambios de última hora.", "comunicacion"),
    ("Soy Marta López y llevo diez años en la línea 3.", "otros"),
    ("Más detalle en https://intranet.example.com/turnos por favor.", "turnos"),
]


def generate_engine_rows(rng: random.Random) -> list[dict]:
    plan = [("Planta", 240), ("Logística", 140), ("Oficinas", 80), ("Comercial", 40)]
    rows: list[dict] = []
    for rol, n in plan:
        for _ in range(n):
            if rol in ("Planta", "Logística"):
                turno = rng.choices(["Mañana", "Tarde", "Noche"], weights=[.50, .35, .15])[0]
            else:
                turno = "Mañana"
            if rol == "Planta":
                q1 = max(1, min(5, round(rng.gauss(3.4, 0.8))))
                q2 = rng.choices([1, 2, 4, 5], weights=[.35, .10, .15, .40])[0]
                q3 = max(1, min(5, round(rng.gauss(3.1, 0.9))))
            elif rol == "Logística":
                q1 = max(1, min(5, round(rng.gauss(3.0, 0.7))))
                q2 = max(1, min(5, round(rng.gauss(3.3, 0.6))))
                q3 = max(1, min(5, round(rng.gauss(2.8, 0.8))))
            elif rol == "Oficinas":
                q1 = max(1, min(5, round(rng.gauss(3.9, 0.5))))
                q2 = max(1, min(5, round(rng.gauss(4.0, 0.5))))
                q3 = max(1, min(5, round(rng.gauss(4.2, 0.5))))
            else:
                q1 = max(1, min(5, round(rng.gauss(3.5, 0.8))))
                q2 = max(1, min(5, round(rng.gauss(3.6, 0.7))))
                q3 = max(1, min(5, round(rng.gauss(3.7, 0.6))))
            rows.append({"rol": rol, "turno": turno, "q1_bienestar": q1, "q2_liderazgo": q2,
                         "q3_recursos": q3, "texto_abierto": "", "tema_real": ""})
    # Celda pequeña a proposito: Oficinas x Tarde con exactamente 3 filas (< k=5)
    oficinas = [r for r in rows if r["rol"] == "Oficinas"]
    for r in rng.sample(oficinas, 3):
        r["turno"] = "Tarde"
    rng.shuffle(rows)
    for i, r in enumerate(rows):
        r["employee_id"] = hashlib.sha256(f"damm_test_{SEED}_{i}".encode()).hexdigest()[:16]
        if rng.random() < 0.30:
            c_text, c_tema = rng.choice(ENGINE_COMMENTS)
            r["texto_abierto"] = c_text
            r["tema_real"] = c_tema
    for r, (text, p_tema) in zip(rng.sample(rows, len(PII_COMMENTS)), PII_COMMENTS):
        r["texto_abierto"] = text
        r["tema_real"] = p_tema
    cols = ["employee_id", "rol", "turno", "q1_bienestar", "q2_liderazgo", "q3_recursos", "texto_abierto", "tema_real"]
    return [{c: r[c] for c in cols} for r in rows]


# --------------------------------------------------------------------------- payloads
def build_payloads() -> dict:
    ok = lambda dim, site, score: {"payload": {"dimension": dim, "site": site, "score": score}}
    valid = [ok("bienestar", "Fábrica", 1), ok("liderazgo", "Logística", 5),
             ok("desarrollo", "Comercial", 3), ok("cultura", "Oficina", 4),
             ok("relaciones", "Fábrica", 2), ok("condiciones", "Logística", 5)]
    bad = lambda case, payload: {"case": case, "payload": payload}
    invalid = [
        bad("score_cero", {"dimension": "bienestar", "site": "Fábrica", "score": 0}),
        bad("score_seis", {"dimension": "bienestar", "site": "Fábrica", "score": 6}),
        bad("score_negativo", {"dimension": "bienestar", "site": "Fábrica", "score": -1}),
        bad("score_string", {"dimension": "bienestar", "site": "Fábrica", "score": "3"}),
        bad("score_float", {"dimension": "bienestar", "site": "Fábrica", "score": 3.5}),
        bad("score_bool", {"dimension": "bienestar", "site": "Fábrica", "score": True}),
        bad("score_ausente", {"dimension": "bienestar", "site": "Fábrica"}),
        bad("dimension_desconocida", {"dimension": "salario", "site": "Fábrica", "score": 3}),
        bad("dimension_mayuscula", {"dimension": "Bienestar", "site": "Fábrica", "score": 3}),
        bad("dimension_ausente", {"site": "Fábrica", "score": 3}),
        bad("site_desconocido", {"dimension": "bienestar", "site": "Almacén", "score": 3}),
        bad("site_es_cohorte_no_site", {"dimension": "bienestar", "site": "Planta", "score": 3}),
        bad("site_ausente", {"dimension": "bienestar", "score": 3}),
        bad("payload_lista", [1, 2, 3]),
        bad("payload_null", None),
        bad("payload_string", "hola"),
        bad("payload_vacio", {}),
    ]
    return {"valid": valid, "invalid": invalid,
            "nota": "Todos los 'invalid' deben lanzar RequestError (HTTP 400) en validate_response."}


# --------------------------------------------------------------------------- oraculo
def sstd(values: list[int]) -> float:
    return round(statistics.stdev(values), 3) if len(values) > 1 else 0.0


def build_expected(resp: list[dict], eng: list[dict]) -> dict:
    by_pc = Counter((r["period"], r["cohort"]) for r in resp)
    actual = [r for r in resp if r["period"] == "actual"]
    anterior = [r for r in resp if r["period"] == "anterior"]
    total_actual = len(actual)

    representation = {}
    for c in COHORTS:
        n = by_pc[("actual", c)]
        ratio = (n / total_actual) / POPULATION[c]
        representation[c] = {
            "n": n,
            "observed_pct": round(100 * n / total_actual) if n >= MIN_K else None,
            "expected_pct": round(100 * POPULATION[c]),
            "status": ("Protegida" if n < MIN_K else "Baja" if ratio < 0.7
                       else "Alta" if ratio > 1.5 else "Equilibrada"),
        }

    cells = defaultdict(list)
    for r in resp:
        cells[(r["period"], r["cohort"], r["dimension"])].append(r["score"])
    small_cells = sorted(f"{p}|{c}|{d}|n={len(v)}" for (p, c, d), v in cells.items() if len(v) < MIN_K)
    polarized = [{"period": p, "cohort": c, "dimension": d, "n": len(v), "std": sstd(v)}
                 for (p, c, d), v in sorted(cells.items()) if len(v) >= MIN_K and sstd(v) >= 1.25]

    def topic_counts(rows):
        return Counter(classify_comment(r["comment"]) for r in rows if r["comment"])
    tc_a, tc_p = topic_counts(actual), topic_counts(anterior)
    topics = {}
    for t in TOPICS:
        change = round((tc_a[t] / max(1, len(actual)) - tc_p[t] / max(1, len(anterior))) * 100)
        topics[t] = {"actual": tc_a[t], "anterior": tc_p[t], "change_pct": change,
                     "emerging": change >= 8 and tc_a[t] >= MIN_K}

    # engine csv
    n_eng = len(eng)
    por_rol = Counter(r["rol"] for r in eng)
    por_rol_turno = Counter((r["rol"], r["turno"]) for r in eng)
    pol = []
    for rol in por_rol:
        for q in ("q1_bienestar", "q2_liderazgo", "q3_recursos"):
            vals = [r[q] for r in eng if r["rol"] == rol]
            s = sstd(vals)
            if len(vals) >= MIN_K and s >= 1.25:
                pol.append({"rol": rol, "pregunta": q, "std": s})
    pii_tokens = ["juan.perez@example.com", "600 123 456", "Marta López", "intranet.example.com"]
    return {
        "seed": SEED,
        "responses_csv": {
            "total": len(resp),
            "por_periodo_cohorte": {f"{p}|{c}": n for (p, c), n in sorted(by_pc.items())},
            "representacion_actual": representation,
            "celdas_menores_k": small_cells,
            "polarizacion_cohorte_dimension_std_muestral_ge_1.25": polarized,
            "temas": topics,
            "temas_emergentes_esperados": sorted(t for t, v in topics.items() if v["emerging"]),
            "typeform_ids_unicos": len({r["typeform_response_id"] for r in resp if r["typeform_response_id"]}),
        },
        "engine_csv": {
            "total": n_eng,
            "porcentaje_por_rol": {r: round(100 * n / n_eng, 2) for r, n in sorted(por_rol.items())},
            "roles_bajo_15pct": sorted(r for r, n in por_rol.items() if 100 * n / n_eng < 15),
            "celdas_rol_turno_menores_k": sorted(f"{r}|{t}|n={n}" for (r, t), n in por_rol_turno.items() if n < MIN_K),
            "polarizacion_rol_pregunta": pol,
            "tokens_pii_que_no_deben_aparecer_en_el_payload": pii_tokens,
        },
    }


# --------------------------------------------------------------------------- io
def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def load_into_db(rows: list[dict], project_root: Path, db_path: Path, live: bool, reset: bool) -> int:
    os.environ["PULSO_DATABASE"] = str(db_path)
    sys.path.insert(0, str(project_root))
    from backend import app  # noqa: WPS433  (import tardio: respeta PULSO_DATABASE)
    cols = ["day", "dimension", "site", "score", "is_demo", "cohort", "center_type",
            "channel", "comment", "survey_id", "period", "typeform_response_id"]
    with app.connect() as db:
        if reset:
            db.execute("DELETE FROM responses")
        db.executemany(
            f"INSERT INTO responses ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [tuple((0 if live and c == "is_demo" else r[c]) for c in cols) for r in rows],
        )
        db.commit()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="test_dataset", help="directorio de salida")
    ap.add_argument("--today", default=None, help="fecha ancla YYYY-MM-DD (por defecto, hoy)")
    ap.add_argument("--load", action="store_true", help="cargar las respuestas en una BBDD SQLite de pruebas")
    ap.add_argument("--project-root", default=".", help="raiz del proyecto Damn (donde esta backend/)")
    ap.add_argument("--db", default=None, help="ruta de la BBDD de pruebas (def.: <project-root>/data/pulso_test.sqlite3)")
    ap.add_argument("--live", action="store_true",
                    help="cargar con is_demo=0 (ruta 'datos reales'); OJO: la app sembrara ademas 600 filas demo")
    ap.add_argument("--no-reset", action="store_true", help="no vaciar `responses` antes de cargar")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    resp = generate_responses(rng, today)
    eng = generate_engine_rows(rng)

    write_csv(out / "pulso_test_responses.csv", resp, list(resp[0]))
    write_csv(out / "pulso_test_engine.csv", eng, list(eng[0]))
    (out / "invalid_payloads.json").write_text(
        json.dumps(build_payloads(), ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "expected_checks.json").write_text(
        json.dumps(build_expected(resp, eng), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK -> {out.resolve()}  ({len(resp)} respuestas, {len(eng)} filas de motor, ancla {today})")

    if args.load:
        root = Path(args.project_root).resolve()
        db_path = Path(args.db) if args.db else root / "data" / "pulso_test.sqlite3"
        n = load_into_db(resp, root, db_path, args.live, not args.no_reset)
        print(f"Cargadas {n} filas en {db_path}")
        print(f"Arranca la app con:  PULSO_DATABASE={db_path} python -m backend.app")


if __name__ == "__main__":
    main()