"""Envío SMTP y cola persistente de notificaciones por destinatario."""

from config.archivos import crear_error_clasificado, es_error

from types import SimpleNamespace
from html import escape
from email.message import EmailMessage

import hashlib
import json
import re
import smtplib
import sqlite3
import ssl
import time
from config import configuracion
from config.archivos import bloqueo
from config.configuracion import (
    EXCEL_SALIDA,
    NOMBRE_PROCESO_TEXTO,
    actual,
    contexto,
    leer_credencial,
    obtener_logger,
    obtener_variable_opcional,
)
from contextlib import contextmanager
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, parseaddr
from pathlib import Path


# ======================
# ERRORES DE ENTREGA DE CORREOS
# ======================
def crear_error_entrega(codigo, *, incierto=False, permanente=False):
    error = crear_error_clasificado(codigo, 'ErrorEntrega')
    error.codigo, error.incierto, error.permanente = codigo, incierto, permanente
    return error


# ======================
# COLA PERSISTENTE DE CORREOS
# ======================
def crear_cola_correo(carpeta, max_intentos=5, espera=60):
    """Prepara la base de notificaciones y valida los límites de recuperación por destinatario."""

    cola = SimpleNamespace()

    @contextmanager
    def conexion():
        """Abre una conexión transaccional a la cola SMTP y la cierra siempre."""
        con = sqlite3.connect(cola.ruta, timeout=configuracion.CORREO_DB_TIMEOUT)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    def registrar(identificador, remitente, destinatarios, mime, datos_contexto):
        """Conserva un mensaje y sus destinatarios sin duplicar su identificador de envío."""
        if not 0 < len(mime) <= 30 * 1024 * 1024:
            raise ValueError('El correo vacio o mayor que 30 MiB no puede encolarse')
        with cola.conexion() as con:
            con.execute('INSERT OR IGNORE INTO mensajes VALUES (?,?,?,?,?,?)',
                (identificador, time.time(), remitente, json.dumps(datos_contexto), mime, hashlib.sha256(mime).hexdigest()))
            con.executemany("INSERT OR IGNORE INTO destinatarios(mensaje_id,correo,estado) VALUES (?,?,'PENDIENTE')",
                            [(identificador, correo) for correo in destinatarios])
        return identificador

    def entregado(identificador):
        """Indica si todos los destinatarios registrados ya fueron aceptados por SMTP."""
        with cola.conexion() as con:
            filas = con.execute('SELECT estado FROM destinatarios WHERE mensaje_id=?', (identificador,)).fetchall()
        return bool(filas) and all(f['estado'] == 'ENVIADO' for f in filas)

    def cierres_confirmados():
        """Devuelve los cierres de reporte con aceptación de todos los destinatarios."""

        with cola.conexion() as con:
            filas = con.execute("""
                SELECT m.contexto FROM mensajes m
                JOIN destinatarios d ON d.mensaje_id=m.id
                WHERE json_extract(m.contexto, '$.evento')='resultados'
                GROUP BY m.id HAVING COUNT(*)=COUNT(CASE WHEN d.estado='ENVIADO' THEN 1 END)
            """)
            return [json.loads(fila[0]) for fila in filas]

    def procesar(enviar, *, solo=None, ahora=None):
        """Procesa destinatarios pendientes bajo bloqueo y conserva rechazos e intentos inciertos."""
        ahora = time.time() if ahora is None else ahora
        with bloqueo(cola.carpeta / 'envio.lock'):
            with cola.conexion() as con:
                # No hay otro emisor activo bajo este lock. ENVIANDO es una caida previa.
                con.execute("UPDATE destinatarios SET estado='INCIERTO',codigo='INTERRUPCION' WHERE estado='ENVIANDO'")
                sql = "SELECT DISTINCT mensaje_id FROM destinatarios WHERE estado='PENDIENTE' AND proximo<=?"
                args = [ahora]
                if solo is not None:
                    sql += ' AND mensaje_id=?'
                    args.append(solo)
                ids = [f[0] for f in con.execute(sql + ' ORDER BY mensaje_id LIMIT 20', args)]
            for identificador in ids:
                with cola.conexion() as con:
                    mensaje = dict(con.execute('SELECT * FROM mensajes WHERE id=?', (identificador,)).fetchone())
                    filas = con.execute("SELECT correo,intentos FROM destinatarios WHERE mensaje_id=? AND estado='PENDIENTE' AND proximo<=?",
                                        (identificador, ahora)).fetchall()
                    destinatarios = [f['correo'] for f in filas]
                    con.executemany("UPDATE destinatarios SET estado='ENVIANDO',intentos=intentos+1 WHERE mensaje_id=? AND correo=?",
                                    [(identificador, correo) for correo in destinatarios])
                try:
                    if hashlib.sha256(mensaje['mime']).hexdigest() != mensaje['sha256']:
                        raise crear_error_entrega('CONTENIDO_ALTERADO', permanente=True)
                    with contexto(json.loads(mensaje['contexto'])):
                        rechazados = enviar(mensaje['remitente'], destinatarios, mensaje['mime'])
                    if not isinstance(rechazados, dict) or not set(rechazados) <= set(destinatarios):
                        raise crear_error_entrega('RESPUESTA_SMTP_INCOMPATIBLE', incierto=True)
                    estados = {}
                    for fila in filas:
                        correo = fila['correo']
                        if correo not in rechazados:
                            estados[correo] = ('ENVIADO', 0, 'ACEPTADO_SMTP')
                        else:
                            codigo = int(rechazados[correo][0])
                            agotado = codigo >= 500 or fila['intentos'] + 1 >= cola.max_intentos
                            estados[correo] = ('AGOTADO' if agotado else 'PENDIENTE',
                                ahora + min(86400, cola.espera * (2 ** fila['intentos'])), f'SMTP_{codigo}')
                except Exception as error:
                    if not es_error(error, 'ErrorEntrega'):
                        error = crear_error_entrega('RESULTADO_DESCONOCIDO', incierto=True)
                    estados = {}
                    for fila in filas:
                        agotado = error.permanente or fila['intentos'] + 1 >= cola.max_intentos
                        estado = 'INCIERTO' if error.incierto else ('AGOTADO' if agotado else 'PENDIENTE')
                        estados[fila['correo']] = (estado, ahora + min(86400, cola.espera * (2 ** fila['intentos'])), error.codigo)
                with cola.conexion() as con:
                    con.executemany('UPDATE destinatarios SET estado=?,proximo=?,codigo=? WHERE mensaje_id=? AND correo=?',
                        [(estado, proximo, codigo, identificador, correo)
                         for correo, (estado, proximo, codigo) in estados.items()])
        return cola.resumen(solo=solo)

    def resumen(*, solo=None):
        """Cuenta los destinatarios por estado, opcionalmente para un mensaje concreto."""
        with cola.conexion() as con:
            sql = 'SELECT estado,COUNT(*) AS cantidad FROM destinatarios'
            args = ()
            if solo is not None:
                sql += ' WHERE mensaje_id=?'
                args = (solo,)
            return {f['estado']: f['cantidad'] for f in con.execute(sql + ' GROUP BY estado', args)}

    def listar():
        """Devuelve los destinatarios registrados y sus datos operativos de recuperación."""
        with cola.conexion() as con:
            return [dict(f) for f in con.execute('''SELECT mensaje_id,correo,estado,intentos,proximo,codigo
                FROM destinatarios ORDER BY mensaje_id,correo''')]

    def reactivar(identificador):
        """Habilita de nuevo destinatarios agotados sin reenviar los ya entregados o inciertos."""
        with bloqueo(cola.carpeta / 'envio.lock'), cola.conexion() as con:
            con.execute("UPDATE destinatarios SET estado='PENDIENTE',intentos=0,proximo=0 WHERE mensaje_id=? AND estado='AGOTADO'", (identificador,))

    def resolver_incierto(identificador, entregado, destinatario=None):
        """Registra la resolución manual de una entrega incierta por mensaje o destinatario."""
        with bloqueo(cola.carpeta / 'envio.lock'), cola.conexion() as con:
            sql = "UPDATE destinatarios SET estado=?,intentos=0,proximo=0,codigo='RESUELTO_OPERADOR' WHERE mensaje_id=? AND estado='INCIERTO'"
            args = ['ENVIADO' if entregado else 'PENDIENTE', identificador]
            if destinatario is not None:
                sql += ' AND correo=?'
                args.append(destinatario.casefold())
            return con.execute(sql, args).rowcount

    cola.conexion = conexion
    cola.registrar = registrar
    cola.entregado = entregado
    cola.cierres_confirmados = cierres_confirmados
    cola.procesar = procesar
    cola.resumen = resumen
    cola.listar = listar
    cola.reactivar = reactivar
    cola.resolver_incierto = resolver_incierto

    cola.carpeta = Path(carpeta).resolve()
    cola.carpeta.mkdir(parents=True, exist_ok=True)
    cola.ruta = cola.carpeta / 'correos.db'
    cola.max_intentos, cola.espera = max_intentos, espera
    if not 1 <= max_intentos <= 20 or not 1 <= espera <= 86400:
        raise ValueError('Limites de reintento de correo invalidos')
    with cola.conexion() as con:
        con.executescript('''
            CREATE TABLE IF NOT EXISTS mensajes (
                id TEXT PRIMARY KEY, creado REAL NOT NULL, remitente TEXT NOT NULL,
                contexto TEXT NOT NULL, mime BLOB NOT NULL, sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS destinatarios (
                mensaje_id TEXT NOT NULL REFERENCES mensajes(id), correo TEXT NOT NULL,
                estado TEXT NOT NULL CHECK(estado IN ('PENDIENTE','ENVIANDO','ENVIADO','INCIERTO','AGOTADO')),
                intentos INTEGER NOT NULL DEFAULT 0, proximo REAL NOT NULL DEFAULT 0, codigo TEXT,
                PRIMARY KEY(mensaje_id, correo));
            CREATE INDEX IF NOT EXISTS idx_correo_pendiente ON destinatarios(estado, proximo);
        ''')
    return cola



