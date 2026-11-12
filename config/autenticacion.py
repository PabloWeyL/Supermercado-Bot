"""JWT del bot; el flujo JWT no utiliza sesión web, cookies ni CSRF."""

import math
import requests
import threading
import time
from config.archivos import (
    ErrorHTTPWeyland,
    ErrorRedWeyland,
    ErrorWeyland,
    PREFIJO_API_BOT,
    RUTA_AUTH_CSRF,
    RUTA_AUTH_LOGIN,
    RUTA_AUTH_PERFIL,
    RUTA_AUTH_SESION,
    RUTA_BOT_CERRAR,
    RUTA_BOT_RENOVAR,
    RUTA_BOT_SESION,
    RUTA_SIGUIENTE,
)
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def validar_url(url, permitir_http_local=False):
    """Valida el origen HTTPS o el permiso explícito para HTTP local sin credenciales embebidas."""
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


def validar_ruta_relativa(valor, nombre):
    """Rechaza rutas absolutas, parámetros y segmentos que puedan escapar del backend."""
    if not isinstance(valor, str):
        raise ValueError(nombre + ' debe ser una ruta relativa')
    ruta = urlsplit(valor)
    if (not valor.startswith('/') or valor.startswith('//') or ruta.scheme or ruta.netloc
            or ruta.query or ruta.fragment or '\\' in valor
            or any(c.isspace() for c in valor) or any(p in ('.', '..') for p in ruta.path.split('/'))):
        raise ValueError(nombre + ' debe ser relativa al mismo backend')
    return valor


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
    refresh_activo: bool = False
    refresh_margen_segundos: int = 60
    refresh_ruta: str = RUTA_AUTH_SESION
    bot_auth_mode: str = 'api_key'
    bot_login_ruta: str = RUTA_BOT_SESION
    bot_refresh_ruta: str = RUTA_BOT_RENOVAR
    bot_cerrar_ruta: str = RUTA_BOT_CERRAR
    bot_maquina_id: int | None = None

    def __post_init__(self):
        """Valida tiempos, rutas, autenticación y coherencia de orígenes antes de usar el cliente."""
        for nombre in ('timeout_conexion', 'timeout_lectura'):
            valor = getattr(self, nombre)
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or valor <= 0:
                raise ValueError(nombre + ' debe ser un numero finito positivo')
        if type(self.solo_api_key) is not bool:
            raise ValueError('solo_api_key debe ser booleano')
        if type(self.refresh_activo) is not bool:
            raise ValueError('refresh_activo debe ser booleano')
        if type(self.refresh_margen_segundos) is not int or not 0 <= self.refresh_margen_segundos <= 86400:
            raise ValueError('BACKEND_REFRESH_MARGEN_SEGUNDOS debe ser un entero entre 0 y 86400')
        validar_ruta_relativa(self.refresh_ruta, 'BACKEND_REFRESH_RUTA')
        if self.bot_auth_mode not in ('api_key', 'jwt'):
            raise ValueError('BACKEND_BOT_AUTH_MODE debe ser jwt o api_key')
        if self.solo_api_key and self.bot_auth_mode == 'jwt':
            raise ValueError('JWT requiere BACKEND_AUTH_MODE=weyland')
        for nombre in ('bot_login_ruta', 'bot_refresh_ruta', 'bot_cerrar_ruta'):
            valor = validar_ruta_relativa(getattr(self, nombre), nombre)
            if not valor.startswith('/bot/'):
                raise ValueError(nombre + ' debe estar bajo /bot/')
        if self.bot_maquina_id is not None and (type(self.bot_maquina_id) is not int or self.bot_maquina_id < 1):
            raise ValueError('BACKEND_MAQUINA_ID debe ser un entero positivo')
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
    """Obtiene un valor obligatorio y permite conservar los espacios de una contraseña."""
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
    refresh = str(valores.get('BACKEND_REFRESH_ACTIVO', 'false')).strip().lower()
    if refresh not in ('true', 'false'):
        raise ValueError('BACKEND_REFRESH_ACTIVO debe ser true o false')
    margen = str(valores.get('BACKEND_REFRESH_MARGEN_SEGUNDOS', '60'))
    if not margen.isdecimal():
        raise ValueError('BACKEND_REFRESH_MARGEN_SEGUNDOS debe ser un entero entre 0 y 86400')
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
    modo_bot = str(valores.get('BACKEND_BOT_AUTH_MODE') or 'api_key').strip().lower()
    login_bot = str(valores.get('BACKEND_BOT_LOGIN_RUTA') or RUTA_BOT_SESION)
    maquina = valores.get('BACKEND_MAQUINA_ID')
    if maquina not in (None, ''):
        if isinstance(maquina, bool) or not str(maquina).isdigit() or int(maquina) < 1:
            raise ValueError('BACKEND_MAQUINA_ID debe ser un entero positivo')
        maquina = int(maquina)
    else:
        maquina = None
    return ConfiguracionWeyland(
        url=url, login_url=('' if solo_api_key else url + login_bot if modo_bot == 'jwt'
                           else str(valores.get('BACKEND_LOGIN_URL') or url + RUTA_AUTH_LOGIN).strip()),
        correo='' if solo_api_key else obligatorio(credenciales, 'BACKEND_EMAIL'),
        password='' if solo_api_key else obligatorio(credenciales, 'BACKEND_PASSWORD', preservar=True),
        api_key=str(credenciales.get('BACKEND_BOT_API_KEY') or ''),
        permitir_http_local=permitir == 'true', solo_api_key=solo_api_key,
        refresh_activo=refresh == 'true', refresh_margen_segundos=int(margen),
        refresh_ruta=str(valores.get('BACKEND_REFRESH_RUTA', RUTA_AUTH_SESION)),
        bot_auth_mode=modo_bot,
        bot_login_ruta=login_bot,
        bot_refresh_ruta=str(valores.get('BACKEND_BOT_REFRESH_RUTA') or RUTA_BOT_RENOVAR),
        bot_cerrar_ruta=str(valores.get('BACKEND_BOT_CERRAR_RUTA') or RUTA_BOT_CERRAR),
        bot_maquina_id=maquina, **tiempos)


