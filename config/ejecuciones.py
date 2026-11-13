"""Coordinación de solicitudes, estados, exportación y recuperación."""

import json
import math
import os
from collections.abc import Callable
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
    ErrorWeyland,
    FORMATO_FECHA_ARCHIVO,
    RUTA_BOT_INPUT_API,
    RUTA_ENTRADA_BASE,
    RUTA_METADATOS_EXPORT,
    bloqueo,
    confinar,
    guardar_json,
    sha256,
)
from config.backend import (
    ClienteColaWeyland,
    MAX_BYTES,
    entero_configurado,
    id_positivo,
    validar_datos_publicables,
)
from config.configuracion import BACKEND_MAX_INPUTS_BYTES
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile


#----------------------------------------------
# CONTRATO DEL RESULTADO DEL BOT
#----------------------------------------------
@dataclass(frozen=True)
class ResultadoBot:
    """Salida del negocio: archivos, resumen y opciones de exportación."""
    archivos: tuple
    resumen: dict = field(default_factory=lambda: {'procesados': 1, 'correctos': 1, 'errores': 0})
    detalles: dict = field(default_factory=dict)
    permite_sin_export: bool = False
    incluir_metadatos_export: bool = True

#----------------------------------------------
# FUNCIONES DE EXPORTACIÓN
#----------------------------------------------
def exportar(carpeta, resultado, estado):
    """Valida los artefactos y prepara el ZIP o los metadatos de un resultado sin exportación."""
    if not isinstance(resultado, ResultadoBot):
        raise ErrorWeyland('El bot debe entregar ResultadoBot')
    if type(resultado.incluir_metadatos_export) is not bool:
        raise ErrorWeyland('incluir_metadatos_export debe ser booleano')
    if not resultado.archivos and resultado.permite_sin_export is not True:
        raise ErrorWeyland('El bot debe entregar ResultadoBot con al menos un archivo')
    resumen = resultado.resumen
    if any(type(resumen.get(k)) is not int or resumen[k] < 0 for k in ('procesados', 'correctos', 'errores')):
        raise ErrorWeyland('Resumen de negocio invalido')
    if not resultado.archivos:
        metadatos = {k: estado[k] for k in ('solicitud_id', 'ejecucion_id', 'ejecucion_bot_id')}
        metadatos.update(estado='COMPLETADA', resumen=resumen, export=None, resultado=resultado.detalles)
        if len(json.dumps(metadatos, allow_nan=False).encode('utf-8')) > MAX_BYTES:
            raise ErrorWeyland('Resultado JSON supera el limite de entrega')
        return None, metadatos
    archivos = []
    for ruta in resultado.archivos:
        ruta = Path(ruta)
        if ruta.is_absolute():
            try:
                ruta = ruta.relative_to(carpeta)
            except ValueError:
                raise ErrorWeyland('Artefacto fuera de la ejecucion') from None
        archivo = confinar(carpeta, ruta)
        if (not archivo.is_file() or any(p in (CARPETA_INPUT, CARPETA_LOGS) for p in ruta.parts)
                or archivo.name.startswith('.env') or archivo.name in (ARCHIVO_ESTADO, ARCHIVO_BLOQUEO)
                or archivo.suffix.lower() in (
                '.db', '.sqlite', '.sqlite3', '.env', '.pem', '.key', '.log')):
            raise ErrorWeyland('Artefacto de negocio no permitido')
        if ruta.as_posix() == RUTA_METADATOS_EXPORT:
            raise ErrorWeyland('Nombre de artefacto reservado')
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
        raise ErrorWeyland('Reporte supera el limite de 20 MiB')
    metadatos = {k: datos[k] for k in ('solicitud_id', 'ejecucion_id', 'ejecucion_bot_id', 'estado', 'resumen')}
    metadatos['export'] = {'nombre': nombre, 'content_type': 'application/zip',
                          'size_bytes': reporte.stat().st_size, 'sha256': sha256(reporte)}
    return reporte, metadatos


