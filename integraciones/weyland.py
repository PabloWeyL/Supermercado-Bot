from config.archivos import es_error

from types import SimpleNamespace
# ======================
# IMPORT
# ======================

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile
from base64 import urlsafe_b64decode
from ipaddress import IPv4Address
import hashlib
import json
import math
import os
import re
import requests
from config.monitor import configurar_monitor, obtener_parametros_monitor
import threading
import time
from config.archivos import (
    ARCHIVO_ACTIVO,
    ARCHIVO_BLOQUEO,
    ARCHIVO_CONTEXTO,
    ARCHIVO_COORDINADOR,
    ARCHIVO_ESTADO,
    ARCHIVO_FINALIZACION,
    CARPETA_EJECUCIONES,
    CARPETA_INPUT,
    CARPETA_LOGS,
    CARPETA_PARAMETROS,
    EXTENSIONES_INPUT_POR_DEFECTO,
    crear_error_weyland,
    FORMATO_FECHA_ARCHIVO,
    PREFIJO_API_BOT,
    RUTA_ARCHIVO_EJECUCION,
    RUTA_BOT_CERRAR,
    RUTA_BOT_ERROR,
    RUTA_BOT_ESTADO,
    RUTA_BOT_INPUT,
    RUTA_BOT_INPUT_API,
    RUTA_BOT_RENOVAR,
    RUTA_BOT_REPORTE,
    RUTA_BOT_SESION,
    RUTA_CATALOGO_ESTADOS,
    RUTA_DETALLE_EJECUCION,
    RUTA_EJECUCIONES,
    RUTA_ENTRADA_BASE,
    RUTA_METADATOS_EXPORT,
    RUTA_SIGUIENTE,
    bloqueo,
    confinar,
    guardar_json,
    reemplazar_atomico,
    sha256,
)
from config.configuracion import (
    BACKEND_MAX_BYTES as MAX_BYTES,
    BACKEND_MAX_INPUTS,
    BACKEND_MAX_INPUTS_BYTES,
    BACKEND_MAX_PAGINAS,
)


# ======================
# AUTENTICACIÓN JWT
# ======================

# ======================
# VALIDACIÓN Y CONFIGURACIÓN
# ======================

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


def configuracion_desde_valores(valores, credenciales=None, *, maquina_id=None):
    """Valida ajustes JWT y devuelve configuración inmutable sin credenciales visibles."""
    credenciales = valores if credenciales is None else credenciales
    maquina_id = id_positivo(maquina_id) if maquina_id is not None else None
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
    api_key = obligatorio(credenciales, 'BACKEND_API_KEY')
    if not re.fullmatch(r'wak_[0-9a-f]{12}_[A-Za-z0-9_-]{43}', api_key):
        raise ValueError('BACKEND_API_KEY no tiene el formato de una API key de Weyland')

    def obtener_credenciales():
        """Entrega una copia de las credenciales únicamente al preparar el login."""
        return {'api_key': api_key}

    return MappingProxyType({
        'url': url, 'bot_auth_mode': 'jwt', 'credenciales': obtener_credenciales,
        'permitir_http_local': flags['BACKEND_PERMITIR_HTTP_LOCAL'],
        'refresh_activo': flags['BACKEND_REFRESH_ACTIVO'],
        'refresh_margen_segundos': int(margen), 'bot_maquina_id': maquina_id,
        **rutas, **tiempos,
    })


def leer_json(respuesta):
    """Exige una respuesta JSON válida cuyo contenido sea un objeto."""
    try:
        datos = respuesta.json()
    except ValueError:
        raise crear_error_weyland('Weyland no devolvio un JSON valido') from None
    if not isinstance(datos, dict):
        raise crear_error_weyland('Respuesta de Weyland incompatible')
    return datos


def exigir_estado(respuesta, codigo=200):
    """Comprueba el código HTTP esperado y propaga un rechazo explícito cuando difiere."""
    if respuesta.status_code != codigo:
        raise crear_error_weyland(status_code=respuesta.status_code)


# ======================
# CONTEXTO DE SESIÓN JWT
# ======================
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
        access_token=None, refresh_token=None, sesion_id=None, maquina_id=None, vence=0, margen=0,
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


# ======================
# LOGIN Y RENOVACIÓN
# ======================

def token_valido(valor, maximo):
    """Comprueba el tamaño y los caracteres permitidos de un token recibido."""
    return isinstance(valor, str) and 0 < len(valor) <= maximo and all((32 < ord(c) < 127 for c in valor))


def limpiar(sesion):
    """Elimina tokens, identificador de sesión y tiempos de vigencia de la memoria."""
    estado = sesion._weyland
    estado['access_token'] = estado['refresh_token'] = estado['sesion_id'] = None
    estado['maquina_id'] = None
    estado['vence'] = estado['margen'] = 0


def maquina_del_token(token):
    """Lee la máquina del JWT recibido directamente por HTTPS del backend configurado."""
    if not token_valido(token, 16384):
        return None
    try:
        _, carga, _ = token.split('.')
        reclamos = json.loads(urlsafe_b64decode(carga + '=' * (-len(carga) % 4)))
    except (ValueError, TypeError, UnicodeError):
        return None
    return reclamos.get('maquina') if isinstance(reclamos, dict) else None


def autenticar(sesion, renovar=False):
    """Inicia o renueva la sesión y valida la rotación de tokens antes de conservarlos."""
    estado = sesion._weyland
    config = estado['config']
    if estado['cerrado'] or estado['bloqueado']:
        raise crear_error_weyland('Sesion JWT cerrada o bloqueada; crear un cliente nuevo tras revisar el acceso')
    anterior = estado['refresh_token']
    sesion_anterior = estado['sesion_id']
    maquina_solicitada = config['bot_maquina_id']
    if renovar:
        datos = {'refresh_token': anterior}
    else:
        datos = config['credenciales']()
        if maquina_solicitada is None:
            datos['resolver_maquina_por_ip'] = True
        else:
            datos['maquina_id'] = maquina_solicitada
    if renovar and anterior is None:
        raise crear_error_weyland('No hay token de renovacion JWT disponible')
    inicio = estado['reloj']()
    ruta = config['bot_refresh_ruta'] if renovar else config['bot_login_ruta']
    try:
        with enviar_http(sesion, 'POST', ruta, json=datos) as respuesta:
            if respuesta.status_code != 200:
                raise crear_error_weyland(status_code=respuesta.status_code)
            try:
                tokens = respuesta.json()
            except ValueError:
                raise crear_error_weyland('Respuesta de autenticacion JWT no valida') from None
        if not isinstance(tokens, dict):
            raise crear_error_weyland('Respuesta de autenticacion JWT incompatible')
        ttl = tokens.get('expires_in')
        sesion_id = tokens.get('sesion_id')
        maquina = tokens.get('maquina_id')
        if maquina_solicitada is not None and maquina is None:
            maquina = maquina_del_token(tokens.get('access_token'))
        if type(maquina) is not int or maquina < 1:
            raise crear_error_weyland('Weyland no confirmo la maquina de la sesion BOT')
        if maquina_solicitada is not None and maquina_solicitada != maquina:
            raise crear_error_weyland('La maquina confirmada por Weyland no coincide con la solicitada')
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
            or (renovar and (tokens['refresh_token'] == anterior or sesion_id != sesion_anterior
                              or maquina != estado['maquina_id']))
        ):
            raise crear_error_weyland('Tokens JWT incompletos o renovacion incompatible')
        estado['access_token'] = tokens['access_token']
        estado['refresh_token'] = tokens['refresh_token']
        estado['sesion_id'] = sesion_id
        estado['maquina_id'] = maquina
        estado['vence'] = inicio + ttl
        estado['margen'] = min(config['refresh_margen_segundos'], ttl / 2) if config['refresh_activo'] else 0
    except (RuntimeError, OSError) as error:
        if not (es_error(error, 'ErrorWeyland') or isinstance(error, OSError)):
            raise
        limpiar(sesion)
        estado['bloqueado'] = True
        raise


def asegurar_acceso(sesion):
    """Obtiene o renueva el acceso cuando falta o se acerca su vencimiento."""
    estado = sesion._weyland
    if estado['cerrado'] or estado['bloqueado']:
        raise crear_error_weyland('Sesion JWT cerrada o bloqueada; revisar el acceso')
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
            raise crear_error_weyland('Sesion JWT cerrada o bloqueada; revisar el acceso')
        if estado['refresh_token'] is None:
            raise crear_error_weyland('Iniciar sesión con login() antes de refrescar()')
        autenticar(sesion, renovar=True)


# ======================
# TRANSPORTE Y VERIFICACIÓN
# ======================

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
        raise crear_error_weyland('Fallo de transporte JWT; no repetir una renovacion o escritura incierta', tipo='RED') from None
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
            raise crear_error_weyland(status_code=codigo)


def verificar(sesion):
    """Confirma el acceso JWT mediante el GET que debe devolver 405 sin tomar una solicitud."""
    with solicitar(sesion, 'GET', RUTA_SIGUIENTE, _aceptar_405=True) as respuesta:
        try:
            datos = respuesta.json()
        except ValueError:
            raise crear_error_weyland('Verificacion JWT sin confirmacion JSON valida') from None
        if (
            respuesta.status_code != 405
            or not isinstance(datos, dict)
            or not isinstance(datos.get('error'), dict)
            or datos['error'].get('codigo') != 'METODO_NO_PERMITIDO'
        ):
            raise crear_error_weyland('La respuesta no confirma el acceso JWT sin tomar trabajo')
        return id_positivo(sesion._weyland['maquina_id'])


# ======================
# CIERRE DE SESIÓN
# ======================

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
                except RuntimeError as error:
                    if not es_error(error, 'ErrorWeyland'):
                        raise
                    estado['cierre_remoto_confirmado'] = False
        finally:
            limpiar(sesion)
            estado['cerrado'] = True
            sesion.cookies.clear()
            sesion.close()


# ======================
# CLIENTE DE LA COLA WEYLAND AUTOMATE
# ======================

