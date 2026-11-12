"""Configuración central, credenciales, trazabilidad, logs y validaciones."""

import ipaddress
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from config.archivos import ARCHIVO_ENV, RUTA_PROYECTO
from config.base_datos import configurar_base_datos
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from uuid import uuid4


CAMPOS = {
    'BACKEND_EMAIL': ('AUTOMATE', 'USER'),
    'BACKEND_PASSWORD': ('AUTOMATE', 'PWD'),
    'BACKEND_BOT_API_KEY': ('AUTOMATE', 'API_KEY'),
    'EMAIL_REMITENTE': ('SMTP', 'USER'),
    'EMAIL_PASSWORD': ('SMTP', 'PWD'),
    'MONITOR_USER': ('MONITOR', 'USER'),
    'MONITOR_PASSWORD': ('MONITOR', 'PWD'),
    'DB_USER': ('SQL', 'USER'),
    'DB_PASSWORD': ('SQL', 'PWD'),
}


IDENTIDAD = ('CREDENCIALES_EMPRESA', 'CREDENCIALES_VM')


def nombres_estandar(valores):
    """Calcula los nombres de credenciales según la empresa y la máquina configuradas."""
    empresa = str(valores.get('CREDENCIALES_EMPRESA') or '').strip().upper()
    vm = str(valores.get('CREDENCIALES_VM') or '').strip().upper()
    if not empresa and not vm:
        return {}
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*', empresa) or not re.fullmatch(r'MV[A-Z0-9]+', vm):
        raise ValueError('Configurar CREDENCIALES_EMPRESA y CREDENCIALES_VM con identificadores validos')
    prefijo = 'WEYLAND' if empresa == 'WEYLAND' else 'WEYLAND_' + empresa
    return {interno: f'{prefijo}_{sistema}_{vm}_{tipo}'
            for interno, (sistema, tipo) in CAMPOS.items()}


def normalizar_credenciales(valores):
    """Acepta nombres anteriores; alias contradictorios fallan sin exponer secretos."""
    datos = {}
    for nombre, valor in valores.items():
        clave = nombre.upper()
        if clave in datos and datos[clave] != valor and clave in CAMPOS:
            raise ValueError('Nombres duplicados de credencial: ' + clave)
        datos[clave] = valor
    for interno, nuevo in nombres_estandar(datos).items():
        if nuevo not in datos:
            continue
        anterior, actual = datos.get(interno), datos.pop(nuevo)
        if anterior not in (None, '') and actual not in (None, '') and anterior != actual:
            raise ValueError(f'Credenciales incompatibles entre {interno} y {nuevo}')
        datos[interno] = actual if actual not in (None, '') else anterior
    return datos


def combinar_configuracion(archivo, entorno):
    """La inyeccion por entorno prevalece, tambien al cambiar de nombre de variable."""
    archivo = {k.upper(): v for k, v in archivo.items()}
    entorno = {k.upper(): v for k, v in entorno.items()}
    identidad = {k: entorno.get(k, archivo.get(k)) for k in IDENTIDAD}
    return {**normalizar_credenciales({**archivo, **identidad}),
            **normalizar_credenciales({**entorno, **identidad})}


def leer_credencial(nombre):
    """Retorna el valor literal; los passwords nunca se recortan."""
    return normalizar_credenciales(os.environ).get(nombre.upper())


_contexto = ContextVar('contexto_bot', default=None)


def actual():
    """Recupera el contexto vigente o construye sus identificadores desde el entorno."""
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
    """Valida y activa la trazabilidad temporal, restaura la anterior y enriquece las excepciones."""
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
    """Devuelve la trazabilidad actual junto con la fecha UTC de generación."""
    return {**actual(), 'generado_en': datetime.now(timezone.utc).isoformat()}


load_dotenv(ARCHIVO_ENV)


def _resolver_ruta_base(valor):
    """Resuelve la raíz operativa absoluta usando la carpeta del proyecto cuando corresponde."""
    valor =(valor or "").strip()
    if not valor:
        return RUTA_PROYECTO
    ruta = Path(valor)
    if not ruta.is_absolute():
        ruta = RUTA_PROYECTO / ruta
    return ruta.resolve()


