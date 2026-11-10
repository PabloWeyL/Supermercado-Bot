"""Compatibilidad de imports; el cliente reutilizable vive en integraciones.weyland."""
from .weyland import (ClienteWeyland, ConfiguracionWeyland, ErrorWeyland,
                      ErrorRedWeyland, SinPendientes, AsignacionIncompatible)
from .weyland.configuracion import validar_url

__all__ = ['ClienteWeyland', 'ConfiguracionWeyland', 'ErrorWeyland',
           'ErrorRedWeyland', 'SinPendientes', 'AsignacionIncompatible', 'validar_url']