MAX_ADJUNTOS_BYTES = configuracion.obtener_entero_positivo('MAX_ADJUNTOS_BYTES')


# ======================
# FUNCIONES DE DESTINATARIOS Y ADJUNTOS
# ======================
def normalizar_destinatarios(valor):
    """Separa direcciones y elimina duplicados sin distinguir mayúsculas."""
    salida, vistos = [], set()
    for correo in re.split(r"[,;\s]+", valor or ""):
        if correo and correo.casefold() not in vistos:
            salida.append(correo)
            vistos.add(correo.casefold())
    return salida


def destinatarios_evento(evento):
    """Selecciona las listas de distribución de inicio, resultados o fallas."""
    if evento == 'error':
        return normalizar_destinatarios(configuracion.LISTA_DISTRIBUCION_ERROR)
    if evento in ('inicio', 'resultados'):
        return normalizar_destinatarios(configuracion.LISTA_DISTRIBUCION_ERROR + ',' + configuracion.LISTA_DISTRIBUCION_CORREO)
    raise ValueError('Evento de correo desconocido: ' + str(evento))


def adjuntar(mensaje, rutas):
    """Valida los adjuntos y su tamaño total antes de incorporarlos al mensaje."""
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


# ======================
# FUNCIONES DE CONSTRUCCIÓN Y ENVÍO DE CORREOS
# ======================
def rellenar_template(ruta, valores):
    """Reemplaza las claves `{Clave}` de la plantilla sin ejecutar contenido dinámico."""

    plantilla = Path(ruta).read_text(encoding='utf-8')
    for marca, valor in valores.items():
        plantilla = plantilla.replace(marca, valor)
    return plantilla


