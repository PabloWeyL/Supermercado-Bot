# ======================
# Import
# ======================

import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


RUTA_BASE = Path(__file__).resolve().parent.parent


# ======================
# Registro local de fallos
# ======================

def registrar_fallo(modulo, error, intentos=None, datos_observados=None, descartado=None):
    """Escribe un diagnóstico sanitizado, incluso con la base de datos caída."""

    try:
        configuracion = sys.modules.get("config.configuracion")
        identificador = getattr(configuracion, "IDENTIFICADOR_LOCAL", None)
        id_ejecucion = (getattr(error, "id_ejecucion_local", None)
                        or (identificador.get() if identificador is not None else None)
                        or getattr(configuracion, "id_ejecucion", None) or "SIN_ID")
        id_ejecucion = re.sub(r"[^A-Za-z0-9_-]", "_", str(id_ejecucion))[:64]
        nombre_proceso = getattr(configuracion, "NOMBRE_PROCESO", RUTA_BASE.name)
        nombre_proceso = re.sub(r"[^A-Za-z0-9_-]", "_", nombre_proceso).upper()
        modulo = " ".join(re.findall(r"[\w-]+", str(modulo).replace("_", " ").upper()))[:100]
        ahora = datetime.now()
        categoria = tipo_de_fallo(error)
        lineas = [
            f"# FALLO — {nombre_proceso}",
            "",
            f"- Fecha: {ahora:%Y-%m-%d %H:%M:%S}",
            f"- ID_EJECUCION: {id_ejecucion}",
            f"- Módulo: {modulo}",
            f"- Tipo de error: {categoria}",
            "- Resultado: ERROR",
            "- Máquina y versión desplegada: no incluidas en el diagnóstico local",
        ]
        if type(intentos) is int and intentos >= 0:
            lineas.append(f"- Intentos: {intentos}")
        datos = contexto_seguro(id_ejecucion, ahora, datos_observados)
        lineas.extend(linea for linea in datos if linea.startswith("- Duración:"))
        marcos, origen = traza_segura(error.__traceback__)
        observaciones = [linea for linea in datos if linea.startswith(("- Extensión", "- ID de solicitud"))]
        estado = [linea for linea in datos if linea.startswith(("- Estado de Queue", "- Details"))]
        recientes = ultimos_registros_seguros(getattr(configuracion, "RUTA_LOG_ACTUAL", None), id_ejecucion)
        secciones = (
            ("1. Qué falló", [f"{categoria}. El texto original se omite por seguridad."]),
            ("2. Dónde", [f"Origen: {origen}"] if origen else []),
            ("3. Traceback sanitizado", [f"- {marco}" for marco in marcos]),
            ("4. Código donde falló", [f"Consultar {origen} en el repositorio."] if origen else []),
            ("5. Datos observados", observaciones),
            ("6. Estado dejado", estado),
            ("7. Reproducción controlada", [
                "Verifica el estado persistido y conserva el checkpoint antes de reintentar.",
                "Reproduce con datos de prueba. Si la ejecución está cerrada, usa un ID nuevo.",
            ]),
            ("8. Últimas 30 líneas del log", recientes),
            ("9. Descartado", []),
            ("10. Restricciones", [
                "Conservar Queue, Details y checkpoints. Conciliar entregas inciertas antes de repetirlas.",
                "Usar entradas de prueba y mantener credenciales y contenido operativo fuera del diagnóstico.",
            ]),
        )
        for titulo, contenido in secciones:
            lineas.extend(("", f"## {titulo}", "", *(contenido or ["No disponible."])))
        carpeta = RUTA_BASE / "fallos"
        carpeta.mkdir(parents=True, exist_ok=True)
        for milisegundos in range(1000):
            instante = ahora + timedelta(milliseconds=milisegundos)
            ruta = carpeta / f"FALLO_{id_ejecucion}_{instante:%H%M%S}_{instante.microsecond // 1000:03d}.md"
            try:
                with ruta.open("x", encoding="utf-8") as archivo:
                    archivo.write("\n".join(lineas) + "\n")
                return ruta
            except FileExistsError:
                continue
        raise OSError("No se pudo reservar un nombre de diagnóstico")
    except Exception:
        try:
            logging.getLogger(__name__).warning("NO SE PUDO ESCRIBIR EL DIAGNÓSTICO LOCAL")
        except Exception:
            pass
        return None


def contexto_seguro(id_ejecucion, ahora, datos):
    """Admite solo campos estructurados conocidos y evita consultas durante el fallo."""

    lineas = []
    if re.fullmatch(r"[0-9]{8}_[0-9]{6}", id_ejecucion):
        try:
            inicio = datetime.strptime(id_ejecucion, "%d%m%Y_%H%M%S")
        except ValueError:
            inicio = None
        if inicio is not None and inicio <= ahora:
            duracion = ahora - inicio
            lineas.append(f"- Duración: {str(duracion).split('.')[0]}")
    if not isinstance(datos, dict):
        return lineas
    extension = datos.get("extension_entrada")
    if isinstance(extension, str) and extension in (".xls", ".xlsx", ".xlsm", ".csv"):
        lineas.append(f"- Extensión de entrada: {extension}")
    solicitud = datos.get("ejecucion_id")
    if type(solicitud) is int and solicitud > 0:
        lineas.append(f"- ID de solicitud Weyland: {solicitud}")
    estado = datos.get("estado_queue")
    if isinstance(estado, str) and estado in ("PENDIENTE", "EN_EJECUCION", "PROCESADO", "FINALIZADO", "ERROR"):
        lineas.append(f"- Estado de Queue: {estado}")
    conteo = datos.get("conteo_details")
    if isinstance(conteo, dict):
        for nombre in ("PENDIENTE", "LEIDO", "DESCARGADO", "SIN_REPORTE", "NO_APLICA", "ERROR"):
            cantidad = conteo.get(nombre)
            if type(cantidad) is int and cantidad >= 0:
                lineas.append(f"- Details {nombre}: {cantidad}")
    return lineas