# ======================
# FUNCIONES DE VALIDACIÓN DEL CONTRATO
# ======================
def id_positivo(valor):
    """Exige un identificador entero positivo y rechaza booleanos u otros tipos."""
    if type(valor) is not int or valor < 1:
        raise ValueError('Se requiere un ID entero positivo')
    return valor


def hash_valido(valor):
    """Comprueba que una huella tenga el formato hexadecimal SHA-256 esperado."""
    return isinstance(valor, str) and re.fullmatch(r'[a-f0-9]{64}', valor) is not None




def entero_configurado(valores, nombre, defecto=None):
    """Lee un entero positivo del mapping y rechaza valores ausentes o inválidos."""
    valor = valores.get(nombre, defecto)
    if isinstance(valor, bool) or not re.fullmatch(r'[0-9]+', str(valor or '')) or int(valor) < 1:
        raise ValueError(f'{nombre} debe ser un entero positivo configurado')
    return int(valor)


# ======================
# FUNCIONES DE POLÍTICA DE CORREOS Y DATOS PUBLICABLES
# ======================
def leer_notificar_correo(configuracion):
    """Booleano web opcional. None conserva el interruptor local del bot.

    Se extrae solo esta bandera: nunca persistir el objeto de configuracion,
    que puede incluir datos privados propios de la empresa.
    """
    if configuracion is None:
        return None
    if not isinstance(configuracion, dict):
        raise crear_error_weyland('La configuracion del proceso debe ser un objeto o null')
    if 'notificar_correo' not in configuracion:
        return None
    valor = configuracion['notificar_correo']
    if type(valor) is not bool:
        raise crear_error_weyland('configuracion.notificar_correo debe ser true o false JSON')
    return valor


def validar_datos_publicables(datos, profundidad=0):
    """Evita persistir credenciales accidentales en parámetros o manifiestos."""
    if profundidad > 32:
        raise crear_error_weyland('Datos demasiado anidados')
    prohibidos = {'password', 'pwd', 'secret', 'api_key', 'access_token', 'refresh_token',
                  'authorization', 'cookie', 'cookies', 'token', 'contraseña', 'clave'}
    if isinstance(datos, dict):
        for clave, valor in datos.items():
            if str(clave).casefold() in prohibidos:
                raise crear_error_weyland('El sobre contiene campos de credenciales; usar el gestor de secretos')
            validar_datos_publicables(valor, profundidad + 1)
    elif isinstance(datos, list):
        for valor in datos:
            validar_datos_publicables(valor, profundidad + 1)
    elif isinstance(datos, str) and datos.lstrip().startswith(('{', '[')):
        try:
            interpretado = json.loads(datos)
        except ValueError:
            return
        validar_datos_publicables(interpretado, profundidad + 1)


# ======================
# CLIENTE DE LA COLA DEL BACKEND
# ======================
def normalizar_ipv4_publica(valor):
    """Acepta solo una IPv4 pública canónica para la comprobación de red."""
    try:
        if not isinstance(valor, str):
            raise ValueError()
        direccion = IPv4Address(valor.strip())
    except ValueError:
        raise crear_error_weyland('Se requiere una IPv4 publica valida para la maquina') from None
    if not direccion.is_global or direccion.is_multicast or direccion.is_reserved:
        raise crear_error_weyland('La direccion de la maquina debe ser una IPv4 publica')
    return str(direccion)


def consultar_ipv4_publica():
    """Consulta la IPv4 de salida con el mismo criterio de proxy que el cliente de Weyland."""
    try:
        with requests.Session() as sesion:
            sesion.trust_env = False
            with sesion.get('https://api.ipify.org', timeout=(5, 10), allow_redirects=False) as respuesta:
                if respuesta.status_code != 200:
                    raise crear_error_weyland('No se pudo verificar la IPv4 publica del equipo')
                return normalizar_ipv4_publica(respuesta.text)
    except requests.RequestException:
        raise crear_error_weyland('No se pudo verificar la IPv4 publica del equipo') from None


