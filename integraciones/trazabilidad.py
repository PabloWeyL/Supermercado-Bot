"""Contexto de una ejecucion; distingue ID web, ID SQLite y activacion local."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import os
import re
from uuid import uuid4

_contexto = ContextVar('contexto_bot', default=None)


def actual():
    guardado = _contexto.get()
    if guardado is not None:
        return dict(guardado)
    valor = os.getenv('WEYLAND_SOLICITUD_ID', '').strip()
    identificador = int(valor) if valor.isdecimal() and int(valor) > 0 else None
    activacion = os.getenv('BOT_ACTIVACION_ID', '').strip()
    return {'solicitud_id': identificador, 'ejecucion_id_web': identificador,
            'activacion_id': activacion if re.fullmatch(r'[a-f0-9]{32}', activacion) else None}


@contextmanager
def contexto(datos=None, solicitud_id=None):
    datos = dict(datos or actual())
    if solicitud_id is not None:
        datos.update(solicitud_id=solicitud_id, ejecucion_id_web=solicitud_id)
    datos['activacion_id'] = datos.get('activacion_id') or uuid4().hex
    if (not re.fullmatch(r'[a-f0-9]{32}', str(datos['activacion_id']))
            or any(datos.get(k) is not None and (type(datos[k]) is not int or datos[k] < 1)
                   for k in ('solicitud_id', 'ejecucion_id_web'))):
        raise ValueError('Contexto de trazabilidad invalido')
    token = _contexto.set(datos)
    try:
        yield datos
    except Exception as error:
        if not hasattr(error, 'contexto_trazabilidad'):
            error.contexto_trazabilidad = dict(datos)
        raise
    finally:
        _contexto.reset(token)


def metadatos():
    return {**actual(), 'generado_en': datetime.now(timezone.utc).isoformat()}
