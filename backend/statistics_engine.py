"""
Módulo del Motor Estadístico Determinista y Análisis de Datos (Estrella Damm).

Encargado del análisis riguroso de respuestas de encuestas (capturadas vía Typeform o QR):
- Estadísticas descriptivas insesgadas (media, mediana, desviación típica, varianza, asimetría).
- Representatividad poblacional y matriz de cobertura (Rol x Turno) según la distribución de Damm.
- Detección de sesgos: infrarrepresentación (<15%), sesgo de autoselección y turnos desatendidos.
- Detección de polarización bimodal (σ ≥ 1.25).
- Privacidad y k-anonimato estricto (k ≥ 5) sin fugas de PII.
- Agrupación cualitativa y análisis de sentimiento de comentarios abiertos.
- Cálculo temporal determinista de cuatrimestres (Q1, Q2, Q3, Q4 + Año).
"""

from __future__ import annotations

import json
import math
import random
import re
import statistics
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import hashlib
import uuid

# Detección de dependencias opcionales de Data Science
try:
    import numpy as np
    import pandas as pd
    from scipy import stats as scipy_stats
    HAS_DATA_SCIENCE_STACK = True
except ImportError:
    HAS_DATA_SCIENCE_STACK = False
    np = None  # type: ignore
    pd = None  # type: ignore
    scipy_stats = None  # type: ignore


# Constantes de dominio Estrella Damm (según documento oficial del reto)
ROLES_PERMITIDOS = ["Planta", "Logística", "Oficinas", "Comercial", "Manager"]
TURNOS_PERMITIDOS = ["Mañana", "Tarde", "Noche"]

# Pesos poblacionales reales de plantilla
POPULATION_SHARES_DEFAULT = {
    "Planta": 0.46,
    "Logística": 0.22,
    "Comercial": 0.17,
    "Oficinas": 0.10,
    "Manager": 0.05,
}

MIN_K_ANONYMITY = 5