def preparar_cuerpo_html(mensaje, raiz, proceso, cliente, asunto, texto):
    """Agrega alternativas de texto y HTML con los logos disponibles por Content-ID."""

    raiz_recursos = Path(raiz) / 'recursos'
    valores = {
        '{Process}': escape(proceso),
        '{Message}': escape(asunto),
        '{Detail}': escape(texto).replace('\n', '<br>'),
        '{Client}': escape(cliente or proceso),
    }
    imagenes = []
    for clave, nombre in (('{LogoCliente}', 'logo_cliente'), ('{LogoWeyland}', 'logo_weyland')):
        valores[clave] = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=='
        for extension in ('.png', '.jpg', '.jpeg', '.gif'):
            ruta = raiz_recursos / 'imagenes' / (nombre + extension)
            if ruta.is_file() and not ruta.is_symlink():
                valores[clave] = f'cid:{nombre}'
                imagenes.append((nombre, ruta, 'jpeg' if extension in ('.jpg', '.jpeg') else extension[1:]))
                break
    mensaje.set_content(texto)
    mensaje.add_alternative(rellenar_template(raiz_recursos / 'templates/email_template.html', valores), subtype='html')
    parte_html = mensaje.get_payload()[-1]
    for nombre, ruta, subtipo in imagenes:
        parte_html.add_related(ruta.read_bytes(), maintype='image', subtype=subtipo, cid=f'<{nombre}>')