def crear_cliente_weyland(config, session=None, *, max_input_bytes=MAX_BYTES, empresa_id=None):
    """No reutiliza los POST por ID ni presupone idempotencia del servidor."""

    cliente = SimpleNamespace(ipv4_esperada=None, ipv4_salida=None)

    def verificar_ipv4():
        """Verifica la red antes de cualquier toma y guarda solo la comparación de esta corrida."""
        if config['bot_maquina_id'] is not None and cliente.ipv4_esperada is None:
            raise crear_error_weyland('Verifica y guarda la IPv4 de la maquina antes de tomar solicitudes')
        salida = consultar_ipv4_publica()
        if cliente.ipv4_esperada is not None and cliente.ipv4_esperada != salida:
            raise crear_error_weyland('La IPv4 publica del equipo no coincide con la registrada para la maquina')
        cliente.ipv4_salida = salida

    def iniciar():
        """Inicia o reutiliza JWT mediante el módulo funcional."""
        return login(cliente.sesion)

    def renovar():
        """Renueva JWT sin exponer los tokens al negocio."""
        return refrescar(cliente.sesion)

    def cerrar_cliente():
        """Revoca y limpia la sesión HTTP y sus tokens."""
        return cerrar(cliente.sesion)

    def verificar_cliente():
        """Comprueba el acceso JWT sin tomar solicitudes."""
        return verificar(cliente.sesion)

    def cierre_remoto_confirmado():
        """Indica si el backend confirmó el cierre de esta sesión."""
        return cliente.sesion._weyland['cierre_remoto_confirmado']

    def solicitar_cliente(metodo, ruta, **opciones):
        """Delega validación, Bearer y renovación en el transporte funcional común."""
        return solicitar(cliente.sesion, metodo, ruta, **opciones)

    def get(ruta, *, params=None, **opciones):
        """Consulta una ruta relativa y devuelve la respuesta HTTP para cerrar con with."""
        return cliente.solicitar('GET', ruta, params=params, **opciones)

    def post(ruta, *, json=None, files=None, data=None, params=None, **opciones):
        """Envía JSON o archivos multipart mediante el transporte autenticado común."""
        return cliente.solicitar('POST', ruta, datos=json, archivos=files, formulario=data, params=params, **opciones)

    def put(ruta, *, json=None, files=None, data=None, params=None, **opciones):
        """Envía una sustitución a una ruta que el backend admita mediante PUT."""
        return cliente.solicitar('PUT', ruta, datos=json, archivos=files, formulario=data, params=params, **opciones)

    def patch(ruta, *, json=None, files=None, data=None, params=None, **opciones):
        """Envía una actualización parcial a una ruta habilitada para PATCH."""
        return cliente.solicitar('PATCH', ruta, datos=json, archivos=files, formulario=data, params=params, **opciones)

    def consultar_estados():
        """Consulta explícita del catálogo cuando el despliegue lo habilite.

        Devuelve el catálogo web (listas ejecucion y bot). No se invoca para
        tomar trabajo ni se guarda la respuesta completa en el checkpoint.
        """
        with cliente.get(RUTA_CATALOGO_ESTADOS, empresa_id=cliente.empresa_id) as respuesta:
            exigir_estado(respuesta)
            try:
                datos = respuesta.json()
            except ValueError:
                raise crear_error_weyland('El catalogo de estados no devolvio JSON valido') from None
        if not isinstance(datos, (dict, list)):
            raise crear_error_weyland('Catalogo de estados incompatible; revisar el contrato')
        return datos

    def tomar_siguiente(maquina_id, id_externo):
        """Toma como máximo una solicitud con la máquina y la correlación persistida."""
        id_positivo(maquina_id)
        if cliente.ipv4_salida is None:
            raise crear_error_weyland('Se requiere verificar la IPv4 publica antes de tomar solicitudes')
        if not isinstance(id_externo, str) or not re.fullmatch(r'[a-f0-9]{32}', id_externo):
            raise ValueError('id_externo debe ser la correlacion local persistida')
        with cliente.post(RUTA_SIGUIENTE,
                json={'maquina_id': maquina_id, 'id_externo': id_externo,
                      'soporta_multiples_inputs': True}) as respuesta:
            if respuesta.status_code == 204:
                return None
            exigir_estado(respuesta)
            return leer_json(respuesta)

    def validar_asignacion(datos, maquina_id, id_externo, codigos):
        """Valida la identidad y el input recibido y conserva únicamente los datos publicables necesarios."""
        if (not isinstance(datos, dict) or type(datos.get('ejecucion_id')) is not int
                or datos['ejecucion_id'] < 1 or datos.get('id_externo') != id_externo
                or not isinstance(datos.get('maquina'), dict)
                or type(datos['maquina'].get('id')) is not int or datos['maquina']['id'] != maquina_id
                or not isinstance(datos.get('proceso'), dict) or datos['proceso'].get('codigo') not in codigos
                or not isinstance(datos.get('parametros'), dict)
                or not any(k in datos for k in ('input', 'inputs'))):
            raise crear_error_weyland('Asignacion incompatible con VM, correlacion, proceso o parametros')
        if cliente.ipv4_salida is not None:
            asignada = normalizar_ipv4_publica(datos['maquina'].get('direccion_ip'))
            if asignada != cliente.ipv4_salida:
                raise crear_error_weyland('La IPv4 de la asignacion no coincide con la salida publica del equipo')
        multiples = 'inputs' in datos
        entradas = datos['inputs'] if multiples else ([datos['input']] if datos['input'] is not None else [])
        if not isinstance(entradas, list) or len(entradas) > BACKEND_MAX_INPUTS:
            raise crear_error_weyland('Lista de inputs invalida o superior al limite configurado')
        normalizadas, vistos = [], set()
        for entrada in entradas:
            nombre = entrada.get('nombre') if isinstance(entrada, dict) else None
            archivo_id = entrada.get('archivo_id') if isinstance(entrada, dict) else None
            ruta = RUTA_BOT_INPUT_API.format(identificador=datos['ejecucion_id'])
            if multiples:
                if type(archivo_id) is not int or archivo_id < 1 or archivo_id in vistos:
                    raise crear_error_weyland('ID de archivo de input invalido o repetido')
                vistos.add(archivo_id)
                ruta += '?archivo_id=' + str(archivo_id)
            if (not isinstance(nombre, str) or not nombre or len(nombre) > 255
                    or any(c in nombre for c in '/\\:\x00\r\n')
                    or entrada.get('descarga') != ruta):
                raise crear_error_weyland('Metadatos de input incompatibles')
            item = {'nombre': nombre, 'descarga': ruta}
            if multiples:
                item['archivo_id'] = archivo_id
            normalizadas.append(item)
        if multiples and not entradas and datos.get('input') is not None:
            raise crear_error_weyland('El input legacy contradice la lista vacia de inputs')
        validar_datos_publicables(datos['parametros'])
        if len(json.dumps(datos['parametros'], allow_nan=False).encode()) > MAX_BYTES:
            raise crear_error_weyland('Parametros demasiado grandes')
        # No guardar configuracion arbitraria ni la URL firmada de Blob Storage.
        reproceso = datos.get('reproceso_de')
        if reproceso is not None:
            id_positivo(reproceso)
        return {'ejecucion_id': datos['ejecucion_id'], 'id_externo': id_externo,
                'reproceso_de': reproceso,
                'maquina_id': maquina_id, 'proceso_codigo': datos['proceso']['codigo'],
                'parametros': datos['parametros'],
                'notificar_correo': leer_notificar_correo(datos.get('configuracion')),
                'input': normalizadas[0] if len(normalizadas) == 1 else None,
                'inputs': normalizadas}

    def descargar(ruta, destino, limite, *, usuario=False, params=None):
        """Descarga en un archivo parcial, verifica su tamaño y lo publica mediante sustitución atómica."""
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = None
        try:
            with cliente.get(ruta, stream=True, params=params,
                    empresa_id=cliente.empresa_id if usuario else None) as respuesta:
                exigir_estado(respuesta)
                cabecera = respuesta.headers.get('Content-Length')
                esperado = None
                if cabecera is not None and not respuesta.headers.get('Content-Encoding'):
                    if not str(cabecera).isdigit() or not 1 <= int(cabecera) <= limite:
                        raise crear_error_weyland('Tamano de descarga incompatible')
                    esperado = int(cabecera)
                total, digest = 0, hashlib.sha256()
                with NamedTemporaryFile(dir=destino.parent, suffix='.part', delete=False) as archivo:
                    parcial = Path(archivo.name)
                    for bloque in respuesta.iter_content(65536):
                        total += len(bloque)
                        if total > limite:
                            raise crear_error_weyland('La descarga supera el limite configurado')
                        digest.update(bloque)
                        archivo.write(bloque)
                    archivo.flush()
                    os.fsync(archivo.fileno())
                if total < 1 or esperado is not None and total != esperado:
                    raise crear_error_weyland('Descarga incompleta; conservar el mismo ID', tipo='RED')
            reemplazar_atomico(parcial, destino)
            return {'size_bytes': total, 'sha256': digest.hexdigest()}
        except requests.RequestException:
            raise crear_error_weyland('Descarga interrumpida; no ejecutar un parcial', tipo='RED') from None
        finally:
            if parcial is not None:
                parcial.unlink(missing_ok=True)

    def descargar_input(identificador, destino, *, archivo_id=None):
        """Descarga un input concreto mediante su ID o el primero en el contrato legacy."""
        id_positivo(identificador)
        params = {'archivo_id': id_positivo(archivo_id)} if archivo_id is not None else None
        return cliente.descargar(RUTA_BOT_INPUT.format(identificador=identificador), destino,
                               cliente.max_input_bytes, params=params)

    def entregar_resultado(identificador, reporte, huella, tamano, *, estado_bot=None, mensaje='', resumen=None):
        """Verifica el reporte, lo sube con su estado final y exige una confirmación compatible."""
        id_positivo(identificador)
        if not hash_valido(huella) or type(tamano) is not int or not 1 <= tamano <= MAX_BYTES:
            raise crear_error_weyland('Faltan las huellas locales de la entrega')
        reporte = Path(reporte)
        mime = 'application/json' if reporte.suffix.lower() == '.json' else 'application/zip'
        formulario = None
        if estado_bot is not None:
            if estado_bot not in ('FINALIZADO', 'FINALIZADO_CON_OBSERVACIONES'):
                raise ValueError('El reporte requiere un estado final exitoso')
            validar_estado_bot(estado_bot, mensaje, resumen)
            formulario = {'estado': estado_bot, 'mensaje': mensaje}
            if resumen is not None:
                formulario['resumen'] = json.dumps(resumen, allow_nan=False)
        with reporte.open('rb') as archivo:
            if os.fstat(archivo.fileno()).st_size != tamano or hashlib.file_digest(archivo, 'sha256').hexdigest() != huella:
                raise crear_error_weyland('La entrega cambio; no se enviara')
            archivo.seek(0)
            with cliente.post(RUTA_BOT_REPORTE.format(identificador=identificador),
                    files={'archivo': (reporte.name, archivo, mime)}, data=formulario) as respuesta:
                exigir_estado(respuesta)
                confirmado = leer_json(respuesta)
        cliente.validar_confirmacion(confirmado, identificador, 'COMPLETADA')
        if estado_bot is not None:
            validar_confirmacion_estado(confirmado, identificador, estado_bot)

    def informar_estado(identificador, estado, mensaje='', resumen=None):
        """Valida y publica un estado del bot y comprueba el ID y el cierre confirmados."""
        id_positivo(identificador)
        validar_estado_bot(estado, mensaje, resumen)
        datos = {'estado': estado, 'mensaje': mensaje}
        if resumen is not None:
            datos['resumen'] = resumen
        with cliente.post(RUTA_BOT_ESTADO.format(identificador=identificador), json=datos) as respuesta:
            exigir_estado(respuesta)
            validar_confirmacion_estado(leer_json(respuesta), identificador, estado)

    def validar_confirmacion(datos, identificador, estado):
        """Comprueba que el backend confirme la ejecución y el estado de sistema esperados."""
        if (not isinstance(datos, dict) or type(datos.get('ejecucion_id')) is not int
                or datos['ejecucion_id'] != identificador or datos.get('estado') != estado):
            raise crear_error_weyland('Confirmacion incompatible; conservar el checkpoint y conciliar')

    def informar_error(identificador, mensaje):
        """Publica un fallo mediante el endpoint compatible y exige confirmación de ERROR."""
        id_positivo(identificador)
        if not isinstance(mensaje, str) or not mensaje.strip() or len(mensaje) > 4000:
            raise ValueError('Mensaje de error invalido')
        with cliente.post(RUTA_BOT_ERROR.format(identificador=identificador), json={'mensaje': mensaje}) as respuesta:
            exigir_estado(respuesta)
            cliente.validar_confirmacion(leer_json(respuesta), identificador, 'ERROR')

    def consultar_ejecucion(identificador):
        """Normaliza el código BOT del detalle web, sin usar su etiqueta visible."""
        id_positivo(identificador)
        with cliente.get(RUTA_DETALLE_EJECUCION.format(identificador=identificador), empresa_id=cliente.empresa_id) as respuesta:
            exigir_estado(respuesta)
            datos = leer_json(respuesta)
        if type(datos.get('id')) is not int or datos['id'] != identificador:
            raise crear_error_weyland('Detalle de ejecucion incompatible')
        estado_bot = datos.get('estado_bot')
        if isinstance(estado_bot, dict):
            codigo = estado_bot.get('codigo')
            if not isinstance(codigo, str) or not re.fullmatch(r'[A-Z0-9_]{2,30}', codigo):
                raise crear_error_weyland('El detalle no contiene un codigo de estado BOT valido')
            datos = {**datos, 'estado_bot': codigo}
        return datos

    def buscar_correlacion(maquina_id, id_externo, limite_paginas=BACKEND_MAX_PAGINAS, *, permitir_ausente=False):
        """Busca en todas las páginas; una ausencia no autoriza repetir una toma incierta.

        permitir_ausente se reserva a una conciliación explícita del operador.
        El coordinador conserva el modo estricto y no reintenta POST por ausencia.
        """
        id_positivo(maquina_id)
        if not isinstance(id_externo, str) or not re.fullmatch(r'[a-f0-9]{32}', id_externo):
            raise ValueError('Correlacion local invalida')
        if type(limite_paginas) is not int or limite_paginas < 1 or type(permitir_ausente) is not bool:
            raise ValueError('Opciones de conciliacion invalidas')
        encontrados = []
        for pagina in range(1, limite_paginas + 1):
            with cliente.get(RUTA_EJECUCIONES, params={'maquina_id': maquina_id, 'page': pagina},
                    empresa_id=cliente.empresa_id) as respuesta:
                exigir_estado(respuesta)
                datos = leer_json(respuesta)
            if isinstance(datos.get('data'), list):
                filas = datos['data']
                total, actual, tamano = (datos.get(k) for k in ('total', 'page', 'page_size'))
                if (type(total) is not int or total < 0 or type(actual) is not int or actual != pagina
                        or type(tamano) is not int or tamano < 1 or len(filas) > tamano
                        or not filas and pagina * tamano < total):
                    raise crear_error_weyland('Paginacion de conciliacion incompatible')
                final = pagina * tamano >= total
            elif isinstance(datos.get('results'), list) and 'next' in datos:
                filas = datos['results']
                siguiente = datos['next']
                if siguiente is not None and (not isinstance(siguiente, str) or not siguiente):
                    raise crear_error_weyland('Paginacion de conciliacion incompatible')
                final = siguiente is None
            else:
                raise crear_error_weyland('Listado de conciliacion incompatible')
            if any(not isinstance(fila, dict) for fila in filas):
                raise crear_error_weyland('Listado de conciliacion con filas incompatibles')
            encontrados.extend(d for d in filas if d.get('id_externo') == id_externo)
            if final:
                if not encontrados and permitir_ausente:
                    return None
                if len(encontrados) != 1:
                    raise crear_error_weyland('Toma incierta sin coincidencia unica; requiere revision manual')
                identificador = id_positivo(encontrados[0]['id'])
                return cliente.consultar_ejecucion(identificador)
            # No seguir URLs de next; solo consultar el siguiente numero en el mismo origen.
        raise crear_error_weyland('Conciliacion incompleta por limite de paginas; requiere revision manual')

    def descargar_reporte_confirmado(identificador, archivo_id, destino):
        """Solicita el reporte del detalle web para conciliar su tamaño y huella con la entrega local."""
        id_positivo(identificador)
        id_positivo(archivo_id)
        return cliente.descargar(RUTA_ARCHIVO_EJECUCION.format(identificador=identificador, archivo_id=archivo_id), destino,
                               MAX_BYTES, usuario=True)

    cliente.login = iniciar
    cliente.verificar_ipv4 = verificar_ipv4
    cliente.refrescar = renovar
    cliente.cerrar = cerrar_cliente
    cliente.verificar = verificar_cliente
    cliente.cierre_remoto_confirmado = cierre_remoto_confirmado
    cliente.solicitar = solicitar_cliente
    cliente.get = get
    cliente.post = post
    cliente.put = put
    cliente.patch = patch
    cliente.consultar_estados = consultar_estados
    cliente.tomar_siguiente = tomar_siguiente
    cliente.validar_asignacion = validar_asignacion
    cliente.descargar = descargar
    cliente.descargar_input = descargar_input
    cliente.entregar_resultado = entregar_resultado
    cliente.informar_estado = informar_estado
    cliente.validar_confirmacion = validar_confirmacion
    cliente.informar_error = informar_error
    cliente.consultar_ejecucion = consultar_ejecucion
    cliente.buscar_correlacion = buscar_correlacion
    cliente.descargar_reporte_confirmado = descargar_reporte_confirmado
    cliente.contrato = 'cola_actual'

    cliente.max_input_bytes = entero_configurado({'limite': max_input_bytes}, 'limite')
    cliente.empresa_id = id_positivo(empresa_id) if empresa_id is not None else None
    cliente.config = config
    cliente.sesion = crear_sesion(config, session=session)
    return cliente


