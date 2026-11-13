"""Autenticación JWT mediante funciones; cada sesión conserva su estado en memoria."""

import hashlib
import math
import requests
import threading
import time
from contextlib import contextmanager
from types import MappingProxyType
from urllib.parse import urlsplit
from config.archivos import (
    ErrorWeyland, PREFIJO_API_BOT, RUTA_BOT_CERRAR, RUTA_BOT_RENOVAR,
    RUTA_BOT_SESION, RUTA_SIGUIENTE,
)


#----------------------------------------------
# VALIDACIÓN Y CONFIGURACIÓN
#----------------------------------------------

def validar_url(url, permitir_http_local=False):
    """Valida el origen HTTPS o el permiso explícito para HTTP local sin credenciales embebidas."""
    try:
        partes = urlsplit(url)
        partes.port
    except ValueError:
        raise ValueError('URL de Weyland invalida') from None
    local = partes.scheme == 'http' and partes.hostname in ('127.0.0.1', 'localhost', '::1')
    if (
        not partes.hostname
        or partes.username
        or partes.password
        or partes.query
        or partes.fragment
        or '\\' in url
        or any((c.isspace() for c in url))
        or not (partes.scheme == 'https' or (permitir_http_local and local))
    ):
        raise ValueError('Se requiere HTTPS; HTTP solo en loopback con permiso local explicito')
    return (partes.scheme, partes.hostname, partes.port)


def validar_ruta_relativa(valor, nombre):
    """Rechaza rutas absolutas, parámetros y segmentos que puedan escapar del backend."""
    if not isinstance(valor, str):
        raise ValueError(nombre + ' debe ser una ruta relativa')
    ruta = urlsplit(valor)
    if (
        not valor.startswith('/')
        or valor.startswith('//')
        or ruta.scheme
        or ruta.netloc
        or ruta.query
        or ruta.fragment
        or '\\' in valor
        or any((c.isspace() for c in valor))
        or any((p in ('.', '..') for p in ruta.path.split('/')))
    ):
        raise ValueError(nombre + ' debe ser relativa al mismo backend')
    return valor


def obligatorio(valores, nombre, preservar=False):
    """Obtiene un valor obligatorio y permite conservar los espacios de una contraseña."""
    valor = str(valores.get(nombre) or '')
    if not valor.strip():
        raise ValueError('Falta variable: ' + nombre)
    return valor if preservar else valor.strip()