def enviar_email(titulo, texto, destinatarios=None, adjuntos=None, *, evento=None):
    """Valida y encola el mensaje dentro de su contexto y procesa su entrega sin duplicarlo."""
    if not configuracion.MODULO_CORREO:
        return False
    with contexto():
        logger = obtener_logger('CORREO')
        remitente = obtener_variable_opcional("email_remitente", "")
        if destinatarios is None:
            destinatarios = destinatarios_evento("resultados")
        destinatarios = normalizar_destinatarios(','.join(destinatarios) if not isinstance(destinatarios, str) else destinatarios)
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
        mensaje["From"] = f"Weyland <{remitente}>"
        mensaje["To"] = ", ".join(destinatarios)
        cuerpo = EmailMessage()
        preparar_cuerpo_html(cuerpo, configuracion.RUTA_PROYECTO, NOMBRE_PROCESO_TEXTO,
                             obtener_variable_opcional("NOMBRE_CLIENTE", ""), titulo, texto)
        mensaje.attach(cuerpo)
        adjuntar(mensaje, adjuntos)
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
        id_ejecucion = datos_contexto['activacion_id']
        if datos_contexto.get('solicitud_id') is None:
            id_ejecucion = configuracion.IDENTIFICADOR_LOCAL.get() or id_ejecucion
        cola.registrar(identificador, remitente, destinatarios, mensaje.as_bytes(),
                       {**datos_contexto, 'evento': evento, 'id_ejecucion': id_ejecucion})
        cola.procesar(enviar_preparado, solo=identificador)
        aceptado = cola.entregado(identificador)
        if aceptado:
            logger.info('CORREO %s: ACEPTADO_SMTP', identificador)
        else:
            logger.warning('CORREO_PENDIENTE %s: mensaje y adjuntos conservados en la cola. '
                           'Revisar con correos-estado; recuperar con correos-reintentar. '
                           'No repetir el proceso ni la entrega web por este fallo.', identificador)
        return aceptado


# ======================
# FUNCIONES DE CONFIGURACIÓN DE LA COLA
# ======================
def obtener_cola():
    """Construye la cola de notificaciones con la ruta y los reintentos configurados."""
    return crear_cola_correo(ruta_cola(), configuracion.obtener_entero_positivo('CORREO_MAX_INTENTOS', 5),
                      configuracion.obtener_entero_positivo('CORREO_REINTENTO_SEGUNDOS', 60))


def ruta_cola():
    """Resuelve el directorio persistente de notificaciones pendientes."""
    return Path(obtener_variable_opcional('CORREO_PENDIENTES_DIR') or configuracion.RUTA_TEMPORALES / 'correos')