@contextmanager
def sesion_cliente(cliente):
    """Cierra la sesión del cliente incluso cuando falla una operación."""
    try:
        yield cliente
    finally:
        cliente.cerrar()



# ======================
# FUNCIONES DE CONFIGURACIÓN Y CREACIÓN DEL CLIENTE
# ======================
def crear_cliente_cola(valores, credenciales=None, session=None, *, maquina_id=None):
    """Construye el cliente del contrato de cola con sus credenciales y límites de entrada."""
    empresa_id = entero_configurado(valores, 'BACKEND_EMPRESA_ID') if valores.get('BACKEND_EMPRESA_ID') else None
    return crear_cliente_weyland(configuracion_desde_valores(valores, credenciales, maquina_id=maquina_id), session,
        max_input_bytes=entero_configurado(valores, 'MAX_ARCHIVO', MAX_BYTES), empresa_id=empresa_id)


def leer_valores(archivo=None):
    """Combina el archivo privado con el entorno y normaliza los nombres de las credenciales."""
    from dotenv import dotenv_values
    from config.configuracion import combinar_configuracion
    from config.archivos import ARCHIVO_ENV
    archivo = Path(archivo) if archivo else ARCHIVO_ENV
    return combinar_configuracion(dotenv_values(archivo), os.environ)


def guardar_maquina_verificada(raiz, cliente, maquina_id, ipv4):
    """Guarda solo una máquina explícita que la sesión de Weyland confirmó."""
    maquina_id = id_positivo(maquina_id)
    if cliente.config['bot_maquina_id'] != maquina_id or cliente.sesion._weyland['maquina_id'] != maquina_id:
        raise crear_error_weyland('La sesion no confirma la maquina que se quiere guardar')
    ipv4 = normalizar_ipv4_publica(ipv4)
    if consultar_ipv4_publica() != ipv4:
        raise crear_error_weyland('La IPv4 publica del equipo no coincide con la registrada para la maquina')
    ruta = confinar(raiz, '.runtime/maquina_weyland.json')
    ruta.parent.mkdir(parents=True, exist_ok=True)
    guardar_json(ruta, {'backend': cliente.config['url'], 'maquina_id': maquina_id, 'ipv4': ipv4})


def crear_cliente_api(archivo=None, session=None, *, maquina_id=None):
    """Exige el contrato y el modo JWT del bot antes de construir su cliente."""
    valores = leer_valores(archivo)
    if valores.get('BACKEND_CONTRATO', 'cola_actual') != 'cola_actual':
        raise ValueError('Este bot utiliza BACKEND_CONTRATO=cola_actual')
    if valores.get('BACKEND_AUTH_MODE', 'weyland') != 'weyland':
        raise ValueError('Este bot utiliza BACKEND_AUTH_MODE=weyland')
    if valores.get('BACKEND_BOT_AUTH_MODE', 'jwt') != 'jwt':
        raise ValueError('Este backend requiere BACKEND_BOT_AUTH_MODE=jwt')
    from config.archivos import ARCHIVO_ENV
    raiz = (Path(archivo) if archivo else ARCHIVO_ENV).parent.resolve()
    ruta = confinar(raiz, '.runtime/maquina_weyland.json')
    ipv4_esperada = None
    if ruta.exists():
        maquina_local = leer(ruta)
        backend = str(valores.get('BACKEND_API_URL') or '').strip().rstrip('/')
        if maquina_local.get('backend') != backend:
            if maquina_id is None:
                raise crear_error_weyland('La maquina guardada pertenece a otro backend; verificar la maquina actual')
        elif maquina_id is None or maquina_local.get('maquina_id') == maquina_id:
            maquina_id = id_positivo(maquina_local.get('maquina_id'))
            if maquina_local.get('ipv4') is not None:
                ipv4_esperada = normalizar_ipv4_publica(maquina_local['ipv4'])
    cliente = crear_cliente_cola({**valores, 'BACKEND_BOT_AUTH_MODE': 'jwt'}, session=session, maquina_id=maquina_id)
    cliente.ipv4_esperada = ipv4_esperada
    return cliente


# ======================
# SOLICITUDES, CHECKPOINTS Y ENTREGA DE RESULTADOS
# ======================

# ======================
# CONTRATO DEL RESULTADO DEL BOT
# ======================
def crear_resultado_bot(archivos, resumen=None, detalles=None, permite_sin_export=False,
                        incluir_metadatos_export=True):
    """Agrupa el resultado antes de validarlo y guardarlo en el checkpoint."""

    return SimpleNamespace(
        archivos=archivos,
        resumen={'procesados': 1, 'correctos': 1, 'errores': 0} if resumen is None else resumen,
        detalles={} if detalles is None else detalles,
        permite_sin_export=permite_sin_export,
        incluir_metadatos_export=incluir_metadatos_export,
    )


def es_resultado_bot(resultado):
    """Comprueba la forma del resultado antes de leer sus campos."""

    return isinstance(resultado, SimpleNamespace) and vars(resultado).keys() == {
        'archivos', 'resumen', 'detalles', 'permite_sin_export', 'incluir_metadatos_export',
    }

# ======================
# FUNCIONES DE EXPORTACIÓN
# ======================
def exportar(carpeta, resultado, estado):
    """Valida los artefactos y prepara el ZIP o los metadatos de un resultado sin exportación."""
    if not es_resultado_bot(resultado):
        raise crear_error_weyland('El bot debe entregar crear_resultado_bot')
    if type(resultado.incluir_metadatos_export) is not bool:
        raise crear_error_weyland('incluir_metadatos_export debe ser booleano')
    if not resultado.archivos and resultado.permite_sin_export is not True:
        raise crear_error_weyland('El bot debe entregar crear_resultado_bot con al menos un archivo')
    resumen = resultado.resumen
    if any(type(resumen.get(k)) is not int or resumen[k] < 0 for k in ('procesados', 'correctos', 'errores')):
        raise crear_error_weyland('Resumen de negocio invalido')
    if not resultado.archivos:
        metadatos = {k: estado[k] for k in ('solicitud_id', 'ejecucion_id', 'ejecucion_bot_id')}
        metadatos.update(estado='COMPLETADA', resumen=resumen, export=None, resultado=resultado.detalles)
        if len(json.dumps(metadatos, allow_nan=False).encode('utf-8')) > MAX_BYTES:
            raise crear_error_weyland('Resultado JSON supera el limite de entrega')
        return None, metadatos
    archivos = []
    for ruta in resultado.archivos:
        ruta = Path(ruta)
        if ruta.is_absolute():
            try:
                ruta = ruta.relative_to(carpeta)
            except ValueError:
                raise crear_error_weyland('Artefacto fuera de la ejecucion') from None
        archivo = confinar(carpeta, ruta)
        if (not archivo.is_file() or any(p in (CARPETA_INPUT, CARPETA_LOGS) for p in ruta.parts)
                or archivo.name.startswith('.env') or archivo.name in (ARCHIVO_ESTADO, ARCHIVO_BLOQUEO)
                or archivo.suffix.lower() in (
                '.db', '.sqlite', '.sqlite3', '.env', '.pem', '.key', '.log')):
            raise crear_error_weyland('Artefacto de negocio no permitido')
        if ruta.as_posix() == RUTA_METADATOS_EXPORT:
            raise crear_error_weyland('Nombre de artefacto reservado')
        archivos.append((archivo, ruta.as_posix()))
    datos = {**resultado.detalles, 'solicitud_id': estado['solicitud_id'],
             'ejecucion_id': estado['ejecucion_id'], 'ejecucion_bot_id': estado['ejecucion_bot_id'],
             'estado': 'COMPLETADA', 'resumen': resumen}
    nombre = 'resultado_' + datetime.now().strftime(FORMATO_FECHA_ARCHIVO) + '.zip'
    reporte = confinar(carpeta, nombre)
    with ZipFile(reporte, 'w', ZIP_DEFLATED) as paquete:
        for archivo, relativo in archivos:
            paquete.write(archivo, relativo)
        if resultado.incluir_metadatos_export:
            paquete.writestr(RUTA_METADATOS_EXPORT, json.dumps(datos, ensure_ascii=False, indent=2))
    with reporte.open('r+b') as archivo:
        os.fsync(archivo.fileno())
    if not 1 <= reporte.stat().st_size <= MAX_BYTES:
        raise crear_error_weyland('Reporte supera el limite de 20 MiB')
    metadatos = {k: datos[k] for k in ('solicitud_id', 'ejecucion_id', 'ejecucion_bot_id', 'estado', 'resumen')}
    metadatos['export'] = {'nombre': nombre, 'content_type': 'application/zip',
                          'size_bytes': reporte.stat().st_size, 'sha256': sha256(reporte)}
    return reporte, metadatos


