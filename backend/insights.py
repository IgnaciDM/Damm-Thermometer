from __future__ import annotations

import json
import math
import statistics
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
    sanitize_comment,
)
from .statistics_engine import (
    analyze_aggregated_dataset,
    analyze_qualitative_feedback,
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


def analyze_typeform_submissions(survey: dict, submissions: list[object]) -> dict:
    """Resume respuestas importadas por pregunta sin exponer texto identificable."""
    answers_by_question = {
        question["id"]: []
        for question in survey.get("questions", [])
        if isinstance(question, dict) and isinstance(question.get("id"), str)
    }
    imported_at = []
    for submission in submissions:
        raw_answers = _row_value(submission, "answers_json", "[]")
        try:
            answers = json.loads(raw_answers)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(answers, list):
            continue
        imported_value = _row_value(submission, "imported_at", "")
        if isinstance(imported_value, str) and imported_value:
            imported_at.append(imported_value)
        for answer in answers:
            if (
                isinstance(answer, dict)
                and answer.get("question_id") in answers_by_question
            ):
                answers_by_question[answer["question_id"]].append(answer.get("answer"))

    question_results = []
    for question in survey.get("questions", []):
        question_id = question.get("id")
        kind = question.get("type")
        values = answers_by_question.get(question_id, [])
        answered = [
            value for value in values
            if value is not None and value != "" and value != []
        ]
        result = {
            "question_id": question_id,
            "question": question.get("text", ""),
            "type": kind,
            "dimension": question.get("dimension", "bienestar"),
            "responses": len(answered) if len(answered) >= MIN_GROUP_SIZE else None,
            "protected": len(answered) < MIN_GROUP_SIZE,
        }

        if kind == "rating":
            scores = [
                value for value in answered
                if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5
            ]
            counts = {str(score): scores.count(score) for score in range(1, 6)}
            protected = len(scores) < MIN_GROUP_SIZE or any(0 < count < MIN_GROUP_SIZE for count in counts.values())
            result.update(
                {
                    "responses": len(scores) if len(scores) >= MIN_GROUP_SIZE else None,
                    "protected": protected,
                    "mean": round(statistics.mean(scores), 2) if len(scores) >= MIN_GROUP_SIZE else None,
                    "median": statistics.median(scores) if len(scores) >= MIN_GROUP_SIZE else None,
                    "standard_deviation": round(statistics.stdev(scores), 2) if len(scores) >= MIN_GROUP_SIZE else None,
                    "distribution": [
                        {
                            "label": score,
                            "count": None if protected else counts[str(score)],
                            "percentage": round(counts[str(score)] / len(scores) * 100, 1)
                            if scores and not protected else None,
                        }
                        for score in range(1, 6)
                    ],
                }
            )
        elif kind in {"multiple_choice", "yes_no"}:
            if kind == "multiple_choice":
                labels = question.get("choices", [])
                if not isinstance(labels, list):
                    labels = []
                labels = [str(label) for label in labels]
                selected = [
                    choice
                    for value in answered
                    for choice in (value if isinstance(value, list) else [value])
                    if isinstance(choice, str)
                ]
            else:
                labels = ["Sí", "No"]
                selected = [
                    "Sí" if value else "No"
                    for value in answered
                    if isinstance(value, bool)
                ]
            counts = {label: selected.count(label) for label in labels}
            protected = len(answered) < MIN_GROUP_SIZE or any(
                0 < count < MIN_GROUP_SIZE for count in counts.values()
            )
            result.update(
                {
                    "responses": len(answered) if len(answered) >= MIN_GROUP_SIZE else None,
                    "protected": protected,
                    "distribution": [
                        {
                            "label": label,
                            "count": None if protected else counts[label],
                            "percentage": round(counts[label] / len(answered) * 100, 1)
                            if answered and not protected else None,
                        }
                        for label in labels
                    ],
                }
            )
        elif kind == "open_text":
            comments = [
                sanitize_comment(value)
                for value in answered
                if isinstance(value, str) and len(value.strip()) >= 4
            ]
            protected = len(comments) < MIN_GROUP_SIZE
            result.update(
                {
                    "responses": len(comments) if not protected else None,
                    "protected": protected,
                    "qualitative": None,
                }
            )
            if not protected:
                qualitative = analyze_qualitative_feedback(comments)
                sentiment = qualitative["sentimiento_global"]
                sentiment_counts = {
                    "positivo": round(sentiment["positivo_pct"] * len(comments) / 100),
                    "crítico": round(sentiment["critico_pct"] * len(comments) / 100),
                    "neutro": round(sentiment["neutro_pct"] * len(comments) / 100),
                }
                sentiment_protected = any(0 < count < MIN_GROUP_SIZE for count in sentiment_counts.values())
                topics = qualitative["temas"]
                topics_protected = any(
                    0 < topic["frecuencia"] < MIN_GROUP_SIZE for topic in topics
                )
                result["qualitative"] = {
                    "sentiment": None if sentiment_protected else sentiment,
                    "topics": [
                        {
                            "topic": topic["tema"],
                            "count": None if topics_protected else topic["frecuencia"],
                            "percentage": None if topics_protected else topic["porcentaje"],
                        }
                        for topic in topics
                    ],
                    "protected": sentiment_protected or topics_protected,
                }

        question_results.append(result)

    return {
        "survey_id": survey.get("id", ""),
        "title": survey.get("title", ""),
        "total_responses": len(submissions),
        "last_imported_at": max(imported_at, default=""),
        "questions": question_results,
    }


def _unique_response_rows(rows: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for row in rows:
        key = row.get("typeform_submission_key") or row.get("typeform_response_id")
        if key:
            if key in seen:
                continue
            seen.add(key)
        unique.append(row)
    return unique


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
    analysis_surveys = []
    submissions_by_survey = {}

    if filter_type == "quarter":
        target_quarter = filter_value or all_quarters[0]
        matching_surveys = [s for s in surveys_list if s.get("quarter") == target_quarter]
        analysis_surveys = matching_surveys
        matching_ids = [s["id"] for s in matching_surveys]

        with connect() as db:
            if matching_ids:
                placeholders = ",".join("?" for _ in matching_ids)
                resp_rows = db.execute(
                    f"SELECT * FROM responses WHERE survey_id IN ({placeholders}) ORDER BY day DESC, id DESC",
                    tuple(matching_ids)
                ).fetchall()
                submission_rows = db.execute(
                    f"""SELECT * FROM typeform_submissions
                        WHERE survey_id IN ({placeholders}) ORDER BY submitted_at DESC, id DESC""",
                    tuple(matching_ids),
                ).fetchall()
            else:
                resp_rows = []
                submission_rows = []

        real_responses = [dict(r) for r in resp_rows]
        submissions_by_survey = {}
        for submission in submission_rows:
            submissions_by_survey.setdefault(submission["survey_id"], []).append(dict(submission))
        combined_responses = _unique_response_rows(real_responses)
        has_typeform_surveys = any(s.get("typeform_connected") for s in matching_surveys)
        if len(combined_responses) < 25 and not has_typeform_surveys:
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
        analysis_surveys = matching_surveys
        matching_ids = [s["id"] for s in matching_surveys]

        with connect() as db:
            if matching_ids:
                placeholders = ",".join("?" for _ in matching_ids)
                resp_rows = db.execute(
                    f"SELECT * FROM responses WHERE survey_id IN ({placeholders}) ORDER BY day DESC, id DESC",
                    tuple(matching_ids)
                ).fetchall()
                submission_rows = db.execute(
                    f"""SELECT * FROM typeform_submissions
                        WHERE survey_id IN ({placeholders}) ORDER BY submitted_at DESC, id DESC""",
                    tuple(matching_ids),
                ).fetchall()
            else:
                resp_rows = []
                submission_rows = []

        real_responses = [dict(r) for r in resp_rows]
        submissions_by_survey = {}
        for submission in submission_rows:
            submissions_by_survey.setdefault(submission["survey_id"], []).append(dict(submission))
        combined_responses = _unique_response_rows(real_responses)
        has_typeform_surveys = any(s.get("typeform_connected") for s in matching_surveys)
        if len(combined_responses) < 30 and not has_typeform_surveys:
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
        analysis_surveys = [target_survey]

        with connect() as db:
            resp_rows = db.execute(
                "SELECT * FROM responses WHERE survey_id = ? ORDER BY day DESC, id DESC",
                (target_survey["id"],),
            ).fetchall()
            submission_rows = db.execute(
                """SELECT * FROM typeform_submissions
                   WHERE survey_id = ? ORDER BY submitted_at DESC, id DESC""",
                (target_survey["id"],),
            ).fetchall()

        real_responses = [dict(r) for r in resp_rows]
        submissions_by_survey = {
            target_survey["id"]: [dict(row) for row in submission_rows]
        }
        response_rows_for_analysis = _unique_response_rows(real_responses)
        if len(response_rows_for_analysis) < 15 and not target_survey.get("typeform_connected"):
            mock_responses = generate_mock_responses_for_survey(target_survey, count=45)
            combined_responses = response_rows_for_analysis + mock_responses
        else:
            combined_responses = response_rows_for_analysis

        analysis = analyze_survey_dataset(target_survey, combined_responses)
        analysis["survey_id_activo"] = target_survey["id"]
        analysis["filtro_activo"] = {"tipo": "survey", "valor": target_survey["id"]}

    typeform_analyses = [
        analyze_typeform_submissions(
            survey,
            submissions_by_survey.get(survey["id"], []),
        )
        for survey in analysis_surveys
        if survey.get("typeform_connected")
    ]
    typeform_submission_count = sum(item["total_responses"] for item in typeform_analyses)
    analysis["analisis_respuestas_typeform"] = {
        "total_responses": typeform_submission_count,
        "surveys": typeform_analyses,
    }

    analysis["encuestas_disponibles"] = [
        {
            "id": s["id"],
            "title": s["title"],
            "quarter": s.get("quarter") or calculate_quarter_label(s.get("created_at")),
            "year": s.get("year", "2026"),
            "cohort": s.get("cohort", "Todos"),
            "status": s.get("status", "draft"),
            "created_at": s.get("created_at", ""),
            "typeform_connected": s.get("typeform_connected", False),
        }
        for s in surveys_list
    ]
    analysis["cuatrimestres_disponibles"] = all_quarters
    analysis["anos_disponibles"] = all_years
    analysis["respuestas_typeform_importadas"] = typeform_submission_count
    analysis["respuestas_reales_capturadas"] = (
        typeform_submission_count if typeform_analyses else len(_unique_response_rows(real_responses))
    )
    if typeform_analyses and filter_type == "survey":
        analysis["kpis_ejecutivos"]["total_respuestas"] = typeform_submission_count
        analysis["kpis_ejecutivos"]["nivel_confianza"] = (
            "Alta" if typeform_submission_count >= 60
            else "Media" if typeform_submission_count >= 20
            else "Baja"
        )
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