# ======================
# FUNCIONES DE TRANSPORTE SMTP
# ======================
def enviar_preparado(remitente, destinatarios, mime):
    """Envía el MIME por SMTP con TLS y traduce fallos de entrega a errores recuperables."""
    servidor, durante_datos = None, False
    try:
        actual_remitente = obtener_variable_opcional('email_remitente', '')
        if remitente.casefold() != actual_remitente.casefold():
            raise crear_error_entrega('REMITENTE_CAMBIO', permanente=True)
        password = leer_credencial('EMAIL_PASSWORD') or ''
        if not password:
            raise crear_error_entrega('FALTA_CREDENCIAL', permanente=True)
        servidor = smtplib.SMTP(
            obtener_variable_opcional("smtp_server", ""),
            int(obtener_variable_opcional("smtp_port", "587")),
            timeout=configuracion.SMTP_TIMEOUT,
        )
        servidor.starttls(context=ssl.create_default_context())
        servidor.login(remitente, password)
        durante_datos = True
        obtener_logger('CORREO').info('INTENTO SMTP PARA %s DESTINATARIOS', len(destinatarios))
        return servidor.sendmail(remitente, destinatarios, mime)
    except smtplib.SMTPRecipientsRefused as error:
        return error.recipients
    except smtplib.SMTPResponseException as error:
        raise crear_error_entrega(f'SMTP_{error.smtp_code}', permanente=error.smtp_code >= 500) from None
    except Exception as error:
        if es_error(error, 'ErrorEntrega'):
            raise
        raise crear_error_entrega('SMTP_SIN_CONFIRMACION' if durante_datos else 'SMTP_NO_DISPONIBLE',
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


# ======================
# FUNCIONES DE RECUPERACIÓN DE CORREOS PENDIENTES
# ======================
def reintentar_pendientes(*, solo=None, propagar_error=False):
    """Reenvia el MIME conservado; no consulta la web ni invoca el negocio.

    solo limita envio y resumen al mensaje seleccionado. Los destinatarios
    INCIERTO requieren resolucion manual; nunca los reactiva este metodo.
    """
    if not configuracion.MODULO_CORREO:
        return {}
    try:
        return obtener_cola().procesar(enviar_preparado, solo=solo)
    except Exception:
        obtener_logger('CORREO').error('NO SE PUDO REVISAR LA COLA DE CORREOS; EL NEGOCIO NO SE REPETIRA POR ESTE FALLO')
        if propagar_error:
            raise
        return {}


# ======================
# FUNCIONES DE NOTIFICACIONES DEL PROCESO
# ======================
def notificar(titulo, texto, adjuntos=None, evento="resultados"):
    """Aplica los flags y destinatarios del evento y registra un fallo de envío sin ocultarlo."""
    if not configuracion.MODULO_CORREO:
        return False
    try:
        destinatarios = destinatarios_evento(evento)
        if not destinatarios:
            obtener_logger("CORREO").warning('CORREO %s OMITIDO: LISTA DE DESTINATARIOS VACIA', evento)
            return False
        return enviar_email(titulo, texto, destinatarios, adjuntos, evento=evento)
    except Exception:
        obtener_logger("CORREO").exception('FALLO DE CORREO %s; EL PROCESAMIENTO CONTINUA', evento)
        return False


def enviar_correo_inicial():
    """Construye el aviso inicial con la referencia de ejecución cuando está habilitado."""
    if not configuracion.MODULO_CORREO_INICIAL or not configuracion.MODULO_CORREO:
        return False
    logger = obtener_logger("CORREO")
    logger.info('ENVIANDO CORREO INICIAL')
    return notificar(f"Inicio del proceso: {NOMBRE_PROCESO_TEXTO}", "El proceso " + NOMBRE_PROCESO_TEXTO + " ha iniciado." + referencia_web(), evento="inicio")


def referencia_web():
    """Genera la referencia de solicitud web o de activación local para la notificación."""
    identificador = actual().get('solicitud_id')
    if identificador is not None:
        return f'\nSolicitud: {identificador}. Ejecucion web: {identificador}.'
    return f'\nActivacion local: {actual().get("activacion_id") or "SIN_ACTIVACION"}.'


def enviar_correo_error(mensaje, adjuntos=None):
    """Construye el aviso de fallo y sus adjuntos para la lista de errores."""
    logger = obtener_logger("CORREO")
    logger.info('ENVIANDO CORREO POR ERROR')
    return notificar(f"Error del proceso: {NOMBRE_PROCESO_TEXTO}",
                     f"El proceso ha fallado:\n\n{mensaje}{referencia_web()}", adjuntos, evento="error")


def enviar_correo_reporte(resumen=None, mensaje=None, adjuntos=None):
    """Resume búsquedas e incidencias y adjunta el Excel y las capturas disponibles."""
    titulo = f"Fin del proceso: {NOMBRE_PROCESO_TEXTO}"
    if resumen is None:
        return notificar(titulo, mensaje, adjuntos, evento="resultados")
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
    adjuntos = [EXCEL_SALIDA]
    capturas = resumen.get("capturas") or []
    adjuntos.extend(capturas)
    if capturas:
        texto += "\n\nCapturas de error adjuntas: " + str(len(capturas))
    return notificar(titulo, texto, adjuntos, evento="resultados")

def enviar_correo_sin_datos():
    """Avisa que no hay productos de entrada para procesar."""

    return notificar(f"Sin datos para procesar: {NOMBRE_PROCESO_TEXTO}",
                     "No se encontraron archivos de entrada." + referencia_web(), evento="resultados")
