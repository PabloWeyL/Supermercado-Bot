"""Compatibilidad: el flujo por ID nunca crea ni reutiliza solicitudes."""
from integraciones.weyland import ErrorWeyland


def reutilizar(*args, **kwargs):
    raise ErrorWeyland('No se permite reutilizar solicitudes; el operador debe seleccionar un ID EN_COLA')