SISTEMA_POR_ESTADO = {
    'PENDIENTE': 'EN_PROCESO', 'EN_EJECUCION': 'EN_PROCESO', 'EN_REPROCESO': 'EN_PROCESO',
    'FINALIZADO': 'COMPLETADA', 'FINALIZADO_CON_OBSERVACIONES': 'COMPLETADA',
    'CANCELADO': 'CANCELADA', 'INTERRUMPIDO': 'ERROR', 'ERROR': 'ERROR',
}


#----------------------------------------------
# FUNCIONES ESTADOS
#----------------------------------------------
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
        raise ErrorWeyland('Confirmacion de estado incompatible; conservar checkpoint')


def publicar_estado(cliente, estado, journal, nuevo, mensaje, resumen=None):
    """Un POST incierto se concilia antes de otra escritura; nunca se repite a ciegas."""
    pendiente = estado.get('estado_bot_pendiente')
    if pendiente:
        detalle = cliente.consultar_ejecucion(estado['ejecucion_id'])
        if (detalle.get('id_externo') != estado['id_externo']
                or detalle.get('estado_bot') != pendiente['estado']
                or detalle.get('estado') != SISTEMA_POR_ESTADO[pendiente['estado']]):
            raise ErrorWeyland('Cambio de estado incierto; requiere revision antes de reenviar')
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
    except ErrorWeyland as error:
        # Rechazo conocido: no afirmar que el cambio se aplico.
        if error.tipo == 'HTTP' and error.status_code in (400, 401, 403, 404, 409, 422):
            estado.pop('estado_bot_pendiente')
            guardar_json(journal, estado)
        raise
    estado['estado_bot_publicado'] = nuevo
    estado.pop('estado_bot_pendiente')
    guardar_json(journal, estado)


