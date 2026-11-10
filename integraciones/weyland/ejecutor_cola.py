"""Coordinador portable del contrato actual; nunca reintenta POST inciertos.

El callback de negocio y la notificación son inyectados por cada proyecto.
"""
from dataclasses import dataclass
from collections.abc import Callable
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .archivos import bloqueo, confinar, guardar_json, sha256
from .cliente import id_positivo
from .errores import ErrorWeyland, ErrorRedWeyland, ErrorHTTPWeyland
from .resultado_cola import snapshot_resultado, empaquetar_resultado


@dataclass(frozen=True)
class ProcesoBot:
    ejecutar: Callable
    requiere_input: bool | None = None
    extensiones: tuple = ('.xlsx',)


FASES = {'TOMA_INCIERTA', 'SIN_TRABAJO', 'ASIGNACION_INCOMPATIBLE', 'DESCARGANDO',
         'PREPARADO', 'EJECUTANDO', 'EXPORT_PENDIENTE', 'ENTREGA_LISTA', 'ENTREGA_INCIERTA',
         'ERROR_LISTO', 'ERROR_INCIERTO', 'COMPLETADA', 'ERROR'}
FINALES = {'COMPLETADA', 'ERROR', 'SIN_TRABAJO'}


def _leer(ruta):
    try:
        datos = json.loads(ruta.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise ErrorWeyland('Checkpoint ilegible; no se sobrescribira ni se tomara otro trabajo') from None
    if not isinstance(datos, dict):
        raise ErrorWeyland('Checkpoint incompatible')
    return datos


def _identidad(cliente, maquina_id, procesos):
    return {'version': 1, 'contrato': 'cola_actual', 'backend': cliente.config.url,
            'cuenta_sha256': hashlib.sha256(cliente.config.correo.casefold().encode()).hexdigest(),
            'maquina_id': maquina_id, 'codigos': sorted(procesos)}


@contextmanager
def _contexto_de_error():
    referencia = {}
    try:
        yield referencia
    except Exception as error:
        estado = referencia.get('estado') or {}
        if estado.get('id_externo') and not hasattr(error, 'contexto_trazabilidad'):
            error.contexto_trazabilidad = {'solicitud_id': estado.get('ejecucion_id'),
                'ejecucion_id_web': estado.get('ejecucion_id'), 'activacion_id': estado['id_externo']}
        raise


def _validar_estado(estado, identidad):
    if (any(estado.get(k) != v for k, v in identidad.items()) or estado.get('fase') not in FASES
            or not isinstance(estado.get('id_externo'), str)
            or len(estado['id_externo']) != 32
            or any(c not in '0123456789abcdef' for c in estado['id_externo'])):
        raise ErrorWeyland('Checkpoint de otra cuenta, VM, contrato o proceso; requiere revision')
    if estado['fase'] not in ('TOMA_INCIERTA', 'SIN_TRABAJO', 'ASIGNACION_INCOMPATIBLE'):
        id_positivo(estado.get('ejecucion_id'))
        if estado.get('solicitud_id') != estado['ejecucion_id']:
            raise ErrorWeyland('IDs incompatibles en el checkpoint')


def _avisar(notificar, evento, estado, carpeta):
    if notificar is not None:
        try:
            notificar(evento, dict(estado), carpeta)
        except Exception:
            # La cola SMTP pertenece al bot; una falla de notificación no repite negocio.
            pass


def _carpeta(base, estado):
    return confinar(base, 'ejecuciones/' + str(id_positivo(estado['ejecucion_id'])))


def _asignar(cliente, datos, estado, procesos, base, journal):
    try:
        trabajo = cliente.validar_asignacion(datos, estado['maquina_id'], estado['id_externo'], procesos)
        identificador = trabajo['ejecucion_id']
        carpeta = confinar(base, 'ejecuciones/' + str(identificador))
        contexto = {'solicitud_id': identificador, 'ejecucion_id_web': identificador,
                    'activacion_id': estado['id_externo']}
        if carpeta.exists() and any(carpeta.iterdir()):
            ruta = carpeta / 'contexto.json'
            if not ruta.is_file() or _leer(ruta) != contexto:
                raise ErrorWeyland('El ID ya tiene evidencia de otra activacion; no se repetira')
        carpeta.mkdir(parents=True, exist_ok=True)
        guardar_json(carpeta / 'contexto.json', contexto)
        estado.update(trabajo=trabajo, ejecucion_id=identificador, solicitud_id=identificador,
                      ejecucion_bot_id=estado['id_externo'], fase='DESCARGANDO')
    except (ErrorWeyland, ValueError, TypeError):
        estado['fase'] = 'ASIGNACION_INCOMPATIBLE'
        guardar_json(journal, estado)
        raise ErrorWeyland('La asignacion ya pudo ocurrir pero es incompatible; revisar la web, no tomar otra') from None
    guardar_json(journal, estado)


def _comprobar_detalle(datos, estado):
    if (datos.get('id') != estado['ejecucion_id'] or datos.get('id_externo') != estado['id_externo']
            or not isinstance(datos.get('proceso'), dict)
            or datos['proceso'].get('codigo') != estado['trabajo']['proceso_codigo']
            or not isinstance(datos.get('maquina'), dict)
            or datos['maquina'].get('id') != estado['maquina_id']):
        raise ErrorWeyland('La conciliacion no coincide con la asignacion local')


def _recuperar_toma(cliente, estado):
    detalle = cliente.buscar_correlacion(estado['maquina_id'], estado['id_externo'])
    if detalle.get('estado') != 'EN_PROCESO':
        raise ErrorWeyland('Toma incierta en otro estado; requiere revision, no se repetira siguiente')
    archivos = detalle.get('archivos')
    if not isinstance(archivos, list):
        raise ErrorWeyland('Detalle sin archivos compatibles para recuperar la toma')
    entradas = [a for a in archivos if isinstance(a, dict) and a.get('tipo') == 'INPUT']
    if len(entradas) > 1:
        raise ErrorWeyland('Inputs ambiguos al recuperar una toma')
    identificador = id_positivo(detalle.get('id'))
    return {'ejecucion_id': identificador, 'id_externo': detalle.get('id_externo'),
            'maquina': detalle.get('maquina'), 'proceso': detalle.get('proceso'),
            'parametros': detalle.get('parametros'),
            'input': {'nombre': entradas[0].get('nombre'),
                      'descarga': f'/api/bot/ejecuciones/{identificador}/input'} if entradas else None}


def _conciliar(cliente, estado, carpeta):
    detalle = cliente.consultar_ejecucion(estado['ejecucion_id'])
    _comprobar_detalle(detalle, estado)
    if estado['fase'] == 'ERROR_INCIERTO':
        if detalle.get('estado') != 'ERROR':
            raise ErrorWeyland('Aviso de error incierto; requiere revision antes de reenviar')
        estado['fase'] = 'ERROR'
        return
    if detalle.get('estado') != 'COMPLETADA':
        raise ErrorWeyland('Entrega incierta: el estado no prueba que pueda reenviarse; requiere revision')
    archivos = detalle.get('archivos')
    reportes = [a for a in archivos if isinstance(a, dict) and a.get('tipo') == 'REPORTE'] if isinstance(archivos, list) else []
    if len(reportes) != 1:
        raise ErrorWeyland('Reporte de conciliacion ausente o ambiguo')
    snapshot = cliente.descargar_reporte_confirmado(estado['ejecucion_id'], reportes[0]['id'],
                                                   carpeta / 'verificacion_backend.bin')
    if snapshot != {k: estado['entrega'][k] for k in ('size_bytes', 'sha256')}:
        raise ErrorWeyland('El reporte web no coincide con la entrega local; requiere revision')
    estado['fase'] = 'COMPLETADA'


def ejecutar_cola(cliente, maquina_id, trabajos, procesos, *, notificar=None):
    """Una invocacion toma como maximo un trabajo; los POST nunca se repiten solos."""
    id_positivo(maquina_id)
    if not procesos or any(not isinstance(k, str) or not k.strip() or not isinstance(p, ProcesoBot)
                          or not callable(p.ejecutar) or p.requiere_input is not None and type(p.requiere_input) is not bool
                          for k, p in procesos.items()):
        raise ValueError('Configurar una tabla explicita de procesos y adaptadores')
    ruta_base = Path(trabajos)
    if ruta_base.is_symlink():
        raise ErrorWeyland('Directorio operativo no permitido')
    base = ruta_base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    identidad = _identidad(cliente, maquina_id, procesos)
    journal = base / 'coordinador.json'
    with bloqueo(base / 'worker.lock'), _contexto_de_error() as referencia:
        estado = _leer(journal) if journal.exists() else None
        referencia['estado'] = estado
        if estado is not None:
            _validar_estado(estado, identidad)
            if estado['fase'] in ('COMPLETADA', 'ERROR'):
                _avisar(notificar, 'resultados' if estado['fase'] == 'COMPLETADA' else 'fallas', estado, _carpeta(base, estado))
        if estado is None or estado['fase'] in FINALES:
            estado = {**identidad, 'id_externo': uuid4().hex, 'fase': 'TOMA_INCIERTA'}
            referencia['estado'] = estado
            guardar_json(journal, estado)
            # Guardado antes del POST. Una caída desde aquí exige conciliación.
            datos = cliente.tomar_siguiente(maquina_id, estado['id_externo'])
            if datos is None:
                estado['fase'] = 'SIN_TRABAJO'
                guardar_json(journal, estado)
                return estado
            _asignar(cliente, datos, estado, procesos, base, journal)
        elif estado['fase'] == 'TOMA_INCIERTA':
            _asignar(cliente, _recuperar_toma(cliente, estado), estado, procesos, base, journal)
        if estado['fase'] == 'ASIGNACION_INCOMPATIBLE':
            raise ErrorWeyland('Asignacion incompatible pendiente de revision; no se tomara otro trabajo')
        carpeta = _carpeta(base, estado)
        if estado['fase'] in ('ENTREGA_INCIERTA', 'ERROR_INCIERTO'):
            _conciliar(cliente, estado, carpeta)
            guardar_json(journal, estado)
        if estado['fase'] == 'EJECUTANDO':
            raise ErrorWeyland('Negocio interrumpido; conserva su evidencia y requiere revision, no se repetira')
        proceso = procesos[estado['trabajo']['proceso_codigo']]
        if estado['fase'] in ('DESCARGANDO', 'PREPARADO'):
            _avisar(notificar, 'inicio', estado, carpeta)
        if estado['fase'] == 'DESCARGANDO':
            try:
                entrada = estado['trabajo']['input']
                if proceso.requiere_input is True and entrada is None or proceso.requiere_input is False and entrada is not None:
                    raise ValueError('Modalidad de input no admitida')
                if entrada is not None:
                    extension = Path(entrada['nombre']).suffix.lower()
                    if extension not in proceso.extensiones:
                        raise ValueError('Extension de entrada no admitida')
                    nombre = 'datos/input/entrada' + extension
                    snapshot = cliente.descargar_input(estado['ejecucion_id'], confinar(carpeta, nombre))
                    estado.update(entrada=nombre, snapshot_input=snapshot)
                else:
                    estado['entrada'] = None
                estado['fase'] = 'PREPARADO'
            except (ErrorRedWeyland, OSError):
                raise
            except ErrorHTTPWeyland as error:
                if error.status_code in (408, 429) or error.status_code >= 500:
                    raise
                estado.update(fase='ERROR_LISTO', mensaje_error='No se pudo obtener un input valido para la ejecucion.')
            except (ValueError, ErrorWeyland):
                estado.update(fase='ERROR_LISTO', mensaje_error='La entrada de la ejecucion es incompatible o invalida.')
            guardar_json(journal, estado)
        if estado['fase'] == 'PREPARADO':
            estado['fase'] = 'EJECUTANDO'
            guardar_json(journal, estado)
            try:
                entrada = confinar(carpeta, estado['entrada']) if estado['entrada'] else None
                if entrada is not None and (sha256(entrada) != estado['snapshot_input']['sha256']
                        or entrada.stat().st_size != estado['snapshot_input']['size_bytes']):
                    raise ErrorWeyland('Input alterado')
                resultado = proceso.ejecutar(carpeta, entrada, estado['trabajo']['parametros'])
                estado.update(resultado=snapshot_resultado(carpeta, resultado), fase='EXPORT_PENDIENTE')
            except Exception:
                estado.update(fase='ERROR_LISTO', mensaje_error='El proceso no pudo completarse; revisar su evidencia privada.')
            guardar_json(journal, estado)
        if estado['fase'] == 'EXPORT_PENDIENTE':
            estado.update(entrega=empaquetar_resultado(carpeta, estado), fase='ENTREGA_LISTA')
            guardar_json(journal, estado)
        if estado['fase'] == 'ENTREGA_LISTA':
            entrega = estado['entrega']
            reporte = confinar(carpeta, entrega['ruta'])
            if sha256(reporte) != entrega['sha256'] or reporte.stat().st_size != entrega['size_bytes']:
                raise ErrorWeyland('La entrega cambio; requiere revision')
            estado['fase'] = 'ENTREGA_INCIERTA'
            guardar_json(journal, estado)
            cliente.entregar_resultado(estado['ejecucion_id'], reporte, entrega['sha256'], entrega['size_bytes'])
            estado['fase'] = 'COMPLETADA'
            guardar_json(journal, estado)
        elif estado['fase'] == 'ERROR_LISTO':
            estado['fase'] = 'ERROR_INCIERTO'
            guardar_json(journal, estado)
            cliente.informar_error(estado['ejecucion_id'], estado['mensaje_error'])
            estado['fase'] = 'ERROR'
            guardar_json(journal, estado)
        if estado['fase'] not in ('COMPLETADA', 'ERROR'):
            raise ErrorWeyland('Fase inesperada; no se repetira el proceso')
        guardar_json(carpeta / 'finalizacion.json', {'fase': estado['fase'], 'ejecucion_id': estado['ejecucion_id'],
                                                   'id_externo': estado['id_externo']})
        _avisar(notificar, 'resultados' if estado['fase'] == 'COMPLETADA' else 'fallas', estado, carpeta)
        return estado