SISTEMA_POR_ESTADO = {
    'PENDIENTE': 'EN_PROCESO', 'EN_EJECUCION': 'EN_PROCESO', 'EN_REPROCESO': 'EN_PROCESO',
    'FINALIZADO': 'COMPLETADA', 'FINALIZADO_CON_OBSERVACIONES': 'COMPLETADA',
    'CANCELADO': 'CANCELADA', 'INTERRUMPIDO': 'ERROR', 'ERROR': 'ERROR',
}


# ======================
# FUNCIONES ESTADOS
# ======================
def validar_estado_bot(estado, mensaje, resumen):
    """Valida el código de estado, su mensaje y los valores publicables del resumen."""
    if estado not in SISTEMA_POR_ESTADO:
        raise ValueError('Estado del bot no admitido')
    if not isinstance(mensaje, str) or len(mensaje) > 4000:
        raise ValueError('Mensaje de estado invalido')
    if estado in ('FINALIZADO_CON_OBSERVACIONES', 'INTERRUMPIDO', 'ERROR') and not mensaje.strip():
        raise ValueError('El estado requiere un mensaje explicativo')
    if resumen is not None:
        if not isinstance(resumen, dict) or len(resumen) > 20:
            raise ValueError('Resumen de estado invalido')
        for clave, valor in resumen.items():
            if not isinstance(clave, str) or not clave.strip() or len(clave) > 60:
                raise ValueError('Nombre de dato de resumen invalido')
            if not (type(valor) in (bool, int) or type(valor) is float and math.isfinite(valor)
                    or isinstance(valor, str) and len(valor) <= 200):
                raise ValueError('Valor de resumen invalido')


def validar_confirmacion_estado(datos, identificador, estado):
    """Comprueba que la respuesta confirme el ID y los estados de sistema y bot solicitados."""
    if (type(datos.get('ejecucion_id')) is not int or datos['ejecucion_id'] != identificador
            or datos.get('estado') != SISTEMA_POR_ESTADO[estado] or datos.get('estado_bot') != estado):
        raise crear_error_weyland('Confirmacion de estado incompatible; conservar checkpoint')


def registrar_id_ejecucion_bot(cliente, estado, journal):
    """Publica el identificador de la corrida y guarda la confirmación en el checkpoint."""
    valor = estado.get('id_ejecucion_bot')
    confirmado = estado.get('id_ejecucion_bot_confirmado')
    if valor is None and not confirmado:
        valor = datetime.now().strftime('%d%m%Y_%H%M%S')
    if not isinstance(valor, str) or not re.fullmatch(r'[0-9]{8}_[0-9]{6}', valor):
        raise crear_error_weyland('ID_EJECUCION del checkpoint invalido; requiere revision')
    if estado.get('id_ejecucion_bot_pendiente'):
        raise crear_error_weyland('Registro de ID_EJECUCION incierto; verificar el ID en Weyland antes de continuar')
    if confirmado is True:
        return

    identificador = id_positivo(estado['ejecucion_id'])
    estado.update(id_ejecucion_bot=valor, id_ejecucion_bot_pendiente=True)
    guardar_json(journal, estado)
    try:
        with cliente.post(f'/bot/ejecuciones/{identificador}/id-ejecucion',
                          json={'id_ejecucion': valor}) as respuesta:
            exigir_estado(respuesta)
            datos = leer_json(respuesta)
        if (type(datos.get('ejecucion_id')) is not int or datos['ejecucion_id'] != identificador
                or datos.get('id_ejecucion_bot') != valor):
            raise crear_error_weyland('Confirmacion de ID_EJECUCION incompatible; conservar checkpoint')
    except RuntimeError as error:
        if (es_error(error, 'ErrorWeyland') and error.tipo == 'HTTP'
                and error.status_code in (400, 401, 403, 404, 409, 422)):
            estado.pop('id_ejecucion_bot_pendiente')
            guardar_json(journal, estado)
        raise

    estado['id_ejecucion_bot_confirmado'] = True
    estado.pop('id_ejecucion_bot_pendiente')
    guardar_json(journal, estado)


def publicar_estado(cliente, estado, journal, nuevo, mensaje, resumen=None):
    """Un POST incierto se concilia antes de otra escritura; nunca se repite a ciegas."""
    pendiente = estado.get('estado_bot_pendiente')
    if pendiente:
        detalle = cliente.consultar_ejecucion(estado['ejecucion_id'])
        if (detalle.get('id_externo') != estado['id_externo']
                or detalle.get('estado_bot') != pendiente['estado']
                or detalle.get('estado') != SISTEMA_POR_ESTADO[pendiente['estado']]):
            raise crear_error_weyland('Cambio de estado incierto; requiere revision antes de reenviar')
        estado['estado_bot_publicado'] = pendiente['estado']
        estado.pop('estado_bot_pendiente')
        guardar_json(journal, estado)
    if estado.get('estado_bot_publicado') == nuevo:
        return
    validar_estado_bot(nuevo, mensaje, resumen)
    estado['estado_bot_pendiente'] = {'estado': nuevo, 'mensaje': mensaje, 'resumen': resumen}
    guardar_json(journal, estado)
    try:
        cliente.informar_estado(estado['ejecucion_id'], nuevo, mensaje, resumen)
    except RuntimeError as error:
        if not es_error(error, 'ErrorWeyland'):
            raise
        # Rechazo conocido: no afirmar que el cambio se aplico.
        if error.tipo == 'HTTP' and error.status_code in (400, 401, 403, 404, 409, 422):
            estado.pop('estado_bot_pendiente')
            guardar_json(journal, estado)
        raise
    estado['estado_bot_publicado'] = nuevo
    estado.pop('estado_bot_pendiente')
    guardar_json(journal, estado)


