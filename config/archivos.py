"""Constantes, rutas por ejecución y persistencia de archivos."""

from types import SimpleNamespace

from datetime import datetime
from zipfile import ZIP_DEFLATED, ZipFile

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile


# ======================
# ERRORES DE INTEGRACIÓN
# ======================
def crear_error_clasificado(mensaje, categoria, tipo=RuntimeError):
    """Crea un error estándar con una categoría para decidir la recuperación."""
    error = tipo(mensaje)
    error.categoria = categoria
    return error


def es_error(error, *categorias):
    return getattr(error, 'categoria', None) in categorias


def crear_error_weyland(mensaje=None, *, tipo="CONTRATO", status_code=None, ejecucion_id=None):
    """Conserva categoría, rechazo HTTP e ID cuando existen para decidir la recuperación."""
    if status_code is not None:
        if tipo not in ('CONTRATO', 'HTTP'):
            raise ValueError('Un rechazo HTTP no puede tener otra categoria')
        if type(status_code) is not int or not 100 <= status_code <= 599:
            raise ValueError('Codigo HTTP invalido')
        tipo = 'HTTP'
    if tipo not in frozenset(('CONTRATO', 'HTTP', 'RED', 'SIN_PENDIENTES', 'ASIGNACION_INCOMPATIBLE')) or tipo == 'HTTP' and status_code is None:
        raise ValueError('Tipo de error Weyland incompatible')
    if ejecucion_id is not None and (type(ejecucion_id) is not int or ejecucion_id < 1):
        raise ValueError('ID de ejecucion invalido')
    mensajes = {
        'CONTRATO': 'La operacion no cumple el contrato Weyland.',
        'HTTP': f'Weyland rechazo la operacion (HTTP {status_code})',
        'RED': 'Operacion sin confirmacion por un fallo de transporte; conservar evidencia.',
        'SIN_PENDIENTES': 'No hay solicitudes pendientes.',
        'ASIGNACION_INCOMPATIBLE': 'La asignacion no coincide con el bot; revisar antes de otra toma.',
    }
    error = crear_error_clasificado(mensaje if mensaje is not None else mensajes[tipo], 'ErrorWeyland')
    error.tipo = tipo
    error.status_code = status_code
    error.ejecucion_id = ejecucion_id
    return error


CARPETA_DATOS = 'datos'
CARPETA_REPORTES = 'reportes'
CARPETA_INPUT = 'input'
CARPETA_PARAMETROS = 'parametros'
CARPETA_DESCARGAS = 'descargas'
CARPETA_LOGS = 'logs'
CARPETA_HISTORICO = 'historico'
CARPETA_CAPTURAS = 'capturas'
CARPETA_EJECUCIONES = 'ejecuciones'
ARCHIVO_CONTEXTO = 'contexto.json'
ARCHIVO_FINALIZACION = 'finalizacion.json'
ARCHIVO_COORDINADOR = 'coordinador.json'
ARCHIVO_ESTADO = 'estado.json'
ARCHIVO_ACTIVO = 'activo.json'
ARCHIVO_BLOQUEO = 'worker.lock'
ARCHIVO_REPORTE_EXCEL = 'resultados.xlsx'
ARCHIVO_BASE_DATOS = 'historial.db'
ARCHIVO_PRODUCTOS_JSON = 'productos.json'
ARCHIVO_LOG_PROCESO = 'proceso.log'
ARCHIVO_RESULTADO_ZIP = 'resultado.zip'
RUTA_ENTRADA_BASE = f'{CARPETA_DATOS}/{CARPETA_INPUT}/entrada'
RUTA_METADATOS_EXPORT = f'{CARPETA_REPORTES}/ejecucion.json'
FORMATO_FECHA_ARCHIVO = '%d%m%Y-%H%M%S'
EXTENSIONES_INPUT_POR_DEFECTO = ('.xlsx',)
EXTENSIONES_EXCEL = (*EXTENSIONES_INPUT_POR_DEFECTO, '.xlsm')
EXTENSIONES_TABULARES = (*EXTENSIONES_EXCEL, '.csv')
EXTENSIONES_EXPORTABLES = (*EXTENSIONES_TABULARES, '.json', '.txt')
RUTA_BOT_SESION = '/bot/sesion'
RUTA_BOT_RENOVAR = '/bot/sesion/renovar'
RUTA_BOT_CERRAR = '/bot/sesion/cerrar'
PREFIJO_API_BOT = '/bot/'
RUTA_EJECUCIONES = '/ejecuciones'
RUTA_CATALOGO_ESTADOS = f'{RUTA_EJECUCIONES}/estados'
RUTA_SIGUIENTE = '/bot/ejecuciones/siguiente'
RUTA_BOT_EJECUCION = '/bot/ejecuciones/{identificador}'
RUTA_BOT_INPUT = RUTA_BOT_EJECUCION + '/input'
RUTA_BOT_INPUT_API = '/api' + RUTA_BOT_INPUT
RUTA_BOT_REPORTE = RUTA_BOT_EJECUCION + '/reporte'
RUTA_BOT_ESTADO = RUTA_BOT_EJECUCION + '/estado'
RUTA_BOT_ERROR = RUTA_BOT_EJECUCION + '/error'
RUTA_DETALLE_EJECUCION = RUTA_EJECUCIONES + '/{identificador}'
RUTA_ARCHIVO_EJECUCION = RUTA_DETALLE_EJECUCION + '/archivos/{archivo_id}'
RUTA_PROYECTO = Path(__file__).resolve().parents[1]
ARCHIVO_ENV = RUTA_PROYECTO / '.env'

