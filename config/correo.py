import smtplib
import re
import hashlib
import json
import ssl
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from email.utils import formatdate, parseaddr

from config import configuracion
from config.configuracion import EXCEL_SALIDA, NOMBRE_PROCESO_TEXTO, obtener_logger, obtener_variable_opcional
from integraciones.cola_correo import ColaCorreo, ErrorEntrega
from integraciones.trazabilidad import actual, contexto
from config.credenciales import leer_credencial

MAX_ADJUNTOS_BYTES = configuracion.obtener_entero_positivo('MAX_ADJUNTOS_BYTES')

def _destinatarios(valor):
    salida, vistos = [], set()
    for correo in re.split(r"[,;\s]+", valor or ""):
        if correo and correo.casefold() not in vistos:
            salida.append(correo)
            vistos.add(correo.casefold())
    return salida


def destinatarios_evento(evento):
    if evento == 'error':
        return _destinatarios(configuracion.LISTA_DISTRIBUCION_ERROR)
    if evento in ('inicio', 'resultados'):
        return _destinatarios(configuracion.LISTA_DISTRIBUCION_ERROR + ',' + configuracion.LISTA_DISTRIBUCION_CORREO)
    raise ValueError('Evento de correo desconocido: ' + str(evento))

def _adjuntar(mensaje, rutas):
    total = 0
    for ruta in rutas or []:
        if not ruta or not Path(ruta).is_file() or Path(ruta).is_symlink():
            raise ValueError('Falta un adjunto regular del correo; no se enviara incompleto')
        if Path(ruta).stat().st_size > MAX_ADJUNTOS_BYTES - total:
            raise ValueError(f'Los adjuntos superan MAX_ADJUNTOS_BYTES ({MAX_ADJUNTOS_BYTES} bytes)')
        with open(ruta, "rb") as archivo:
            contenido = archivo.read(MAX_ADJUNTOS_BYTES - total + 1)
        total += len(contenido)
        if total > MAX_ADJUNTOS_BYTES:
            raise ValueError(f'Los adjuntos superan MAX_ADJUNTOS_BYTES ({MAX_ADJUNTOS_BYTES} bytes)')
        adjunto = MIMEApplication(contenido, _substype="octet-stream")
        adjunto.add_header("Content-Disposition", "attachment", filename=Path(ruta).name)
        mensaje.attach(adjunto)

def enviar_email(titulo, texto, destinatarios=None, adjuntos=None):
    if not configuracion.MODULO_CORREO:
        return False
    with contexto():
        return _encolar_y_enviar(titulo, texto, destinatarios, adjuntos)


def obtener_cola():
    return ColaCorreo(ruta_cola(), configuracion.obtener_entero_positivo('CORREO_MAX_INTENTOS', 5),
                      configuracion.obtener_entero_positivo('CORREO_REINTENTO_SEGUNDOS', 60))


def ruta_cola():
    return Path(obtener_variable_opcional('CORREO_PENDIENTES_DIR') or configuracion.RUTA_TEMPORALES / 'correos')


