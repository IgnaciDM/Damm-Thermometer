"""
Pruebas unitarias para el Motor Estadístico Determinista (Estrella Damm).

Verifica:
1. Integridad del esquema de datos sintéticos (Paso 1).
2. Exactitud matemática y determinismo de las métricas descriptivas y de cobertura (Paso 2).
3. Activación precisa de alertas de representatividad y polarización (Paso 2).
4. Estricta garantía de privacidad (Zero-PII / Zero-Comments) en el JSON exportado (Paso 3 y 4).
"""

import json
import os
import sys
from pathlib import Path
import unittest

# Asegurar que el directorio raíz del proyecto esté en sys.path al ejecutar el test directamente
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.statistics_engine import (
    StatisticalEngine,
    generate_mock_survey_data,
    generate_llm_statistical_payload,
    ROLES_PERMITIDOS,
    TURNOS_PERMITIDOS,
    HAS_DATA_SCIENCE_STACK,
    SimpleDataFrame
)

try:
    import pandas as pd
except ImportError:
    pd = None


class TestStatisticalEngine(unittest.TestCase):

    def setUp(self):
        # Dataset de prueba controlado
        self.raw_data = {
            "employee_id": ["hash_1", "hash_2", "hash_3", "hash_4", "hash_5", "hash_6"],
            "rol": ["Planta", "Planta", "Planta", "Logística", "Logística", "Comercial"],
            "turno": ["Mañana", "Tarde", "Noche", "Mañana", "Tarde", "Mañana"],
            "q1_bienestar": [4, 4, 4, 2, 2, 5],
            "q2_liderazgo": [1, 5, 1, 3, 3, 4],  # Alta varianza en Planta
            "q3_recursos": [3, 3, 3, 2, 4, 4],
            "texto_abierto": ["Comentario A", None, "Comentario B", None, None, "Comentario C"],
        }
        if pd is not None:
            self.df_test = pd.DataFrame(self.raw_data)
        else:
            self.df_test = SimpleDataFrame(self.raw_data)

    def test_mock_data_structure(self):
        """Verifica que el generador de mock data cumple con las especificaciones del reto."""
        df_mock = generate_mock_survey_data(num_responses=50, seed=123)
        
        expected_cols = {
            "employee_id", "rol", "turno",
            "q1_bienestar", "q2_liderazgo", "q3_recursos",
            "texto_abierto"
        }
        cols = set(df_mock.columns if hasattr(df_mock, "columns") else df_mock[0].keys())
        self.assertTrue(expected_cols.issubset(cols))
        self.assertEqual(len(df_mock), 50)

    def test_participation_metrics_coverage(self):
        """Verifica el cálculo de porcentajes de cobertura por rol y por rol x turno."""
        engine = StatisticalEngine(self.df_test, min_k_anonymity=2)
        metrics = engine.compute_participation_metrics()
        
        self.assertEqual(metrics["total_respuestas"], 6)
        # Planta: 3 de 6 = 50.0%
        self.assertEqual(metrics["por_rol"]["Planta"]["n_respuestas"], 3)
        self.assertEqual(metrics["por_rol"]["Planta"]["porcentaje_total"], 50.0)
        
        # Comercial: 1 de 6 = 16.67%
        self.assertEqual(metrics["por_rol"]["Comercial"]["n_respuestas"], 1)
        self.assertEqual(metrics["por_rol"]["Comercial"]["porcentaje_total"], 16.67)

    def test_descriptive_statistics_accuracy(self):
        """Valida que la media, mediana, desviación típica y varianza se calculen sin sesgo."""
        engine = StatisticalEngine(self.df_test, min_k_anonymity=1)
        stats = engine.compute_descriptive_statistics()
        
        # Planta en q1_bienestar tiene valores [4, 4, 4] -> media 4.0, std 0.0, var 0.0
        planta_q1 = stats["por_rol"]["Planta"]["q1_bienestar"]
        self.assertEqual(planta_q1["media"], 4.0)
        self.assertEqual(planta_q1["mediana"], 4.0)
        self.assertEqual(planta_q1["desviacion_tipica"], 0.0)
        self.assertEqual(planta_q1["varianza"], 0.0)

    def test_polarization_and_bias_alerts(self):
        """Comprueba que se activen las alertas de baja participación (<15%) y polarización."""
        rows = [
            {"employee_id": f"e{i}", "rol": "Planta", "turno": "Mañana", "q1_bienestar": 3, "q2_liderazgo": 1 if i % 2 == 0 else 5, "q3_recursos": 3, "texto_abierto": None}
            for i in range(8)
        ]
        rows.append({"employee_id": "e8", "rol": "Logística", "turno": "Tarde", "q1_bienestar": 3, "q2_liderazgo": 3, "q3_recursos": 3, "texto_abierto": None})
        rows.append({"employee_id": "e9", "rol": "Comercial", "turno": "Mañana", "q1_bienestar": 4, "q2_liderazgo": 4, "q3_recursos": 4, "texto_abierto": None})
        
        if pd is not None:
            df = pd.DataFrame(rows)
        else:
            cols = {k: [r[k] for r in rows] for k in rows[0].keys()}
            df = SimpleDataFrame(cols)

        engine = StatisticalEngine(df, min_k_anonymity=1, threshold_polarization_std=1.25, threshold_min_participation_pct=15.0)
        part = engine.compute_participation_metrics()
        desc = engine.compute_descriptive_statistics()
        alerts = engine.detect_anomalies_and_bias(part, desc)

        self.assertTrue(alerts["resumen_alertas_activas"])
        
        roles_con_sesgo = [item["rol"] for item in alerts["alerta_representatividad_baja_participacion"]]
        self.assertIn("Comercial", roles_con_sesgo)
        self.assertIn("Logística", roles_con_sesgo)

        roles_polarizados = [item["rol"] for item in alerts["alerta_polarizacion_grupal"]]
        self.assertIn("Planta", roles_polarizados)

    def test_strict_privacy_and_zero_pii_leak(self):
        """Verifica que el JSON final NO contenga employee_id ni textos individuales."""
        payload_str = generate_llm_statistical_payload(self.df_test, min_k_anonymity=1)
        payload = json.loads(payload_str)

        # Claves requeridas por contrato
        self.assertIn("metadatos_motor", payload)
        self.assertIn("participacion_y_cobertura", payload)
        self.assertIn("metricas_globales", payload)
        self.assertIn("estadisticas_por_rol", payload)
        self.assertIn("alertas_de_sesgo", payload)

        # Comprobación estricta de ausencia de PII o comentarios abiertos
        raw_text = payload_str.lower()
        self.assertNotIn("hash_", raw_text)
        self.assertNotIn("comentario a", raw_text)
        self.assertNotIn("comentario b", raw_text)
        self.assertNotIn("comentario c", raw_text)

    def test_bootstrap_ci_percentile_empirical(self):
        """Verifica que el bootstrap percentil al 95% (B=1000) calcula cotas empíricas válidas."""
        from backend.statistics_engine import calculate_bootstrap_ci
        scores = [1, 2, 3, 4, 5, 2, 3, 4, 4, 5, 1, 2, 4, 5, 3]
        ci = calculate_bootstrap_ci(scores, n_resamples=1000, seed=42)
        self.assertIn("ci_95", ci)
        low, high = ci["ci_95"]
        self.assertLessEqual(low, ci["media"])
        self.assertGreaterEqual(high, ci["media"])
        self.assertGreaterEqual(low, 1.0)
        self.assertLessEqual(high, 5.0)

    def test_statistical_significance_and_non_significant_warning(self):
        """Verifica que variaciones pequeñas (< alpha) activen el aviso explícito de no significancia."""
        from backend.statistics_engine import calculate_statistical_significance
        # Caso 1: cambio no significativo (medias muy similares con alta dispersión)
        current = [3, 4, 3, 4, 3, 4, 3, 4, 3, 4]
        previous = [3, 3, 4, 3, 4, 3, 4, 3, 4, 3]
        res_ns = calculate_statistical_significance(current, previous, alpha=0.05)
        self.assertFalse(res_ns["es_significativo"])
        self.assertIn("Aviso de significación", res_ns["aviso_significancia"])

        # Caso 2: cambio contundente y estadísticamente significativo (1s vs 5s)
        current_sig = [5, 5, 5, 5, 4, 5, 5, 5, 4, 5]
        previous_sig = [1, 2, 1, 2, 1, 2, 1, 1, 2, 1]
        res_sig = calculate_statistical_significance(current_sig, previous_sig, alpha=0.05)
        self.assertTrue(res_sig["es_significativo"])
        self.assertLess(res_sig["p_valor"], 0.05)

    def test_post_stratification_weighted_mean_and_quien_falta(self):
        """Valida el cálculo de la Media Ponderada Censal (Damm) frente a la Bruta y el diagnóstico '¿Quién falta?'."""
        from backend.statistics_engine import calculate_post_stratification_weights_and_gaps
        # Muestra sesgada: 90 personas en Planta muy felices (5.0), 10 en Oficinas insatisfechas (1.0)
        # Planta censo = 46%, Oficinas censo = 10%
        responses = []
        for i in range(90):
            responses.append({"submission_key": f"planta_{i}", "cohort": "Planta", "score": 5, "turno": "Mañana"})
        for i in range(10):
            responses.append({"submission_key": f"oficinas_{i}", "cohort": "Oficinas", "score": 1, "turno": "Mañana"})

        ps = calculate_post_stratification_weights_and_gaps(responses)
        # Media bruta = (90*5 + 10*1) / 100 = 4.60
        self.assertAlmostEqual(ps["media_bruta"], 4.60, delta=0.05)
        # Media ponderada censal = (0.46*5 + 0.10*1) / (0.46 + 0.10) = (2.3 + 0.1) / 0.56 = 4.285
        self.assertAlmostEqual(ps["media_ponderada_censo"], 4.29, delta=0.05)
        self.assertGreater(ps["sesgo_muestral_puntos"], 0.20)
        # Diagnóstico '¿Quién falta?': Faltan Comercial (17% esperado, 0 observados) y Logística (22% esperado, 0)
        falta = ps["analisis_quien_falta"]
        missing_roles = [d["rol"] for d in falta["colectivos_deficitarios"]]
        self.assertIn("Logística", missing_roles)
        self.assertIn("Comercial", missing_roles)

    def test_k_anonymity_counts_unique_persons_not_dimension_rows(self):
        """Verifica que n y k-anonimato cuenten personas únicas (no filas de preguntas multi-item)."""
        from backend.statistics_engine import calculate_coverage_and_participation
        # 2 personas en Logística que responden cada una 3 dimensiones = 6 filas
        # Si contase filas (6 >= 5), violaría el k-anonimato. Como son 2 personas (2 < 5), DEBE proteger.
        responses = [
            {"submission_key": "persona_1", "cohort": "Logística", "dimension": "bienestar", "score": 4, "turno": "Tarde"},
            {"submission_key": "persona_1", "cohort": "Logística", "dimension": "liderazgo", "score": 3, "turno": "Tarde"},
            {"submission_key": "persona_1", "cohort": "Logística", "dimension": "condiciones", "score": 4, "turno": "Tarde"},
            {"submission_key": "persona_2", "cohort": "Logística", "dimension": "bienestar", "score": 2, "turno": "Tarde"},
            {"submission_key": "persona_2", "cohort": "Logística", "dimension": "liderazgo", "score": 2, "turno": "Tarde"},
            {"submission_key": "persona_2", "cohort": "Logística", "dimension": "condiciones", "score": 1, "turno": "Tarde"},
        ]
        cov = calculate_coverage_and_participation(responses)
        self.assertEqual(cov["total_personas"], 2)
        log_info = cov["por_rol"]["Logística"]
        self.assertEqual(log_info["n_personas"], 2)
        self.assertTrue(log_info["protegido_k_anonimato"])

    def test_enhanced_pii_sanitization_ner(self):
        """Valida que la desidentificación Zero-PII elimine nombres, DNI/IBAN, contactos y roles indirectos únicos."""
        from backend.common import anonymize_comment_ner_and_pii
        comment = (
            "Para dudas preguntale a Marta o habla con Carlos. "
            "Soy la única mujer del turno de noche en envasado. Mi DNI es 12345678Z y mi cuenta ES12 3456 7890 1234 5678 9012. "
            "Escribidme a juan@damm.com o llamad al 600123456."
        )
        cleaned = anonymize_comment_ner_and_pii(comment)
        self.assertNotIn("Marta", cleaned)
        self.assertNotIn("Carlos", cleaned)
        self.assertNotIn("la única mujer del turno de noche", cleaned)
        self.assertNotIn("12345678Z", cleaned)
        self.assertNotIn("ES12", cleaned)
        self.assertNotIn("juan@damm.com", cleaned)
        self.assertNotIn("600123456", cleaned)

    def test_tema_real_precision_evaluation(self):
        """Valida la evaluación matemática de precisión cuando el dataset incluye ground-truth 'tema_real'."""
        from backend.statistics_engine import analyze_qualitative_feedback
        comments = [
            {"comment": "Falta información clara sobre los avisos de turno y comunicación.", "tema_real": "comunicacion"},
            {"comment": "Los turnos rotativos dejan poco margen para el descanso en relevos.", "tema_real": "turnos"},
            {"comment": "Las herramientas informáticas van muy lentas y faltan repuestos.", "tema_real": "herramientas"},
            {"comment": "Agradezco el apoyo y reconocimiento de los mandos en el día a día.", "tema_real": "reconocimiento"},
        ]
        res = analyze_qualitative_feedback(comments)
        prec = res.get("evaluacion_precision", {})
        self.assertTrue(prec.get("disponible"))
        self.assertEqual(prec.get("total_evaluados"), 4)
        self.assertGreaterEqual(prec.get("precision_pct"), 75.0)

    def test_data_retention_policy_90_days(self):
        """Verifica la caducidad y eliminación estricta de registros con antigüedad > 90 días."""
        import sqlite3
        from datetime import date, timedelta
        from backend.common import enforce_data_retention_policy

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""
            CREATE TABLE responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        today = date.today()
        fresh_day = (today - timedelta(days=20)).isoformat()
        expired_day = (today - timedelta(days=95)).isoformat()

        db.execute("INSERT INTO responses (day, created_at) VALUES (?, ?)", (fresh_day, fresh_day))
        db.execute("INSERT INTO responses (day, created_at) VALUES (?, ?)", (expired_day, expired_day))
        db.commit()

        count_before = db.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        self.assertEqual(count_before, 2)

        deleted = enforce_data_retention_policy(db, retention_days=90)
        self.assertEqual(deleted["responses_deleted"], 1)

        remaining = db.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        self.assertEqual(remaining, 1)
        remaining_day = db.execute("SELECT day FROM responses").fetchone()[0]
        self.assertEqual(remaining_day, fresh_day)


if __name__ == "__main__":
    unittest.main()