# ======================
# RUTAS DE CADA EJECUCIÓN
# ======================
def rutas_ejecucion(raiz):
    """Calcula las rutas sin crear archivos ni directorios."""

    raiz = Path(raiz)
    datos = raiz / CARPETA_DATOS
    reportes = datos / CARPETA_REPORTES
    input = datos / CARPETA_INPUT
    parametros = datos / CARPETA_PARAMETROS
    descargas = datos / CARPETA_DESCARGAS
    logs = raiz / CARPETA_LOGS
    historico = raiz / CARPETA_HISTORICO
    capturas = raiz / CARPETA_CAPTURAS
    reporte_excel = reportes / ARCHIVO_REPORTE_EXCEL
    base_datos = datos / ARCHIVO_BASE_DATOS
    productos_json = parametros / ARCHIVO_PRODUCTOS_JSON
    log_proceso = raiz / ARCHIVO_LOG_PROCESO
    contexto = raiz / ARCHIVO_CONTEXTO
    return SimpleNamespace(
        raiz=raiz,
        datos=datos,
        reportes=reportes,
        input=input,
        parametros=parametros,
        descargas=descargas,
        logs=logs,
        historico=historico,
        capturas=capturas,
        reporte_excel=reporte_excel,
        base_datos=base_datos,
        productos_json=productos_json,
        log_proceso=log_proceso,
        contexto=contexto,
    )


# ======================
# FUNCIONES DE ESCRITURA ATÓMICA
# ======================
def reemplazar_atomico(parcial, destino):
    """Tolera bloqueos transitorios de Windows sin repetir HTTP ni el negocio."""
    from config.configuracion import ARCHIVO_REINTENTOS, ARCHIVO_REINTENTO_PAUSA
    for intento in range(ARCHIVO_REINTENTOS):
        try:
            parcial.replace(destino)
            return
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or intento + 1 == ARCHIVO_REINTENTOS:
                raise
            time.sleep(ARCHIVO_REINTENTO_PAUSA * (2 ** intento))


def guardar_json(ruta, datos):
    """Escribe JSON con fsync y sustitución atómica, limpiando cualquier temporal sobrante."""
    parcial = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=ruta.parent,
                                prefix='estado_', suffix='.tmp', delete=False) as archivo:
            parcial = Path(archivo.name)
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
            archivo.flush()
            os.fsync(archivo.fileno())
        reemplazar_atomico(parcial, ruta)
    finally:
        if parcial is not None:
            parcial.unlink(missing_ok=True)


# ======================
# FUNCIONES DE INTEGRIDAD Y RUTAS SEGURAS
# ======================
def sha256(ruta):
    """Calcula la huella SHA-256 de un archivo para comprobar su integridad."""
    with Path(ruta).open('rb') as archivo:
        return hashlib.file_digest(archivo, 'sha256').hexdigest()