RUTA_BASE = _resolver_ruta_base(os.getenv("RUTA_BASE"))
RUTA_CONFIG = RUTA_PROYECTO / "config"
RUTA_LOGS = RUTA_BASE / "logs"
RUTA_RECURSOS = RUTA_BASE / "recursos"
RUTA_IMAGENES = RUTA_RECURSOS / "imagenes"
RUTA_TEMPLATES = RUTA_RECURSOS / "templates"
RUTA_PROCESADOS = RUTA_BASE / "procesados"
RUTA_REPORTES = RUTA_BASE / "reportes"
RUTA_TEMPORALES = RUTA_BASE / "temporales"


def obtener_variable_opcional(nombre, defecto=None):
    """Lee una variable o credencial normalizada y utiliza el valor predeterminado si está vacía."""
    valor = (leer_credencial(nombre) if nombre.upper() in CAMPOS
             else os.getenv(nombre.upper(), os.getenv(nombre)))
    if valor is None or not str(valor).strip():
        return defecto
    return str(valor).strip()


def obtener_variable_obligatoria(nombre):
    """Exige que exista un valor de configuración no vacío."""
    valor = obtener_variable_opcional(nombre)
    if valor is None:
        raise RuntimeError(f"falta variable obligatoria: {nombre}")
    return valor


def obtener_entero(nombre, defecto=None):
    """Convierte una variable a entero y explica cualquier valor inválido."""
    valor = obtener_variable_opcional(nombre)
    if valor is None:
        return defecto
    try:
        return int(valor)
    except ValueError:
        raise RuntimeError(f"Valor invalido para {nombre} : {valor}. Debe ser un numero entero")


def obtener_entero_positivo(nombre, defecto=None):
    """Exige un entero configurado mayor que cero."""
    valor = obtener_entero(nombre, defecto)
    if valor is None:
        raise RuntimeError(f"falta variable obligatoria: {nombre}")
    if valor <= 0:
        raise RuntimeError(f"Valor invalido para {nombre}, debe ser un valor positivo")
    return valor


def obtener_decimal(nombre, defecto=None):
    """Convierte una variable a decimal conservando su valor predeterminado."""
    valor = obtener_variable_opcional(nombre)
    if valor is None:
        return defecto
    try:
        return float(valor)
    except ValueError:
        raise RuntimeError(f"Valor invalido para {nombre}: {valor} debe ser un numero.")


def obtener_booleano(nombre, defecto=False):
    """Interpreta los valores booleanos admitidos y rechaza otros textos."""
    valor = obtener_variable_opcional(nombre)
    if valor is None:
        return defecto
    normalizado = valor.strip().upper()
    if normalizado in ("TRUE", "SI", "1", "YES", "Y"):
        return True
    if normalizado in ("FALSE", "NO", "0", "N"):
        return False
    raise RuntimeError(f"Valor booleano invalido para {nombre}")


def ruta_proyecto(valor, defecto):
    """Resuelve una ruta absoluta o la hace relativa a la raíz operativa."""
    ruta = valor or defecto
    ruta_path = Path(ruta)
    if ruta_path.is_absolute():
        return str(ruta_path)
    return str(RUTA_BASE / ruta_path)


RUTA_INPUT = Path(ruta_proyecto(obtener_variable_opcional('CARPETA_INPUTS'), 'input'))
BACKEND_ACTIVO = obtener_booleano('BACKEND_ACTIVO', True)


def obtener_decimal_positivo(nombre, defecto):
    """Exige un número finito mayor que cero para límites y tiempos operativos."""
    valor = obtener_decimal(nombre, defecto)
    if valor is None or not math.isfinite(valor) or valor <= 0:
        raise RuntimeError(f'{nombre} debe ser un número positivo y finito')
    return valor