# ======================
# FUNCIONES DE INTEGRIDAD Y EMPAQUETADO DEL RESULTADO
# ======================
def snapshot_resultado(carpeta, resultado):
    """Valida la salida del negocio y registra rutas, tamaños y hashes para recuperarla sin repetirlo."""
    if not es_resultado_bot(resultado) or not isinstance(resultado.resumen, dict):
        raise crear_error_weyland('El adaptador debe devolver crear_resultado_bot')
    if type(resultado.incluir_metadatos_export) is not bool:
        raise crear_error_weyland('incluir_metadatos_export debe ser booleano')
    if any(type(resultado.resumen.get(k)) is not int or resultado.resumen[k] < 0
           for k in ('procesados', 'correctos', 'errores')):
        raise crear_error_weyland('Resumen de negocio incompatible')
    if not resultado.archivos and resultado.permite_sin_export is not True:
        raise crear_error_weyland('El proceso termino sin su export obligatorio')
    validar_datos_publicables(resultado.detalles)
    json.dumps(resultado.detalles, allow_nan=False)
    archivos = []
    for origen in resultado.archivos:
        origen = Path(origen)
        if origen.is_symlink():
            raise crear_error_weyland('No se exportan enlaces simbolicos')
        try:
            relativo = origen.relative_to(carpeta) if origen.is_absolute() else origen
        except ValueError:
            raise crear_error_weyland('Resultado fuera de la ejecucion') from None
        archivo = confinar(carpeta, relativo)
        if (not archivo.is_file() or any(p in (CARPETA_INPUT, CARPETA_PARAMETROS, CARPETA_LOGS) for p in relativo.parts)
                or archivo.name.startswith('.env') or archivo.name in (ARCHIVO_CONTEXTO, ARCHIVO_FINALIZACION)
                or archivo.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.log', '.pem', '.key')):
            raise crear_error_weyland('Archivo de resultado no permitido')
        archivos.append({'ruta': relativo.as_posix(), 'sha256': sha256(archivo), 'size_bytes': archivo.stat().st_size})
    return {'archivos': archivos, 'resumen': resultado.resumen, 'detalles': resultado.detalles,
            'permite_sin_export': resultado.permite_sin_export,
            'incluir_metadatos_export': resultado.incluir_metadatos_export}


def empaquetar_resultado(carpeta, estado):
    """Verifica los archivos guardados y prepara una entrega ZIP o JSON con su huella de integridad."""
    guardado = estado['resultado']
    archivos = []
    for info in guardado['archivos']:
        archivo = confinar(carpeta, info['ruta'])
        if archivo.stat().st_size != info['size_bytes'] or sha256(archivo) != info['sha256']:
            raise crear_error_weyland('Un resultado cambio despues del negocio; requiere revision')
        archivos.append(archivo)
    resultado = crear_resultado_bot(tuple(archivos), guardado['resumen'],
                             {**guardado['detalles'], 'id_externo': estado['id_externo']}, guardado['permite_sin_export'],
                             guardado.get('incluir_metadatos_export', True))
    reporte, metadatos = exportar(carpeta, resultado, estado)
    if reporte is None:
        reporte = carpeta / ('resultado_' + datetime.now().strftime(FORMATO_FECHA_ARCHIVO) + '.json')
        guardar_json(reporte, {'ejecucion_id': estado['ejecucion_id'], 'id_externo': estado['id_externo'],
            'proceso_codigo': estado['trabajo']['proceso_codigo'], 'estado_negocio': 'COMPLETADO',
            'tiene_export_negocio': False, 'resumen': resultado.resumen, 'resultado': resultado.detalles})
    if not 1 <= reporte.stat().st_size <= MAX_BYTES:
        raise crear_error_weyland('Resultado fuera del limite de entrega')
    return {'ruta': reporte.relative_to(carpeta).as_posix(), 'size_bytes': reporte.stat().st_size,
            'sha256': sha256(reporte)}


# ======================
# CONTRATO DEL PROCESO
# ======================
def crear_proceso_bot(ejecutar, requiere_input=None, extensiones=EXTENSIONES_INPUT_POR_DEFECTO,
                       informar_estados=False):
    """Declara la función de negocio y sus requisitos de entrada."""

    return SimpleNamespace(ejecutar=ejecutar, requiere_input=requiere_input,
                           extensiones=extensiones, informar_estados=informar_estados)


def es_proceso_bot(proceso):
    """Comprueba la estructura del adaptador antes de tomar una solicitud."""

    return isinstance(proceso, SimpleNamespace) and vars(proceso).keys() == {
        'ejecutar', 'requiere_input', 'extensiones', 'informar_estados',
    }


FASES = {'TOMA_INCIERTA', 'TOMA_RECHAZADA', 'SIN_TRABAJO', 'ASIGNACION_INCOMPATIBLE', 'DESCARGANDO',
         'PREPARADO', 'EJECUTANDO', 'EXPORT_PENDIENTE', 'ENTREGA_LISTA', 'ENTREGA_INCIERTA',
         'ERROR_LISTO', 'ERROR_INCIERTO', 'COMPLETADA', 'ERROR', 'CANCELADO', 'INTERRUMPIDO'}


FINALES = {'COMPLETADA', 'ERROR', 'SIN_TRABAJO', 'CANCELADO', 'INTERRUMPIDO', 'TOMA_RECHAZADA'}


# ======================
# FUNCIONES DE CHECKPOINT Y CONTEXTO DE ERROR
# ======================
def leer(ruta):
    """Lee un checkpoint y detiene el flujo si es ilegible o no contiene un objeto JSON."""
    try:
        datos = json.loads(ruta.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise crear_error_weyland('Checkpoint ilegible; no se sobrescribira ni se tomara otro trabajo') from None
    if not isinstance(datos, dict):
        raise crear_error_weyland('Checkpoint incompatible')
    return datos


@contextmanager
def contexto_de_error():
    """Añade fase e identificadores de trazabilidad a la excepción antes de propagarla."""
    referencia = {}
    try:
        yield referencia
    except Exception as error:
        estado = referencia.get('estado') or {}
        error.fase_weyland = estado.get('fase')
        if estado.get('id_externo') and not hasattr(error, 'contexto_trazabilidad'):
            error.contexto_trazabilidad = {'solicitud_id': estado.get('ejecucion_id'),
                'ejecucion_id_web': estado.get('ejecucion_id'), 'activacion_id': estado['id_externo']}
        raise


def validar_checkpoint(estado, identidad):
    """Comprueba backend, máquina y proceso, incluida la migración de la sesión anterior."""
    if 'autenticacion_bot' not in estado:
        huella_anterior = estado.get('cuenta_sha256')
        if isinstance(huella_anterior, str) and re.fullmatch(r'[0-9a-f]{64}', huella_anterior):
            identidad = {k: v for k, v in identidad.items() if k != 'autenticacion_bot'}
    if (any(estado.get(k) != v for k, v in identidad.items()) or estado.get('fase') not in FASES
            or not isinstance(estado.get('id_externo'), str)
            or len(estado['id_externo']) != 32
            or any(c not in '0123456789abcdef' for c in estado['id_externo'])):
        raise crear_error_weyland('Checkpoint de otro backend, VM, contrato o proceso; requiere revision')
    if estado['fase'] not in ('TOMA_INCIERTA', 'TOMA_RECHAZADA', 'SIN_TRABAJO', 'ASIGNACION_INCOMPATIBLE'):
        id_positivo(estado.get('ejecucion_id'))
        if estado.get('solicitud_id') != estado['ejecucion_id']:
            raise crear_error_weyland('IDs incompatibles en el checkpoint')


# ======================
# FUNCIONES DE POLÍTICA DE CORREOS
# ======================
def correo_permitido(estado):
    """La politica de correo se congela al tomar el trabajo, incluidos reintentos.

    Un journal anterior sin la bandera no demuestra autorizacion web: no enviar
    automaticamente sus correos. None explicito significa que la web delega
    en la configuracion local. La entrega del resultado web nunca depende de esto.
    """
    trabajo = estado.get('trabajo')
    if not isinstance(trabajo, dict):
        return False
    valor = trabajo.get('notificar_correo', False)
    return valor is None or valor is True


def avisar(notificar, evento, estado, carpeta):
    """Autoriza el aviso inicial y entrega al adaptador el resultado del cierre web."""
    if notificar is not None and (evento != 'inicio' or correo_permitido(estado)):
        try:
            notificar(evento, dict(estado), carpeta)
        except Exception:
            if evento == 'inicio':
                raise
            # Un correo final pendiente no repite negocio ni entrega web.
            pass


# ======================
# FUNCIONES DE ASIGNACIÓN Y RECUPERACIÓN DE SOLICITUDES
# ======================
def asignar(cliente, datos, estado, procesos, base, journal):
    """Valida la solicitud recibida, prepara su carpeta y persiste el contexto y el checkpoint."""
    try:
        trabajo = cliente.validar_asignacion(datos, estado['maquina_id'], estado['id_externo'], procesos)
        identificador = trabajo['ejecucion_id']
        carpeta = confinar(base, CARPETA_EJECUCIONES + '/' + str(identificador))
        contexto = {'solicitud_id': identificador, 'ejecucion_id_web': identificador,
                    'activacion_id': estado['id_externo']}
        if carpeta.exists() and any(carpeta.iterdir()):
            ruta = carpeta / ARCHIVO_CONTEXTO
            if not ruta.is_file() or leer(ruta) != contexto:
                raise crear_error_weyland('El ID ya tiene evidencia de otra activacion; no se repetira')
        carpeta.mkdir(parents=True, exist_ok=True)
        guardar_json(carpeta / ARCHIVO_CONTEXTO, contexto)
        estado.update(trabajo=trabajo, ejecucion_id=identificador, solicitud_id=identificador,
                      ejecucion_bot_id=estado['id_externo'], fase='DESCARGANDO',
                      informar_estados=procesos[trabajo['proceso_codigo']].informar_estados)
    except (RuntimeError, ValueError, TypeError) as error:
        if not (es_error(error, 'ErrorWeyland') or isinstance(error, (ValueError, TypeError))):
            raise
        estado['fase'] = 'ASIGNACION_INCOMPATIBLE'
        guardar_json(journal, estado)
        raise crear_error_weyland('La asignacion ya pudo ocurrir pero es incompatible; revisar la web, no tomar otra',
                           tipo='ASIGNACION_INCOMPATIBLE') from None
    guardar_json(journal, estado)


def comprobar_detalle(datos, estado):
    """Verifica que el detalle remoto coincida con el ID, correlación, proceso y máquina locales."""
    if (datos.get('id') != estado['ejecucion_id'] or datos.get('id_externo') != estado['id_externo']
            or not isinstance(datos.get('proceso'), dict)
            or datos['proceso'].get('codigo') != estado['trabajo']['proceso_codigo']
            or not isinstance(datos.get('maquina'), dict)
            or datos['maquina'].get('id') != estado['maquina_id']):
        raise crear_error_weyland('La conciliacion no coincide con la asignacion local')


def recuperar_toma(cliente, estado):
    """Reconstruye una asignación incierta y sus inputs a partir de un detalle remoto inequívoco."""
    detalle = cliente.buscar_correlacion(estado['maquina_id'], estado['id_externo'])
    if detalle.get('estado') != 'EN_PROCESO':
        raise crear_error_weyland('Toma incierta en otro estado; requiere revision, no se repetira siguiente')
    archivos = detalle.get('archivos')
    if not isinstance(archivos, list):
        raise crear_error_weyland('Detalle sin archivos compatibles para recuperar la toma')
    entradas = [a for a in archivos if isinstance(a, dict) and a.get('tipo') == 'INPUT']
    identificador = id_positivo(detalle.get('id'))
    trabajo = {'ejecucion_id': identificador, 'id_externo': detalle.get('id_externo'),
            # El detalle de usuario no devuelve la configuracion de habilitacion.
            # Sin prueba de esa politica no enviar correos, aunque el negocio retome.
            'configuracion': {'notificar_correo': False},
            'maquina': detalle.get('maquina'), 'proceso': detalle.get('proceso'),
            'reproceso_de': detalle.get('reproceso_de'),
            'parametros': detalle.get('parametros'),
            'input': {'nombre': entradas[0].get('nombre'),
                      'descarga': RUTA_BOT_INPUT_API.format(identificador=identificador)} if entradas else None}

    if len(entradas) > 1:
        trabajo['input'] = None
        trabajo['inputs'] = [{'archivo_id': id_positivo(e.get('id')), 'nombre': e.get('nombre'),
            'descarga': RUTA_BOT_INPUT_API.format(identificador=identificador) +
                        '?archivo_id=' + str(id_positivo(e.get('id')))} for e in entradas]
    return trabajo


# ======================
# FUNCIONES DE DESCARGA Y VALIDACIÓN DE ENTRADAS
# ======================
def preparar_entradas(cliente, estado, carpeta, journal, proceso):
    """Descarga todos los inputs y persiste cada huella sin sobrescribir archivos de igual nombre."""
    trabajo = estado['trabajo']
    entradas = trabajo.get('inputs')
    if entradas is None:
        entradas = [trabajo['input']] if trabajo['input'] else []
    if (proceso.requiere_input is True and not entradas
            or proceso.requiere_input is False and entradas):
        raise ValueError('Modalidad de input no admitida')
    for info in entradas:
        if Path(info['nombre']).suffix.lower() not in proceso.extensiones:
            raise ValueError('Extension de entrada no admitida')
    descargados = estado.setdefault('inputs_descargados', [])
    if len(descargados) > len(entradas):
        raise crear_error_weyland('Checkpoint con inputs incompatibles')
    total = 0
    for numero, info in enumerate(entradas):
        extension = Path(info['nombre']).suffix.lower()
        archivo_id = info.get('archivo_id')
        nombre = (RUTA_ENTRADA_BASE + extension if len(entradas) == 1 and archivo_id is None
                  else RUTA_ENTRADA_BASE + f'_{numero + 1:06d}_{id_positivo(archivo_id)}' + extension)
        destino = confinar(carpeta, nombre)
        if numero < len(descargados):
            guardado = descargados[numero]
            if (guardado.get('ruta') != nombre or guardado.get('archivo_id') != archivo_id
                    or destino.is_symlink() or not destino.is_file()
                    or destino.stat().st_size != guardado['size_bytes']
                    or sha256(destino) != guardado['sha256']):
                raise crear_error_weyland('Un input descargado cambio; requiere revision')
        else:
            if total >= BACKEND_MAX_INPUTS_BYTES:
                raise ValueError('Los inputs superan el limite total de descarga')
            opciones = {'archivo_id': archivo_id} if archivo_id is not None else {}
            snapshot = cliente.descargar_input(estado['ejecucion_id'], destino, **opciones)
            guardado = {'ruta': nombre, 'archivo_id': archivo_id, **snapshot}
            descargados.append(guardado)
            guardar_json(journal, estado)
        total += guardado['size_bytes']
        if total > BACKEND_MAX_INPUTS_BYTES:
            raise ValueError('Los inputs superan el limite total de descarga')
    estado['entrada'] = descargados[0]['ruta'] if len(descargados) == 1 else None
    if len(descargados) == 1:
        estado['snapshot_input'] = {k: descargados[0][k] for k in ('size_bytes', 'sha256')}
    else:
        estado.pop('snapshot_input', None)


def entradas_preparadas(carpeta, estado):
    """Verifica todas las entradas antes del negocio y admite checkpoints anteriores de un solo input."""
    registros = estado.get('inputs_descargados')
    if registros is None:
        registros = ([{'ruta': estado['entrada'], **estado['snapshot_input']}]
                     if estado['entrada'] else [])
    else:
        esperadas = estado['trabajo'].get('inputs')
        if esperadas is None:
            esperadas = [estado['trabajo']['input']] if estado['trabajo']['input'] else []
        if len(registros) != len(esperadas):
            raise crear_error_weyland('No estan preparados todos los inputs de la solicitud')
    rutas, total = [], 0
    for registro in registros:
        ruta = confinar(carpeta, registro['ruta'])
        if (not ruta.is_relative_to(confinar(carpeta, RUTA_ENTRADA_BASE).parent)
                or ruta.is_symlink() or not ruta.is_file()
                or ruta.stat().st_size != registro['size_bytes'] or sha256(ruta) != registro['sha256']):
            raise crear_error_weyland('Input alterado o no permitido')
        total += registro['size_bytes']
        if total > BACKEND_MAX_INPUTS_BYTES:
            raise crear_error_weyland('Los inputs preparados superan el limite total')
        rutas.append(ruta)
    return rutas[0] if len(rutas) == 1 else tuple(rutas) if rutas else None


# ======================
# FUNCIONES DE CONCILIACIÓN DE ENTREGAS
# ======================
def conciliar(cliente, estado, carpeta):
    """Confirma el cierre remoto y la huella del reporte antes de resolver una entrega incierta."""
    detalle = cliente.consultar_ejecucion(estado['ejecucion_id'])
    comprobar_detalle(detalle, estado)
    if estado.get('informar_estados') and detalle.get('estado_bot') in ('CANCELADO', 'INTERRUMPIDO'):
        final = detalle['estado_bot']
        esperado = 'CANCELADA' if final == 'CANCELADO' else 'ERROR'
        if detalle.get('estado') != esperado:
            raise crear_error_weyland('Cierre del bot incompatible con el estado del sistema')
        estado.update(fase=final, estado_bot_publicado=final)
        estado.pop('estado_bot_pendiente', None)
        return
    if estado['fase'] == 'ERROR_INCIERTO':
        if detalle.get('estado') != 'ERROR':
            raise crear_error_weyland('Aviso de error incierto; requiere revision antes de reenviar')
        estado['fase'] = 'ERROR'
        if estado.get('informar_estados'):
            if detalle.get('estado_bot') != 'ERROR':
                raise crear_error_weyland('No se confirmo el estado de error del bot')
            estado['estado_bot_publicado'] = 'ERROR'
            estado.pop('estado_bot_pendiente', None)
        return
    if detalle.get('estado') != 'COMPLETADA':
        raise crear_error_weyland('Entrega incierta: el estado no prueba que pueda reenviarse; requiere revision')
    if estado.get('informar_estados') and detalle.get('estado_bot') != estado.get('estado_bot_objetivo'):
        raise crear_error_weyland('El estado final web no coincide con la entrega local')
    archivos = detalle.get('archivos')
    reportes = [a for a in archivos if isinstance(a, dict) and a.get('tipo') == 'REPORTE'] if isinstance(archivos, list) else []
    if len(reportes) != 1:
        raise crear_error_weyland('Reporte de conciliacion ausente o ambiguo')
    snapshot = cliente.descargar_reporte_confirmado(estado['ejecucion_id'], reportes[0]['id'],
                                                   carpeta / 'verificacion_backend.bin')
    if snapshot != {k: estado['entrega'][k] for k in ('size_bytes', 'sha256')}:
        raise crear_error_weyland('El reporte web no coincide con la entrega local; requiere revision')
    estado['fase'] = 'COMPLETADA'
    if estado.get('informar_estados'):
        estado['estado_bot_publicado'] = detalle['estado_bot']


# ======================
# FUNCIONES DE ORQUESTACIÓN DE LA COLA
# ======================
def ejecutar_cola(cliente, maquina_id, trabajos, procesos, *, notificar=None):
    """Ejecuta el flujo y prepara el aviso de error ante fallos transitorios, conservando el checkpoint."""
    try:
        return procesar_solicitud_pendiente(cliente, maquina_id, trabajos, procesos, notificar=notificar)
    except (RuntimeError, OSError) as error:
        if not (es_error(error, 'ErrorWeyland') or isinstance(error, OSError)):
            raise
        if es_error(error, 'ErrorWeyland') and error.tipo not in ('RED', 'HTTP'):
            raise
        temporal = (not es_error(error, 'ErrorWeyland') or error.tipo == 'RED'
                    or error.status_code in (408, 429) or error.status_code >= 500)
        journal = Path(trabajos) / ARCHIVO_COORDINADOR
        if temporal and journal.is_file():
            with bloqueo(Path(trabajos) / ARCHIVO_BLOQUEO):
                estado = leer(journal)
                if (estado.get('informar_estados') and estado.get('ejecucion_id')
                        and not estado.get('id_ejecucion_bot_pendiente')
                        and estado['fase'] not in FINALES | {'ENTREGA_INCIERTA', 'ERROR_INCIERTO'}):
                    estado.update(fase='ERROR_LISTO', estado_final_pendiente='ERROR',
                        mensaje_error='Problema de comunicacion o disponibilidad; no se completo la operacion.')
                    guardar_json(journal, estado)
        raise


def procesar_solicitud_pendiente(cliente, maquina_id, trabajos, procesos, *, notificar=None):
    """Una invocacion toma como maximo un trabajo; los POST nunca se repiten solos."""
    id_positivo(maquina_id)
    if not procesos or any(not isinstance(k, str) or not k.strip() or not es_proceso_bot(p)
                          or not callable(p.ejecutar) or p.requiere_input is not None and type(p.requiere_input) is not bool
                          or type(p.informar_estados) is not bool for k, p in procesos.items()):
        raise ValueError('Configurar una tabla explicita de procesos y adaptadores')
    ruta_base = Path(trabajos)
    if ruta_base.is_symlink():
        raise crear_error_weyland('Directorio operativo no permitido')
    base = ruta_base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    identidad = {'version': 1, 'contrato': 'cola_actual', 'backend': cliente.config['url'],
                 'autenticacion_bot': 'api_key',
                 'maquina_id': maquina_id, 'codigos': sorted(procesos)}
    journal = base / ARCHIVO_COORDINADOR
    with bloqueo(base / ARCHIVO_BLOQUEO), contexto_de_error() as referencia:
        estado = leer(journal) if journal.exists() else None
        referencia['estado'] = estado
        if estado is not None:
            validar_checkpoint(estado, identidad)
            if 'autenticacion_bot' not in estado:
                estado.pop('cuenta_sha256')
                estado['autenticacion_bot'] = identidad['autenticacion_bot']
                guardar_json(journal, estado)
            if estado['fase'] in ('COMPLETADA', 'ERROR', 'CANCELADO', 'INTERRUMPIDO'):
                avisar(notificar, 'resultados' if estado['fase'] == 'COMPLETADA' else 'fallas', estado,
                        confinar(base, CARPETA_EJECUCIONES + '/' + str(id_positivo(estado['ejecucion_id']))))
        if estado is None or estado['fase'] in FINALES:
            estado = {**identidad, 'id_externo': uuid4().hex, 'fase': 'TOMA_INCIERTA'}
            referencia['estado'] = estado
            guardar_json(journal, estado)
            # Guardado antes del POST. Una caída desde aquí exige conciliación.
            try:
                datos = cliente.tomar_siguiente(maquina_id, estado['id_externo'])
            except RuntimeError as error:
                if not es_error(error, 'ErrorWeyland'):
                    raise
                if error.tipo == 'HTTP' and error.status_code in (401, 403):
                    # El backend rechaza autenticación/permisos antes del handler.
                    # Es una toma no aplicada, distinta de un timeout/5xx incierto.
                    estado.update(fase='TOMA_RECHAZADA', rechazo_http=error.status_code)
                    guardar_json(journal, estado)
                raise
            if datos is None:
                estado['fase'] = 'SIN_TRABAJO'
                guardar_json(journal, estado)
                return estado
            asignar(cliente, datos, estado, procesos, base, journal)
        elif estado['fase'] == 'TOMA_INCIERTA':
            asignar(cliente, recuperar_toma(cliente, estado), estado, procesos, base, journal)
        if estado['fase'] == 'ASIGNACION_INCOMPATIBLE':
            raise crear_error_weyland('Asignacion incompatible pendiente de revision; no se tomara otro trabajo',
                               tipo='ASIGNACION_INCOMPATIBLE')
        carpeta = confinar(base, CARPETA_EJECUCIONES + '/' + str(id_positivo(estado['ejecucion_id'])))
        if estado['fase'] in ('ENTREGA_INCIERTA', 'ERROR_INCIERTO'):
            conciliar(cliente, estado, carpeta)
            guardar_json(journal, estado)
        if estado['fase'] == 'EJECUTANDO':
            if not estado.get('informar_estados'):
                raise crear_error_weyland('Negocio interrumpido; conserva su evidencia y requiere revision, no se repetira')
            estado.update(fase='ERROR_LISTO', estado_final_pendiente='INTERRUMPIDO',
                mensaje_error='Se detecto una interrupcion de la ejecucion anterior; no se repetira el negocio.')
            guardar_json(journal, estado)
        proceso = procesos[estado['trabajo']['proceso_codigo']]
        if estado.get('informar_estados') and estado['fase'] == 'DESCARGANDO':
            publicar_estado(cliente, estado, journal, 'PENDIENTE', 'Preparando el trabajo')
        if estado['fase'] in ('DESCARGANDO', 'PREPARADO'):
            try:
                avisar(notificar, 'inicio', estado, carpeta)
            except Exception:
                estado.update(fase='ERROR_LISTO', estado_final_pendiente='ERROR',
                    mensaje_error='No se pudo confirmar el correo inicial; no se inició el negocio.')
                guardar_json(journal, estado)
        if estado['fase'] == 'DESCARGANDO':
            try:
                preparar_entradas(cliente, estado, carpeta, journal, proceso)
                estado['fase'] = 'PREPARADO'
            except KeyboardInterrupt:
                if not estado.get('informar_estados'):
                    raise
                estado.update(fase='ERROR_LISTO', estado_final_pendiente='CANCELADO',
                    mensaje_error='El operador cancelo la ejecucion durante la preparacion.')
            except OSError:
                raise
            except RuntimeError as error:
                if not es_error(error, 'ErrorWeyland'):
                    raise
                if error.tipo == 'RED' or (error.tipo == 'HTTP' and (
                        error.status_code in (408, 429) or error.status_code >= 500)):
                    raise
                mensaje = ('No se pudo obtener un input valido para la ejecucion.' if error.tipo == 'HTTP'
                           else 'La entrada de la ejecucion es incompatible o invalida.')
                estado.update(fase='ERROR_LISTO', etapa_error='input', mensaje_error=mensaje)
            except ValueError:
                estado.update(fase='ERROR_LISTO', etapa_error='input', mensaje_error='La entrada de la ejecucion es incompatible o invalida.')
            guardar_json(journal, estado)
        if estado['fase'] == 'PREPARADO':
            registrar_id_ejecucion_bot(cliente, estado, journal)
            if estado.get('informar_estados'):
                reproceso = estado['trabajo'].get('reproceso_de')
                publicar_estado(cliente, estado, journal, 'EN_REPROCESO' if reproceso else 'EN_EJECUCION',
                    'Reprocesando una solicitud anterior' if reproceso else 'Ejecutando el trabajo asignado')
            estado['fase'] = 'EJECUTANDO'
            guardar_json(journal, estado)
            try:
                entrada = entradas_preparadas(carpeta, estado)
                configurar_monitor(estado['trabajo'])
                resultado = proceso.ejecutar(carpeta, entrada, obtener_parametros_monitor())
                estado.update(resultado=snapshot_resultado(carpeta, resultado), fase='EXPORT_PENDIENTE')
            except KeyboardInterrupt:
                if not estado.get('informar_estados'):
                    raise
                estado.update(fase='ERROR_LISTO', estado_final_pendiente='CANCELADO',
                    mensaje_error='El operador cancelo la ejecucion.')
            except Exception as error:
                final = getattr(error, 'estado_bot', 'ERROR')
                if final not in ('ERROR', 'INTERRUMPIDO', 'CANCELADO'):
                    final = 'ERROR'
                estado.update(fase='ERROR_LISTO', etapa_error='procesamiento', estado_final_pendiente=final,
                    resumen_error=getattr(error, 'resumen_publico', None),
                    mensaje_error='El proceso se interrumpio antes de finalizar.' if final == 'INTERRUMPIDO'
                    else 'El proceso no pudo completarse; revisar su evidencia privada.')
            guardar_json(journal, estado)
        if estado['fase'] == 'EXPORT_PENDIENTE':
            estado.update(entrega=empaquetar_resultado(carpeta, estado), fase='ENTREGA_LISTA')
            guardar_json(journal, estado)
        if estado['fase'] == 'ENTREGA_LISTA':
            entrega = estado['entrega']
            reporte = confinar(carpeta, entrega['ruta'])
            if sha256(reporte) != entrega['sha256'] or reporte.stat().st_size != entrega['size_bytes']:
                raise crear_error_weyland('La entrega cambio; requiere revision')
            opciones = {}
            if estado.get('informar_estados'):
                resumen = estado['resultado']['resumen']
                observaciones = resumen.get('observaciones', 0)
                final = 'FINALIZADO_CON_OBSERVACIONES' if observaciones else 'FINALIZADO'
                estado['estado_bot_objetivo'] = final
                opciones = {'estado_bot': final, 'resumen': resumen,
                    'mensaje': f'Finalizado con {observaciones} consultas con observaciones; ver el Excel.'
                    if observaciones else 'Proceso finalizado correctamente.'}
            estado['fase'] = 'ENTREGA_INCIERTA'
            guardar_json(journal, estado)
            cliente.entregar_resultado(estado['ejecucion_id'], reporte, entrega['sha256'], entrega['size_bytes'], **opciones)
            estado['fase'] = 'COMPLETADA'
            if estado.get('informar_estados'):
                estado['estado_bot_publicado'] = estado['estado_bot_objetivo']
            guardar_json(journal, estado)
        elif estado['fase'] == 'ERROR_LISTO':
            estado['fase'] = 'ERROR_INCIERTO'
            guardar_json(journal, estado)
            if estado.get('informar_estados'):
                final = estado.get('estado_final_pendiente', 'ERROR')
                publicar_estado(cliente, estado, journal, final, estado['mensaje_error'], estado.get('resumen_error'))
                estado['fase'] = final
            else:
                cliente.informar_error(estado['ejecucion_id'], estado['mensaje_error'])
                estado['fase'] = 'ERROR'
            guardar_json(journal, estado)
        if estado['fase'] not in ('COMPLETADA', 'ERROR', 'CANCELADO', 'INTERRUMPIDO'):
            raise crear_error_weyland('Fase inesperada; no se repetira el proceso')
        guardar_json(carpeta / ARCHIVO_FINALIZACION, {'fase': estado['fase'], 'ejecucion_id': estado['ejecucion_id'],
                                                   'id_externo': estado['id_externo']})
        avisar(notificar, 'resultados' if estado['fase'] == 'COMPLETADA' else 'fallas', estado, carpeta)
        return estado


# ======================
# FUNCIONES DE MIGRACIÓN Y ARRANQUE CONFIGURADO
# ======================
def verificar_migracion(base):
    """No ocultar trabajos del contrato anterior al estrenar un namespace."""
    for ruta in base.rglob(ARCHIVO_ESTADO):
        try:
            datos = json.loads(ruta.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise crear_error_weyland('Hay un journal anterior ilegible; revisar antes de migrar') from None
        if not isinstance(datos, dict) or datos.get('fase') not in ('COMPLETADA', 'ERROR'):
            raise crear_error_weyland('Hay una ejecucion pendiente del contrato anterior; revisar antes de migrar')
    for ruta in base.rglob(ARCHIVO_ACTIVO):
        raise crear_error_weyland('Existe un journal heredado; revisar antes de habilitar cola_actual')


def ejecutar_cola_configurada(fabrica, valores, raiz, procesos, *, notificar=None, revisar_trabajos=None):
    """Valida contrato y rutas, autentica el cliente y coordina una solicitud con la configuración recibida."""
    if valores.get('BACKEND_CONTRATO') != 'cola_actual' or valores.get('BACKEND_AUTH_MODE') != 'weyland':
        raise ValueError('Configurar BACKEND_CONTRATO=cola_actual y BACKEND_AUTH_MODE=weyland')
    if str(valores.get('BACKEND_REUTILIZAR_ULTIMA') or 'false').lower() != 'false':
        raise ValueError('No se reutilizan solicitudes anteriores')
    raiz = Path(raiz).resolve()
    ruta = Path(valores.get('BACKEND_TRABAJOS_DIR') or 'temporales/weyland')
    base = (ruta if ruta.is_absolute() else raiz / ruta).resolve()
    if str(valores.get('ENTORNO') or '').upper() == 'PRODUCCION' and base.is_relative_to(raiz):
        raise ValueError('En produccion BACKEND_TRABAJOS_DIR debe estar fuera del repositorio')
    verificar_migracion(base)
    cliente = fabrica()
    if getattr(cliente, 'contrato', None) != 'cola_actual':
        raise crear_error_weyland('La fabrica no corresponde al contrato cola_actual')
    with sesion_cliente(cliente):
        if cliente.config['bot_auth_mode'] != 'jwt':
            raise crear_error_weyland('Se requiere autenticacion JWT antes de tomar trabajo')
        cliente.verificar_ipv4()
        maquina = id_positivo(cliente.verificar())
        trabajos = base / 'cola_actual' / f'maquina_{maquina}'
        if revisar_trabajos is not None:
            revisar_trabajos(trabajos)
        return ejecutar_cola(cliente, maquina, trabajos,
                              procesos, notificar=notificar)