def confinar(raiz, ruta):
    """Valida que una ruta relativa permanezca dentro del directorio operativo autorizado."""
    raiz = Path(raiz).resolve()
    ruta = Path(ruta)
    if ruta.is_absolute() or '..' in ruta.parts:
        raise crear_error_weyland('Ruta de artefacto invalida')
    destino = (raiz / ruta).resolve()
    if destino == raiz or not destino.is_relative_to(raiz):
        raise crear_error_weyland('Artefacto fuera del directorio operativo')
    return destino


def buscar_carpeta_ejecucion_web(base: Path, solicitud: int) -> Path | None:
    """Localiza los artefactos existentes por ID de solicitud, sin abrir sesión web."""
    if type(solicitud) is not int or solicitud < 1:
        raise ValueError('ID de solicitud invalido')
    base = Path(base).resolve()
    carpetas = [
        ruta for ruta in base.glob(f'cola_actual/maquina_*/ejecuciones/{solicitud}')
        if ruta.is_dir() and ruta.parent.parent.name.removeprefix('maquina_').isdecimal()
    ]
    if len(carpetas) > 1:
        raise ValueError('La solicitud tiene evidencia en varias maquinas locales; requiere revision')
    if not carpetas:
        return None
    return confinar(base, carpetas[0].relative_to(base))


# ======================
# FUNCIONES DE BLOQUEO DE EJECUCIÓN
# ======================
@contextmanager
def bloqueo(ruta):
    """Mantiene un bloqueo exclusivo de archivo y lo libera incluso si ocurre un error."""
    with ruta.open('a+b') as archivo:
        if archivo.tell() == 0:
            archivo.write(b'0')
            archivo.flush()
        archivo.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise crear_error_weyland('Ya hay un worker local activo') from None
        try:
            yield
        finally:
            archivo.seek(0)
            if os.name == 'nt':
                msvcrt.locking(archivo.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)

# ======================
# RESPALDOS HISTÓRICOS
# ======================

def crear_respaldo_historico(carpeta, proceso, ejecucion, fuentes, retencion_meses=18):
    """Crea atómicamente el ZIP histórico de una ejecución confirmada."""

    carpeta = Path(carpeta).resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"respaldo_{proceso}_{ejecucion}.zip"
    destino = carpeta / nombre
    temporal = carpeta / ("." + nombre + ".tmp")
    agregados = 0
    try:
        with ZipFile(temporal, "w", ZIP_DEFLATED) as paquete:
            for categoria, origen in fuentes.items():
                origen = Path(origen)
                rutas = [origen] if origen.is_file() else sorted(origen.rglob("*")) if origen.is_dir() else []
                for ruta in rutas:
                    if (ruta.is_symlink() or not ruta.is_file()
                            or ruta.name.startswith(".env")
                            or ruta.suffix.lower() in (".db", ".sqlite", ".sqlite3", ".log", ".key", ".pem")):
                        continue
                    relativo = ruta.name if origen.is_file() else ruta.relative_to(origen).as_posix()
                    paquete.write(ruta, f"{categoria}/{relativo}")
                    agregados += 1
        if agregados == 0:
            raise ValueError("El respaldo histórico no contiene archivos permitidos")
        temporal.replace(destino)
    finally:
        temporal.unlink(missing_ok=True)
    limpiar_respaldos_antiguos(carpeta, retencion_meses)
    return destino


def limpiar_respaldos_antiguos(carpeta, meses):
    """Elimina únicamente respaldos ZIP que superan la retención configurada."""

    ahora = datetime.now()
    indice = ahora.year * 12 + ahora.month - 1 - meses
    anio, mes_cero = divmod(indice, 12)
    limite = datetime(anio, mes_cero + 1, 1).timestamp()
    for ruta in Path(carpeta).glob("respaldo_*.zip"):
        if ruta.is_file() and not ruta.is_symlink() and ruta.stat().st_mtime < limite:
            ruta.unlink()


# ======================
# FUNCIONES DE ESCRITURA ATÓMICA
# ======================
