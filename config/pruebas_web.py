"""Parametros obligatorios del simulador; se leen del .env, sin valores por defecto."""
from pathlib import Path

from dotenv import dotenv_values


def cargar_configuracion_prueba(archivo=None):
    ruta = Path(archivo) if archivo is not None else Path(__file__).resolve().parents[1] / '.env'
    if not ruta.is_file():
        raise ValueError('Falta el archivo .env para configurar ID_PRUEBA y CODIGO_PRUEBA')
    valores = dotenv_values(ruta)
    identificador = str(valores.get('ID_PRUEBA') or '').strip()
    codigo = str(valores.get('CODIGO_PRUEBA') or '').strip()
    if not identificador.isdecimal() or int(identificador) < 1:
        raise ValueError('ID_PRUEBA es obligatorio en .env y debe ser un entero positivo')
    if not codigo:
        raise ValueError('CODIGO_PRUEBA es obligatorio en .env y no puede estar vacio')
    return int(identificador), codigo
