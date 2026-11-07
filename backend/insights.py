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
    _now_iso,
    _survey_dict,
    classify_comment,
    connect,
)
from .statistics_engine import (
    analyze_aggregated_dataset,
    analyze_survey_dataset,
    calculate_quarter_label,
    generate_mock_responses_for_survey,
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


def get_statistical_engine_payload(
    survey_id: str | None = None,
    filter_type: str = "survey",
    filter_value: str | None = None,
) -> dict:
    """
    Ejecuta el Motor Estadístico Determinista filtrando por:
    - 'survey': Encuesta individual concreta (por ID).
    - 'quarter': Cuatrimestre completo (ej. 'Q4 2026') consolidando todas sus microconsultas.
    - 'year': Año completo (ej. '2026') con desglose comparativo por cuatrimestres.
    """
    with connect() as db:
        survey_rows = db.execute("SELECT * FROM surveys ORDER BY created_at DESC").fetchall()
    
    surveys_list = [_survey_dict(row, include_private=True) for row in survey_rows]
    for s in surveys_list:
        s["quarter"] = s.get("quarter") or calculate_quarter_label(s.get("created_at"))
        s["year"] = str(s.get("created_at", "")[:4]) or str(date.today().year)

    all_quarters = sorted(list({s["quarter"] for s in surveys_list if s.get("quarter")}), reverse=True)
    if not all_quarters:
        all_quarters = [calculate_quarter_label(None)]

    all_years = sorted(list({s["year"] for s in surveys_list if s.get("year") and len(s["year"]) == 4}), reverse=True)
    if not all_years:
        all_years = [str(date.today().year)]

    # Si se pasa survey_id explícito sin filter_type, forzar filtro de encuesta
    if survey_id and (not filter_type or filter_type == "survey"):
        filter_type = "survey"
        filter_value = survey_id

    real_responses = []

    if filter_type == "quarter":
        target_quarter = filter_value or all_quarters[0]
        matching_surveys = [s for s in surveys_list if s.get("quarter") == target_quarter]
        matching_ids = [s["id"] for s in matching_surveys]

        with connect() as db:
            if matching_ids:
                placeholders = ",".join("?" for _ in matching_ids)
                resp_rows = db.execute(
                    f"SELECT * FROM responses WHERE survey_id IN ({placeholders}) ORDER BY day DESC, id DESC",
                    tuple(matching_ids)
                ).fetchall()
            else:
                resp_rows = []

        real_responses = [dict(r) for r in resp_rows]
        combined_responses = list(real_responses)
        if len(combined_responses) < 25:
            seed_surveys = matching_surveys if matching_surveys else [
                {"id": f"seed_q_{target_quarter}", "title": f"Pulso Operativo {target_quarter}", "cohort": "Todos", "created_at": _now_iso()}
            ]
            for s in seed_surveys:
                combined_responses.extend(generate_mock_responses_for_survey(s, count=25))

        analysis = analyze_aggregated_dataset("quarter", target_quarter, matching_surveys, combined_responses)
        analysis["survey_id_activo"] = ""
        analysis["filtro_activo"] = {"tipo": "quarter", "valor": target_quarter}

    elif filter_type == "year":
        target_year = filter_value or all_years[0]
        matching_surveys = [s for s in surveys_list if s.get("year") == target_year]
        matching_ids = [s["id"] for s in matching_surveys]

        with connect() as db:
            if matching_ids:
                placeholders = ",".join("?" for _ in matching_ids)
                resp_rows = db.execute(
                    f"SELECT * FROM responses WHERE survey_id IN ({placeholders}) ORDER BY day DESC, id DESC",
                    tuple(matching_ids)
                ).fetchall()
            else:
                resp_rows = []

        real_responses = [dict(r) for r in resp_rows]
        combined_responses = list(real_responses)
        if len(combined_responses) < 30:
            seed_surveys = matching_surveys if matching_surveys else [
                {"id": f"seed_y_{target_year}", "title": f"Pulso Anual {target_year}", "cohort": "Todos", "created_at": _now_iso()}
            ]
            for s in seed_surveys:
                combined_responses.extend(generate_mock_responses_for_survey(s, count=30))

        analysis = analyze_aggregated_dataset("year", target_year, matching_surveys, combined_responses)
        analysis["survey_id_activo"] = ""
        analysis["filtro_activo"] = {"tipo": "year", "valor": target_year}

    else:
        # Por defecto: filter_type == "survey"
        target_survey = None
        target_id = filter_value or survey_id
        if target_id:
            for s in surveys_list:
                if s["id"] == target_id:
                    target_survey = s
                    break

        if not target_survey and surveys_list:
            target_survey = next((s for s in surveys_list if s["status"] == "published"), surveys_list[0])

        if not target_survey:
            target_survey = {
                "id": "general",
                "title": "Pulso Clima General",
                "quarter": calculate_quarter_label(None),
                "created_at": _now_iso(),
                "status": "published",
                "cohort": "Todos",
                "questions": [{"id": "q1", "text": "Clima General", "type": "rating", "dimension": "bienestar"}],
            }

        with connect() as db:
            resp_rows = db.execute(
                "SELECT * FROM responses WHERE survey_id = ? ORDER BY day DESC, id DESC",
                (target_survey["id"],),
            ).fetchall()

        real_responses = [dict(r) for r in resp_rows]
        if len(real_responses) < 15:
            mock_responses = generate_mock_responses_for_survey(target_survey, count=45)
            combined_responses = real_responses + mock_responses
        else:
            combined_responses = real_responses

        analysis = analyze_survey_dataset(target_survey, combined_responses)
        analysis["survey_id_activo"] = target_survey["id"]
        analysis["filtro_activo"] = {"tipo": "survey", "valor": target_survey["id"]}

    analysis["encuestas_disponibles"] = [
        {
            "id": s["id"],
            "title": s["title"],
            "quarter": s.get("quarter") or calculate_quarter_label(s.get("created_at")),
            "year": s.get("year", "2026"),
            "cohort": s.get("cohort", "Todos"),
            "status": s.get("status", "draft"),
            "created_at": s.get("created_at", ""),
        }
        for s in surveys_list
    ]
    analysis["cuatrimestres_disponibles"] = all_quarters
    analysis["anos_disponibles"] = all_years
    analysis["respuestas_reales_capturadas"] = len(real_responses)
    return analysis


class InsightsPanel:
    """Gestiona la computación matemática determinista y síntesis de Insights."""
    @staticmethod
    def get_metrics(
        survey_id: str | None = None,
        filter_type: str = "survey",
        filter_value: str | None = None,
    ) -> dict:
        return get_statistical_engine_payload(
            survey_id=survey_id, filter_type=filter_type, filter_value=filter_value
        )

    @staticmethod
    def generate_llm_synthesis(
        survey_id: str | None = None,
        filter_type: str = "survey",
        filter_value: str | None = None,
    ) -> dict:
        payload_stats = get_statistical_engine_payload(
            survey_id=survey_id, filter_type=filter_type, filter_value=filter_value
        )
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