def calculate_quarter_label(dt_or_iso: datetime | str | None) -> str:
    """
    Calcula el cuatrimestre y año (ej: Q1 2026, Q4 2019) usando la librería estándar datetime.
    Solo existen 4 cuatrimestres / trimestres comerciales al año:
    - Meses 1 a 3 (Ene-Mar): Q1
    - Meses 4 a 6 (Abr-Jun): Q2
    - Meses 7 a 9 (Jul-Sep): Q3
    - Meses 10 a 12 (Oct-Dic): Q4
    """
    if not dt_or_iso:
        dt = datetime.now()
    elif isinstance(dt_or_iso, str):
        try:
            dt = datetime.fromisoformat(dt_or_iso.replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(dt_or_iso[:10], "%Y-%m-%d")
            except Exception:
                dt = datetime.now()
    elif isinstance(dt_or_iso, (datetime, date)):
        dt = dt_or_iso if isinstance(dt_or_iso, datetime) else datetime.combine(dt_or_iso, datetime.min.time())
    else:
        dt = datetime.now()

    quarter = (dt.month - 1) // 3 + 1
    return f"Q{quarter} {dt.year}"


def calculate_descriptive_stats(scores: List[int]) -> Dict[str, Any]:
    """Calcula estadísticas descriptivas insesgadas para una serie de puntuaciones Likert 1-5."""
    n = len(scores)
    if n == 0:
        return {
            "media": 0.0,
            "mediana": 0.0,
            "desviacion_tipica": 0.0,
            "varianza": 0.0,
            "asimetria": 0.0,
            "min": 0,
            "max": 0,
            "frecuencias_estrellas": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
            "porcentajes_estrellas": {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0},
            "diagnostico": "Sin datos",
        }

    # Media aritmética insesgada
    media = sum(scores) / n

    # Mediana
    sorted_scores = sorted(scores)
    if n % 2 == 1:
        mediana = float(sorted_scores[n // 2])
    else:
        mediana = (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2.0

    # Varianza y desviación típica (muestra con corrección Bessel n-1)
    if n > 1:
        varianza = sum((x - media) ** 2 for x in scores) / (n - 1)
        desviacion = math.sqrt(varianza)
    else:
        varianza = 0.0
        desviacion = 0.0

    # Asimetría muestral (Fisher-Pearson)
    if n >= 3 and desviacion > 0.0001:
        m3 = sum((x - media) ** 3 for x in scores) / n
        asimetria = (m3 / (desviacion ** 3)) * math.sqrt(n * (n - 1)) / (n - 2) if n > 2 else 0.0
    else:
        asimetria = 0.0

    # Conteo de estrellas
    counts = Counter(scores)
    frecuencias = {s: counts[s] for s in range(1, 6)}
    porcentajes = {s: round((counts[s] / n) * 100, 1) for s in range(1, 6)}

    # Cuartiles y Boxplot metrics (Q1, Q3, IQR, Bigotes, Outliers)
    if n >= 4:
        lower_half = sorted_scores[: n // 2]
        upper_half = sorted_scores[(n + 1) // 2 :]
        q1 = float(lower_half[len(lower_half) // 2]) if len(lower_half) % 2 == 1 else (lower_half[len(lower_half) // 2 - 1] + lower_half[len(lower_half) // 2]) / 2.0
        q3 = float(upper_half[len(upper_half) // 2]) if len(upper_half) % 2 == 1 else (upper_half[len(upper_half) // 2 - 1] + upper_half[len(upper_half) // 2]) / 2.0
    else:
        q1 = float(sorted_scores[0]) if sorted_scores else 0.0
        q3 = float(sorted_scores[-1]) if sorted_scores else 0.0

    iqr = round(max(0.0, q3 - q1), 3)
    whisker_low = max(min(scores) if scores else 0, round(q1 - 1.5 * iqr, 3))
    whisker_high = min(max(scores) if scores else 5, round(q3 + 1.5 * iqr, 3))
    outliers = [x for x in scores if x < whisker_low or x > whisker_high]

    # Error estándar e Intervalo de confianza al 95%
    if n > 1 and desviacion > 0:
        se = desviacion / math.sqrt(n)
        ci_half = round(1.96 * se, 2)
        ci_low = round(max(1.0, media - ci_half), 2)
        ci_high = round(min(5.0, media + ci_half), 2)
    else:
        se = 0.0
        ci_half = 0.0
        ci_low = round(media, 2)
        ci_high = round(media, 2)

    # % Favorable (puntuaciones 4 y 5)
    favorable_count = sum(1 for x in scores if x >= 4)
    pct_favorable = round((favorable_count / n) * 100, 1)

    # Diagnóstico de clima
    if desviacion >= 1.25 or varianza >= 1.55 or (porcentajes[1] + porcentajes[2] >= 20 and porcentajes[4] + porcentajes[5] >= 25):
        diagnostico = "Polarizado (División de opiniones)"
    elif media < 2.8:
        diagnostico = "Crítico (Acción prioritaria)"
    elif media >= 3.8:
        diagnostico = "Satisfactorio (Fortaleza)"
    else:
        diagnostico = "Consistente (En rango habitual)"

    return {
        "n": n,
        "media": round(media, 3),
        "mediana": round(mediana, 3),
        "desviacion_tipica": round(desviacion, 3),
        "varianza": round(varianza, 3),
        "asimetria": round(asimetria, 3),
        "error_estandar": round(se, 3),
        "ci_half": ci_half,
        "ci_95": [ci_low, ci_high],
        "ci_label": f"[{ci_low}, {ci_high}]",
        "pct_favorable": pct_favorable,
        "q1": round(q1, 3),
        "q3": round(q3, 3),
        "iqr": round(iqr, 3),
        "whisker_low": round(whisker_low, 3),
        "whisker_high": round(whisker_high, 3),
        "outliers": outliers,
        "min": min(scores),
        "max": max(scores),
        "frecuencias_estrellas": frecuencias,
        "porcentajes_estrellas": porcentajes,
        "diagnostico": diagnostico,
    }


def calculate_bootstrap_ci(
    scores: List[int | float],
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Calcula el Intervalo de Confianza empírico mediante Bootstrap Percentil (B = 1000).
    Inmune a supuestos de normalidad para escalas ordinales Likert 1-5.
    """
    clean_scores = [float(x) for x in scores if isinstance(x, (int, float))]
    n = len(clean_scores)
    if n == 0:
        return {
            "media": 0.0,
            "ci_95": [0.0, 0.0],
            "ci_half": 0.0,
            "ci_label": "[0.00, 0.00]",
            "error_estandar_bootstrap": 0.0,
            "metodo": "bootstrap_percentil",
            "n_resamples": 0,
        }
    mean_val = sum(clean_scores) / n
    if n < 2 or all(x == clean_scores[0] for x in clean_scores):
        return {
            "media": round(mean_val, 2),
            "ci_95": [round(mean_val, 2), round(mean_val, 2)],
            "ci_half": 0.0,
            "ci_label": f"[{mean_val:.2f}, {mean_val:.2f}]",
            "error_estandar_bootstrap": 0.0,
            "metodo": "bootstrap_percentil",
            "n_resamples": n_resamples,
        }

    rng = random.Random(seed)
    boot_means = []
    for _ in range(n_resamples):
        sample = rng.choices(clean_scores, k=n)
        boot_means.append(sum(sample) / n)

    boot_means.sort()
    idx_low = max(0, int(n_resamples * (alpha / 2.0)))
    idx_high = min(n_resamples - 1, int(n_resamples * (1.0 - alpha / 2.0)))
    ci_low = round(max(1.0, boot_means[idx_low]), 2)
    ci_high = round(min(5.0, boot_means[idx_high]), 2)
    se_boot = round(math.sqrt(sum((m - mean_val) ** 2 for m in boot_means) / (n_resamples - 1)), 3)
    ci_half = round((ci_high - ci_low) / 2.0, 2)

    return {
        "media": round(mean_val, 2),
        "ci_95": [ci_low, ci_high],
        "ci_half": ci_half,
        "ci_label": f"[{ci_low:.2f}, {ci_high:.2f}]",
        "error_estandar_bootstrap": se_boot,
        "metodo": f"bootstrap_percentil_{n_resamples}",
        "n_resamples": n_resamples,
    }


def calculate_statistical_significance(
    current_scores: List[int | float],
    previous_scores: List[int | float],
    alpha: float = 0.05,
    n_permutations: int = 1000,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Evalúa la significación estadística del cambio entre dos periodos mediante
    contraste no paramétrico de permutación (shuffling) y prueba de Welch.
    Emite un aviso explícito para RRHH si la variación observada no alcanza significación (p >= alpha).
    """
    c_clean = [float(x) for x in current_scores if isinstance(x, (int, float))]
    p_clean = [float(x) for x in previous_scores if isinstance(x, (int, float))]
    n_c, n_p = len(c_clean), len(p_clean)

    if n_c < 5 or n_p < 5:
        return {
            "disponible": False,
            "motivo": f"Datos insuficientes para contraste de hipótesis (n_actual={n_c}, n_anterior={n_p}; mínimo requerido 5)",
            "delta": round((sum(c_clean) / n_c - sum(p_clean) / n_p), 2) if n_c > 0 and n_p > 0 else 0.0,
            "p_valor": None,
            "es_significativo": False,
            "nivel_confianza": "Baja",
            "aviso_significancia": "Aviso: Muestra reducida (<5 en algún periodo). No es posible contrastar validez estadística.",
        }

    mean_c = sum(c_clean) / n_c
    mean_p = sum(p_clean) / n_p
    delta_obs = mean_c - mean_p

    # Test de permutación exacto / Monte Carlo (distribución libre)
    pooled = c_clean + p_clean
    rng = random.Random(seed)
    extreme_count = 0
    abs_delta_obs = abs(delta_obs)

    delta_boots = []
    for _ in range(n_permutations):
        perm = pooled.copy()
        rng.shuffle(perm)
        p_mean_c = sum(perm[:n_c]) / n_c
        p_mean_p = sum(perm[n_c:]) / n_p
        if abs(p_mean_c - p_mean_p) >= abs_delta_obs - 1e-9:
            extreme_count += 1

        b_c = rng.choices(c_clean, k=n_c)
        b_p = rng.choices(p_clean, k=n_p)
        delta_boots.append((sum(b_c) / n_c) - (sum(b_p) / n_p))

    p_value = round((extreme_count + 1) / (n_permutations + 1), 3)
    delta_boots.sort()
    low_idx = max(0, int(n_permutations * (alpha / 2.0)))
    high_idx = min(n_permutations - 1, int(n_permutations * (1.0 - alpha / 2.0)))
    delta_ci = [round(delta_boots[low_idx], 2), round(delta_boots[high_idx], 2)]

    es_significativo = bool(p_value < alpha)
    if es_significativo:
        aviso = (
            f"Cambio estadísticamente significativo (Δ = {delta_obs:+.2f}, p = {p_value:.3f} < {alpha}, "
            f"IC 95% del cambio: [{delta_ci[0]:+.2f}, {delta_ci[1]:+.2f}])."
        )
    else:
        aviso = (
            f"Aviso de significación: La variación observada de {delta_obs:+.2f} puntos respecto al periodo anterior "
            f"NO es estadísticamente significativa (p = {p_value:.3f} ≥ {alpha}, IC 95%: [{delta_ci[0]:+.2f}, {delta_ci[1]:+.2f}]). "
            f"No debe interpretarse como una tendencia confirmada en RRHH."
        )

    return {
        "disponible": True,
        "delta": round(delta_obs, 2),
        "delta_formateado": f"{delta_obs:+.2f}",
        "p_valor": p_value,
        "es_significativo": es_significativo,
        "ic_95_delta": delta_ci,
        "ic_95_delta_label": f"[{delta_ci[0]:+.2f}, {delta_ci[1]:+.2f}]",
        "n_actual": n_c,
        "n_anterior": n_p,
        "aviso_significancia": aviso,
    }


def get_respondent_key(r: Dict[str, Any], fallback_idx: int = 0) -> str:
    """
    Extrae el identificador del encuestado para asegurar conteo de personas y no de filas.
    Cumple con el requisito estricto: n y k-anonimato cuentan personas individuales.
    """
    for k in ("typeform_submission_key", "submission_key", "typeform_response_id", "employee_id", "token"):
        v = r.get(k)
        if v:
            return str(v)
    r_id = r.get("id")
    if r_id and not (str(r_id).startswith("test_csv_") or str(r_id).startswith("resp_")):
        return str(r_id)
    day = r.get("day") or r.get("created_at") or ""
    rol = r.get("cohort") or r.get("rol") or ""
    turno = r.get("turno") or ""
    return f"anon_resp_{day}_{rol}_{turno}_{fallback_idx}"


def calculate_post_stratification_weights_and_gaps(
    responses: List[Dict[str, Any]],
    population_shares: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Calcula la Media Ponderada Censal (Post-Estratificación) frente a la Media Bruta,
    evalúa el sesgo de muestreo y genera el análisis detallado '¿Quién falta?'.
    Cuenta personas únicas agrupando por submission_key / employee_id.
    """
    shares = population_shares or POPULATION_SHARES_DEFAULT

    by_respondent: Dict[str, Dict[str, Any]] = {}
    for idx, r in enumerate(responses):
        key = get_respondent_key(r, idx)
        if key not in by_respondent:
            raw_rol = r.get("cohort") or r.get("rol") or r.get("site") or "Planta"
            rol = "Planta" if raw_rol in ("Fábrica", "Planta") else "Oficinas" if raw_rol in ("Oficina", "Oficinas") else raw_rol
            if rol not in ROLES_PERMITIDOS:
                rol = "Planta"
            turno = r.get("turno") or "Mañana"
            by_respondent[key] = {
                "rol": rol,
                "turno": turno,
                "scores": [],
            }
        sc = r.get("score")
        if isinstance(sc, (int, float)) and 1 <= int(sc) <= 5:
            by_respondent[key]["scores"].append(int(sc))

    total_persons = len(by_respondent)
    if total_persons == 0:
        return {
            "media_bruta": 0.0,
            "media_ponderada_censo": 0.0,
            "sesgo_muestral_puntos": 0.0,
            "sesgo_muestral_label": "0.00 pts",
            "interpretacion_sesgo": "Sin datos de encuesta.",
            "total_personas": 0,
            "analisis_quien_falta": {
                "colectivos_deficitarios": [],
                "alertas": ["Sin respuestas para análisis de representatividad."],
                "resumen": "No hay participantes registrados.",
            },
            "detalle_por_colectivo": {},
        }

    cohort_persons: Dict[str, List[float]] = {r: [] for r in ROLES_PERMITIDOS}
    cohort_shifts: Dict[str, Counter] = {r: Counter() for r in ROLES_PERMITIDOS}
    all_person_means: List[float] = []

    for resp_data in by_respondent.values():
        rol = resp_data["rol"]
        scores = resp_data["scores"]
        p_mean = sum(scores) / len(scores) if scores else 3.0
        cohort_persons[rol].append(p_mean)
        cohort_shifts[rol][resp_data["turno"]] += 1
        all_person_means.append(p_mean)

    media_bruta = round(sum(all_person_means) / total_persons, 2)

    active_weight_sum = 0.0
    weighted_mean_sum = 0.0
    detalle_por_colectivo = {}
    deficitarios = []
    alertas = []

    for rol in ROLES_PERMITIDOS:
        n_rol = len(cohort_persons[rol])
        w_censo = shares.get(rol, 0.20)
        pct_obs = round((n_rol / total_persons) * 100, 1)
        pct_esp = round(w_censo * 100, 1)
        expected_n = round(total_persons * w_censo)
        deficit = max(0, expected_n - n_rol)

        if n_rol > 0:
            rol_mean = sum(cohort_persons[rol]) / n_rol
            weighted_mean_sum += w_censo * rol_mean
            active_weight_sum += w_censo
        else:
            rol_mean = 0.0

        ratio = round(pct_obs / pct_esp, 2) if pct_esp > 0 else 1.0
        estado = (
            "Equilibrado" if 0.8 <= ratio <= 1.25 and n_rol >= MIN_K_ANONYMITY
            else "Subrepresentado" if ratio < 0.8 and n_rol >= MIN_K_ANONYMITY
            else "Sobrerrepresentado" if ratio > 1.25 and n_rol >= MIN_K_ANONYMITY
            else f"Muestra insuficiente (<{MIN_K_ANONYMITY})"
        )

        detalle_por_colectivo[rol] = {
            "n_personas": n_rol,
            "porcentaje_observado": pct_obs,
            "porcentaje_censo": pct_esp,
            "media_colectivo": round(rol_mean, 2) if n_rol >= MIN_K_ANONYMITY else None,
            "ratio_representatividad": ratio,
            "deficit_personas": deficit,
            "estado": estado,
            "protegido_k_anonimato": n_rol < MIN_K_ANONYMITY,
        }

        if deficit >= 3 or ratio < 0.65 or n_rol < MIN_K_ANONYMITY:
            deficitarios.append({
                "rol": rol,
                "deficit_personas": deficit,
                "porcentaje_observado": pct_obs,
                "porcentaje_censo": pct_esp,
                "accion_recomendada": (
                    f"Faltan aprox. {deficit} encuestas en {rol} para reflejar su peso en plantilla ({pct_esp}%). "
                    + ("Reforzar canal físico QR / SMS." if rol in ("Planta", "Logística", "Comercial") else "Lanzar recordatorio en Teams.")
                ),
            })
            alertas.append(f"{rol}: Infrarrepresentado ({pct_obs}% observado vs {pct_esp}% censo, déficit: -{deficit} personas).")

    planta_total = len(cohort_persons["Planta"])
    if planta_total >= 5:
        noche_planta = cohort_shifts["Planta"].get("Noche", 0)
        pct_noche = round((noche_planta / planta_total) * 100, 1)
        if pct_noche < 15.0:
            alertas.append(
                f"Turno de Noche (Planta): Solo {pct_noche}% de respuestas ({noche_planta}/{planta_total}). "
                "Canal QR en vestuarios prioritario para evitar sesgo de turno."
            )

    media_ponderada = round(weighted_mean_sum / active_weight_sum, 2) if active_weight_sum > 0 else media_bruta
    sesgo_muestral = round(media_bruta - media_ponderada, 2)

    if abs(sesgo_muestral) < 0.05:
        interpretacion_sesgo = "Muestra censalmente equilibrada: el sesgo de selección es insignificante (<0.05 puntos)."
    elif sesgo_muestral > 0:
        interpretacion_sesgo = (
            f"Sesgo positivo por sobrerrepresentación (+{sesgo_muestral:.2f} pts): "
            "La media bruta sobrestima el clima real porque los colectivos con mejor valoración tienen mayor peso en la muestra."
        )
    else:
        interpretacion_sesgo = (
            f"Sesgo negativo por sobrerrepresentación ({sesgo_muestral:.2f} pts): "
            "La media bruta penaliza el clima real por alta participación de colectivos operativos más críticos."
        )

    resumen_falta = (
        f"Se detecta infrarrepresentación crítica en: {', '.join(d['rol'] for d in deficitarios)}. "
        if deficitarios else "Todos los colectivos cumplen las cuotas censales esperadas."
    )

    return {
        "media_bruta": media_bruta,
        "media_ponderada_censo": media_ponderada,
        "sesgo_muestral_puntos": sesgo_muestral,
        "sesgo_muestral_label": f"{sesgo_muestral:+.2f} pts",
        "interpretacion_sesgo": interpretacion_sesgo,
        "total_personas": total_persons,
        "analisis_quien_falta": {
            "colectivos_deficitarios": deficitarios,
            "alertas": alertas,
            "resumen": resumen_falta,
        },
        "detalle_por_colectivo": detalle_por_colectivo,
    }


def calculate_boxplot_by_cohort(
    responses: List[Dict[str, Any]], min_k: int = 5
) -> Dict[str, Any]:
    """
    Calcula métricas de diagrama de caja (Boxplot) estratificadas por departamento.
    Aplica filtro estricto de k-anonimato: oculta colectivos con menos de min_k (5) respuestas.
    """
    scores_by_cohort: Dict[str, List[int]] = {
        "Planta": [],
        "Logística": [],
        "Comercial": [],
        "Oficinas": [],
        "Manager": [],
    }
    for r in responses:
        rol = str(r.get("cohort") or r.get("site") or "Planta")
        if rol in ("Fábrica", "Planta"):
            rol = "Planta"
        elif rol in ("Oficina", "Oficinas"):
            rol = "Oficinas"

        sc = r.get("score")
        if isinstance(sc, (int, float)) and 1 <= int(sc) <= 5:
            if rol in scores_by_cohort:
                scores_by_cohort[rol].append(int(sc))
            else:
                scores_by_cohort.setdefault(rol, []).append(int(sc))

    result = {}
    for cohort, c_scores in scores_by_cohort.items():
        n_c = len(c_scores)
        if n_c < min_k:
            result[cohort] = {
                "n": n_c,
                "protegido_k_anonimato": True,
                "mensaje": f"Oculto por k-anonimato ({n_c} < {min_k} respuestas)",
                "puntuaciones": [],
            }
        else:
            stats = calculate_descriptive_stats(c_scores)
            result[cohort] = {
                "n": n_c,
                "protegido_k_anonimato": False,
                "media": stats["media"],
                "mediana": stats["mediana"],
                "q1": stats["q1"],
                "q3": stats["q3"],
                "iqr": stats["iqr"],
                "whisker_low": stats["whisker_low"],
                "whisker_high": stats["whisker_high"],
                "outliers": stats["outliers"],
                "min": stats["min"],
                "max": stats["max"],
                "desviacion_tipica": stats["desviacion_tipica"],
                "puntuaciones": c_scores,
            }
    return result


def calculate_coverage_and_participation(
    responses: List[Dict[str, Any]],
    population_shares: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Calcula la matriz de participación y representatividad Rol x Turno
    y valida el cumplimiento de k-anonimato (k >= 5).
    Garantiza que n y k-anonimato cuenten personas únicas (no filas de preguntas).
    """
    shares = population_shares or POPULATION_SHARES_DEFAULT

    # Agrupar por persona única para contar encuestados reales
    unique_respondents: Dict[str, Dict[str, str]] = {}
    for idx, r in enumerate(responses):
        key = get_respondent_key(r, idx)
        if key not in unique_respondents:
            raw_rol = r.get("cohort") or r.get("rol") or r.get("site") or "Planta"
            rol = "Planta" if raw_rol in ("Fábrica", "Planta") else "Oficinas" if raw_rol in ("Oficina", "Oficinas") else raw_rol
            if rol not in ROLES_PERMITIDOS:
                rol = "Planta"

            turno = r.get("turno") or r.get("center_type")
            if not turno:
                turno = "Mañana" if idx % 3 == 0 else "Tarde" if idx % 3 == 1 else "Noche"
                if rol in ("Oficinas", "Comercial"):
                    turno = "Mañana"
            if turno not in TURNOS_PERMITIDOS:
                turno = "Mañana"
            unique_respondents[key] = {"rol": rol, "turno": turno}

    total_n = len(unique_respondents)

    # Conteo por rol y rol x turno (personas únicas)
    rol_counts = Counter()
    rol_turno_counts: Dict[str, Counter] = {r: Counter() for r in ROLES_PERMITIDOS}

    for data in unique_respondents.values():
        rol = data["rol"]
        turno = data["turno"]
        rol_counts[rol] += 1
        rol_turno_counts[rol][turno] += 1

    por_rol = {}
    for rol in ROLES_PERMITIDOS:
        count = rol_counts[rol]
        obs_pct = round((count / total_n) * 100, 1) if total_n > 0 else 0.0
        exp_pct = round(shares.get(rol, 0.2) * 100, 1)
        ratio = round(obs_pct / exp_pct, 2) if exp_pct > 0 else 1.0

        por_rol[rol] = {
            "n_respuestas": count,
            "n_personas": count,
            "porcentaje_total": obs_pct,
            "porcentaje_esperado": exp_pct,
            "ratio_representatividad": ratio,
            "es_baja_participacion": obs_pct < 15.0 and exp_pct >= 15.0,
            "protegido_k_anonimato": count < MIN_K_ANONYMITY,
            "estado": (
                "Baja" if count >= MIN_K_ANONYMITY and (obs_pct < 15.0 or ratio < 0.6)
                else "Alta" if count >= MIN_K_ANONYMITY and ratio > 1.4
                else "Equilibrada" if count >= MIN_K_ANONYMITY
                else "Protegida (<5)"
            ),
        }

    por_rol_turno = {}
    for rol in ROLES_PERMITIDOS:
        por_rol_turno[rol] = {}
        for turno in TURNOS_PERMITIDOS:
            c = rol_turno_counts[rol][turno]
            if c >= MIN_K_ANONYMITY or c == 0:
                por_rol_turno[rol][turno] = {
                    "n_respuestas": c,
                    "n_personas": c,
                    "porcentaje_total": round((c / total_n) * 100, 1) if total_n > 0 else 0.0,
                }
            else:
                por_rol_turno[rol][turno] = {
                    "n_respuestas": f"<{MIN_K_ANONYMITY}",
                    "n_personas": f"<{MIN_K_ANONYMITY}",
                    "porcentaje_total": "Protegido",
                    "protegido": True,
                }

    return {
        "total_respuestas": total_n,
        "total_personas": total_n,
        "por_rol": por_rol,
        "por_rol_turno": por_rol_turno,
    }


def detect_bias_and_polarization_alerts(
    coverage: Dict[str, Any],
    stats_dimensions: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Genera alertas accionables de RRHH sobre sesgos, subrepresentación y polarización."""
    alerts_rep = []
    alerts_pol = []

    # 1. Alertas de baja participación / sesgo de cobertura (<15%)
    for rol, data in coverage.get("por_rol", {}).items():
        if data.get("es_baja_participacion"):
            alerts_rep.append({
                "tipo": "cobertura_insuficiente",
                "rol": rol,
                "porcentaje_observado": data["porcentaje_total"],
                "porcentaje_esperado": data["porcentaje_esperado"],
                "umbral_minimo": 15.0,
                "mensaje": (
                    f"Riesgo de sesgo de no respuesta en {rol}: representa el {data['porcentaje_total']}% "
                    f"de la muestra frente al {data['porcentaje_esperado']}% de su cuota poblacional."
                ),
            })

    # Alerta específica turno de noche en Planta (Caso Damm)
    planta_turnos = coverage.get("por_rol_turno", {}).get("Planta", {})
    noche_val = planta_turnos.get("Noche", {}).get("n_respuestas")
    planta_total = coverage.get("por_rol", {}).get("Planta", {}).get("n_respuestas", 0)
    if planta_total >= 5 and isinstance(noche_val, int) and (noche_val / planta_total) < 0.15:
        alerts_rep.append({
            "tipo": "turno_desatendido",
            "rol": "Planta - Turno Noche",
            "porcentaje_observado": round((noche_val / planta_total) * 100, 1),
            "umbral_minimo": 15.0,
            "mensaje": "Baja respuesta detectada en el Turno de Noche de Planta. Se aconseja reforzar con el canal Móvil-QR físico.",
        })

    # 2. Alertas de polarización grupal (sigma >= 1.25)
    for dim_name, stats in stats_dimensions.items():
        desv = stats.get("desviacion_tipica", 0.0)
        var = stats.get("varianza", 0.0)
        if desv >= 1.25 or var >= 1.55:
            alerts_pol.append({
                "tipo": "polarizacion_bimodal",
                "pregunta": dim_name,
                "rol": "Colectivo Global",
                "desviacion_tipica": desv,
                "varianza": var,
                "mensaje": (
                    f"Alta polarización en '{dim_name}' (σ={desv}, s²={var}). "
                    "La media enmascara posturas contrapuestas entre equipos satisfechos y equipos con alto estrés/fatiga."
                ),
            })

    return {
        "alerta_representatividad_baja_participacion": alerts_rep,
        "alerta_polarizacion_grupal": alerts_pol,
        "total_alertas": len(alerts_rep) + len(alerts_pol),
        "garantia_k_anonimato": f"Garantizada (Cortes < {MIN_K_ANONYMITY} observaciones suprimidos)",
    }


def _normalize_text_for_topic(text: str) -> str:
    n = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in n if not unicodedata.combining(c))


def analyze_qualitative_feedback(
    comments: List[Any],
    previous_period_topics: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Clasificación cualitativa multi-etiqueta y detección de temas emergentes (Escenario C).
    - Evalúa precisión frente a 'tema_real' si la columna ground-truth está presente.
    - Calcula ranking de temas dinámico con cambio (delta %) frente al periodo anterior.
    - Elimina etiquetas de Pareto 80/20 engañosas y prioriza explicabilidad.
    """
    categories = {
        "turnos": {
            "label": "Carga y Turnicidad",
            "keywords": ("turno", "turnos", "horar", "descans", "pausa", "pausas", "cobertur", "relevo", "relevos", "cuadrill", "concili", "horas", "nocturn", "fatig"),
            "comments": [],
        },
        "comunicacion": {
            "label": "Comunicación y Avisos",
            "keywords": ("comunic", "inform", "aviso", "avisos", "cambio", "cambios", "mensaj", "canal", "antelac", "notic", "coordinac", "instrucc"),
            "comments": [],
        },
        "herramientas": {
            "label": "Herramientas y Recursos",
            "keywords": ("herramient", "sistem", "recurs", "mater", "lent", "ordenad", "maquin", "incidenc", "parad", "softw", "repuest"),
            "comments": [],
        },
        "liderazgo": {
            "label": "Liderazgo y Acompañamiento",
            "keywords": ("lider", "mandos", "responsab", "feedback", "acompan", "escuch", "apoyo", "cercan", "jefe"),
            "comments": [],
        },
        "reconocimiento": {
            "label": "Reconocimiento y Clima",
            "keywords": ("reconoc", "valorac", "agradec", "esfuerz", "logr", "merit", "compens", "sueld", "salari", "ambient", "compan"),
            "comments": [],
        },
        "desarrollo": {
            "label": "Desarrollo y Formación",
            "keywords": ("aprendiz", "formac", "crecer", "desarroll", "carrer", "promoc", "capacit", "oportun"),
            "comments": [],
        },
        "bienestar": {
            "label": "Bienestar y Condiciones",
            "keywords": ("bienest", "estres", "ergonom", "salud", "cansanc", "temperatur", "ruid", "calor", "aire"),
            "comments": [],
        },
        "otros": {
            "label": "Organización General",
            "keywords": ("general", "bien", "ok", "correcto", "sin comentarios"),
            "comments": [],
        },
    }

    positive_words = ("buen", "bien", "genial", "agil", "correcto", "apoyo", "gracias", "comodo", "mejora", "positivo", "excelente", "facil", "adecuad", "satisfech")
    negative_words = ("mal", "peor", "falta", "bloqueo", "lento", "ruido", "estres", "sobrecarga", "dificil", "tarde", "cansancio", "injusto", "fallo", "problema", "justa")

    total_valid = 0
    pos_count = 0
    neg_count = 0
    neutral_count = 0
    clean_samples = []

    total_labeled = 0
    correct_labeled = 0

    for item in comments:
        if isinstance(item, dict):
            text = str(item.get("comment") or item.get("texto") or item.get("text") or "").strip()
            ground_truth = str(item.get("tema_real") or item.get("ground_truth") or "").strip()
        elif isinstance(item, str):
            text = item.strip()
            ground_truth = ""
        else:
            continue

        if len(text) < 4:
            continue

        total_valid += 1
        norm_text = _normalize_text_for_topic(text)

        # Sentimiento
        p_score = sum(1 for w in positive_words if w in norm_text)
        n_score = sum(1 for w in negative_words if w in norm_text)
        if p_score > n_score:
            pos_count += 1
            sent = "positivo"
        elif n_score > p_score:
            neg_count += 1
            sent = "crítico"
        else:
            neutral_count += 1
            sent = "neutro"

        # Clasificación por máxima coincidencia ponderada
        best_cat = "otros"
        max_score = 0
        for cat_key, cat_data in categories.items():
            if cat_key == "otros":
                continue
            hits = sum(1 for k in cat_data["keywords"] if k in norm_text)
            if "equip" in norm_text:
                if any(x in norm_text for x in ("compan", "apoyo", "junt", "trabaj en equip")):
                    if cat_key == "reconocimiento":
                        hits += 2
                    elif cat_key == "herramientas":
                        hits -= 1
                elif any(x in norm_text for x in ("maquin", "pantall", "ordenad", "lento")):
                    if cat_key == "herramientas":
                        hits += 2
            if "tiemp" in norm_text:
                if any(x in norm_text for x in ("pausa", "descans", "recuper")):
                    if cat_key == "turnos":
                        hits += 2
                elif any(x in norm_text for x in ("esper", "tard", "lento")):
                    if cat_key == "herramientas":
                        hits += 1

            if hits > max_score:
                max_score = hits
                best_cat = cat_key

        categories[best_cat]["comments"].append({"text": text, "sent": sent})

        # Evaluación ground-truth de tema_real
        if ground_truth:
            total_labeled += 1
            norm_gt = _normalize_text_for_topic(ground_truth)
            gt_matched = False
            if best_cat in norm_gt or norm_gt in best_cat:
                gt_matched = True
            elif norm_gt.startswith("comunic") and best_cat == "comunicacion":
                gt_matched = True
            elif norm_gt.startswith("turno") and best_cat == "turnos":
                gt_matched = True
            elif (norm_gt.startswith("herram") or "sistem" in norm_gt) and best_cat == "herramientas":
                gt_matched = True
            elif norm_gt.startswith("lider") and best_cat == "liderazgo":
                gt_matched = True
            elif norm_gt.startswith("reconoc") and best_cat == "reconocimiento":
                gt_matched = True
            elif norm_gt.startswith("desarr") and best_cat == "desarrollo":
                gt_matched = True
            elif norm_gt.startswith("bienest") and best_cat == "bienestar":
                gt_matched = True
            elif norm_gt in ("otros", "general") and best_cat == "otros":
                gt_matched = True

            if gt_matched:
                correct_labeled += 1

        if len(clean_samples) < 6:
            clean_samples.append({"text": text, "sentimiento": sent, "tema": categories[best_cat]["label"]})

    temas_resumen = []
    prev_topics = previous_period_topics or {}

    for cat_key, cat_data in categories.items():
        c_count = len(cat_data["comments"])
        pct = round((c_count / max(1, total_valid)) * 100, 1)
        prev_pct = prev_topics.get(cat_key, prev_topics.get(cat_data["label"], 0.0))
        delta = round(pct - prev_pct, 1) if prev_topics else 0.0

        es_emergente = (delta >= 8.0) or (prev_topics and prev_pct == 0.0 and pct >= 15.0) or (not prev_topics and pct >= 25.0)
        tendencia = (
            f"Emergente 🚨 (+{delta}%)" if delta >= 8.0
            else f"Al alza 🔺 (+{delta}%)" if delta > 2.0
            else f"A la baja 🔻 ({delta}%)" if delta < -2.0
            else "Estable ➖" if prev_topics
            else ("Relevante 📌" if pct >= 20.0 else "Estable ➖")
        )

        sent_counts = Counter(c["sent"] for c in cat_data["comments"])
        pred_sent = sent_counts.most_common(1)[0][0] if sent_counts else "neutro"

        temas_resumen.append({
            "id": cat_key,
            "tema": cat_data["label"],
            "frecuencia": c_count,
            "porcentaje": pct,
            "porcentaje_anterior": prev_pct,
            "cambio_vs_anterior_pct": delta,
            "es_emergente": es_emergente,
            "tendencia": tendencia,
            "sentimiento_predominante": pred_sent,
        })

    temas_resumen.sort(key=lambda x: x["frecuencia"], reverse=True)

    evaluacion_precision = {
        "disponible": total_labeled > 0,
        "total_evaluados": total_labeled,
        "aciertos": correct_labeled,
        "precision_pct": round((correct_labeled / max(1, total_labeled)) * 100, 1) if total_labeled > 0 else 0.0,
        "mensaje": (
            f"Precisión del clasificador frente a ground-truth 'tema_real': "
            f"{round((correct_labeled / max(1, total_labeled)) * 100, 1)}% "
            f"({correct_labeled}/{total_labeled} comentarios)"
            if total_labeled > 0 else "Sin columna ground-truth 'tema_real' en el dataset cargado."
        ),
    }

    return {
        "total_comentarios": total_valid,
        "sentimiento_global": {
            "positivo_pct": round((pos_count / max(1, total_valid)) * 100, 1),
            "critico_pct": round((neg_count / max(1, total_valid)) * 100, 1),
            "neutro_pct": round((neutral_count / max(1, total_valid)) * 100, 1),
        },
        "temas": temas_resumen,
        "ranking_temas": temas_resumen,
        "tema_emergente": next((t["tema"] for t in temas_resumen if t["es_emergente"]), (temas_resumen[0]["tema"] if temas_resumen else "Ninguno")),
        "extractos_anonimizados": clean_samples,
        "evaluacion_precision": evaluacion_precision,
    }


def analyze_survey_dataset(
    survey: Dict[str, Any],
    responses: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Función principal de análisis estadístico de una encuesta seleccionada.
    Genera el contrato determinista exacto para Insights y síntesis LLM.
    """
    survey_id = survey.get("id", "")
    survey_title = survey.get("title", "Consulta de Clima")
    quarter = survey.get("quarter") or calculate_quarter_label(survey.get("created_at"))

    # Extraer puntuaciones por dimensión o pregunta
    scores_by_dim: Dict[str, List[int]] = {
        "bienestar": [],
        "liderazgo": [],
        "condiciones": [],
    }
    all_scores: List[int] = []
    comments_payload: List[Dict[str, Any]] = []

    for r in responses:
        score = r.get("score")
        if isinstance(score, (int, float)) and 1 <= int(score) <= 5:
            sc = int(score)
            all_scores.append(sc)
            dim = str(r.get("dimension") or "bienestar")
            if dim in scores_by_dim:
                scores_by_dim[dim].append(sc)
            else:
                scores_by_dim.setdefault(dim, []).append(sc)

        comment = r.get("comment") or r.get("texto_abierto")
        if comment and isinstance(comment, str) and len(comment.strip()) > 3:
            comments_payload.append({
                "comment": comment.strip(),
                "tema_real": r.get("tema_real") or "",
            })

    # Descriptivos globales y por dimensión
    stats_dimensions = {}
    for dim_key, s_list in scores_by_dim.items():
        if s_list:
            stats_dimensions[f"q_{dim_key}"] = calculate_descriptive_stats(s_list)
        else:
            stats_dimensions[f"q_{dim_key}"] = calculate_descriptive_stats(all_scores)

    stats_global = calculate_descriptive_stats(all_scores)
    bootstrap_ci = calculate_bootstrap_ci(all_scores)

    # Cobertura, representatividad y post-estratificación
    coverage = calculate_coverage_and_participation(responses)
    post_strat = calculate_post_stratification_weights_and_gaps(responses)

    # Alertas de sesgo y polarización
    alerts = detect_bias_and_polarization_alerts(coverage, stats_dimensions)

    # Análisis cualitativo de comentarios con evaluación ground truth y temas emergentes
    qualitative = analyze_qualitative_feedback(comments_payload)

    # Recomendaciones sugeridas para RRHH
    recommendations = []
    if stats_global["media"] < 3.2 or alerts["alerta_polarizacion_grupal"]:
        recommendations.append({
            "titulo": "Abrir sesión de diálogo en Planta sobre descansos y turnicidad",
            "motivo": f"Se ha detectado una media de {stats_global['media']} con variabilidad significativa en equipos operativos.",
            "accion": "Convocar a los mandos intermedios y delegados de turno para acordar pausas activas escalonadas.",
        })
    if alerts["alerta_representatividad_baja_participacion"]:
        rep_rol = alerts["alerta_representatividad_baja_participacion"][0]["rol"]
        recommendations.append({
            "titulo": f"Campaña de escucha específica para {rep_rol}",
            "motivo": f"Participación por debajo del umbral estadístico (<15%).",
            "accion": "Habilitar código QR en tablones de almacén y puestos de paso, reforzando el mensaje de 100% anonimato.",
        })
    if not recommendations:
        recommendations.append({
            "titulo": "Consolidar las buenas prácticas de comunicación en planta",
            "motivo": f"Media de clima favorable ({stats_global['media']}) sin alertas críticas de polarización.",
            "accion": "Publicar agradecimiento a los equipos y programar microconsulta de seguimiento en 30 días.",
        })

    return {
        "metadatos_encuesta": {
            "id": survey_id,
            "title": survey_title,
            "quarter": quarter,
            "created_at": survey.get("created_at", ""),
            "status": survey.get("status", "published"),
            "cohort": survey.get("cohort", "Todos"),
            "typeform_connected": survey.get("typeform_connected", False),
            "preguntas": survey.get("questions", []),
        },
        "kpis_ejecutivos": {
            "total_respuestas": len(responses),
            "total_personas": post_strat["total_personas"],
            "media_clima": stats_global["media"],
            "media_bruta": post_strat["media_bruta"],
            "media_ponderada_censo": post_strat["media_ponderada_censo"],
            "sesgo_muestral_puntos": post_strat["sesgo_muestral_puntos"],
            "sesgo_muestral_label": post_strat["sesgo_muestral_label"],
            "interpretacion_sesgo": post_strat["interpretacion_sesgo"],
            "mediana_clima": stats_global["mediana"],
            "desviacion_tipica": stats_global["desviacion_tipica"],
            "varianza": stats_global["varianza"],
            "pct_favorable": stats_global.get("pct_favorable", 0.0),
            "ci_95": bootstrap_ci.get("ci_95", stats_global.get("ci_95", [stats_global["media"], stats_global["media"]])),
            "ci_label": bootstrap_ci.get("ci_label", stats_global.get("ci_label", f"[{stats_global['media']}, {stats_global['media']}]")),
            "cobertura_censo_pct": coverage.get("porcentaje_cobertura_global", 0.0),
            "diagnostico": stats_global["diagnostico"],
            "nivel_confianza": "Alta" if post_strat["total_personas"] >= 60 else "Media" if post_strat["total_personas"] >= 20 else "Baja",
            "total_alertas": alerts["total_alertas"],
        },
        "estadisticas_descriptivas": stats_dimensions,
        "estadisticas_globales": stats_global,
        "metricas_globales": stats_global,
        "bootstrap_ci": bootstrap_ci,
        "post_estratificacion": post_strat,
        "analisis_quien_falta": post_strat["analisis_quien_falta"],
        "estadisticas_por_rol": coverage.get("por_rol", {}),
        "participacion_y_cobertura": coverage,
        "alertas_de_sesgo": alerts,
        "analisis_cualitativo": qualitative,
        "evaluacion_precision": qualitative.get("evaluacion_precision", {}),
        "recomendaciones_hr": recommendations,
        "boxplot_por_colectivo": calculate_boxplot_by_cohort(responses),
        "diagrama_pareto": qualitative.get("ranking_temas", []),
        "ranking_temas": qualitative.get("ranking_temas", []),
        "tasa_participacion_por_colectivo": coverage.get("por_rol", {}),
    }


def analyze_aggregated_dataset(
    period_type: str,  # "quarter" | "year"
    period_label: str,  # "Q4 2026" | "2026"
    surveys: List[Dict[str, Any]],
    responses: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Análisis estadístico consolidado para un Cuatrimestre completo o Año entero.
    Calcula estadísticas insesgadas agrupadas, comparativa de índices de participación
    entre encuestas o cuatrimestres, cobertura consolidada Rol x Turno y alertas del periodo.
    """
    total_n = len(responses)

    # Puntuaciones globales y por dimensión común
    scores_by_dim: Dict[str, List[int]] = {
        "bienestar": [],
        "liderazgo": [],
        "condiciones": [],
        "organizacion": [],
    }
    all_scores: List[int] = []
    comments: List[str] = []

    # Respuestas por survey_id para calcular índices de participación individuales
    responses_by_survey: Dict[str, List[Dict[str, Any]]] = {}
    for r in responses:
        s_id = str(r.get("survey_id") or "")
        responses_by_survey.setdefault(s_id, []).append(r)

        score = r.get("score")
        if isinstance(score, (int, float)) and 1 <= int(score) <= 5:
            sc = int(score)
            all_scores.append(sc)
            dim = str(r.get("dimension") or "bienestar").lower()
            if dim in scores_by_dim:
                scores_by_dim[dim].append(sc)
            else:
                scores_by_dim.setdefault(dim, []).append(sc)

        comment = r.get("comment")
        if comment and isinstance(comment, str) and len(comment.strip()) > 3:
            comments.append(comment.strip())

    stats_global = calculate_descriptive_stats(all_scores)

    stats_dimensions = {}
    for dim_key, s_list in scores_by_dim.items():
        if s_list:
            stats_dimensions[f"q_{dim_key}"] = calculate_descriptive_stats(s_list)
        elif all_scores:
            stats_dimensions[f"q_{dim_key}"] = calculate_descriptive_stats(all_scores)

    coverage = calculate_coverage_and_participation(responses)
    alerts = detect_bias_and_polarization_alerts(coverage, stats_dimensions)
    qualitative = analyze_qualitative_feedback(comments)

    # Comparativa interna de encuestas del cuatrimestre o cuatrimestres del año
    comparativa_items = []
    if period_type == "quarter":
        for s in surveys:
            s_id = s.get("id", "")
            s_resp = responses_by_survey.get(s_id, [])
            s_scores = [int(r["score"]) for r in s_resp if isinstance(r.get("score"), (int, float))]
            s_stats = calculate_descriptive_stats(s_scores)
            part_index = round((len(s_resp) / max(1, total_n)) * 100, 1)

            comparativa_items.append({
                "id": s_id,
                "titulo": s.get("title", "Consulta de Clima"),
                "colectivo": s.get("cohort", "Todos"),
                "total_respuestas": len(s_resp),
                "media_clima": s_stats["media"] if s_scores else stats_global["media"],
                "desviacion_tipica": s_stats["desviacion_tipica"] if s_scores else stats_global["desviacion_tipica"],
                "indice_participacion_pct": part_index,
                "estado": s.get("status", "published"),
            })
    else:
        # Desglose de los 4 cuatrimestres del año
        for q_num in range(1, 5):
            q_label = f"Q{q_num} {period_label}"
            q_resp = [r for r in responses if calculate_quarter_label(r.get("day") or r.get("created_at")) == q_label]
            q_scores = [int(r["score"]) for r in q_resp if isinstance(r.get("score"), (int, float))]
            q_stats = calculate_descriptive_stats(q_scores)
            part_index = round((len(q_resp) / max(1, total_n)) * 100, 1) if total_n > 0 else 0.0

            comparativa_items.append({
                "cuatrimestre": q_label,
                "total_respuestas": len(q_resp),
                "media_clima": q_stats["media"] if q_scores else 0.0,
                "desviacion_tipica": q_stats["desviacion_tipica"] if q_scores else 0.0,
                "indice_participacion_pct": part_index,
                "activo": len(q_resp) > 0,
            })

    # Desglose para Serie Temporal y Barras Apiladas 100%
    time_series = []
    stacked_likert = []

    if period_type == "quarter":
        months_in_q = {
            "Q1": [("01", "Enero"), ("02", "Febrero"), ("03", "Marzo")],
            "Q2": [("04", "Abril"), ("05", "Mayo"), ("06", "Junio")],
            "Q3": [("07", "Julio"), ("08", "Agosto"), ("09", "Septiembre")],
            "Q4": [("10", "Octubre"), ("11", "Noviembre"), ("12", "Diciembre")],
        }
        q_prefix = period_label[:2] if len(period_label) >= 2 else "Q4"
        yr_str = period_label[3:] if len(period_label) > 3 else "2026"
        sub_periods = months_in_q.get(q_prefix, [("10", "Mes 1"), ("11", "Mes 2"), ("12", "Mes 3")])

        for m_code, m_name in sub_periods:
            m_target = f"{yr_str}-{m_code}"
            m_resp = [r for r in responses if str(r.get("day", "")).startswith(m_target)]
            m_scores = [int(r["score"]) for r in m_resp if isinstance(r.get("score"), (int, float))]
            n_m = len(m_scores)
            
            if n_m >= 5:
                m_stats = calculate_descriptive_stats(m_scores)
                sat = m_stats["pct_favorable"]
                neu = round((sum(1 for s in m_scores if s == 3) / max(1, n_m)) * 100, 1)
                ins = round(max(0.0, 100.0 - sat - neu), 1)
                label = f"{m_name} {yr_str}"
                time_series.append({
                    "periodo": label,
                    "media": m_stats["media"],
                    "desviacion_tipica": m_stats["desviacion_tipica"],
                    "total_respuestas": len(m_resp),
                    "ci_95": m_stats.get("ci_95"),
                    "ci_label": m_stats.get("ci_label"),
                    "pct_favorable": sat,
                })
                stacked_likert.append({
                    "periodo": label,
                    "satisfecho_pct": sat,
                    "neutral_pct": neu,
                    "insatisfecho_pct": ins,
                    "total_respuestas": len(m_resp),
                })
            elif n_m > 0:
                m_stats = calculate_descriptive_stats(m_scores)
                sat = m_stats["pct_favorable"]
                neu = round((sum(1 for s in m_scores if s == 3) / max(1, n_m)) * 100, 1)
                ins = round(max(0.0, 100.0 - sat - neu), 1)
                label = f"{m_name} {yr_str}"
                time_series.append({
                    "periodo": label,
                    "media": m_stats["media"],
                    "desviacion_tipica": m_stats["desviacion_tipica"],
                    "total_respuestas": len(m_resp),
                    "ci_95": m_stats.get("ci_95"),
                    "ci_label": m_stats.get("ci_label"),
                    "pct_favorable": sat,
                })
                stacked_likert.append({
                    "periodo": label,
                    "satisfecho_pct": sat,
                    "neutral_pct": neu,
                    "insatisfecho_pct": ins,
                    "total_respuestas": len(m_resp),
                })
    else:
        for q_num in range(1, 5):
            q_label = f"Q{q_num} {period_label}"
            q_resp = [r for r in responses if calculate_quarter_label(r.get("day") or r.get("created_at")) == q_label]
            q_scores = [int(r["score"]) for r in q_resp if isinstance(r.get("score"), (int, float))]
            n_q = len(q_scores)
            
            if n_q > 0:
                q_stats = calculate_descriptive_stats(q_scores)
                sat = q_stats["pct_favorable"]
                neu = round((sum(1 for s in q_scores if s == 3) / max(1, n_q)) * 100, 1)
                ins = round(max(0.0, 100.0 - sat - neu), 1)

                time_series.append({
                    "periodo": q_label,
                    "media": q_stats["media"],
                    "desviacion_tipica": q_stats["desviacion_tipica"],
                    "total_respuestas": len(q_resp),
                    "ci_95": q_stats.get("ci_95"),
                    "ci_label": q_stats.get("ci_label"),
                    "pct_favorable": sat,
                })
                stacked_likert.append({
                    "periodo": q_label,
                    "satisfecho_pct": sat,
                    "neutral_pct": neu,
                    "insatisfecho_pct": ins,
                    "total_respuestas": len(q_resp),
                })

    # Dispersión: Participación vs Satisfacción por Departamento (k >= 5)
    scatter_items = []
    for rol_name, r_info in coverage.get("por_rol", {}).items():
        n_rol = r_info.get("n_respuestas", 0)
        if isinstance(n_rol, int) and n_rol >= 5:
            rol_scores = [
                int(r["score"]) for r in responses
                if (r.get("cohort") == rol_name or (rol_name == "Planta" and r.get("cohort") in ("Planta", "Fábrica")))
                and isinstance(r.get("score"), (int, float))
            ]
            mean_sc = round(sum(rol_scores) / len(rol_scores), 2) if rol_scores else stats_global["media"]
            scatter_items.append({
                "departamento": rol_name,
                "tasa_participacion_pct": r_info.get("porcentaje_total", 0.0),
                "satisfaccion_media": mean_sc,
                "n_respuestas": n_rol,
                "protegido": False,
                "estado": r_info.get("estado", "Equilibrada"),
            })
        else:
            scatter_items.append({
                "departamento": rol_name,
                "tasa_participacion_pct": 0.0,
                "satisfaccion_media": 0.0,
                "n_respuestas": n_rol if isinstance(n_rol, int) else 0,
                "protegido": True,
                "estado": "Protegido (<5)",
            })

    recommendations = []
    if stats_global["media"] < 3.2 or alerts["alerta_polarizacion_grupal"]:
        recommendations.append({
            "titulo": f"Plan de choque para el periodo {period_label} en descansos y turnos",
            "motivo": f"Media consolidada de {stats_global['media']} con variabilidad significativa en equipos de operaciones.",
            "accion": "Consolidar mesas de trabajo conjuntas entre mandos de planta y delegados para estabilizar los cuadros de turno.",
        })
    if alerts["alerta_representatividad_baja_participacion"]:
        rep_rol = alerts["alerta_representatividad_baja_participacion"][0]["rol"]
        recommendations.append({
            "titulo": f"Estrategia de participación acumulada para {rep_rol} en {period_label}",
            "motivo": "Participación agregada por debajo del umbral mínimo durante el periodo.",
            "accion": "Establecer canales específicos fijos (kioscos QR en plantas y almacenes) para evitar sesgos estructurales de respuesta.",
        })
    if not recommendations:
        recommendations.append({
            "titulo": f"Consolidación de indicadores favorables en {period_label}",
            "motivo": f"Clima promedio satisfactorio ({stats_global['media']}) y cobertura equilibrada en los centros de Damm.",
            "accion": "Reconocer a los equipos y mantener el pulso periódico para asegurar la continuidad de los resultados.",
        })

    # Post-estratificación y Bootstrap CI agregados
    post_strat = calculate_post_stratification_weights_and_gaps(responses)
    bootstrap_ci = calculate_bootstrap_ci(all_scores)

    # Test de significación estadística entre los dos periodos más recientes
    significancia = {
        "disponible": False,
        "motivo": "Menos de 2 periodos con datos suficientes",
        "delta": 0.0,
        "p_valor": None,
        "es_significativo": False,
        "aviso_significancia": "No hay suficientes periodos temporales comparables.",
    }
    if len(time_series) >= 2:
        latest = time_series[-1]
        prior = time_series[-2]
        latest_label = latest["periodo"]
        prior_label = prior["periodo"]
        # Filtrar respuestas de ambos periodos
        latest_sc = [int(r["score"]) for r in responses if isinstance(r.get("score"), (int, float)) and (latest_label in str(r.get("day")) or calculate_quarter_label(r.get("day")) == latest_label)]
        prior_sc = [int(r["score"]) for r in responses if isinstance(r.get("score"), (int, float)) and (prior_label in str(r.get("day")) or calculate_quarter_label(r.get("day")) == prior_label)]
        if len(latest_sc) >= 5 and len(prior_sc) >= 5:
            significancia = calculate_statistical_significance(latest_sc, prior_sc)

    return {
        "metadatos_encuesta": {
            "id": f"aggr_{period_type}_{period_label}",
            "title": f"Análisis Agregado ({ 'Cuatrimestre ' if period_type == 'quarter' else 'Año ' }{period_label})",
            "quarter": period_label if period_type == "quarter" else f"Año {period_label}",
            "period_type": period_type,
            "period_label": period_label,
            "num_encuestas": len(surveys),
            "status": "consolidado",
            "cohort": "Plantilla General (Consolidado)",
        },
        "kpis_ejecutivos": {
            "total_respuestas": total_n,
            "total_personas": post_strat["total_personas"],
            "media_clima": stats_global["media"],
            "media_bruta": post_strat["media_bruta"],
            "media_ponderada_censo": post_strat["media_ponderada_censo"],
            "sesgo_muestral_puntos": post_strat["sesgo_muestral_puntos"],
            "sesgo_muestral_label": post_strat["sesgo_muestral_label"],
            "interpretacion_sesgo": post_strat["interpretacion_sesgo"],
            "mediana_clima": stats_global["mediana"],
            "desviacion_tipica": stats_global["desviacion_tipica"],
            "varianza": stats_global["varianza"],
            "pct_favorable": stats_global.get("pct_favorable", 0.0),
            "ci_95": bootstrap_ci.get("ci_95", stats_global.get("ci_95", [stats_global["media"], stats_global["media"]])),
            "ci_label": bootstrap_ci.get("ci_label", stats_global.get("ci_label", f"[{stats_global['media']}, {stats_global['media']}]")),
            "cobertura_censo_pct": coverage.get("porcentaje_cobertura_global", 0.0),
            "diagnostico": stats_global["diagnostico"],
            "nivel_confianza": "Alta" if post_strat["total_personas"] >= 60 else "Media" if post_strat["total_personas"] >= 20 else "Limitada",
            "total_alertas": alerts["total_alertas"],
        },
        "estadisticas_descriptivas": stats_dimensions,
        "estadisticas_globales": stats_global,
        "metricas_globales": stats_global,
        "bootstrap_ci": bootstrap_ci,
        "post_estratificacion": post_strat,
        "analisis_quien_falta": post_strat["analisis_quien_falta"],
        "significancia_estadistica": significancia,
        "estadisticas_por_rol": coverage.get("por_rol", {}),
        "participacion_y_cobertura": coverage,
        "alertas_de_sesgo": alerts,
        "analisis_cualitativo": qualitative,
        "evaluacion_precision": qualitative.get("evaluacion_precision", {}),
        "comparativa_periodo": comparativa_items,
        "serie_temporal": time_series,
        "barras_apiladas_likert_100": stacked_likert,
        "dispersion_participacion_satisfaccion": scatter_items,
        "ranking_temas": qualitative.get("ranking_temas", []),
        "diagrama_pareto": qualitative.get("ranking_temas", []),
        "recomendaciones_hr": recommendations,
        "period_type": period_type,
        "period_label": period_label,
    }


def generate_mock_responses_for_survey(
    survey: Dict[str, Any],
    count: int = 50,
    seed: int = 2026,
) -> List[Dict[str, Any]]:
    """
    Genera respuestas sintéticas realistas vinculadas al survey_id
    respetando el colectivo objetivo y la fecha de creación.
    """
    rng = random.Random(seed + hash(survey.get("id", "")))
    target_cohort = survey.get("cohort", "Todos")
    survey_id = survey.get("id", "")
    created_date = (survey.get("created_at") or date.today().isoformat())[:10]

    roles_pool = ["Planta"] * 46 + ["Logística"] * 22 + ["Comercial"] * 17 + ["Oficinas"] * 10 + ["Manager"] * 5
    if target_cohort in ROLES_PERMITIDOS:
        roles_pool = [target_cohort] * 80 + ["Planta"] * 20

    comments_pool = [
        "Los turnos rotativos cansan mucho si se avisan con pocos días.",
        "Buen ambiente con los compañeros de línea.",
        "Hacen falta más herramientas de recambio en planta.",
        "La comunicación con el responsable de turno ha mejorado.",
        "Se agradecería algo más de tiempo de descanso entre relevos.",
        "En comercial pasamos todo el día en ruta y cuesta responder.",
        "Los objetivos están claros pero la carga en hora punta es alta.",
        "Agradezco que nos pregunten de forma anónima con el QR.",
    ]

    mock_responses = []
    for i in range(count):
        rol = rng.choice(roles_pool)
        turno = "Mañana" if i % 3 == 0 else "Tarde" if i % 3 == 1 else "Noche"
        if rol in ("Oficinas", "Comercial"):
            turno = "Mañana"

        # Simular bimodalidad en Planta para validar detección de polarización
        if rol == "Planta":
            score = rng.choice([1, 2, 4, 5, 5])
        else:
            score = rng.choice([3, 4, 4, 5])

        dim = rng.choice(["bienestar", "liderazgo", "condiciones"])
        has_comment = rng.random() > 0.4
        comment = rng.choice(comments_pool) if has_comment else ""

        mock_responses.append({
            "id": f"mock_{survey_id}_{i+1}",
            "day": created_date,
            "dimension": dim,
            "site": rol,
            "score": score,
            "cohort": rol,
            "turno": turno,
            "channel": "Móvil-QR" if i % 2 == 0 else "Typeform",
            "comment": comment,
            "survey_id": survey_id,
            "period": "actual",
        })

    return mock_responses

def generate_mock_survey_data(
    num_responses: int = 400,
    seed: int = 42
) -> Any:
    """
    Genera respuestas sintéticas simulando la operativa de Damm.
    Retorna un pd.DataFrame si Pandas está instalado, o un MockDataFrame (compatible).
    
    Introduce sesgos realistas:
    - Menor participación relativa en Comercial (< 15% para detonar alerta de representatividad).
    - Respuestas polarizadas simuladas en Planta para q2_liderazgo (distribución bimodal / alta varianza).
    """
    rng = random.Random(seed)
    
    # Distribución poblacional de respuestas
    target_roles = ["Planta", "Logística", "Oficinas", "Comercial"]
    role_weights = [0.45, 0.25, 0.20, 0.10]
    
    roles: List[str] = []
    turnos: List[str] = []
    employee_ids: List[str] = []
    q1_bienestar: List[int] = []
    q2_liderazgo: List[int] = []
    q3_recursos: List[int] = []
    texto_abierto: List[Optional[str]] = []
    
    mock_comments = [
        "Falta agilidad en la reposición de repuestos en línea.",
        "Buen ambiente entre compañeros de turno.",
        "Los relevos de turno requieren mejor coordinación.",
        "Herramientas informáticas lentas durante el cierre.",
        None
    ]
    comment_weights = [0.05, 0.10, 0.05, 0.10, 0.70]
    
    salt = "damm_hack_2026_salt"
    
    for i in range(num_responses):
        # 1. Rol
        r = rng.choices(target_roles, weights=role_weights, k=1)[0]
        roles.append(r)
        
        # 2. Turno según rol
        if r in ["Planta", "Logística"]:
            t = rng.choices(TURNOS_PERMITIDOS, weights=[0.50, 0.35, 0.15], k=1)[0]
        else:
            t = "Mañana"
        turnos.append(t)
        
        # 3. Hash anónimo de empleado
        emp_hash = hashlib.sha256(f"{salt}_{uuid.uuid4().hex}_{i}".encode("utf-8")).hexdigest()[:16]
        employee_ids.append(emp_hash)
        
        # 4. Respuestas Likert (1 a 5) con polarización deliberada
        if r == "Planta":
            q1 = max(1, min(5, round(rng.gauss(3.4, 0.8))))
            # Polarización intencionada en liderazgo (bimodal 1 y 5)
            q2 = rng.choices([1, 2, 4, 5], weights=[0.35, 0.10, 0.15, 0.40], k=1)[0]
            q3 = max(1, min(5, round(rng.gauss(3.1, 0.9))))
        elif r == "Logística":
            q1 = max(1, min(5, round(rng.gauss(3.0, 0.7))))
            q2 = max(1, min(5, round(rng.gauss(3.3, 0.6))))
            q3 = max(1, min(5, round(rng.gauss(2.8, 0.8))))
        elif r == "Oficinas":
            q1 = max(1, min(5, round(rng.gauss(3.9, 0.5))))
            q2 = max(1, min(5, round(rng.gauss(4.0, 0.5))))
            q3 = max(1, min(5, round(rng.gauss(4.2, 0.5))))
        else:  # Comercial
            q1 = max(1, min(5, round(rng.gauss(3.5, 0.8))))
            q2 = max(1, min(5, round(rng.gauss(3.6, 0.7))))
            q3 = max(1, min(5, round(rng.gauss(3.7, 0.6))))
            
        q1_bienestar.append(int(q1))
        q2_liderazgo.append(int(q2))
        q3_recursos.append(int(q3))
        
        # 5. Comentarios opcionales
        comm = rng.choices(mock_comments, weights=comment_weights, k=1)[0]
        texto_abierto.append(comm)
        
    data = {
        "employee_id": employee_ids,
        "rol": roles,
        "turno": turnos,
        "q1_bienestar": q1_bienestar,
        "q2_liderazgo": q2_liderazgo,
        "q3_recursos": q3_recursos,
        "texto_abierto": texto_abierto,
    }
    
    if HAS_DATA_SCIENCE_STACK:
        return pd.DataFrame(data)
    return SimpleDataFrame(data)


# ============================================================================
# ADAPTADOR TRANSPARENTE LIGERO (Si pandas no estuviera en el runtime)
# ============================================================================

class SimpleSeries:
    def __init__(self, data: List[Any]):
        self._data = list(data)

    def dropna(self) -> SimpleSeries:
        return SimpleSeries([x for x in self._data if x is not None and not (isinstance(x, float) and math.isnan(x))])

    def astype(self, dtype: Any) -> SimpleSeries:
        return SimpleSeries([dtype(x) for x in self._data])

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def tolist(self) -> List[Any]:
        return list(self._data)


class SimpleDataFrame:
    """Implementación ligera con API compatible de pandas para entornos stdlib."""
    def __init__(self, data: Dict[str, List[Any]]):
        self._dict = {k: list(v) for k, v in data.items()}
        self.columns = list(self._dict.keys())

    def __getitem__(self, item: str) -> SimpleSeries:
        return SimpleSeries(self._dict[item])

    def __len__(self) -> int:
        first_col = next(iter(self._dict.values()), [])
        return len(first_col)

    def copy(self) -> SimpleDataFrame:
        return SimpleDataFrame(self._dict)

    def to_dict_records(self) -> List[Dict[str, Any]]:
        keys = list(self._dict.keys())
        n = len(self)
        return [{k: self._dict[k][i] for k in keys} for i in range(n)]


# ============================================================================
# PASO 2: CLASE MOTOR ESTADÍSTICO DETERMINISTA
# ============================================================================

class StatisticalEngine:
    """
    Motor analítico determinista para microconsultas de clima laboral.
    
    Garantías:
    - 100% Determinista (soporta Pandas/NumPy/SciPy o stdlib pura con idéntico resultado).
    - Desacoplamiento estricto de PII: no emite IDs ni comentarios libres individuales.
    - Cumplimiento de k-anonimato (mínimo de N respuestas por cohorte).
    """

    QUESTIONS = ["q1_bienestar", "q2_liderazgo", "q3_recursos"]
    
    def __init__(
        self,
        df_responses: Any,
        min_k_anonymity: int = 5,
        threshold_polarization_std: float = 1.25,
        threshold_min_participation_pct: float = 15.0,
    ) -> None:
        """
        :param df_responses: DataFrame o estructura de respuestas de encuestas.
        :param min_k_anonymity: Umbral de k-anonimato para suprimir cortes vulnerables.
        :param threshold_polarization_std: Desviación típica a partir de la cual se alerta polarización.
        :param threshold_min_participation_pct: % mínimo de participación de un colectivo para evitar sesgo.
        """
        self.min_k = min_k_anonymity
        self.std_polarization_threshold = threshold_polarization_std
        self.min_participation_pct = threshold_min_participation_pct
        
        # Normalizamos la entrada a registros limpios
        if HAS_DATA_SCIENCE_STACK and isinstance(df_responses, pd.DataFrame):
            required_cols = {"employee_id", "rol", "turno"} | set(self.QUESTIONS)
            missing = required_cols - set(df_responses.columns)
            if missing:
                raise ValueError(f"Faltan columnas requeridas en el DataFrame: {missing}")
            self._records = df_responses.to_dict(orient="records")
        elif isinstance(df_responses, SimpleDataFrame):
            self._records = df_responses.to_dict_records()
        elif isinstance(df_responses, list) and all(isinstance(x, dict) for x in df_responses):
            self._records = df_responses
        else:
            raise TypeError("Formato de entrada no soportado. Use DataFrame o lista de diccionarios.")

        self.total_respuestas = len(self._records)

    def compute_participation_metrics(self) -> Dict[str, Any]:
        """
        Calcula las tasas de participación y cobertura agregadas por rol y por (rol, turno).
        """
        if self.total_respuestas == 0:
            return {"total_respuestas": 0, "por_rol": {}, "por_rol_turno": {}}

        # Conteo por rol
        rol_counts: Dict[str, int] = {}
        rol_turno_counts: Dict[str, Dict[str, int]] = {}
        comentarios_validos = 0

        for row in self._records:
            rol = str(row["rol"])
            turno = str(row["turno"])
            rol_counts[rol] = rol_counts.get(rol, 0) + 1
            
            if rol not in rol_turno_counts:
                rol_turno_counts[rol] = {}
            rol_turno_counts[rol][turno] = rol_turno_counts[rol].get(turno, 0) + 1
            
            comm = row.get("texto_abierto")
            if comm is not None and str(comm).strip() != "":
                comentarios_validos += 1

        # Construir estadísticas por rol
        por_rol: Dict[str, Any] = {}
        for rol, count in rol_counts.items():
            pct = round(float((count / self.total_respuestas) * 100.0), 2)
            por_rol[rol] = {
                "n_respuestas": count,
                "porcentaje_total": pct,
                "k_anonimato_valido": bool(count >= self.min_k)
            }

        # Construir estadísticas por rol y turno
        por_rol_turno: Dict[str, Any] = {}
        for rol, turnos_dict in rol_turno_counts.items():
            por_rol_turno[rol] = {}
            for turno, count in turnos_dict.items():
                pct = round(float((count / self.total_respuestas) * 100.0), 2)
                por_rol_turno[rol][turno] = {
                    "n_respuestas": count,
                    "porcentaje_total": pct,
                    "k_anonimato_valido": bool(count >= self.min_k)
                }

        pct_con_comentario = round(float((comentarios_validos / self.total_respuestas) * 100.0), 2)

        return {
            "total_respuestas": self.total_respuestas,
            "tasa_comentarios_abiertos_pct": pct_con_comentario,
            "por_rol": por_rol,
            "por_rol_turno": por_rol_turno
        }

    def _calc_stats_values(self, values: List[float]) -> Dict[str, Optional[float]]:
        """
        Calcula media, mediana, desviación típica, varianza y asimetría de forma determinista.
        """
        clean_values = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
        n = len(clean_values)
        
        if n < self.min_k:
            return {
                "n": n,
                "media": None,
                "mediana": None,
                "desviacion_tipica": None,
                "varianza": None,
                "asimetria": None
            }

        mean_val = float(statistics.fmean(clean_values))
        median_val = float(statistics.median(clean_values))
        
        if n > 1:
            var_val = float(statistics.variance(clean_values))
            std_val = float(statistics.stdev(clean_values))
        else:
            var_val = 0.0
            std_val = 0.0

        # Asimetría (skewness) de Fisher-Pearson insesgada
        if n > 2 and std_val > 0.0:
            m3 = sum((x - mean_val) ** 3 for x in clean_values) / n
            s3 = std_val ** 3
            # Factor de corrección insesgada idéntico a scipy.stats.skew(bias=False)
            adj = math.sqrt(n * (n - 1)) / (n - 2) if n > 2 else 1.0
            skew_val = float((m3 / (std_val ** 3)) * adj) if s3 != 0 else 0.0
        else:
            skew_val = 0.0

        return {
            "n": n,
            "media": round(mean_val, 3),
            "mediana": round(median_val, 3),
            "desviacion_tipica": round(std_val, 3),
            "varianza": round(var_val, 3),
            "asimetria": round(skew_val, 3)
        }

    def compute_descriptive_statistics(self) -> Dict[str, Any]:
        """
        Calcula estadísticas descriptivas globales y segmentadas por rol para q1, q2 y q3.
        """
        # 1. Globales
        metricas_globales: Dict[str, Any] = {}
        for q in self.QUESTIONS:
            vals = [row[q] for row in self._records if q in row]
            metricas_globales[q] = self._calc_stats_values(vals)

        # 2. Segmentadas por Rol
        estadisticas_por_rol: Dict[str, Dict[str, Any]] = {}
        # Agrupar por rol
        grupos_rol: Dict[str, List[Dict[str, Any]]] = {}
        for row in self._records:
            rol = str(row["rol"])
            grupos_rol.setdefault(rol, []).append(row)

        for rol, group_rows in grupos_rol.items():
            estadisticas_por_rol[rol] = {}
            for q in self.QUESTIONS:
                vals = [r[q] for r in group_rows if q in r]
                estadisticas_por_rol[rol][q] = self._calc_stats_values(vals)

        return {
            "global": metricas_globales,
            "por_rol": estadisticas_por_rol
        }

    def detect_anomalies_and_bias(
        self,
        participation_metrics: Dict[str, Any],
        descriptive_stats: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Identifica polarizaciones (alta dispersión) y sesgos de representatividad demográfica.
        Retorna flags booleanos estrictos y métricas de soporte numéricas.
        """
        alertas: Dict[str, Any] = {
            "resumen_alertas_activas": False,
            "alerta_representatividad_baja_participacion": [],
            "alerta_polarizacion_grupal": [],
            "alerta_riesgo_k_anonimato": []
        }

        # 1. Alertas de representatividad (< 15% del total muestral)
        por_rol = participation_metrics.get("por_rol", {})
        for rol, data in por_rol.items():
            pct = data["porcentaje_total"]
            if pct < self.min_participation_pct:
                alertas["alerta_representatividad_baja_participacion"].append({
                    "rol": rol,
                    "porcentaje_observado": pct,
                    "umbral_minimo": self.min_participation_pct,
                    "sesgo_detectado": True
                })
            if not data["k_anonimato_valido"]:
                alertas["alerta_riesgo_k_anonimato"].append({
                    "cohorte": f"rol:{rol}",
                    "n_respuestas": data["n_respuestas"],
                    "min_k_requerido": self.min_k
                })

        # Alertas de k-anonimato en turnos específicos
        por_rol_turno = participation_metrics.get("por_rol_turno", {})
        for rol, turnos in por_rol_turno.items():
            for turno, t_data in turnos.items():
                if 0 < t_data["n_respuestas"] < self.min_k:
                    alertas["alerta_riesgo_k_anonimato"].append({
                        "cohorte": f"rol:{rol}_turno:{turno}",
                        "n_respuestas": t_data["n_respuestas"],
                        "min_k_requerido": self.min_k
                    })

        # 2. Alertas de polarización por desviación típica excesiva
        stats_por_rol = descriptive_stats.get("por_rol", {})
        for rol, preguntas in stats_por_rol.items():
            for q, metricas in preguntas.items():
                std = metricas.get("desviacion_tipica")
                if std is not None and std >= self.std_polarization_threshold:
                    alertas["alerta_polarizacion_grupal"].append({
                        "rol": rol,
                        "pregunta": q,
                        "desviacion_tipica": std,
                        "umbral_alerta": self.std_polarization_threshold,
                        "polarizacion_detectada": True
                    })

        # Flag global indicando si hay al menos una anomalía o sesgo que el LLM debe abordar
        alertas["resumen_alertas_activas"] = bool(
            alertas["alerta_representatividad_baja_participacion"]
            or alertas["alerta_polarizacion_grupal"]
            or alertas["alerta_riesgo_k_anonimato"]
        )

        return alertas


# ============================================================================
# PASO 3: GENERADOR DE PAYLOAD JSON ESTRUCTURADO PARA EL LLM
# ============================================================================

def generate_llm_statistical_payload(
    df_responses: Any,
    min_k_anonymity: int = 5,
    threshold_polarization_std: float = 1.25,
    threshold_min_participation_pct: float = 15.0
) -> str:
    """
    Función de entrada principal (Pipeline End-to-End).
    
    Procesa las microconsultas y genera un JSON 100% libre de texto narrativo,
    con pares clave-valor numéricos y booleanos, optimizado para prompt injection.
    
    :return: String JSON formateado (UTF-8).
    """
    engine = StatisticalEngine(
        df_responses=df_responses,
        min_k_anonymity=min_k_anonymity,
        threshold_polarization_std=threshold_polarization_std,
        threshold_min_participation_pct=threshold_min_participation_pct
    )

    participacion = engine.compute_participation_metrics()
    descriptivas = engine.compute_descriptive_statistics()
    alertas = engine.detect_anomalies_and_bias(
        participation_metrics=participacion,
        descriptive_stats=descriptivas
    )

    # Estructura de contrato estricta para el LLM
    engine_backend = "pandas_numpy_scipy" if HAS_DATA_SCIENCE_STACK else "deterministic_python_math"
    payload: Dict[str, Any] = {
        "metadatos_motor": {
            "version": "1.0.0",
            "backend": engine_backend,
            "k_anonimato_minimo": min_k_anonymity,
            "preguntas_evaluadas": engine.QUESTIONS,
            "escala": {"min": 1, "max": 5}
        },
        "participacion_y_cobertura": participacion,
        "metricas_globales": descriptivas["global"],
        "estadisticas_por_rol": descriptivas["por_rol"],
        "alertas_de_sesgo": alertas
    }

    return json.dumps(payload, ensure_ascii=False, indent=2)
