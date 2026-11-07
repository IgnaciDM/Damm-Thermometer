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

from statistical_engine import (
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


if __name__ == "__main__":
    unittest.main()