def tipo_de_fallo(error):
    """Conserva las categorías conocidas de los bots y el tipo de la excepción."""
    categoria = getattr(error, "categoria", None)
    if categoria in (
        "ErrorWeyland", "ErrorEntrega", "ErrorArranque", "ErrorAutenticacion",
        "ErrorCorreoInicial", "CanalInterrumpido", "SinReporte", "ContenidoHTML",
        "SesionCaducada", "SolicitudRechazada",
    ):
        return categoria
    return type(error).__name__


def traza_segura(traza):
    """Retiene los marcos sin argumentos, mensajes, rutas externas ni código fuente."""
    marcos = []
    ultimo_local = None
    while traza is not None:
        codigo = traza.tb_frame.f_code
        ruta = Path(codigo.co_filename)
        relativa = ruta.relative_to(RUTA_BASE) if ruta.is_absolute() and ruta.is_relative_to(RUTA_BASE) else ruta
        if not relativa.is_absolute() and (relativa == Path("main.py") or
                (len(relativa.parts) == 2 and relativa.parts[0] in ("config", "integraciones")
                 and re.fullmatch(r"[a-zA-Z0-9_]+\.py", relativa.name))):
            funcion = f" ({codigo.co_name})" if codigo.co_name.isidentifier() else ""
            ultimo_local = f"{relativa.as_posix()}:{traza.tb_lineno}{funcion}"
            marcos.append(ultimo_local)
        else:
            marcos.append(f"<marco externo>:{traza.tb_lineno}")
        traza = traza.tb_next
    return marcos, ultimo_local


def filtrar_excepcion(registro):
    """Evita que logging vuelque la excepción original o rutas absolutas."""
    if registro.exc_info:
        _, error, traza = registro.exc_info
        marcos, _ = traza_segura(traza)
        registro.exc_text = "\n".join([f"TRAZA SANITIZADA: {tipo_de_fallo(error)}", *marcos])
    return True


def preparar_consola(logger):
    """Da formato a los avisos que se emiten antes de configurar el log mensual."""
    if logger.handlers:
        return
    consola = logging.StreamHandler()
    consola.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] [ID:%(id_ejecucion)s] [%(modulo)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        defaults={"id_ejecucion": "SIN_ID", "modulo": "CONFIGURACION"},
    ))
    consola.addFilter(filtrar_excepcion)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(consola)


def ultimos_registros_seguros(ruta, id_ejecucion):
    """Extrae hasta 30 marcas de tiempo y niveles del ID, sin copiar mensajes."""
    if ruta is None:
        return []
    try:
        ruta = Path(ruta)
        if ruta.suffix != ".log" or not ruta.is_file() or ruta.is_symlink():
            return []
        with ruta.open("rb") as archivo:
            tamano = archivo.seek(0, 2)
            inicio = max(0, tamano - 65536)
            archivo.seek(inicio)
            if inicio:
                archivo.readline(65536)
            candidatos = archivo.read(65536).decode("utf-8", errors="replace").splitlines()
    except (TypeError, OSError, ValueError):
        return []
    prefijo = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
        r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\] \[[^\]\r\n]+\] "
        r"\[ID:" + re.escape(id_ejecucion) + r"\]"
    )
    coincidencias = [coincidencia.groups() for linea in candidatos if (coincidencia := prefijo.match(linea))]
    return [f"- {fecha} [{nivel}] [ID:{id_ejecucion}]" for fecha, nivel in coincidencias[-30:]]


def registrar_fallo_no_capturado(tipo, error, traza):
    """Registra excepciones de arranque y muestra únicamente la traza segura."""
    registrar_fallo("EXCEPCIÓN NO CAPTURADA", error)
    logger = logging.getLogger("WEYLAND")
    preparar_consola(logger)
    logger.error("EXCEPCIÓN NO CAPTURADA", exc_info=(tipo, error, traza))


def limpiar_archivos_antiguos(carpeta, patron, meses):
    """Elimina solo logs y fallos que superan la retención configurada."""

    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return
    ahora = datetime.now()
    indice = ahora.year * 12 + ahora.month - meses
    anio, mes_cero = divmod(indice, 12)
    limite = datetime(anio, mes_cero + 1, 1).timestamp()
    for ruta in carpeta.glob(patron):
        if ruta.is_file() and not ruta.is_symlink() and ruta.stat().st_mtime < limite:
            ruta.unlink(missing_ok=True)