def leer_json(respuesta):
    """Exige una respuesta JSON válida cuyo contenido sea un objeto."""
    try:
        datos = respuesta.json()
    except ValueError:
        raise ErrorWeyland('Weyland no devolvio un JSON valido') from None
    if not isinstance(datos, dict):
        raise ErrorWeyland('Respuesta de Weyland incompatible')
    return datos


def exigir_estado(respuesta, codigo=200):
    """Comprueba el código HTTP esperado y propaga un rechazo explícito cuando difiere."""
    if respuesta.status_code != codigo:
        raise ErrorHTTPWeyland(respuesta.status_code)


class SesionBotJWT:
    def __init__(self, config, session=None, reloj=time.monotonic):
        """Inicializa el transporte y la caché de tokens JWT exclusivamente en memoria."""
        self.config = config
        self._session = session if session is not None else requests.Session()
        if session is None:
            self._session.trust_env = False
        self._reloj = reloj
        self._lock = threading.RLock()
        self._access_token = None
        self._refresh_token = None
        self._sesion_id = None
        self._vence = 0
        self._margen = 0
        self._bloqueado = False
        self._cerrado = False
        self.cierre_remoto_confirmado = False

    def _limpiar(self):
        """Elimina tokens, identificador de sesión y tiempos de vigencia de la memoria."""
        self._access_token = self._refresh_token = self._sesion_id = None
        self._vence = self._margen = 0

    def _enviar(self, metodo, ruta, **kwargs):
        # Ni las cookies WEB ni cookies inesperadas de una respuesta BOT se reenvían.
        """Envía HTTP sin cookies ni redirecciones y convierte fallos de transporte en operaciones inciertas."""
        self._session.cookies.clear()
        try:
            return self._session.request(metodo, self.config.url.rstrip('/') + ruta,
                timeout=(self.config.timeout_conexion, self.config.timeout_lectura),
                allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise ErrorRedWeyland('Fallo de transporte JWT; no repetir una renovacion o escritura incierta') from None
        finally:
            self._session.cookies.clear()

    @staticmethod
    def _token_valido(valor, maximo):
        """Comprueba el tamaño y los caracteres permitidos de un token recibido."""
        return (isinstance(valor, str) and 0 < len(valor) <= maximo
                and all(32 < ord(c) < 127 for c in valor))

    def _autenticar(self, renovar=False):
        """Inicia o renueva la sesión y valida la rotación de tokens antes de conservarlos."""
        if self._cerrado or self._bloqueado:
            raise ErrorWeyland('Sesion JWT cerrada o bloqueada; crear un cliente nuevo tras revisar el acceso')
        anterior = self._refresh_token
        sesion_anterior = self._sesion_id
        datos = {'refresh_token': anterior} if renovar else {
            'correo': self.config.correo, 'password': self.config.password}
        if not renovar and self.config.bot_maquina_id is not None:
            datos['maquina_id'] = self.config.bot_maquina_id
        if renovar and anterior is None:
            raise ErrorWeyland('No hay token de renovacion JWT disponible')
        inicio = self._reloj()
        ruta = self.config.bot_refresh_ruta if renovar else self.config.bot_login_ruta
        try:
            with self._enviar('POST', ruta, json=datos) as respuesta:
                if respuesta.status_code != 200:
                    raise ErrorHTTPWeyland(respuesta.status_code)
                try:
                    tokens = respuesta.json()
                except ValueError:
                    raise ErrorWeyland('Respuesta de autenticacion JWT no valida') from None
            if not isinstance(tokens, dict):
                raise ErrorWeyland('Respuesta de autenticacion JWT incompatible')
            ttl = tokens.get('expires_in')
            sesion_id = tokens.get('sesion_id')
            if (not self._token_valido(tokens.get('access_token'), 16384)
                    or not self._token_valido(tokens.get('refresh_token'), 200)
                    or str(tokens.get('token_type', '')).lower() != 'bearer'
                    or type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl <= 0
                    or inicio + ttl <= self._reloj()
                    or type(sesion_id) is not int or sesion_id < 1
                    or renovar and (tokens['refresh_token'] == anterior or sesion_id != sesion_anterior)):
                raise ErrorWeyland('Tokens JWT incompletos o renovacion incompatible')
            self._access_token = tokens['access_token']
            self._refresh_token = tokens['refresh_token']
            self._sesion_id = sesion_id
            self._vence = inicio + ttl
            self._margen = min(self.config.refresh_margen_segundos, ttl / 2) if self.config.refresh_activo else 0
        except (ErrorWeyland, OSError):
            # El backend rota y revoca si se reutiliza un refresh. Un timeout
            # puede ocultar una rotación aplicada: nunca reenviar ese token.
            self._limpiar()
            self._bloqueado = True
            raise

    def _asegurar_acceso(self):
        """Obtiene o renueva el acceso cuando falta o se acerca su vencimiento."""
        if self._cerrado or self._bloqueado:
            raise ErrorWeyland('Sesion JWT cerrada o bloqueada; revisar el acceso')
        if self._access_token is None:
            self._autenticar()
        elif self._reloj() >= self._vence - self._margen:
            self._autenticar(renovar=True)

    def verificar(self):
        # GET verifica autenticación antes del 405; nunca tomar trabajo para probar.
        """Confirma el acceso JWT mediante el GET que debe devolver 405 sin tomar una solicitud."""
        with self.solicitar('GET', RUTA_SIGUIENTE, _aceptar_405=True) as respuesta:
            try:
                datos = respuesta.json()
            except ValueError:
                raise ErrorWeyland('Verificacion JWT sin confirmacion JSON valida') from None
            if (respuesta.status_code != 405 or not isinstance(datos, dict)
                    or not isinstance(datos.get('error'), dict)
                    or datos['error'].get('codigo') != 'METODO_NO_PERMITIDO'):
                raise ErrorWeyland('La respuesta no confirma el acceso JWT sin tomar trabajo')

    def solicitar(self, metodo, ruta, *, datos=None, archivos=None, formulario=None,
                  params=None, stream=False, clave_idempotencia=None, _aceptar_405=False):
        """Envía una petición BOT y permite una sola renovación tras 401, rebobinando los adjuntos."""
        validar_ruta_relativa(ruta, 'ruta JWT')
        metodo = metodo.upper()
        if not ruta.startswith(PREFIJO_API_BOT) or metodo not in ('GET', 'POST'):
            raise ValueError('El token BOT solo se utiliza en endpoints /bot/ GET o POST')
        if (datos is not None and archivos is not None
                or formulario is not None and (archivos is None or datos is not None)):
            raise ValueError('Cuerpo JWT incompatible')
        posiciones = []
        for valor in (archivos or {}).values():
            archivo = valor[1] if isinstance(valor, (tuple, list)) else valor
            if hasattr(archivo, 'read'):
                posiciones.append((archivo, archivo.tell()))
        with self._lock:
            self._asegurar_acceso()
            for intento in range(2):
                headers = {'Authorization': 'Bearer ' + self._access_token}
                if clave_idempotencia is not None:
                    headers['Idempotency-Key'] = clave_idempotencia
                for archivo, posicion in posiciones:
                    archivo.seek(posicion)
                cuerpo = {'files': archivos, 'data': formulario} if archivos is not None else {'json': datos}
                respuesta = self._enviar(metodo, ruta, headers=headers, params=params, stream=stream, **cuerpo)
                codigo = respuesta.status_code
                if 200 <= codigo < 300 or _aceptar_405 and codigo == 405:
                    return respuesta
                respuesta.close()
                if codigo == 401:
                    if intento == 0:
                        self._autenticar(renovar=True)
                        continue
                    self._limpiar()
                    self._bloqueado = True
                raise ErrorHTTPWeyland(codigo)

    def cerrar(self):
        """Intenta revocar la sesión remota y siempre elimina los tokens y cierra el transporte local."""
        with self._lock:
            if self._cerrado:
                return
            try:
                if self._access_token is not None and not self._bloqueado:
                    try:
                        with self._enviar('POST', self.config.bot_cerrar_ruta,
                                headers={'Authorization': 'Bearer ' + self._access_token}, json={}) as respuesta:
                            self.cierre_remoto_confirmado = respuesta.status_code == 204
                    except ErrorWeyland:
                        # El cierre local debe limpiar secretos incluso sin conectividad.
                        self.cierre_remoto_confirmado = False
            finally:
                self._limpiar()
                self._cerrado = True
                self._session.cookies.clear()
                self._session.close()


class SesionWeyland:
    def __init__(self, config, session=None):
        """Prepara el cliente y selecciona el transporte JWT cuando corresponde al modo configurado."""
        self.config = config
        self._session = session if session is not None else requests.Session()
        if session is None:
            self._session.trust_env = False
        self._csrf = None
        self._autenticado = False
        self._cerrado = False
        self._lock = threading.RLock()
        self._bot_jwt = None
        if getattr(config, 'bot_auth_mode', 'api_key') == 'jwt':
            self._bot_jwt = SesionBotJWT(config)

    def __enter__(self):
        """Entrega el cliente para utilizarlo dentro de un bloque with."""
        return self

    def __exit__(self, *_):
        """Cierra el cliente al abandonar el contexto, incluso ante una excepción."""
        self.cerrar()

    def cerrar(self):
        """Cierra el transporte JWT y limpia la sesión y las cookies del cliente."""
        if self._bot_jwt is not None:
            self._bot_jwt.cerrar()
        self._autenticado = False
        self._csrf = None
        self._cerrado = True
        self._session.cookies.clear()
        self._session.close()

    def _enviar(self, metodo, ruta, *, login=False, **kwargs):
        """Ejecuta el transporte del modo heredado con tiempos límite y sin seguir redirecciones."""
        try:
            return self._session.request(metodo,
                self.config.login_url if login else self.config.url.rstrip('/') + ruta,
                timeout=(self.config.timeout_conexion, self.config.timeout_lectura), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise ErrorRedWeyland('No se pudo comunicar con Weyland; no repetir una operacion incierta') from None

    def _obtener_csrf(self):
        """Obtiene CSRF para la compatibilidad heredada; el flujo JWT BOT no utiliza este método."""
        with self._enviar('GET', RUTA_AUTH_CSRF) as respuesta:
            exigir_estado(respuesta)
            token = leer_json(respuesta).get('csrf')
            if not isinstance(token, str) or not token or any(c.isspace() for c in token):
                raise ErrorWeyland('Respuesta CSRF invalida')
            self._csrf = token

    def _login(self):
        """Inicia y verifica la sesión web heredada; no participa del login JWT BOT."""
        if getattr(self.config, 'solo_api_key', False):
            raise ErrorWeyland('El modo api_key no utiliza login ni CSRF')
        self._autenticado = False
        self._session.cookies.clear()
        self._obtener_csrf()
        with self._enviar('POST', '', login=True,
                json={'correo': self.config.correo, 'password': self.config.password},
                headers={'X-CSRFToken': self._csrf, 'Referer': self.config.login_url}) as respuesta:
            exigir_estado(respuesta)
            perfil = leer_json(respuesta)
            usuario = perfil.get('usuario')
            if (not isinstance(usuario, dict) or str(usuario.get('correo', '')).casefold()
                    != self.config.correo.casefold()):
                raise ErrorWeyland('La identidad recibida no coincide con el correo configurado')
        # Django rota el CSRF al hacer login. Obtener el nuevo antes de otro POST.
        self._obtener_csrf()
        self._autenticado = True

    def _perfil(self):
        """Comprueba la identidad de la sesión web heredada y permite un único nuevo login."""
        if self._cerrado:
            raise ErrorWeyland('Cliente cerrado')
        if not self._autenticado:
            self._login()
        for intento in range(2):
            with self._enviar('GET', RUTA_AUTH_PERFIL) as respuesta:
                if respuesta.status_code == 401 and intento == 0:
                    self._login()
                    continue
                if respuesta.status_code != 200:
                    self._autenticado = False
                    exigir_estado(respuesta)
                datos = leer_json(respuesta)
                usuario = datos.get('usuario')
                if (not isinstance(usuario, dict) or
                        str(usuario.get('correo', '')).casefold() != self.config.correo.casefold()):
                    raise ErrorWeyland('Perfil de sesion incompatible')
                return datos

    def verificar(self):
        """Verifica el JWT BOT directamente o utiliza la autenticación heredada correspondiente."""
        with self._lock:
            if self._cerrado:
                raise ErrorWeyland('Cliente cerrado')
            if self._bot_jwt is not None:
                # El JWT BOT es independiente del login del frontend.
                return self._bot_jwt.verificar()
            if getattr(self.config, 'solo_api_key', False):
                return self._verificar_api_key()
            if self.config.refresh_activo:
                self._comprobar_sesion()
            perfil = self._perfil()
            return perfil

    @staticmethod
    def _validar_vigencia(datos):
        """Valida los segundos restantes y la duración informados para la sesión web heredada."""
        restante, duracion = datos.get('restante'), datos.get('duracion')
        if (type(restante) is not int or type(duracion) is not int
                or duracion <= 0 or not 0 <= restante <= duracion):
            raise ErrorWeyland('Estado de sesion incompatible: restante y duracion')
        return restante

    def _comprobar_sesion(self):
        """Consultar sin generar actividad; renovar solo cerca del vencimiento.

        No usa solicitar(): eso provocaria recursion. Un POST sin respuesta no
        se repite. Un 401 o CSRF rechazado permite una unica recuperacion.
        """
        if self._cerrado:
            raise ErrorWeyland('Cliente cerrado')
        if not self._autenticado:
            self._login()
        for intento in range(2):
            with self._enviar('GET', self.config.refresh_ruta,
                    headers={'X-Segundo-Plano': '1'}) as respuesta:
                if respuesta.status_code == 401 and intento == 0:
                    self._login()
                    continue
                exigir_estado(respuesta)
                restante = self._validar_vigencia(leer_json(respuesta))
            if restante > self.config.refresh_margen_segundos:
                return
            with self._enviar('POST', self.config.refresh_ruta, json={}, headers={
                    'X-CSRFToken': self._csrf, 'Referer': self.config.login_url}) as respuesta:
                if respuesta.status_code == 401 and intento == 0:
                    self._login()
                    continue
                if respuesta.status_code == 403 and intento == 0:
                    error = leer_json(respuesta).get('error')
                    if isinstance(error, dict) and error.get('codigo') == 'CSRF_INVALIDO':
                        self._obtener_csrf()
                        continue
                exigir_estado(respuesta)
                if self._validar_vigencia(leer_json(respuesta)) <= 0:
                    raise ErrorWeyland('La renovacion no confirmo una sesion vigente')
                return
        raise ErrorWeyland('No se pudo confirmar la vigencia de la sesion')

    def _verificar_api_key(self):
        """GET autentica pero no puede tomar trabajo: siguiente solo admite POST.

        La API valida EsBot antes de responder METODO_NO_PERMITIDO. Nunca probar
        conectividad con POST siguiente porque ya cambiaria el estado de una orden.
        """
        if self._cerrado:
            raise ErrorWeyland('Cliente cerrado')
        self._session.cookies.clear()
        with self._enviar('GET', RUTA_SIGUIENTE,
                headers={'X-Api-Key': self.config.api_key}) as respuesta:
            if respuesta.status_code != 405:
                if respuesta.status_code in (401, 403) or respuesta.status_code >= 500:
                    raise ErrorHTTPWeyland(respuesta.status_code)
                raise ErrorWeyland('La respuesta no confirma la autenticacion API key sin tomar trabajo')
            datos = leer_json(respuesta)
            error = datos.get('error')
            if not isinstance(error, dict) or error.get('codigo') != 'METODO_NO_PERMITIDO':
                raise ErrorWeyland('El endpoint no confirma el contrato de verificacion API key')
        self._autenticado = True

    def solicitar(self, metodo, ruta, *, datos=None, archivos=None, params=None, stream=False,
                  formulario=None, empresa_id=None, clave_idempotencia=None):
        """Valida la petición y delega en JWT, bloqueando el acceso BOT a endpoints del frontend."""
        partes = urlsplit(ruta)
        if (not ruta.startswith('/') or ruta.startswith('//') or partes.scheme or partes.netloc
                or partes.query or partes.fragment or '\\' in ruta
                or any(c.isspace() for c in ruta) or any(p in ('.', '..') for p in ruta.split('/'))):
            raise ValueError('Ruta relativa de API invalida')
        metodo = metodo.upper()
        if clave_idempotencia is not None and (
                not isinstance(clave_idempotencia, str) or not clave_idempotencia
                or len(clave_idempotencia) > 128
                or any(c.isspace() for c in clave_idempotencia)):
            raise ValueError('Clave de idempotencia invalida')
        if (metodo not in ('GET', 'POST') or (datos is not None and archivos is not None)
                or (formulario is not None and (datos is not None or archivos is None))):
            raise ValueError('Metodo o cuerpo no admitidos por el contrato Weyland')
        if empresa_id is not None and (type(empresa_id) is not int or empresa_id < 1 or ruta.startswith(PREFIJO_API_BOT)):
            raise ValueError('Contexto de empresa invalido')
        posiciones = []
        for campo in (archivos or {}).values():
            contenido = campo[1] if isinstance(campo, (tuple, list)) else campo
            if hasattr(contenido, 'read'):
                posiciones.append((contenido, contenido.tell()))
        with self._lock:
            if self._cerrado:
                raise ErrorWeyland('Cliente cerrado')
            if ruta.startswith(PREFIJO_API_BOT) and self._bot_jwt is not None:
                return self._bot_jwt.solicitar(metodo, ruta, datos=datos, archivos=archivos,
                    formulario=formulario, params=params, stream=stream, clave_idempotencia=clave_idempotencia)
            if self._bot_jwt is not None:
                # El backend limita la audiencia del token a /api/bot/*.
                # No iniciar una sesión web como fallback ni repetir escrituras inciertas.
                raise ErrorWeyland('El JWT BOT no permite endpoints del frontend; '
                                   'revisar catálogo o conciliar la ejecución desde la web. '
                                   'Conservar el checkpoint y no reenviar la toma ni el reporte.')
            # El API key no caduca con la cookie. Verificar la sesion humana antes
            # de cada operacion evita continuar con una sesion vencida sin detectarlo.
            bot = ruta.startswith(PREFIJO_API_BOT)
            solo_api_key = getattr(self.config, 'solo_api_key', False)
            if solo_api_key:
                if self._cerrado:
                    raise ErrorWeyland('Cliente cerrado')
                if not bot:
                    raise ErrorWeyland('La conciliacion por API de usuario requiere sesion; revisar la ejecucion manualmente')
                self._session.cookies.clear()
            else:
                if self.config.refresh_activo:
                    self._comprobar_sesion()
                else:
                    self._perfil()
            if bot and not self.config.api_key:
                raise ErrorWeyland('Falta BACKEND_BOT_API_KEY para la API del bot')
            for intento in range(2):
                headers = {'X-Api-Key': self.config.api_key} if bot else {}
                if empresa_id is not None:
                    headers['X-Empresa-Id'] = str(empresa_id)
                if metodo != 'GET' and not solo_api_key:
                    headers.update({'X-CSRFToken': self._csrf, 'Referer': self.config.login_url})
                if clave_idempotencia is not None:
                    headers['Idempotency-Key'] = clave_idempotencia
                for archivo, posicion in posiciones:
                    archivo.seek(posicion)
                cuerpo = {'files': archivos, 'data': formulario} if archivos is not None else {'json': datos}
                respuesta = self._enviar(metodo, ruta, headers=headers, params=params, stream=stream, **cuerpo)
                if 200 <= respuesta.status_code < 300:
                    return respuesta
                codigo = respuesta.status_code
                error = None
                if codigo in (401, 403):
                    try:
                        error = respuesta.json().get('error', {}).get('codigo')
                    except (ValueError, AttributeError):
                        pass
                respuesta.close()
                if not solo_api_key and intento == 0 and codigo == 401 and (not bot or error == 'SESION_EXPIRADA'):
                    self._login()
                    continue
                if not solo_api_key and intento == 0 and codigo == 403 and error == 'CSRF_INVALIDO':
                    self._obtener_csrf()
                    continue
                # Una clave invalida no se corrige renovando la sesion.
                raise ErrorHTTPWeyland(codigo)