# Ajustes operativos: los valores efectivos proceden de .env o del entorno.
BACKEND_MAX_BYTES = obtener_entero_positivo('BACKEND_MAX_BYTES', 20971520)
BACKEND_MAX_PAGINAS = obtener_entero_positivo('BACKEND_MAX_PAGINAS', 100)
ARCHIVO_REINTENTOS = obtener_entero_positivo('ARCHIVO_REINTENTOS', 5)
MAX_PRODUCTOS_POR_BUSQUEDA = obtener_entero_positivo('MAX_PRODUCTOS_POR_BUSQUEDA', 5)
RPA_JS_REINTENTOS = obtener_entero_positivo('RPA_JS_REINTENTOS', 3)
RPA_DOM_REINTENTOS = obtener_entero_positivo('RPA_DOM_REINTENTOS', 2)
MAX_EXCEL_ENTRADAS_ZIP = obtener_entero_positivo('MAX_EXCEL_ENTRADAS_ZIP', 2000)
SMTP_TIMEOUT = obtener_decimal_positivo('SMTP_TIMEOUT', 30)
DB_TIMEOUT = obtener_decimal_positivo('DB_TIMEOUT', 5)
CORREO_DB_TIMEOUT = obtener_decimal_positivo('CORREO_DB_TIMEOUT', 10)
RPA_HTTP_TIMEOUT = obtener_decimal_positivo('RPA_HTTP_TIMEOUT', 2)
RPA_CIERRE_TIMEOUT = obtener_decimal_positivo('RPA_CIERRE_TIMEOUT', 3)
RPA_SONDEO_INTERVALO = obtener_decimal_positivo('RPA_SONDEO_INTERVALO', 0.2)
RPA_JS_PAUSA = obtener_decimal_positivo('RPA_JS_PAUSA', 1)
RPA_CAPTURA_PAUSA = obtener_decimal_positivo('RPA_CAPTURA_PAUSA', 3)
RPA_RESULTADOS_INTERVALO = obtener_decimal_positivo('RPA_RESULTADOS_INTERVALO', 1.5)
RPA_ESCRITURA_PAUSA = obtener_decimal_positivo('RPA_ESCRITURA_PAUSA', 0.5)
REINTENTOS_PAUSA = obtener_decimal_positivo('REINTENTOS_PAUSA', 2)
REINTENTOS_PAUSA_MAX = obtener_decimal_positivo('REINTENTOS_PAUSA_MAX', 5)
ARCHIVO_REINTENTO_PAUSA = obtener_decimal_positivo('ARCHIVO_REINTENTO_PAUSA', 0.05)
DEPENDENCIAS_TIMEOUT = obtener_decimal_positivo('DEPENDENCIAS_TIMEOUT', 10)



NOMBRE_PROCESO = obtener_variable_opcional("NOMBRE_PROCESO", "SCRAPER_PRECIOS").upper()
NOMBRE_PROCESO_TEXTO = obtener_variable_opcional("NOMBRE_PROCESO_TEXTO", "Robot que saca los precios de los supermercados")
ID_EJECUCION_ENV = obtener_variable_opcional("ID_EJECUCION", "")
REINTENTOS_MAX = obtener_entero("REINTENTOS_MAX", 2)
MAX_REINTENTOS = obtener_entero_positivo("MAX_REINTENTOS", REINTENTOS_MAX + 1)
MAX_REINTENTOS_CORREO = obtener_entero_positivo("MAX_REINTENTOS_CORREO",3)
RPA_HEADLESS = obtener_booleano("RPA_HEADLESS", False)
RPA_TIMEOUT = obtener_entero("RPA_TIMEOUT",20)
RPA_CHROME_PUERTO = obtener_entero("RPA_CHROME_PUERTO", 9222)

if not 1 <= RPA_CHROME_PUERTO <= 65535:
    raise RuntimeError("RPA_CHROME_PUERTO debe estar entre 1 y 65535")


def validar_url_chrome(valor):
    """Acepta una dirección HTTP local sin credenciales, rutas ni parámetros."""
    try:
        url = urlsplit(valor)
        host = url.hostname
        local = host == 'localhost' or bool(host and ipaddress.ip_address(host).is_loopback)
        if (url.scheme != 'http' or not local or url.username is not None
                or url.password is not None or url.path not in ('', '/')
                or url.query or url.fragment or url.port is None or not 1 <= url.port <= 65535):
            raise ValueError
        return url.geturl().rstrip('/'), host, url.port
    except ValueError:
        raise RuntimeError('RPA_CHROME_URL debe ser una URL HTTP local con puerto, sin credenciales ni rutas') from None


RPA_CHROME_URL, RPA_CHROME_HOST, RPA_CHROME_PUERTO = validar_url_chrome(
    obtener_variable_opcional('RPA_CHROME_URL', f'http://127.0.0.1:{RPA_CHROME_PUERTO}'))


