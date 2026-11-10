"""Adaptador del proyecto: lee .env/entorno; el paquete HTTP no conoce estas rutas."""
import os
from pathlib import Path

from dotenv import dotenv_values

from integraciones.weyland import configuracion_desde_valores
from integraciones.weyland import ClienteSolicitudes
from integraciones.weyland.configuracion import obligatorio
from config.credenciales import combinar_configuracion, normalizar_credenciales, IDENTIDAD


def leer_valores(archivo=None):
    archivo = Path(archivo) if archivo else Path(__file__).resolve().parents[1]/'.env'
    return combinar_configuracion(dotenv_values(archivo), os.environ)


def crear_cliente_weyland(archivo=None, session=None, credenciales=None, valores=None):
    valores = leer_valores(archivo) if valores is None else valores
    valores = normalizar_credenciales(valores)
    if credenciales is not None:
        credenciales = normalizar_credenciales({**{k: valores.get(k) for k in IDENTIDAD}, **credenciales})
    contrato = str(valores.get('BACKEND_CONTRATO') or 'por_id').strip().lower()
    if contrato == 'cola_actual':
        from integraciones.weyland.cola_actual import crear_cliente_cola
        return crear_cliente_cola(valores, credenciales, session)
    if contrato != 'por_id':
        raise ValueError('BACKEND_CONTRATO debe ser por_id o cola_actual')
    return ClienteSolicitudes(configuracion_desde_valores(valores, credenciales), session)
