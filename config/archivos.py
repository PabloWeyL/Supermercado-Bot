"""Constantes, rutas por ejecución y persistencia de archivos."""

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


class ErrorWeyland(RuntimeError):
    pass


class ErrorHTTPWeyland(ErrorWeyland):
    def __init__(self, status_code):
        """Conserva el código HTTP rechazado sin incluir datos privados de la petición."""
        self.status_code = status_code
        super().__init__(f'Weyland rechazo la operacion (HTTP {status_code})')


class ErrorRedWeyland(ErrorWeyland):
    """Operacion incierta: no repetir automaticamente peticiones con efectos."""


class SinPendientes(ErrorWeyland):
    pass


class AsignacionIncompatible(ErrorWeyland):
    """La reserva ya ocurrio en la web, pero este bot no debe ejecutarla."""

    def __init__(self, ejecucion_id):
        """Identifica la ejecución reservada que requiere revisión por una asignación incompatible."""
        self.ejecucion_id = ejecucion_id
        super().__init__(f'La asignacion de la ejecucion #{ejecucion_id} no coincide con '
                         'VM/proceso. Ya fue reservada; requiere revision del operador.')


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
RUTA_AUTH_CSRF = '/auth/csrf'
RUTA_AUTH_LOGIN = '/auth/login'
RUTA_AUTH_PERFIL = '/auth/perfil'
RUTA_AUTH_SESION = '/auth/sesion'
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

@dataclass(frozen=True)
class RutasEjecucion:
    raiz: Path

    def __post_init__(self):
        """Normaliza la raíz recibida a Path sin modificar los datos de ejecución."""
        object.__setattr__(self, 'raiz', Path(self.raiz))

    @property
    def datos(self):
        """Devuelve la carpeta de datos de esta ejecución."""
        return self.raiz / CARPETA_DATOS

    @property
    def reportes(self):
        """Devuelve la carpeta de reportes dentro de los datos de ejecución."""
        return self.datos / CARPETA_REPORTES

    @property
    def input(self):
        """Devuelve la carpeta reservada para la entrada de la solicitud."""
        return self.datos / CARPETA_INPUT

    @property
    def parametros(self):
        """Devuelve la carpeta de parámetros locales de la ejecución."""
        return self.datos / CARPETA_PARAMETROS

    @property
    def descargas(self):
        """Devuelve la carpeta de descargas asociada a esta ejecución."""
        return self.datos / CARPETA_DESCARGAS

    @property
    def logs(self):
        """Devuelve la carpeta de logs de esta ejecución."""
        return self.raiz / CARPETA_LOGS

    @property
    def historico(self):
        """Devuelve la carpeta histórica de la ejecución."""
        return self.raiz / CARPETA_HISTORICO

    @property
    def capturas(self):
        """Devuelve la carpeta de capturas de evidencia."""
        return self.raiz / CARPETA_CAPTURAS

    @property
    def reporte_excel(self):
        """Devuelve la ruta del reporte Excel de negocio."""
        return self.reportes / ARCHIVO_REPORTE_EXCEL

    @property
    def base_datos(self):
        """Devuelve la ruta SQLite aislada de esta ejecución."""
        return self.datos / ARCHIVO_BASE_DATOS

    @property
    def productos_json(self):
        """Devuelve la ruta de productos recibidos mediante parámetros JSON."""
        return self.parametros / ARCHIVO_PRODUCTOS_JSON

    @property
    def log_proceso(self):
        """Devuelve la ruta de salida del subproceso de automatización."""
        return self.raiz / ARCHIVO_LOG_PROCESO

    @property
    def contexto(self):
        """Devuelve la ruta del contexto persistido de trazabilidad."""
        return self.raiz / ARCHIVO_CONTEXTO


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


def sha256(ruta):
    """Calcula la huella SHA-256 de un archivo para comprobar su integridad."""
    with Path(ruta).open('rb') as archivo:
        return hashlib.file_digest(archivo, 'sha256').hexdigest()


def confinar(raiz, ruta):
    """Valida que una ruta relativa permanezca dentro del directorio operativo autorizado."""
    raiz = Path(raiz).resolve()
    ruta = Path(ruta)
    if ruta.is_absolute() or '..' in ruta.parts:
        raise ErrorWeyland('Ruta de artefacto invalida')
    destino = (raiz / ruta).resolve()
    if destino == raiz or not destino.is_relative_to(raiz):
        raise ErrorWeyland('Artefacto fuera del directorio operativo')
    return destino


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
            raise ErrorWeyland('Ya hay un worker local activo') from None
        try:
            yield
        finally:
            archivo.seek(0)
            if os.name == 'nt':
                msvcrt.locking(archivo.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)
