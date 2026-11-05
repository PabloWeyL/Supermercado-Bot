import smtplib
import re
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from config.configuracion import EXCEL_SALIDA,NOMBRE_PROCESO_TEXTO, obtener_logger, obtener_variable_opcional, LISTA_DISTRIBUCION_CORREO

def _destinatarios(valor):
    return list(dict.fromkeys(correo for correo in re.split(r"[,;\s]+", valor or "") if correo))

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
    remitente = obtener_variable_opcional("email_remitente", "")
    password = obtener_variable_opcional("email_password", "").replace(" ", "")
    if destinatarios is None:
        destinatarios = _destinatarios(obtener_variable_opcional("lista_distribucion_correo", ""))
    if not remitente or not destinatarios:
        logger.warning("Email: faltan email_remitente o lista_distribucion_correo en el .env")
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

def notificar(titulo,texto, adjuntos=None):
    try:
        enviar_email(titulo, texto, _destinatarios(LISTA_DISTRIBUCION_CORREO), adjuntos)
    except Exception:
        obtener_logger("CORREO").info(
            "Correo con credenciales erroneas, en el .env si todo esta ok"
        )

def enviar_correo_inicial():
    logger = obtener_logger("CORREO")
    logger.info("Enviando correo inicial")
    notificar(NOMBRE_PROCESO_TEXTO, "El proceso " + NOMBRE_PROCESO_TEXTO + " ha iniciado.")

def enviar_correo_error(mensaje, adjuntos=None):
    logger = obtener_logger("CORREO")
    logger.info("Enviando correo por error")
    notificar(NOMBRE_PROCESO_TEXTO + " - ERROR", "El proceso ha fallado:\n\n" + str(mensaje), adjuntos)

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
    notificar(titulo, texto, adjuntos)