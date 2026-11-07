"""
Acceso directo al Dashboard de People Analytics de Estrella Damm.
Permite ejecutar directamente:
streamlit run insights_dashboard.py
o bien:
python insights_dashboard.py
"""

from backend.insights_dashboard import main_streamlit_app

if __name__ == "__main__":
    main_streamlit_app()