def configuracion_desde_valores(valores, credenciales=None):
    """Valida ajustes JWT y devuelve configuración inmutable sin credenciales visibles."""
    credenciales = valores if credenciales is None else credenciales
    for nombre, esperado in (('BACKEND_AUTH_MODE', 'weyland'), ('BACKEND_BOT_AUTH_MODE', 'jwt')):
        if str(valores.get(nombre) or esperado).lower() != esperado:
            raise ValueError('Configurar ' + nombre + '=' + esperado)
    flags = {}
    for nombre in ('BACKEND_PERMITIR_HTTP_LOCAL', 'BACKEND_REFRESH_ACTIVO'):
        texto = str(valores.get(nombre, 'false')).lower()
        if texto not in ('true', 'false'):
            raise ValueError(nombre + ' debe ser true o false')
        flags[nombre] = texto == 'true'
    margen = str(valores.get('BACKEND_REFRESH_MARGEN_SEGUNDOS', '60'))
    if not margen.isdecimal() or not 0 <= int(margen) <= 86400:
        raise ValueError('BACKEND_REFRESH_MARGEN_SEGUNDOS debe estar entre 0 y 86400')
    tiempos = {}
    for nombre, atributo, defecto in (
            ('BACKEND_TIMEOUT_CONEXION', 'timeout_conexion', 10),
            ('BACKEND_TIMEOUT_LECTURA', 'timeout_lectura', 60)):
        try:
            valor = float(valores.get(nombre, defecto))
        except (ValueError, TypeError):
            raise ValueError(nombre + ' debe ser un numero finito positivo') from None
        if not math.isfinite(valor) or valor <= 0:
            raise ValueError(nombre + ' debe ser un numero finito positivo')
        tiempos[atributo] = valor
    maquina = valores.get('BACKEND_MAQUINA_ID')
    if maquina not in (None, ''):
        if isinstance(maquina, bool) or not str(maquina).isdigit() or int(maquina) < 1:
            raise ValueError('BACKEND_MAQUINA_ID debe ser un entero positivo')
        maquina = int(maquina)
    else:
        maquina = None
    url = obligatorio(valores, 'BACKEND_API_URL').rstrip('/')
    validar_url(url, flags['BACKEND_PERMITIR_HTTP_LOCAL'])
    rutas = {}
    for nombre, atributo, defecto in (
            ('BACKEND_BOT_LOGIN_RUTA', 'bot_login_ruta', RUTA_BOT_SESION),
            ('BACKEND_BOT_REFRESH_RUTA', 'bot_refresh_ruta', RUTA_BOT_RENOVAR),
            ('BACKEND_BOT_CERRAR_RUTA', 'bot_cerrar_ruta', RUTA_BOT_CERRAR)):
        ruta = validar_ruta_relativa(str(valores.get(nombre) or defecto), nombre)
        if not ruta.startswith(PREFIJO_API_BOT):
            raise ValueError(nombre + ' debe estar bajo /bot/')
        rutas[atributo] = ruta
    correo = obligatorio(credenciales, 'BACKEND_EMAIL')
    password = obligatorio(credenciales, 'BACKEND_PASSWORD', preservar=True)
    if '@' not in correo:
        raise ValueError('Falta un correo valido de Weyland')

    def obtener_credenciales():
        """Entrega una copia de las credenciales únicamente al preparar el login."""
        return {'correo': correo, 'password': password}

    return MappingProxyType({
        'url': url, 'bot_auth_mode': 'jwt', 'credenciales': obtener_credenciales,
        'cuenta_sha256': hashlib.sha256(correo.casefold().encode()).hexdigest(),
        'permitir_http_local': flags['BACKEND_PERMITIR_HTTP_LOCAL'],
        'refresh_activo': flags['BACKEND_REFRESH_ACTIVO'],
        'refresh_margen_segundos': int(margen), 'bot_maquina_id': maquina,
        **rutas, **tiempos,
    })


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
        raise ErrorWeyland(status_code=respuesta.status_code)


#----------------------------------------------
# CONTEXTO DE SESIÓN JWT
#----------------------------------------------
def crear_sesion(config, session=None, reloj=time.monotonic):
    """Crea una sesión HTTP con configuración validada, bloqueo y tokens independientes."""
    if not isinstance(config, MappingProxyType) or not callable(config.get('credenciales')):
        raise ValueError('Usar configuracion_desde_valores() para validar los ajustes JWT')
    validar_url(config['url'], config['permitir_http_local'])
    for nombre in ('bot_login_ruta', 'bot_refresh_ruta', 'bot_cerrar_ruta'):
        if not validar_ruta_relativa(config[nombre], nombre).startswith(PREFIJO_API_BOT):
            raise ValueError(nombre + ' debe estar bajo /bot/')
    sesion = session if session is not None else requests.Session()
    if '_weyland' in vars(sesion):
        raise ValueError('La sesión HTTP ya pertenece a otra sesión JWT')
    if session is None:
        sesion.trust_env = False
    sesion._weyland = dict(config=config, reloj=reloj, lock=threading.RLock(),
        access_token=None, refresh_token=None, sesion_id=None, vence=0, margen=0,
        bloqueado=False, cerrado=False, cierre_remoto_confirmado=False)
    return sesion


@contextmanager
def sesion_jwt(config, session=None):
    """Delimita una sesión funcional y asegura la limpieza al salir, incluso ante errores."""
    sesion = crear_sesion(config, session=session)
    try:
        yield sesion
    finally:
        cerrar(sesion)


#----------------------------------------------
# LOGIN Y RENOVACIÓN
#----------------------------------------------

def token_valido(valor, maximo):
    """Comprueba el tamaño y los caracteres permitidos de un token recibido."""
    return isinstance(valor, str) and 0 < len(valor) <= maximo and all((32 < ord(c) < 127 for c in valor))


def limpiar(sesion):
    """Elimina tokens, identificador de sesión y tiempos de vigencia de la memoria."""
    estado = sesion._weyland
    estado['access_token'] = estado['refresh_token'] = estado['sesion_id'] = None
    estado['vence'] = estado['margen'] = 0