#----------------------------------------------
# FUNCIONES DE INTEGRIDAD Y EMPAQUETADO DEL RESULTADO
#----------------------------------------------
def snapshot_resultado(carpeta, resultado):
    """Valida la salida del negocio y registra rutas, tamaños y hashes para recuperarla sin repetirlo."""
    if not isinstance(resultado, ResultadoBot) or not isinstance(resultado.resumen, dict):
        raise ErrorWeyland('El adaptador debe devolver ResultadoBot')
    if type(resultado.incluir_metadatos_export) is not bool:
        raise ErrorWeyland('incluir_metadatos_export debe ser booleano')
    if any(type(resultado.resumen.get(k)) is not int or resultado.resumen[k] < 0
           for k in ('procesados', 'correctos', 'errores')):
        raise ErrorWeyland('Resumen de negocio incompatible')
    if not resultado.archivos and resultado.permite_sin_export is not True:
        raise ErrorWeyland('El proceso termino sin su export obligatorio')
    validar_datos_publicables(resultado.detalles)
    json.dumps(resultado.detalles, allow_nan=False)
    archivos = []
    for origen in resultado.archivos:
        origen = Path(origen)
        if origen.is_symlink():
            raise ErrorWeyland('No se exportan enlaces simbolicos')
        try:
            relativo = origen.relative_to(carpeta) if origen.is_absolute() else origen
        except ValueError:
            raise ErrorWeyland('Resultado fuera de la ejecucion') from None
        archivo = confinar(carpeta, relativo)
        if (not archivo.is_file() or any(p in (CARPETA_INPUT, CARPETA_PARAMETROS, CARPETA_LOGS) for p in relativo.parts)
                or archivo.name.startswith('.env') or archivo.name in (ARCHIVO_CONTEXTO, ARCHIVO_FINALIZACION)
                or archivo.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.log', '.pem', '.key')):
            raise ErrorWeyland('Archivo de resultado no permitido')
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
            raise ErrorWeyland('Un resultado cambio despues del negocio; requiere revision')
        archivos.append(archivo)
    resultado = ResultadoBot(tuple(archivos), guardado['resumen'],
                             {**guardado['detalles'], 'id_externo': estado['id_externo']}, guardado['permite_sin_export'],
                             guardado.get('incluir_metadatos_export', True))
    reporte, metadatos = exportar(carpeta, resultado, estado)
    if reporte is None:
        reporte = carpeta / ('resultado_' + datetime.now().strftime(FORMATO_FECHA_ARCHIVO) + '.json')
        guardar_json(reporte, {'ejecucion_id': estado['ejecucion_id'], 'id_externo': estado['id_externo'],
            'proceso_codigo': estado['trabajo']['proceso_codigo'], 'estado_negocio': 'COMPLETADO',
            'tiene_export_negocio': False, 'resumen': resultado.resumen, 'resultado': resultado.detalles})
    if not 1 <= reporte.stat().st_size <= MAX_BYTES:
        raise ErrorWeyland('Resultado fuera del limite de entrega')
    return {'ruta': reporte.relative_to(carpeta).as_posix(), 'size_bytes': reporte.stat().st_size,
            'sha256': sha256(reporte)}


#----------------------------------------------
# CONTRATO DEL PROCESO
#----------------------------------------------
@dataclass(frozen=True)
class ProcesoBot:
    """Contrato del adaptador de negocio y sus requisitos de entrada y estados."""
    ejecutar: Callable
    requiere_input: bool | None = None
    extensiones: tuple = EXTENSIONES_INPUT_POR_DEFECTO
    informar_estados: bool = False


FASES = {'TOMA_INCIERTA', 'TOMA_RECHAZADA', 'SIN_TRABAJO', 'ASIGNACION_INCOMPATIBLE', 'DESCARGANDO',
         'PREPARADO', 'EJECUTANDO', 'EXPORT_PENDIENTE', 'ENTREGA_LISTA', 'ENTREGA_INCIERTA',
         'ERROR_LISTO', 'ERROR_INCIERTO', 'COMPLETADA', 'ERROR', 'CANCELADO', 'INTERRUMPIDO'}


FINALES = {'COMPLETADA', 'ERROR', 'SIN_TRABAJO', 'CANCELADO', 'INTERRUMPIDO', 'TOMA_RECHAZADA'}


#----------------------------------------------
# FUNCIONES DE CHECKPOINT Y CONTEXTO DE ERROR
#----------------------------------------------
def leer(ruta):
    """Lee un checkpoint y detiene el flujo si es ilegible o no contiene un objeto JSON."""
    try:
        datos = json.loads(ruta.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise ErrorWeyland('Checkpoint ilegible; no se sobrescribira ni se tomara otro trabajo') from None
    if not isinstance(datos, dict):
        raise ErrorWeyland('Checkpoint incompatible')
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
    """Comprueba que el checkpoint pertenezca a la cuenta, backend, máquina y proceso actuales."""
    if (any(estado.get(k) != v for k, v in identidad.items()) or estado.get('fase') not in FASES
            or not isinstance(estado.get('id_externo'), str)
            or len(estado['id_externo']) != 32
            or any(c not in '0123456789abcdef' for c in estado['id_externo'])):
        raise ErrorWeyland('Checkpoint de otra cuenta, VM, contrato o proceso; requiere revision')
    if estado['fase'] not in ('TOMA_INCIERTA', 'TOMA_RECHAZADA', 'SIN_TRABAJO', 'ASIGNACION_INCOMPATIBLE'):
        id_positivo(estado.get('ejecucion_id'))
        if estado.get('solicitud_id') != estado['ejecucion_id']:
            raise ErrorWeyland('IDs incompatibles en el checkpoint')


#----------------------------------------------
# FUNCIONES DE POLÍTICA DE CORREOS
#----------------------------------------------
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
    """Aplica la política de correo; propaga fallos iniciales y conserva el cierre ante fallos finales."""
    if notificar is not None and correo_permitido(estado):
        try:
            notificar(evento, dict(estado), carpeta)
        except Exception:
            if evento == 'inicio':
                raise
            # Un correo final pendiente no repite negocio ni entrega web.
            pass


#----------------------------------------------
# FUNCIONES DE ASIGNACIÓN Y RECUPERACIÓN DE SOLICITUDES
#----------------------------------------------
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
                raise ErrorWeyland('El ID ya tiene evidencia de otra activacion; no se repetira')
        carpeta.mkdir(parents=True, exist_ok=True)
        guardar_json(carpeta / ARCHIVO_CONTEXTO, contexto)
        estado.update(trabajo=trabajo, ejecucion_id=identificador, solicitud_id=identificador,
                      ejecucion_bot_id=estado['id_externo'], fase='DESCARGANDO',
                      informar_estados=procesos[trabajo['proceso_codigo']].informar_estados)
    except (ErrorWeyland, ValueError, TypeError):
        estado['fase'] = 'ASIGNACION_INCOMPATIBLE'
        guardar_json(journal, estado)
        raise ErrorWeyland('La asignacion ya pudo ocurrir pero es incompatible; revisar la web, no tomar otra',
                           tipo='ASIGNACION_INCOMPATIBLE') from None
    guardar_json(journal, estado)


def comprobar_detalle(datos, estado):
    """Verifica que el detalle remoto coincida con el ID, correlación, proceso y máquina locales."""
    if (datos.get('id') != estado['ejecucion_id'] or datos.get('id_externo') != estado['id_externo']
            or not isinstance(datos.get('proceso'), dict)
            or datos['proceso'].get('codigo') != estado['trabajo']['proceso_codigo']
            or not isinstance(datos.get('maquina'), dict)
            or datos['maquina'].get('id') != estado['maquina_id']):
        raise ErrorWeyland('La conciliacion no coincide con la asignacion local')


def recuperar_toma(cliente, estado):
    """Reconstruye una asignación incierta y sus inputs a partir de un detalle remoto inequívoco."""
    detalle = cliente.buscar_correlacion(estado['maquina_id'], estado['id_externo'])
    if detalle.get('estado') != 'EN_PROCESO':
        raise ErrorWeyland('Toma incierta en otro estado; requiere revision, no se repetira siguiente')
    archivos = detalle.get('archivos')
    if not isinstance(archivos, list):
        raise ErrorWeyland('Detalle sin archivos compatibles para recuperar la toma')
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


#----------------------------------------------
# FUNCIONES DE DESCARGA Y VALIDACIÓN DE ENTRADAS
#----------------------------------------------
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
        raise ErrorWeyland('Checkpoint con inputs incompatibles')
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
                raise ErrorWeyland('Un input descargado cambio; requiere revision')
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
            raise ErrorWeyland('No estan preparados todos los inputs de la solicitud')
    rutas, total = [], 0
    for registro in registros:
        ruta = confinar(carpeta, registro['ruta'])
        if (not ruta.is_relative_to(confinar(carpeta, RUTA_ENTRADA_BASE).parent)
                or ruta.is_symlink() or not ruta.is_file()
                or ruta.stat().st_size != registro['size_bytes'] or sha256(ruta) != registro['sha256']):
            raise ErrorWeyland('Input alterado o no permitido')
        total += registro['size_bytes']
        if total > BACKEND_MAX_INPUTS_BYTES:
            raise ErrorWeyland('Los inputs preparados superan el limite total')
        rutas.append(ruta)
    return rutas[0] if len(rutas) == 1 else tuple(rutas) if rutas else None


#----------------------------------------------
# FUNCIONES DE CONCILIACIÓN DE ENTREGAS
#----------------------------------------------
def conciliar(cliente, estado, carpeta):
    """Confirma el cierre remoto y la huella del reporte antes de resolver una entrega incierta."""
    detalle = cliente.consultar_ejecucion(estado['ejecucion_id'])
    comprobar_detalle(detalle, estado)
    if estado.get('informar_estados') and detalle.get('estado_bot') in ('CANCELADO', 'INTERRUMPIDO'):
        final = detalle['estado_bot']
        esperado = 'CANCELADA' if final == 'CANCELADO' else 'ERROR'
        if detalle.get('estado') != esperado:
            raise ErrorWeyland('Cierre del bot incompatible con el estado del sistema')
        estado.update(fase=final, estado_bot_publicado=final)
        estado.pop('estado_bot_pendiente', None)
        return
    if estado['fase'] == 'ERROR_INCIERTO':
        if detalle.get('estado') != 'ERROR':
            raise ErrorWeyland('Aviso de error incierto; requiere revision antes de reenviar')
        estado['fase'] = 'ERROR'
        if estado.get('informar_estados'):
            if detalle.get('estado_bot') != 'ERROR':
                raise ErrorWeyland('No se confirmo el estado de error del bot')
            estado['estado_bot_publicado'] = 'ERROR'
            estado.pop('estado_bot_pendiente', None)
        return
    if detalle.get('estado') != 'COMPLETADA':
        raise ErrorWeyland('Entrega incierta: el estado no prueba que pueda reenviarse; requiere revision')
    if estado.get('informar_estados') and detalle.get('estado_bot') != estado.get('estado_bot_objetivo'):
        raise ErrorWeyland('El estado final web no coincide con la entrega local')
    archivos = detalle.get('archivos')
    reportes = [a for a in archivos if isinstance(a, dict) and a.get('tipo') == 'REPORTE'] if isinstance(archivos, list) else []
    if len(reportes) != 1:
        raise ErrorWeyland('Reporte de conciliacion ausente o ambiguo')
    snapshot = cliente.descargar_reporte_confirmado(estado['ejecucion_id'], reportes[0]['id'],
                                                   carpeta / 'verificacion_backend.bin')
    if snapshot != {k: estado['entrega'][k] for k in ('size_bytes', 'sha256')}:
        raise ErrorWeyland('El reporte web no coincide con la entrega local; requiere revision')
    estado['fase'] = 'COMPLETADA'
    if estado.get('informar_estados'):
        estado['estado_bot_publicado'] = detalle['estado_bot']


#----------------------------------------------
# FUNCIONES DE ORQUESTACIÓN DE LA COLA
#----------------------------------------------
def ejecutar_cola(cliente, maquina_id, trabajos, procesos, *, notificar=None):
    """Ejecuta el flujo y prepara el aviso de error ante fallos transitorios, conservando el checkpoint."""
    try:
        return procesar_solicitud_pendiente(cliente, maquina_id, trabajos, procesos, notificar=notificar)
    except (ErrorWeyland, OSError) as error:
        if isinstance(error, ErrorWeyland) and error.tipo not in ('RED', 'HTTP'):
            raise
        temporal = (not isinstance(error, ErrorWeyland) or error.tipo == 'RED'
                    or error.status_code in (408, 429) or error.status_code >= 500)
        journal = Path(trabajos) / ARCHIVO_COORDINADOR
        if temporal and journal.is_file():
            with bloqueo(Path(trabajos) / ARCHIVO_BLOQUEO):
                estado = leer(journal)
                if (estado.get('informar_estados') and estado.get('ejecucion_id')
                        and estado['fase'] not in FINALES | {'ENTREGA_INCIERTA', 'ERROR_INCIERTO'}):
                    estado.update(fase='ERROR_LISTO', estado_final_pendiente='ERROR',
                        mensaje_error='Problema de comunicacion o disponibilidad; no se completo la operacion.')
                    guardar_json(journal, estado)
        raise


def procesar_solicitud_pendiente(cliente, maquina_id, trabajos, procesos, *, notificar=None):
    """Una invocacion toma como maximo un trabajo; los POST nunca se repiten solos."""
    id_positivo(maquina_id)
    if not procesos or any(not isinstance(k, str) or not k.strip() or not isinstance(p, ProcesoBot)
                          or not callable(p.ejecutar) or p.requiere_input is not None and type(p.requiere_input) is not bool
                          or type(p.informar_estados) is not bool for k, p in procesos.items()):
        raise ValueError('Configurar una tabla explicita de procesos y adaptadores')
    ruta_base = Path(trabajos)
    if ruta_base.is_symlink():
        raise ErrorWeyland('Directorio operativo no permitido')
    base = ruta_base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    identidad = {'version': 1, 'contrato': 'cola_actual', 'backend': cliente.config['url'],
                 'cuenta_sha256': cliente.config['cuenta_sha256'],
                 'maquina_id': maquina_id, 'codigos': sorted(procesos)}
    journal = base / ARCHIVO_COORDINADOR
    with bloqueo(base / ARCHIVO_BLOQUEO), contexto_de_error() as referencia:
        estado = leer(journal) if journal.exists() else None
        referencia['estado'] = estado
        if estado is not None:
            validar_checkpoint(estado, identidad)
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
            except ErrorWeyland as error:
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
            raise ErrorWeyland('Asignacion incompatible pendiente de revision; no se tomara otro trabajo',
                               tipo='ASIGNACION_INCOMPATIBLE')
        carpeta = confinar(base, CARPETA_EJECUCIONES + '/' + str(id_positivo(estado['ejecucion_id'])))
        if estado['fase'] in ('ENTREGA_INCIERTA', 'ERROR_INCIERTO'):
            conciliar(cliente, estado, carpeta)
            guardar_json(journal, estado)
        if estado['fase'] == 'EJECUTANDO':
            if not estado.get('informar_estados'):
                raise ErrorWeyland('Negocio interrumpido; conserva su evidencia y requiere revision, no se repetira')
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
            except ErrorWeyland as error:
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
            if estado.get('informar_estados'):
                reproceso = estado['trabajo'].get('reproceso_de')
                publicar_estado(cliente, estado, journal, 'EN_REPROCESO' if reproceso else 'EN_EJECUCION',
                    'Reprocesando una solicitud anterior' if reproceso else 'Ejecutando el trabajo asignado')
            estado['fase'] = 'EJECUTANDO'
            guardar_json(journal, estado)
            try:
                entrada = entradas_preparadas(carpeta, estado)
                resultado = proceso.ejecutar(carpeta, entrada, estado['trabajo']['parametros'])
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
                raise ErrorWeyland('La entrega cambio; requiere revision')
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
            raise ErrorWeyland('Fase inesperada; no se repetira el proceso')
        guardar_json(carpeta / ARCHIVO_FINALIZACION, {'fase': estado['fase'], 'ejecucion_id': estado['ejecucion_id'],
                                                   'id_externo': estado['id_externo']})
        avisar(notificar, 'resultados' if estado['fase'] == 'COMPLETADA' else 'fallas', estado, carpeta)
        return estado


#----------------------------------------------
# FUNCIONES DE MIGRACIÓN Y ARRANQUE CONFIGURADO
#----------------------------------------------
def verificar_migracion(base):
    """No ocultar trabajos del contrato anterior al estrenar un namespace."""
    for ruta in base.rglob(ARCHIVO_ESTADO):
        try:
            datos = json.loads(ruta.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise ErrorWeyland('Hay un journal anterior ilegible; revisar antes de migrar') from None
        if not isinstance(datos, dict) or datos.get('fase') not in ('COMPLETADA', 'ERROR'):
            raise ErrorWeyland('Hay una ejecucion pendiente del contrato anterior; revisar antes de migrar')
    for ruta in base.rglob(ARCHIVO_ACTIVO):
        raise ErrorWeyland('Existe un journal heredado; revisar antes de habilitar cola_actual')


def ejecutar_cola_configurada(fabrica, valores, raiz, procesos, *, notificar=None):
    """Valida contrato y rutas, autentica el cliente y coordina una solicitud con la configuración recibida."""
    if valores.get('BACKEND_CONTRATO') != 'cola_actual' or valores.get('BACKEND_AUTH_MODE') != 'weyland':
        raise ValueError('Configurar BACKEND_CONTRATO=cola_actual y BACKEND_AUTH_MODE=weyland')
    if str(valores.get('BACKEND_REUTILIZAR_ULTIMA') or 'false').lower() != 'false':
        raise ValueError('No se reutilizan solicitudes anteriores')
    maquina = entero_configurado(valores, 'BACKEND_MAQUINA_ID')
    raiz = Path(raiz).resolve()
    ruta = Path(valores.get('BACKEND_TRABAJOS_DIR') or 'temporales/weyland')
    base = (ruta if ruta.is_absolute() else raiz / ruta).resolve()
    if str(valores.get('ENTORNO') or '').upper() == 'PRODUCCION' and base.is_relative_to(raiz):
        raise ValueError('En produccion BACKEND_TRABAJOS_DIR debe estar fuera del repositorio')
    verificar_migracion(base)
    with fabrica() as cliente:
        if not isinstance(cliente, ClienteColaWeyland):
            raise ErrorWeyland('La fabrica no corresponde al contrato cola_actual')
        if cliente.config['bot_auth_mode'] != 'jwt':
            raise ErrorWeyland('Se requiere autenticacion JWT antes de tomar trabajo')
        cliente.verificar()
        return ejecutar_cola(cliente, maquina, base / 'cola_actual' / ('maquina_' + str(maquina)),
                              procesos, notificar=notificar)
