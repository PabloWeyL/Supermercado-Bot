"""Conexion Weyland portable. Solo requests; sin .env, Azure, correo o navegador implicitos."""
from .cliente import ClienteWeyland
from .configuracion import ConfiguracionWeyland, configuracion_desde_valores
from .errores import ErrorWeyland, ErrorHTTPWeyland, ErrorRedWeyland, SinPendientes, AsignacionIncompatible
from .worker import ejecutar as ejecutar_solicitud, ResultadoBot
from .aplicacion import ejecutar_configurado

__version__ = '2.0.0'


def crear_cliente(valores, credenciales=None, session=None):
    """La aplicacion proporciona mappings; construir no conecta ni lee el entorno."""
    return ClienteWeyland(configuracion_desde_valores(valores, credenciales), session)


ClienteSolicitudes = ClienteWeyland
__all__ = ['ClienteWeyland', 'ClienteSolicitudes', 'ConfiguracionWeyland', 'configuracion_desde_valores',
           'crear_cliente', 'ejecutar_solicitud', 'ejecutar_configurado', 'ResultadoBot', 'ErrorWeyland',
           'ErrorHTTPWeyland', 'ErrorRedWeyland', 'SinPendientes', 'AsignacionIncompatible']