def autenticar(sesion, renovar=False):
    """Inicia o renueva la sesión y valida la rotación de tokens antes de conservarlos."""
    estado = sesion._weyland
    config = estado['config']
    if estado['cerrado'] or estado['bloqueado']:
        raise ErrorWeyland('Sesion JWT cerrada o bloqueada; crear un cliente nuevo tras revisar el acceso')
    anterior = estado['refresh_token']
    sesion_anterior = estado['sesion_id']
    datos = {'refresh_token': anterior} if renovar else config['credenciales']()
    if not renovar and config['bot_maquina_id'] is not None:
        datos['maquina_id'] = config['bot_maquina_id']
    if renovar and anterior is None:
        raise ErrorWeyland('No hay token de renovacion JWT disponible')
    inicio = estado['reloj']()
    ruta = config['bot_refresh_ruta'] if renovar else config['bot_login_ruta']
    try:
        with enviar_http(sesion, 'POST', ruta, json=datos) as respuesta:
            if respuesta.status_code != 200:
                raise ErrorWeyland(status_code=respuesta.status_code)
            try:
                tokens = respuesta.json()
            except ValueError:
                raise ErrorWeyland('Respuesta de autenticacion JWT no valida') from None
        if not isinstance(tokens, dict):
            raise ErrorWeyland('Respuesta de autenticacion JWT incompatible')
        ttl = tokens.get('expires_in')
        sesion_id = tokens.get('sesion_id')
        if (
            not token_valido(tokens.get('access_token'), 16384)
            or not token_valido(tokens.get('refresh_token'), 200)
            or str(tokens.get('token_type', '')).lower() != 'bearer'
            or type(ttl) not in (int, float)
            or not math.isfinite(ttl)
            or ttl <= 0
            or inicio + ttl <= estado['reloj']()
            or type(sesion_id) is not int
            or sesion_id < 1
            or (renovar and (tokens['refresh_token'] == anterior or sesion_id != sesion_anterior))
        ):
            raise ErrorWeyland('Tokens JWT incompletos o renovacion incompatible')
        estado['access_token'] = tokens['access_token']
        estado['refresh_token'] = tokens['refresh_token']
        estado['sesion_id'] = sesion_id
        estado['vence'] = inicio + ttl
        estado['margen'] = min(config['refresh_margen_segundos'], ttl / 2) if config['refresh_activo'] else 0
    except (ErrorWeyland, OSError):
        limpiar(sesion)
        estado['bloqueado'] = True
        raise


def asegurar_acceso(sesion):
    """Obtiene o renueva el acceso cuando falta o se acerca su vencimiento."""
    estado = sesion._weyland
    if estado['cerrado'] or estado['bloqueado']:
        raise ErrorWeyland('Sesion JWT cerrada o bloqueada; revisar el acceso')
    if estado['access_token'] is None:
        autenticar(sesion)
    elif estado['reloj']() >= estado['vence'] - estado['margen']:
        autenticar(sesion, renovar=True)


def login(sesion):
    """Obtiene o reutiliza el acceso vigente sin exponer los tokens."""
    estado = sesion._weyland
    with estado['lock']:
        asegurar_acceso(sesion)


def refrescar(sesion):
    """Renueva explícitamente una sesión existente y conserva la rotación de tokens."""
    estado = sesion._weyland
    with estado['lock']:
        if estado['cerrado'] or estado['bloqueado']:
            raise ErrorWeyland('Sesion JWT cerrada o bloqueada; revisar el acceso')
        if estado['refresh_token'] is None:
            raise ErrorWeyland('Iniciar sesión con login() antes de refrescar()')
        autenticar(sesion, renovar=True)


#----------------------------------------------
# TRANSPORTE Y VERIFICACIÓN
#----------------------------------------------

