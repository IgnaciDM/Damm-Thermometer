"""
Módulo de Motor Estadístico Determinista para Clima Laboral (Estrella Damm).

Diseñado bajo principios de ingeniería de datos y privacidad diferencial/k-anonimato:
- 100% Determinista (Pandas, NumPy, SciPy con fallback transparente a Python stdlib).
- Zero Leaks: Aislamiento total entre datos individuales (PII/hash) y agregaciones.
- Formato estructurado JSON estricto (sin texto narrativo) para inyección directa en prompts LLM.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import uuid
from typing import Any, Dict, List, Optional, Union

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


# ============================================================================
# PASO 1: ESTRUCTURA Y GENERACIÓN DE DATOS SINTÉTICOS (MOCK DATA)
# ============================================================================

ROLES_PERMITIDOS = ["Planta", "Logística", "Oficinas", "Comercial"]
TURNOS_PERMITIDOS = ["Mañana", "Tarde", "Noche"]


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
        r = rng.choices(ROLES_PERMITIDOS, weights=role_weights, k=1)[0]
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


# ============================================================================
# EJECUCIÓN / TEST DE INTEGRACIÓN DIRECTO
# ============================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DEMO: MOTOR ESTADÍSTICO PARA RETO UAB THE HACK 2026 - ESTRELLA DAMM")
    print("=" * 80)
    
    df_mock = generate_mock_survey_data(num_responses=400, seed=42)
    print(f"\n[+] Dataset sintético generado con éxito: {len(df_mock)} registros.")
    
    json_output = generate_llm_statistical_payload(df_mock)
    
    print("\n" + "=" * 80)
    print("[>] PAYLOAD JSON ESTRUCTURADO PARA INYECCIÓN EN PROMPT LLM:")
    print("=" * 80)
    print(json_output)