def _encolar_y_enviar(titulo, texto, destinatarios, adjuntos):
    logger = obtener_logger('CORREO')
    remitente = obtener_variable_opcional("email_remitente", "")
    if destinatarios is None:
        destinatarios = destinatarios_evento("resultados")
    destinatarios = _destinatarios(','.join(destinatarios) if not isinstance(destinatarios, str) else destinatarios)
    destinatarios = [correo.casefold() for correo in destinatarios]
    if (not remitente or not destinatarios or len(destinatarios) > 200
            or any('\r' in valor or '\n' in valor for valor in (titulo, remitente))
            or any(parseaddr(correo)[1] != correo or '@' not in correo for correo in (remitente, *destinatarios))):
        raise ValueError('Remitente, destinatarios o encabezados de correo invalidos')
    datos_contexto = actual()
    if datos_contexto.get('solicitud_id') is not None:
        titulo += f' [Solicitud {datos_contexto["solicitud_id"]}]'
    mensaje = MIMEMultipart()
    mensaje["Subject"] = titulo
    mensaje["From"] = remitente
    mensaje["To"] = ", ".join(destinatarios)
    mensaje.attach(MIMEText(texto, "plain", "utf-8"))
    _adjuntar(mensaje, adjuntos)
    componentes = {'titulo': titulo, 'texto': texto, 'remitente': remitente,
        'destinatarios': sorted(destinatarios), 'contexto': datos_contexto,
        'adjuntos': [(parte.get_filename(), hashlib.sha256(parte.get_payload(decode=True)).hexdigest())
                     for parte in mensaje.get_payload()[1:]]}
    identificador = hashlib.sha256(json.dumps(componentes, sort_keys=True).encode()).hexdigest()
    mensaje['Message-ID'] = f'<{identificador}@weyland-bot.local>'
    mensaje['Date'] = formatdate(localtime=False)
    mensaje['X-Weyland-Activacion'] = datos_contexto['activacion_id']
    if datos_contexto.get('solicitud_id') is not None:
        mensaje['X-Weyland-Solicitud'] = str(datos_contexto['solicitud_id'])
    cola = obtener_cola()
    cola.registrar(identificador, remitente, destinatarios, mensaje.as_bytes(), datos_contexto)
    cola.procesar(_enviar_preparado, solo=identificador)
    aceptado = cola.entregado(identificador)
    logger.info('Correo %s: %s', identificador, 'ACEPTADO_SMTP' if aceptado else 'PENDIENTE_O_REQUIERE_REVISION')
    return aceptado


def _enviar_preparado(remitente, destinatarios, mime):
    servidor, durante_datos = None, False
    try:
        actual_remitente = obtener_variable_opcional('email_remitente', '')
        if remitente.casefold() != actual_remitente.casefold():
            raise ErrorEntrega('REMITENTE_CAMBIO', permanente=True)
        password = leer_credencial('EMAIL_PASSWORD') or ''
        if not password:
            raise ErrorEntrega('FALTA_CREDENCIAL', permanente=True)
        servidor = smtplib.SMTP(
            obtener_variable_opcional("smtp_server", ""),
            int(obtener_variable_opcional("smtp_port", "587")),
            timeout=30,
        )
        servidor.starttls(context=ssl.create_default_context())
        servidor.login(remitente, password)
        durante_datos = True
        obtener_logger('CORREO').info('Intento SMTP para %s destinatarios', len(destinatarios))
        return servidor.sendmail(remitente, destinatarios, mime)
    except smtplib.SMTPRecipientsRefused as error:
        return error.recipients
    except smtplib.SMTPResponseException as error:
        raise ErrorEntrega(f'SMTP_{error.smtp_code}', permanente=error.smtp_code >= 500) from None
    except ErrorEntrega:
        raise
    except Exception:
        raise ErrorEntrega('SMTP_SIN_CONFIRMACION' if durante_datos else 'SMTP_NO_DISPONIBLE',
                           incierto=durante_datos) from None
    finally:
        if servidor is not None:
            try:
                servidor.quit()
            except Exception:
                try:
                    servidor.close()
                except Exception:
                    pass


def reintentar_pendientes():
    if not configuracion.MODULO_CORREO:
        return {}
    try:
        return obtener_cola().procesar(_enviar_preparado)
    except Exception:
        obtener_logger('CORREO').error('No se pudo revisar la cola de correos; el negocio no se repetira por este fallo')
        return {}

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
    return notificar(NOMBRE_PROCESO_TEXTO + " - INICIO", "El proceso " + NOMBRE_PROCESO_TEXTO + " ha iniciado." + referencia_web(), evento="inicio")


def referencia_web():
    identificador = actual().get('solicitud_id')
    if identificador is not None:
        return f'\nSolicitud: {identificador}. Ejecucion web: {identificador}.'
    return f'\nActivacion local: {actual().get("activacion_id") or "SIN_ACTIVACION"}.'

def enviar_correo_error(mensaje, adjuntos=None):
    logger = obtener_logger("CORREO")
    logger.info("Enviando correo por error")
    return notificar(NOMBRE_PROCESO_TEXTO + " - ERROR", "El proceso ha fallado:\n\n" + str(mensaje) + referencia_web(), adjuntos, evento="error")

def notificar_resultado_procesamiento(resumen, titulo="Resumen de scraping", mensaje=None):
    if not resumen:
        return
    texto = "Scraping terminado. OK " + str(resumen["ok"]) + " | Errores: " + str(resumen["errores"]) + referencia_web()
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