def enviar_http(sesion, metodo, ruta, **kwargs):
    """Envía HTTP sin cookies ni redirecciones y convierte fallos de transporte en operaciones inciertas."""
    estado = sesion._weyland
    config = estado['config']
    sesion.cookies.clear()
    try:
        return sesion.request(metodo, config['url'].rstrip('/') + ruta,
            timeout=(config['timeout_conexion'], config['timeout_lectura']),
            allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise ErrorWeyland('Fallo de transporte JWT; no repetir una renovacion o escritura incierta', tipo='RED') from None
    finally:
        sesion.cookies.clear()


def solicitar(sesion, metodo, ruta, *, datos=None, archivos=None, formulario=None,
              params=None, stream=False, clave_idempotencia=None, _aceptar_405=False, empresa_id=None):
    """Envía una petición BOT y permite una sola renovación tras 401, rebobinando los adjuntos."""
    estado = sesion._weyland
    validar_ruta_relativa(ruta, 'ruta JWT')
    if empresa_id is not None:
        raise ValueError('El JWT BOT no utiliza contexto de empresa en cabeceras')
    if (
        clave_idempotencia is not None
        and (not isinstance(clave_idempotencia, str) or not clave_idempotencia or len(clave_idempotencia) > 128 or any((c.isspace() for c in clave_idempotencia)))
    ):
        raise ValueError('Clave de idempotencia invalida')
    metodo = metodo.upper()
    if not ruta.startswith(PREFIJO_API_BOT) or metodo not in ('GET', 'POST', 'PUT', 'PATCH'):
        raise ValueError('El token BOT solo se utiliza en endpoints /bot/ GET, POST, PUT o PATCH')
    if (
        (datos is not None and archivos is not None)
        or (formulario is not None and (archivos is None or datos is not None))
    ):
        raise ValueError('Cuerpo JWT incompatible')
    posiciones = []
    for valor in (archivos or {}).values():
        archivo = valor[1] if isinstance(valor, (tuple, list)) else valor
        if hasattr(archivo, 'read'):
            posiciones.append((archivo, archivo.tell()))
    with estado['lock']:
        asegurar_acceso(sesion)
        for intento in range(2):
            headers = {'Authorization': 'Bearer ' + estado['access_token']}
            if clave_idempotencia is not None:
                headers['Idempotency-Key'] = clave_idempotencia
            for archivo, posicion in posiciones:
                archivo.seek(posicion)
            cuerpo = {'files': archivos, 'data': formulario} if archivos is not None else {'json': datos}
            respuesta = enviar_http(sesion, metodo, ruta, headers=headers, params=params, stream=stream, **cuerpo)
            codigo = respuesta.status_code
            if 200 <= codigo < 300 or (_aceptar_405 and codigo == 405):
                return respuesta
            respuesta.close()
            if codigo == 401:
                if intento == 0:
                    autenticar(sesion, renovar=True)
                    continue
                limpiar(sesion)
                estado['bloqueado'] = True
            raise ErrorWeyland(status_code=codigo)


def verificar(sesion):
    """Confirma el acceso JWT mediante el GET que debe devolver 405 sin tomar una solicitud."""
    with solicitar(sesion, 'GET', RUTA_SIGUIENTE, _aceptar_405=True) as respuesta:
        try:
            datos = respuesta.json()
        except ValueError:
            raise ErrorWeyland('Verificacion JWT sin confirmacion JSON valida') from None
        if (
            respuesta.status_code != 405
            or not isinstance(datos, dict)
            or not isinstance(datos.get('error'), dict)
            or datos['error'].get('codigo') != 'METODO_NO_PERMITIDO'
        ):
            raise ErrorWeyland('La respuesta no confirma el acceso JWT sin tomar trabajo')


#----------------------------------------------
# CIERRE DE SESIÓN
#----------------------------------------------

def cerrar(sesion):
    """Intenta revocar la sesión remota y siempre elimina los tokens y cierra el transporte local."""
    estado = sesion._weyland
    config = estado['config']
    with estado['lock']:
        if estado['cerrado']:
            return
        try:
            if estado['access_token'] is not None and (not estado['bloqueado']):
                try:
                    with enviar_http(sesion, 'POST', config['bot_cerrar_ruta'],
                            headers={'Authorization': 'Bearer ' + estado['access_token']}, json={}) as respuesta:
                        estado['cierre_remoto_confirmado'] = respuesta.status_code == 204
                except ErrorWeyland:
                    estado['cierre_remoto_confirmado'] = False
        finally:
            limpiar(sesion)
            estado['cerrado'] = True
            sesion.cookies.clear()
            sesion.close()