RPA_ESPERA_MAX = obtener_decimal("RPA_ESPERA_MAX", 30)
RUTA_DB_SQLITE = ruta_proyecto(obtener_variable_opcional("DB_PATH"), "temporales/scraper.db")
EXCEL_ENTRADA = ruta_proyecto(obtener_variable_opcional("EXCEL_ENTRADA"), "input/productos.xlsx")
LISTA_DISTRIBUCION_CORREO = os.getenv("LISTA_DISTRIBUCION_CORREO", "")
LISTA_DISTRIBUCION_ERROR = os.getenv("LISTA_DISTRIBUCION_ERROR", "")
EXCEL_SALIDA = ruta_proyecto(obtener_variable_opcional("EXCEL_SALIDA"),"reportes/resultados.xlsx")
CARPETA_CAPTURAS = ruta_proyecto(obtener_variable_opcional("CARPETA_CAPTURAS"), "procesados/capturas")
RETENCIONES_LOGS_MESES = obtener_entero_positivo("RETENCION_LOGS_MESES", 6)


class FiltroContexto(logging.Filter):
    def filter(self, record):
        """Añade al registro de log los identificadores y el módulo de la ejecución actual."""
        datos = actual()
        record.solicitud_id = datos['solicitud_id'] or 'SIN_SOLICITUD'
        record.ejecucion_web_id = datos['ejecucion_id_web'] or 'SIN_SOLICITUD'
        record.activacion_id = datos['activacion_id'] or 'SIN_ACTIVACION'
        if not hasattr(record, "id_ejecucion"):
            record.id_ejecucion = "SIN_ID"
        if not hasattr(record, "modulo"):
            record.modulo = "CONFIGURACIÓN"
        return True


def configurar_logging():
    """Configura el log mensual, su formato de trazabilidad y la retención de archivos antiguos."""
    RUTA_LOGS.mkdir(parents=True, exist_ok=True)

    nombre_log = NOMBRE_PROCESO.lower().replace(" ", "_")
    ruta_log = RUTA_LOGS / f"{nombre_log}_{datetime.now():%Y_%m}.log"

    logger = logging.getLogger(NOMBRE_PROCESO)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    formato = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] [ID_LOCAL:%(id_ejecucion)s] "
        "[SOLICITUD:%(solicitud_id)s] [EJECUCION_WEB:%(ejecucion_web_id)s] "
        "[ACTIVACION:%(activacion_id)s] [%(modulo)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    filtro_contexto = FiltroContexto()

    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    consola.addFilter(filtro_contexto)

    archivo = logging.FileHandler(ruta_log, mode="a", encoding="utf-8")
    archivo.setFormatter(formato)
    archivo.addFilter(filtro_contexto)

    logger.addHandler(consola)
    logger.addHandler(archivo)

    logger.info("Log mensual configurado.")

    limpiar_logs_antiguos(logger, ruta_log)


def limpiar_logs_antiguos(logger, ruta_log_actual):
    """Elimina únicamente los logs del proceso que superan la retención configurada."""
    nombre_log = NOMBRE_PROCESO.lower().replace(" ", "_")
    indice_mes_actual = datetime.now().year * 12 + datetime.now().month

    for ruta_log in RUTA_LOGS.glob(f"{nombre_log}_????_??.log"):
        if ruta_log.resolve() == ruta_log_actual.resolve():
            continue

        try:
            partes = ruta_log.stem.rsplit("_", 2)
            anio = int(partes[-2])
            mes = int(partes[-1])

            indice_mes_log = anio * 12 + mes
            antiguedad_meses = indice_mes_actual - indice_mes_log

            if antiguedad_meses >= RETENCIONES_LOGS_MESES:
                ruta_log.unlink()
                logger.info(f"Archivo de log antiguo eliminado: {ruta_log.name}")

        except (ValueError, IndexError):
            logger.warning(f"No se pudo determinar la fecha del archivo de log: {ruta_log.name}")

        except Exception:
            logger.exception(f"No se pudo eliminar el archivo de log antiguo: {ruta_log.name}")


