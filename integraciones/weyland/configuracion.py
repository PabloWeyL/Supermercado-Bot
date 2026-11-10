"""Configuracion portable e inmutable; sin dependencias del robot."""
from dataclasses import dataclass, field
import math
from urllib.parse import urlsplit

def validar_url(url, permitir_http_local=False):
    try:
        partes = urlsplit(url)
        partes.port
    except ValueError:
        raise ValueError('URL de Weyland invalida') from None
    local = partes.scheme == 'http' and partes.hostname in ('127.0.0.1', 'localhost', '::1')
    if (not partes.hostname or partes.username or partes.password or partes.query
            or partes.fragment or '\\' in url or any(c.isspace() for c in url)
            or not (partes.scheme == 'https' or (permitir_http_local and local))):
        raise ValueError('Se requiere HTTPS; HTTP solo en loopback con permiso local explicito')
    return partes.scheme, partes.hostname, partes.port


@dataclass(frozen=True)
class ConfiguracionWeyland:
    url: str
    login_url: str
    correo: str = field(repr=False)
    password: str = field(repr=False)
    api_key: str = field(default='', repr=False)
    permitir_http_local: bool = False
    timeout_conexion: float = 10
    timeout_lectura: float = 60
    solo_api_key: bool = False

    def __post_init__(self):
        for nombre in ('timeout_conexion', 'timeout_lectura'):
            valor = getattr(self, nombre)
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or valor <= 0:
                raise ValueError(nombre + ' debe ser un numero finito positivo')
        if type(self.solo_api_key) is not bool:
            raise ValueError('solo_api_key debe ser booleano')
        origen = validar_url(self.url, self.permitir_http_local)
        if not self.solo_api_key and origen != validar_url(self.login_url, self.permitir_http_local):
            raise ValueError('Login y API de sesion deben estar en el mismo origen')
        if not self.solo_api_key and (not self.correo or '@' not in self.correo or not self.password):
            raise ValueError('Faltan correo y password de Weyland')
        if self.solo_api_key and (not self.api_key or any(c.isspace() for c in self.api_key)):
            raise ValueError('Falta una API key valida para el modo api_key')
        if any(c in self.api_key for c in '\r\n'):
            raise ValueError('API key invalida')


def obligatorio(valores, nombre, preservar=False):
    valor = str(valores.get(nombre) or '')
    if not valor.strip():
        raise ValueError('Falta variable: ' + nombre)
    return valor if preservar else valor.strip()


def configuracion_desde_valores(valores, credenciales=None):
    """Recibe mappings; no lee disco, variables globales ni proveedores de secretos."""
    credenciales = valores if credenciales is None else credenciales
    permitir = str(valores.get('BACKEND_PERMITIR_HTTP_LOCAL', 'false')).strip().lower()
    if permitir not in ('true', 'false'):
        raise ValueError('BACKEND_PERMITIR_HTTP_LOCAL debe ser true o false')
    tiempos = {}
    for variable, atributo, defecto in [
        ('BACKEND_TIMEOUT_CONEXION', 'timeout_conexion', 10),
        ('BACKEND_TIMEOUT_LECTURA', 'timeout_lectura', 60),
    ]:
        try:
            tiempos[atributo] = float(valores.get(variable, defecto))
        except (ValueError, TypeError):
            raise ValueError(variable + ' debe ser un numero finito positivo') from None
    url = obligatorio(valores, 'BACKEND_API_URL').rstrip('/')
    solo_api_key = str(valores.get('BACKEND_AUTH_MODE') or 'weyland').strip().lower() == 'api_key'
    return ConfiguracionWeyland(
        url=url, login_url='' if solo_api_key else str(valores.get('BACKEND_LOGIN_URL') or url+'/auth/login').strip(),
        correo='' if solo_api_key else obligatorio(credenciales, 'BACKEND_EMAIL'),
        password='' if solo_api_key else obligatorio(credenciales, 'BACKEND_PASSWORD', preservar=True),
        api_key=str(credenciales.get('BACKEND_BOT_API_KEY') or ''),
        permitir_http_local=permitir == 'true', solo_api_key=solo_api_key, **tiempos)
