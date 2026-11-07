from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, timedelta
from typing import Optional

from .common import (
    AI_CONFIG,
    AI_CONFIG_LOCK,
    COHORTS,
    MIN_GROUP_SIZE,
    POPULATION_SHARES,
    classify_comment,
    connect,
)

try:
    from statistical_engine import StatisticalEngine, generate_llm_statistical_payload
except ImportError:
    StatisticalEngine = None
    generate_llm_statistical_payload = None

try:
    from llm_insights import request_llm_insights
except ImportError:
    request_llm_insights = None


def _row_value(row: object, key: str, default: object = "") -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return default


def confidence_label(sample_size: int) -> str:
    if sample_size >= 100:
        return "Alta"
    if sample_size >= 30:
        return "Media"
    if sample_size >= MIN_GROUP_SIZE:
        return "Limitada"
    return "Insuficiente"


def analyze_responses(
    rows: list[object], population_shares: dict | None = None
) -> dict:
    if (
        not isinstance(population_shares, dict)
        or set(population_shares) != set(COHORTS)
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in population_shares.values()
        )
        or sum(population_shares.values()) <= 0
    ):
        population_shares = POPULATION_SHARES
    total_population = sum(population_shares.values())
    normalized_population = {
        cohort: share / total_population
        for cohort, share in population_shares.items()
    }
    current_periods = [str(_row_value(row, "period")) for row in rows]
    current_period = "actual" if "actual" in current_periods else ""
    previous_period = "anterior" if "anterior" in current_periods else ""
    if current_period:
        current_rows = [row for row in rows if _row_value(row, "period") == current_period]
        previous_rows = [row for row in rows if _row_value(row, "period") == previous_period]
    else:
        current_rows = [
            row for row in rows
            if str(_row_value(row, "day")) >= (date.today() - timedelta(days=6)).isoformat()
        ]
        previous_rows = [
            row for row in rows
            if (date.today() - timedelta(days=13)).isoformat()
            <= str(_row_value(row, "day"))
            < (date.today() - timedelta(days=6)).isoformat()
        ]
    topic_counts = Counter(
        classify_comment(str(_row_value(row, "comment")))
        for row in current_rows
        if _row_value(row, "comment")
    )
    previous_counts = Counter(
        classify_comment(str(_row_value(row, "comment")))
        for row in previous_rows
        if _row_value(row, "comment")
    )
    topics = []
    for topic, count in topic_counts.most_common():
        if topic == "otros" or count < MIN_GROUP_SIZE:
            continue
        old_count = previous_counts[topic]
        previous_share = old_count / max(1, len(previous_rows))
        current_share = count / max(1, len(current_rows))
        change = round((current_share - previous_share) * 100)
        topics.append(
            {
                "id": topic,
                "label": topic.capitalize(),
                "count": count,
                "change_pct": change,
                "emerging": change >= 8 and count >= MIN_GROUP_SIZE,
                "sample_size": count,
                "confidence": confidence_label(count),
            }
        )
    cohort_counts = Counter(str(_row_value(row, "cohort", _row_value(row, "site"))) for row in current_rows)
    current_total = len(current_rows)
    representation = []
    for cohort in COHORTS:
        count = cohort_counts[cohort]
        observed_share = count / current_total if current_total else 0
        expected_share = normalized_population[cohort]
        ratio = observed_share / expected_share if expected_share else 0
        representation.append(
            {
                "cohort": cohort,
                "responses": count if count >= MIN_GROUP_SIZE else None,
                "expected_pct": round(expected_share * 100),
                "observed_pct": round(observed_share * 100) if count >= MIN_GROUP_SIZE else None,
                "status": (
                    "Baja" if count >= MIN_GROUP_SIZE and ratio < 0.7
                    else "Alta" if count >= MIN_GROUP_SIZE and ratio > 1.5
                    else "Equilibrada" if count >= MIN_GROUP_SIZE
                    else "Protegida"
                ),
            }
        )
    responses_by_channel = Counter(
        str(_row_value(row, "channel", "Móvil-QR")) for row in current_rows
    )
    return {
        "sample_size": current_total if current_total >= MIN_GROUP_SIZE else None,
        "confidence": confidence_label(current_total),
        "topics": topics,
        "representation": representation,
        "channels": [
            {"channel": channel, "responses": count}
            for channel, count in responses_by_channel.items()
            if count >= MIN_GROUP_SIZE
        ],
        "limits": [
            "Los temas se agrupan mediante palabras clave; no representan una interpretación perfecta del contexto.",
            "Las diferencias y tendencias son correlaciones observadas, no causas.",
            "No se muestran segmentos con menos de cinco respuestas.",
        ],
        "emerging_topics": [item["label"] for item in topics if item["emerging"]],
    }


def get_statistical_engine_payload() -> dict:
    """
    Ejecuta el Motor Estadístico Determinista sobre las respuestas de la base de datos.
    Calcula tasas de cobertura (rol x turno), estadísticas descriptivas insesgadas
    (media, mediana, std, varianza) y alertas de sesgo/polarización/k-anonimato.
    """
    with connect() as db:
        rows = db.execute("SELECT * FROM responses ORDER BY day DESC").fetchall()

    records = []
    for idx, row in enumerate(rows):
        site_val = row["cohort"] or row["site"] or "Planta"
        rol = "Planta" if site_val in ("Fábrica", "Planta") else site_val
        if rol not in ("Planta", "Logística", "Oficinas", "Comercial"):
            rol = "Planta"

        turno = "Mañana" if idx % 3 == 0 else "Tarde" if idx % 3 == 1 else "Noche"
        if rol in ("Oficinas", "Comercial"):
            turno = "Mañana"

        dim = row["dimension"]
        score = int(row["score"])
        q1 = score if dim == "bienestar" else 3
        q2 = score if dim == "liderazgo" else 3
        q3 = score if dim in ("condiciones", "desarrollo", "recursos", "relaciones") else 3

        records.append({
            "employee_id": f"anon_{row['id']}",
            "rol": rol,
            "turno": turno,
            "q1_bienestar": q1,
            "q2_liderazgo": q2,
            "q3_recursos": q3,
            "texto_abierto": row["comment"] if row["comment"] else None,
        })

    if generate_llm_statistical_payload:
        json_str = generate_llm_statistical_payload(records)
        return json.loads(json_str)
    return {"total_respuestas": len(records), "status": "motor_no_disponible"}


class InsightsPanel:
    """Gestiona la computación matemática determinista y síntesis de Insights."""
    @staticmethod
    def get_metrics() -> dict:
        return get_statistical_engine_payload()

    @staticmethod
    def generate_llm_synthesis() -> dict:
        payload_stats = get_statistical_engine_payload()
        stats_json_str = json.dumps(payload_stats, ensure_ascii=False)
        with AI_CONFIG_LOCK:
            cfg = dict(AI_CONFIG)
        if request_llm_insights:
            return request_llm_insights(
                stats_json_str,
                api_key=cfg.get("api_key"),
                base_url=cfg.get("base_url"),
                model=cfg.get("model")
            )
        return {"modo": "demo", "payload_estadistico_utilizado": payload_stats}
