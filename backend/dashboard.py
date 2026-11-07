from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, timedelta
from typing import Optional

from .common import (
    COHORT_LABELS,
    DIMENSIONS,
    MIN_GROUP_SIZE,
    RETENTION_DAYS,
    SITES,
    connect,
    validate_response,
)
from .insights import analyze_responses


def seed_demo_data(db: sqlite3.Connection) -> None:
    if db.execute("SELECT COUNT(*) FROM responses WHERE is_demo = 1").fetchone()[0]:
        return
    rng = random.Random(2026)
    baselines = [3.7, 3.8, 3.5, 4.0, 3.8, 3.4]
    distributions = [
        {"Planta": 160, "Logística": 70, "Comercial": 28, "Oficina": 30, "Manager": 12},
        {"Planta": 100, "Logística": 50, "Comercial": 40, "Oficina": 65, "Manager": 45},
    ]
    comments = {
        "comunicacion": [
            "Los cambios de turno podrían llegar con más tiempo.",
            "A veces la comunicación de los cambios se pierde entre mensajes.",
            "Nos iría bien tener la información clara antes de empezar.",
        ],
        "turnos": [
            "La planificación de turnos deja poco margen para organizarse.",
            "Un poco más de previsión en los horarios ayudaría mucho.",
            "En algunos turnos cuesta encontrar tiempo para descansar.",
        ],
        "herramientas": [
            "La herramienta nueva funciona, pero hace falta más formación.",
            "Nos faltan algunos recursos para resolver incidencias rápido.",
            "Sería útil tener instrucciones más sencillas del sistema.",
        ],
        "liderazgo": [
            "Agradezco cuando la persona responsable escucha al equipo.",
            "Vendría bien recibir feedback más a menudo.",
            "Tenemos buena disposición, aunque no siempre encontramos tiempo para hablar.",
        ],
        "reconocimiento": [
            "Un agradecimiento por el trabajo bien hecho marca la diferencia.",
            "El equipo se esfuerza y estaría bien visibilizarlo más.",
            "A veces los pequeños logros pasan desapercibidos.",
        ],
        "desarrollo": [
            "Me gustaría tener más oportunidades para aprender en el puesto.",
            "La formación práctica ayuda a sentir más confianza.",
            "Sería bueno compartir lo que aprendemos con el resto del equipo.",
        ],
        "bienestar": [
            "Una pausa a tiempo ayuda a recuperar energía.",
            "En los días de más carga es difícil parar un momento.",
            "El ambiente del equipo contribuye mucho al bienestar.",
        ],
    }
    previous_topics = ["bienestar", "liderazgo", "herramientas", "reconocimiento", "desarrollo"]
    current_topics = ["comunicacion", "comunicacion", "turnos", "herramientas", "bienestar"]
    demo_rows = []
    for period_index, distribution in enumerate(distributions):
        days_ago = 18 if period_index == 0 else 4
        topics = previous_topics if period_index == 0 else current_topics
        period_name = "anterior" if period_index == 0 else "actual"
        index = 0
        for cohort, count in distribution.items():
            for _ in range(count):
                day = date.today() - timedelta(days=max(0, days_ago - rng.randrange(7)))
                topic = topics[index % len(topics)]
                dimension_index = index % len(DIMENSIONS)
                score = max(
                    1,
                    min(5, round(rng.gauss(baselines[dimension_index], 0.8))),
                )
                comment = rng.choice(comments[topic])
                channel = rng.choices(
                    ["Móvil-QR", "Teams", "Correo"],
                    weights=[65 if cohort in {"Planta", "Logística"} else 35, 25, 10 if cohort in {"Planta", "Logística"} else 40],
                )[0]
                demo_rows.append(
                    (
                        day.isoformat(),
                        DIMENSIONS[dimension_index]["id"],
                        COHORT_LABELS[cohort],
                        score,
                        1,
                        cohort,
                        "Centro amplio",
                        channel,
                        comment,
                        "",
                        period_name,
                    )
                )
                index += 1
    db.executemany(
        """INSERT INTO responses
           (day, dimension, site, score, is_demo, cohort, center_type, channel, comment, survey_id, period)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        demo_rows,
    )
    db.commit()


def _visible_average(rows: list[sqlite3.Row]) -> float | None:
    if len(rows) < MIN_GROUP_SIZE:
        return None
    return round(sum(row["score"] for row in rows) / len(rows), 1)


def aggregate_responses(
    rows: list[sqlite3.Row], today: date | None = None
) -> dict:
    today = today or date.today()
    cutoff = today - timedelta(days=6)
    recent = [row for row in rows if date.fromisoformat(row["day"]) >= cutoff]
    visible_rows = rows if len(rows) >= MIN_GROUP_SIZE else []
    overall = _visible_average(visible_rows)

    dimension_stats = []
    for item in DIMENSIONS:
        subset = [row for row in recent if row["dimension"] == item["id"]]
        previous = [
            row
            for row in rows
            if item["id"] == row["dimension"]
            and cutoff - timedelta(days=7) <= date.fromisoformat(row["day"]) < cutoff
        ]
        dimension_stats.append(
            {
                **item,
                "score": _visible_average(subset),
                "responses": len(subset) if len(subset) >= MIN_GROUP_SIZE else None,
                "trend": (
                    round(_visible_average(subset) - _visible_average(previous), 1)
                    if _visible_average(subset) is not None
                    and _visible_average(previous) is not None
                    else None
                ),
            }
        )

    trend = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        day_rows = [row for row in rows if row["day"] == day.isoformat()]
        trend.append(
            {
                "label": ["L", "M", "X", "J", "V", "S", "D"][day.weekday()],
                "score": _visible_average(day_rows),
            }
        )

    location_stats = []
    for site in SITES:
        site_rows = [row for row in recent if row["site"] == site]
        score = _visible_average(site_rows)
        if score is not None:
            location_stats.append(
                {"label": site, "score": score, "responses": len(site_rows)}
            )

    demo_count = sum(row["is_demo"] for row in rows)
    return {
        "overall": overall,
        "responses_7d": len(recent) if len(recent) >= MIN_GROUP_SIZE else None,
        "total_responses": len(rows) if len(rows) >= MIN_GROUP_SIZE else None,
        "dimensions": dimension_stats,
        "trend": trend,
        "locations": location_stats,
        "is_demo": demo_count > 0,
        "privacy_threshold": MIN_GROUP_SIZE,
    }


def get_dashboard() -> dict:
    with connect() as db:
        seed_demo_data(db)
        db.execute(
            "DELETE FROM responses WHERE day < ?",
            ((date.today() - timedelta(days=RETENTION_DAYS)).isoformat(),),
        )
        db.execute(
            "DELETE FROM typeform_submissions WHERE substr(submitted_at, 1, 10) < ?",
            ((date.today() - timedelta(days=RETENTION_DAYS)).isoformat(),),
        )
        live_rows = db.execute(
            "SELECT * FROM responses WHERE is_demo = 0 ORDER BY day"
        ).fetchall()
        demo_rows = db.execute(
            "SELECT * FROM responses WHERE is_demo = 1 ORDER BY day"
        ).fetchall()
        population_row = db.execute(
            """SELECT population_json FROM surveys WHERE status = 'published'
               ORDER BY published_at DESC LIMIT 1"""
        ).fetchone()
    rows = live_rows if len(live_rows) >= MIN_GROUP_SIZE else demo_rows or live_rows
    result = aggregate_responses(rows)
    population = None
    if population_row:
        try:
            population = json.loads(population_row["population_json"])
        except (json.JSONDecodeError, TypeError):
            population = None
    result["insights"] = analyze_responses(rows, population)
    result["is_demo"] = bool(rows and rows[0]["is_demo"])
    result["synthetic_responses"] = len(demo_rows)
    result["live_responses"] = len(live_rows)
    result["retention_days"] = RETENTION_DAYS
    return result


def save_response(payload: object) -> dict:
    dimension, site, score = validate_response(payload)
    with connect() as db:
        db.execute(
            """INSERT INTO responses
               (day, dimension, site, score, is_demo, cohort, channel)
               VALUES (?, ?, ?, ?, 0, ?, ?)""",
            (date.today().isoformat(), dimension, site, score, site, "Móvil-QR"),
        )
        db.commit()
    return get_dashboard()


class DashboardPanel:
    """Gestiona el panel de Clima Laboral, agregaciones anónimas y KPIs."""
    @staticmethod
    def get_data() -> dict:
        return get_dashboard()

    @staticmethod
    def submit_pulse(payload: object) -> dict:
        return save_response(payload)
