import smtplib
import re
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from config import configuracion
from config.configuracion import EXCEL_SALIDA, NOMBRE_PROCESO_TEXTO, obtener_logger, obtener_variable_opcional

def _destinatarios(valor):
    salida, vistos = [], set()
    for correo in re.split(r"[,;\s]+", valor or ""):
        if correo and correo.casefold() not in vistos:
            salida.append(correo)
            vistos.add(correo.casefold())
    return salida


def destinatarios_evento(evento):
    """Ambas listas reciben inicio y resultados; solo operaciones recibe fallas."""
    if evento == 'error':
        return _destinatarios(configuracion.LISTA_DISTRIBUCION_ERROR)
    if evento in ('inicio', 'resultados'):
        return _destinatarios(configuracion.LISTA_DISTRIBUCION_ERROR + ',' + configuracion.LISTA_DISTRIBUCION_CORREO)
    raise ValueError('Evento de correo desconocido: ' + str(evento))

def _adjuntar(mensaje, rutas):
    for ruta in rutas or []:
        if not ruta or not Path(ruta).is_file():
            obtener_logger("CORREO").warning("No se adjunta (no existe): " + str(ruta))
            continue
        with open(ruta, "rb") as archivo:
            adjunto = MIMEApplication(archivo.read(), _substype="octet-stream")
        adjunto.add_header("Content-Disposition", "attachment", filename=Path(ruta).name)
        mensaje.attach(adjunto)

def enviar_email(titulo, texto, destinatarios=None, adjuntos=None):
    logger = obtener_logger("CORREO")
    if not configuracion.MODULO_CORREO:
        return False
    remitente = obtener_variable_opcional("email_remitente", "")
    password = obtener_variable_opcional("email_password", "").replace(" ", "")
    if destinatarios is None:
        destinatarios = destinatarios_evento("resultados")
    if not remitente or not destinatarios:
        logger.warning("Email: faltan remitente o destinatarios para el evento")
        return False

    mensaje = MIMEMultipart()
    mensaje["Subject"] = titulo
    mensaje["From"] = remitente
    mensaje["To"] = ", ".join(destinatarios)
    mensaje.attach(MIMEText(texto, "plain", "utf-8"))
    _adjuntar(mensaje, adjuntos)

    try:
        servidor = smtplib.SMTP(
            obtener_variable_opcional("smtp_server", ""),
            int(obtener_variable_opcional("smtp_port", "587")),
            timeout=30,
        )
        servidor.starttls()
        servidor.login(remitente, password)
        servidor.sendmail(remitente, destinatarios, mensaje.as_string())
        servidor.quit()
        logger.info("Mensaje enviado por email")
        return True
    except Exception as error:
        logger.error("Error enviando email: " + str(error))
        return False

def notificar(titulo, texto, adjuntos=None, evento="resultados"):
    if not configuracion.MODULO_CORREO:
        return False
    try:
        destinatarios = destinatarios_evento(evento)
        if not destinatarios:
            obtener_logger("CORREO").warning("Correo %s omitido: lista de destinatarios vacia", evento)
            return False
        return enviar_email(titulo, texto, destinatarios, adjuntos)
    except Exception:
        obtener_logger("CORREO").exception("Fallo de correo %s; el procesamiento continua", evento)
        return False


def enviar_correo_inicial():
    if not configuracion.MODULO_CORREO_INICIAL or not configuracion.MODULO_CORREO:
        return False
    logger = obtener_logger("CORREO")
    logger.info("Enviando correo inicial")
    return notificar(NOMBRE_PROCESO_TEXTO + " - INICIO", "El proceso " + NOMBRE_PROCESO_TEXTO + " ha iniciado.", evento="inicio")

def enviar_correo_error(mensaje, adjuntos=None):
    logger = obtener_logger("CORREO")
    logger.info("Enviando correo por error")
    return notificar(NOMBRE_PROCESO_TEXTO + " - ERROR", "El proceso ha fallado:\n\n" + str(mensaje), adjuntos, evento="error")

def notificar_resultado_procesamiento(resumen, titulo="Resumen de scraping", mensaje=None):
    if not resumen:
        return
    texto = "Scraping terminado. OK " + str(resumen["ok"]) + " | Errores: " + str(resumen["errores"])
    if mensaje:
        texto = str(mensaje) + "\n\n" + texto
    carga = resumen.get("carga", {})
    if carga.get("archivos_error"):
        texto += "\nArchivos de entrada con error: " + str(carga["archivos_error"])
    fallos = resumen.get("fallos") or []
    if fallos:
        texto += "\n\nProductos no encontrados / con error:"
        for producto, supermercado, error in fallos:
            texto += "\n- " + str(producto) + " (" + str(supermercado) + "): " + str(error)
    adjuntos = []
    if Path(EXCEL_SALIDA).is_file():
        adjuntos.append(EXCEL_SALIDA)
    capturas = resumen.get("capturas") or []
    adjuntos.extend(capturas)
    if capturas:
        texto += "\n\nCapturas de error adjuntas: " + str(len(capturas))
    return notificar(titulo, texto, adjuntos, evento="resultados")