import logging
import os 
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from config.base_datos import configurar_base_datos


RUTA_PROYECTO = Path(__file__).resolve().parent.parent
load_dotenv(RUTA_PROYECTO / ".env")

def _resolver_ruta_base(valor):
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
RUTA_INPUT = RUTA_BASE / "input"
RUTA_PROCESADOS = RUTA_BASE / "procesados"
RUTA_REPORTES = RUTA_BASE / "reportes"
RUTA_TEMPORALES = RUTA_BASE / "temporales"


def obtener_variable_opcional(nombre, defecto=None):
    valor = os.getenv(nombre)
    if valor is None or not str(valor).strip():
        return defecto
    return str(valor).strip()

def obtener_variable_obligatoria(nombre):
    valor = obtener_variable_opcional(nombre)
    if valor is None:
        raise RuntimeError(f"falta variable obligatoria: {nombre}")
    return valor

def obtener_entero(nombre, defecto=None):
    valor = obtener_variable_opcional(nombre)
    if valor is None:
        return defecto
    try:
        return int(valor)
    except ValueError:
        raise RuntimeError(f"Valor invalido para {nombre} : {valor}. Debe ser un numero entero")

def obtener_entero_positivo(nombre, defecto=None):
    valor = obtener_entero(nombre, defecto)
    if valor is None:
        raise RuntimeError(f"falta variable obligatoria: {nombre}")
    if valor <= 0:
        raise RuntimeError(f"Valor invalido para {nombre}, debe ser un valor positivo")
    return valor

def obtener_decimal(nombre, defecto=None):
    valor = obtener_variable_opcional(nombre)
    if valor is None:
        return defecto
    try:
        return float(valor)
    except ValueError:
        raise RuntimeError(f"Valor invalido para {nombre}: {valor} debe ser un numero.")

def obtener_booleano(nombre, defecto=False):
    valor = obtener_variable_opcional(nombre)
    if valor is None:
        return defecto
    return valor.strip().upper() in ("TRUE", "SI", "1", "YES", "Y")

def ruta_proyecto(valor, defecto):
    ruta = valor or defecto
    ruta_path = Path(ruta)
    if ruta_path.is_absolute():
        return str(ruta_path)
    return str(RUTA_BASE / ruta_path)

NOMBRE_PROCESO = obtener_variable_opcional("NOMBRE_PROCESO", "SCRAPER_PRECIOS").upper()
NOMBRE_PROCESO_TEXTO = obtener_variable_opcional("NOMBRE_PROCESO_TEXTO", "Robot que saca los precios de los supermercados")
ID_EJECUCION_ENV = obtener_variable_opcional("ID_EJECUCION", "")

REINTENTOS_MAX = obtener_entero("REINTENTOS_MAX", 2)
MAX_REINTENTOS = obtener_entero_positivo("MAX_REINTENTOS", 3)
MAX_REINTENTOS_CORREO = obtener_entero_positivo("MAX_REINTENTOS_CORREO",3)
RPA_HEADLESS = obtener_booleano("RPA_HEADLESS", False)
RPA_TIMEOUT = obtener_entero("RPA_TIMEOUT",20)
RPA_CHROME_PUERTO = obtener_entero("RPA_CHROME_PUERTO", 9222)
if not 1 <= RPA_CHROME_PUERTO <= 65535:
    raise RuntimeError("RPA_CHROME_PUERTO debe estar entre 1 y 65535")
RPA_ESPERA_MAX = obtener_decimal("RPA_ESPERA_MAX", 30)

RUTA_DB_SQLITE = ruta_proyecto(obtener_variable_opcional("DB_PATH"), "temporales/scraper.db")
EXCEL_ENTRADA = ruta_proyecto(obtener_variable_opcional("EXCEL_ENTRADA"), "input/productos.xlsx")
LISTA_DISTRIBUCION_CORREO = os.getenv("LISTA_DISTRIBUCION_CORREO", "")
EXCEL_SALIDA = ruta_proyecto(obtener_variable_opcional("EXCEL_SALIDA"),"reportes/resultados.xlsx")
CARPETA_CAPTURAS = ruta_proyecto(obtener_variable_opcional("CARPETA_CAPTURAS"), "procesados/capturas")
ARCHIVO_SUPERMERCADOS = RUTA_CONFIG/"supermercados.json"



RETENCIONES_LOGS_MESES = obtener_entero_positivo("RETENCION_LOGS_MESES", 6)

class FiltroContexto(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "id_ejecucion"):
            record.id_ejecucion = "SIN_ID"
        if not hasattr(record, "modulo"):
            record.modulo = "CONFIGURACIÓN"
        return True

def configurar_logging():
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
        "%(asctime)s [%(levelname)s] [%(name)s] [ID:%(id_ejecucion)s] [%(modulo)s] %(message)s",
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
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def slug(texto):
    return re.sub(r"[^0-9-a-zA-Z]+", "_", str(texto)).strip("_")[:40] or "tarea"

def registrar(mensaje, modulo="GENERAL"):
    obtener_logger(modulo).info(mensaje)

id_ejecucion = None


def configuracion_inicial():
    global id_ejecucion
    configurar_base_datos(RUTA_DB_SQLITE)
    configurar_logging()
    logger = obtener_logger("CONFIGURACION")
    logger.info("INICIANDO CONFIGURACIÓN INICIAL")

    if ID_EJECUCION_ENV:
        try:
            id_ejecucion = int(ID_EJECUCION_ENV)
        except ValueError as exc:
            raise RuntimeError("ID_EJECUCION debe ser un numero entero") from exc
        logger.info(f"CONFIGURAR INICIO CON ID_EJECUCION: {id_ejecucion}")
    else:
        logger.info("CONFIGURAR INICIO SIN ID_EJECUCION")
    logger = obtener_logger("CONFIGURACUÓN", id_ejecucion)
    logger.info("CONFIGURACIÓN INICIAL FINALIZADA CORRECTAMENTE")
    return id_ejecucion

MODULO_CORREO_INICIAL = obtener_booleano("MODULO_CORREO_INICIAL", False)
MODULO_SCRAPING = obtener_booleano("MODULO_SCRAPING", True)