def obtener_logger(modulo, id_ejecucion_logger=None):
    """Obtiene el logger del módulo incorporando la identificación local y el contexto."""
    if id_ejecucion_logger is None:
        id_ejecucion_logger = id_ejecucion if id_ejecucion is not None else "SIN_ID"

    logger = logging.getLogger(NOMBRE_PROCESO)
    return logging.LoggerAdapter(
        logger,
        {
            "id_ejecucion": id_ejecucion_logger,
            "modulo": modulo
        }
    )


def ahora():
    """Devuelve la fecha y hora local en el formato utilizado por la base de datos."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slug(texto):
    """Normaliza un texto para utilizarlo en nombres de evidencia."""
    return re.sub(r"[^0-9-a-zA-Z]+", "_", str(texto)).strip("_")[:40] or "tarea"


def registrar(mensaje, modulo="GENERAL"):
    """Registra un mensaje con el logger y el módulo indicados."""
    obtener_logger(modulo).info(mensaje)


id_ejecucion = None


def configuracion_inicial():
    """Inicia logging, verifica SQLite e inicializa la base y la identificación del proceso."""
    global id_ejecucion
    configurar_logging()
    revisar_integridad(RUTA_DB_SQLITE)
    configurar_base_datos(RUTA_DB_SQLITE)
    logger = obtener_logger("CONFIGURACIÓN")
    logger.info("INICIANDO CONFIGURACIÓN INICIAL")

    if ID_EJECUCION_ENV:
        try:
            id_ejecucion = int(ID_EJECUCION_ENV)
        except ValueError as exc:
            raise RuntimeError("ID_EJECUCION debe ser un numero entero") from exc
        logger.info(f"CONFIGURAR INICIO CON ID_EJECUCION: {id_ejecucion}")
    else:
        logger.info("CONFIGURAR INICIO SIN ID_EJECUCION")
    logger = obtener_logger("CONFIGURACIÓN", id_ejecucion)
    logger.info("CONFIGURACIÓN INICIAL FINALIZADA CORRECTAMENTE")
    return id_ejecucion


MODULO_CORREO = obtener_booleano("MODULO_CORREO", True)
MODULO_CORREO_INICIAL = obtener_booleano("MODULO_CORREO_INICIAL", True)
MODULO_AUTOMATIZACION_SUPERMERCADOS = obtener_booleano(
    "MODULO_AUTOMATIZACION_SUPERMERCADOS", obtener_booleano("MODULO_SCRAPING", True))
MODULO_SCRAPING = MODULO_AUTOMATIZACION_SUPERMERCADOS

class ErrorArranque(RuntimeError):
    pass


def _probar_directorio(ruta):
    """Comprueba que un directorio operativo pueda crearse y recibir archivos temporales."""
    ruta = Path(ruta)
    ruta.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=ruta, prefix='verificacion_', suffix='.tmp'):
        pass


def validar_configuracion(config):
    """Valida los ajustes, flags, carpetas y requisitos de correo antes del negocio."""
    for nombre in ('RPA_TIMEOUT', 'RPA_ESPERA_MAX', 'MAX_REINTENTOS', 'MAX_REINTENTOS_CORREO', 'RETENCIONES_LOGS_MESES'):
        valor = getattr(config, nombre)
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or valor <= 0:
            raise ErrorArranque(f'{nombre} debe ser un numero positivo y finito')
    if config.REINTENTOS_MAX < 0:
        raise ErrorArranque('REINTENTOS_MAX no puede ser negativo')
    if config.ID_EJECUCION_ENV and (not config.ID_EJECUCION_ENV.isdecimal() or int(config.ID_EJECUCION_ENV) < 1):
        raise ErrorArranque('ID_EJECUCION debe ser un entero positivo o estar vacio')
    for nombre in ('MODULO_AUTOMATIZACION_SUPERMERCADOS', 'MODULO_SCRAPING', 'MODULO_CORREO', 'MODULO_CORREO_INICIAL', 'RPA_HEADLESS'):
        valor = os.getenv(nombre)
        if valor is not None and valor.strip() and valor.strip().upper() not in (
                'TRUE', 'SI', '1', 'YES', 'Y', 'FALSE', 'NO', '0', 'N'):
            raise ErrorArranque(f'{nombre} contiene un booleano invalido')
    for ruta in (config.RUTA_LOGS, config.RUTA_REPORTES, config.RUTA_TEMPORALES,
                 Path(config.RUTA_DB_SQLITE).parent, Path(config.EXCEL_SALIDA).parent,
                 Path(config.CARPETA_CAPTURAS)):
        _probar_directorio(ruta)
    if config.MODULO_CORREO:
        for nombre in ('smtp_server', 'email_remitente', 'email_password'):
            if not config.obtener_variable_opcional(nombre):
                raise ErrorArranque(f'Correo habilitado pero falta {nombre}; configurar o desactivar MODULO_CORREO')
        try:
            puerto = int(config.obtener_variable_opcional('smtp_port', '587'))
        except ValueError:
            raise ErrorArranque('smtp_port debe ser un entero') from None
        if not 1 <= puerto <= 65535 or not config.LISTA_DISTRIBUCION_ERROR.strip():
            raise ErrorArranque('Revisar smtp_port y LISTA_DISTRIBUCION_ERROR del correo habilitado')



def validar_dependencias(raiz):
    """Comprueba las versiones de Python y Node.js y localiza un ejecutable de Chrome."""
    if sys.version_info < (3, 11):
        raise ErrorArranque('Se requiere Python 3.11 o posterior')
    nodo = shutil.which('node')
    if not nodo:
        raise ErrorArranque('Falta Node.js 22 o posterior en PATH')
    resultado = subprocess.run([nodo, '--version'], capture_output=True, text=True, timeout=DEPENDENCIAS_TIMEOUT, check=True)
    if int(resultado.stdout.strip().lstrip('v').split('.')[0]) < 22:
        raise ErrorArranque('Se requiere Node.js 22 o posterior')
    candidatos = [os.getenv('CHROME_PATH'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        str(Path(os.getenv('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe'),
        shutil.which('google-chrome'), shutil.which('chromium')]
    chrome = next((p for p in candidatos if p and Path(p).is_file()), None)
    if not chrome:
        raise ErrorArranque('No se encontro Chrome; configurar CHROME_PATH')
    return str(Path(chrome).resolve())


def revisar_integridad(ruta):
    """Comprueba la integridad de una base SQLite existente sin modificarla."""
    ruta = Path(ruta)
    if not ruta.exists():
        return
    # mode=ro revisa la base existente sin crearla ni migrarla.
    with closing(sqlite3.connect(ruta.resolve().as_uri() + '?mode=ro', uri=True, timeout=DB_TIMEOUT)) as conexion:
        if conexion.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ErrorArranque('La base SQLite no supera quick_check; restaurar o revisar antes de ejecutar')
        if conexion.execute('PRAGMA foreign_key_check').fetchall():
            raise ErrorArranque('La base SQLite contiene referencias invalidas')


def revisar_base_operativa(config):
    """Valida las tablas y relaciones necesarias de la base ya inicializada."""
    from config import base_datos as bd
    revisar_integridad(config.RUTA_DB_SQLITE)
    requeridas = {
        'details': {'id', 'archivo', 'ruta', 'estado', 'error', 'fecha_creacion', 'fecha_fin'},
        'cola': {'id', 'detail_id', 'producto', 'supermercado', 'marca', 'precio_minimo',
                 'precio_maximo', 'estado', 'reintentos', 'error', 'fecha_creacion', 'fecha_fin'},
        'resultados': {'id', 'cola_id', 'supermercado', 'producto', 'nombre', 'precio', 'url', 'fecha'},
    }
    with closing(bd.obtener_db_conexion_sql3()) as conexion:
        if conexion.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise ErrorArranque('Version de esquema SQLite incompatible')
        for tabla, columnas in requeridas.items():
            presentes = {fila[1] for fila in conexion.execute('PRAGMA table_info(' + tabla + ')')}
            if not columnas <= presentes:
                raise ErrorArranque(f'La tabla {tabla} no tiene todas sus columnas requeridas')
        indices = {fila[0] for fila in conexion.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        if not {'idx_cola_filtros', 'idx_details_estado', 'idx_cola_estado', 'idx_cola_detail',
                'idx_resultados_busqueda', 'idx_resultados_cola'} <= indices:
            raise ErrorArranque('Faltan indices de la base operativa')
        # Comprobar bloqueo de escritura sin insertar, borrar ni cambiar tareas.
        conexion.execute('BEGIN IMMEDIATE')
        conexion.rollback()
