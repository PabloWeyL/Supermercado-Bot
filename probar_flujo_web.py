"""Prueba aislada del flujo completo; no modifica .env ni usa solicitudes reales."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent / 'tests/simulador_web.py'), run_name='__main__')
