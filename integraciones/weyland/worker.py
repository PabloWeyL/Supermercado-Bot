"""Coordinador comun; el bot proporciona un callback de negocio y sus artefactos."""
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from .archivos import guardar_json, confinar, sha256, bloqueo
from .cliente import id_positivo, ids_coinciden, MAX_BYTES
from .errores import ErrorWeyland, ErrorRedWeyland, ErrorHTTPWeyland

FASES = {'INICIO_PENDIENTE', 'DESCARGANDO', 'PREPARADO', 'EJECUTANDO',
         'REPORTE_PENDIENTE', 'ERROR_PENDIENTE', 'COMPLETADA', 'ERROR'}


@dataclass(frozen=True)
class ResultadoBot:
    archivos: tuple
    resumen: dict = field(default_factory=lambda: {'procesados': 1, 'correctos': 1, 'errores': 0})
    detalles: dict = field(default_factory=dict)
    permite_sin_export: bool = False


def avisar(notificar, evento, identificador):
    if notificar is not None:
        try:
            notificar(evento, identificador)
        except Exception:
            # La falla SMTP no cambia el estado oficial ni repite el proceso.
            pass


def verificar_anteriores(base, identificador):
    """Nunca saltar una operacion incierta guardada por los workers anteriores."""
    for ruta in Path(base).rglob('*.json'):
        if ruta.name != 'activo.json' and not re.fullmatch(r'[a-f0-9]{32}\.json', ruta.name):
            continue
        datos = json.loads(ruta.read_text(encoding='utf-8'))
        if datos.get('fase') not in ('ENTREGADO', 'ERROR_NOTIFICADO', 'SIN_PENDIENTES', 'RESERVA_RECHAZADA'):
            raise ErrorWeyland('Existe un journal anterior pendiente; revisar antes de migrar')
        if datos.get('trabajo', {}).get('ejecucion_id') == identificador:
            raise ErrorWeyland('La solicitud ya finalizo en el worker anterior; no se repetira')
    for ruta in Path(base).rglob('estado.json'):
        datos = json.loads(ruta.read_text(encoding='utf-8'))
        if datos.get('version') == 1:
            continue
        if datos.get('fase') not in ('COMPLETADA', 'ERROR'):
            raise ErrorWeyland('Existe un journal anterior pendiente; revisar antes de migrar')
        if datos.get('ejecucion_id') == identificador:
            raise ErrorWeyland('La solicitud ya finalizo en el worker anterior; no se repetira')


def validar_estado(estado, identificador, codigo, backend, contexto):
    if (not isinstance(estado, dict) or type(estado.get('version')) is not int or estado['version'] != 1
            or not ids_coinciden(estado, identificador) or estado.get('proceso_codigo') != codigo
            or estado.get('backend') != backend or estado.get('contexto') != contexto
            or estado.get('fase') not in FASES
            or not re.fullmatch(r'[a-f0-9]{32}', str(estado.get('ejecucion_bot_id', '')))
            or not isinstance(estado.get('claves'), dict)
            or any(not re.fullmatch(r'[a-f0-9]{32}', str(estado['claves'].get(k, '')))
                   for k in ('inicio', 'reporte', 'error'))):
        raise ErrorWeyland('Journal incompatible o alterado; requiere revision y no se sobrescribira')


def exportar(carpeta, resultado, estado):
    if not isinstance(resultado, ResultadoBot):
        raise ErrorWeyland('El bot debe entregar ResultadoBot')
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
        if (not archivo.is_file() or any(p in ('input', 'logs') for p in ruta.parts)
                or archivo.name.startswith('.env') or archivo.name in ('estado.json', 'worker.lock')
                or archivo.suffix.lower() in (
                '.db', '.sqlite', '.sqlite3', '.env', '.pem', '.key', '.log')):
            raise ErrorWeyland('Artefacto de negocio no permitido')
        if ruta.as_posix() == 'reportes/ejecucion.json':
            raise ErrorWeyland('Nombre de artefacto reservado')
        archivos.append((archivo, ruta.as_posix()))
    datos = {**resultado.detalles, 'solicitud_id': estado['solicitud_id'],
             'ejecucion_id': estado['ejecucion_id'], 'ejecucion_bot_id': estado['ejecucion_bot_id'],
             'estado': 'COMPLETADA', 'resumen': resumen}
    nombre = 'resultado_' + datetime.now().strftime('%d%m%Y-%H%M%S') + '.zip'
    reporte = confinar(carpeta, nombre)
    with ZipFile(reporte, 'w', ZIP_DEFLATED) as paquete:
        for archivo, relativo in archivos:
            paquete.write(archivo, relativo)
        paquete.writestr('reportes/ejecucion.json', json.dumps(datos, ensure_ascii=False, indent=2))
    with reporte.open('r+b') as archivo:
        os.fsync(archivo.fileno())
    if not 1 <= reporte.stat().st_size <= MAX_BYTES:
        raise ErrorWeyland('Reporte supera el limite de 20 MiB')
    metadatos = {k: datos[k] for k in ('solicitud_id', 'ejecucion_id', 'ejecucion_bot_id', 'estado', 'resumen')}
    metadatos['export'] = {'nombre': nombre, 'content_type': 'application/zip',
                          'size_bytes': reporte.stat().st_size, 'sha256': sha256(reporte)}
    return reporte, metadatos


def huella_metadatos(datos):
    return hashlib.sha256(json.dumps(datos, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def ejecutar(cliente, identificador, codigo, trabajos, runner, *, requiere_input=None,
             extensiones=('.xlsx',), notificar=None, contexto=''):
    """runner(carpeta, entrada_o_none, parametros) -> ResultadoBot. No importa otros bots."""
    id_positivo(identificador)
    trabajos = Path(trabajos).resolve()
    trabajos.mkdir(parents=True, exist_ok=True)
    verificar_anteriores(trabajos, identificador)
    carpeta = trabajos / str(identificador)
    if carpeta.is_symlink() or (carpeta.exists() and carpeta.resolve().parent != trabajos):
        raise ErrorWeyland('Carpeta de ejecucion invalida')
    carpeta.mkdir(exist_ok=True)
    with bloqueo(trabajos / 'worker.lock'):
        journal = confinar(carpeta, 'estado.json')
        if journal.exists():
            estado = json.loads(journal.read_text(encoding='utf-8'))
            validar_estado(estado, identificador, codigo, cliente.config.url, contexto)
        else:
            detalle = cliente.consultar(identificador)
            cliente.validar(detalle, identificador, codigo, 'EN_COLA')
            if requiere_input is not None and detalle['requiere_input'] is not requiere_input:
                raise ErrorWeyland('El bot no admite la modalidad de input de esta solicitud')
            if detalle['requiere_input'] and Path(detalle['input']['nombre']).suffix.lower() not in extensiones:
                raise ErrorWeyland('Extension de input no admitida por este bot')
            estado = {'version': 1, 'solicitud_id': identificador, 'ejecucion_id': identificador,
                      'proceso_codigo': codigo, 'backend': cliente.config.url, 'contexto': contexto,
                      'ejecucion_bot_id': uuid4().hex, 'fase': 'INICIO_PENDIENTE',
                      'claves': {k: uuid4().hex for k in ('inicio', 'reporte', 'error')}}
            guardar_json(journal, estado)
        if estado['fase'] == 'EJECUTANDO':
            raise ErrorWeyland('Ejecucion interrumpida durante negocio; requiere revision y no se repetira')
        if estado['fase'] in ('COMPLETADA', 'ERROR'):
            return estado
        if estado['fase'] == 'INICIO_PENDIENTE':
            trabajo = cliente.iniciar(identificador, codigo, estado['claves']['inicio'])
            cliente.validar(trabajo, identificador, codigo, 'EN_PROCESO')
            if requiere_input is not None and trabajo['requiere_input'] is not requiere_input:
                raise ErrorWeyland('El input cambio al iniciar; requiere revision')
            estado.update(fase='DESCARGANDO', trabajo={k: trabajo[k]
                for k in ('requiere_input', 'input', 'parametros')})
            guardar_json(journal, estado)
            avisar(notificar, 'inicio', identificador)
        if estado['fase'] == 'DESCARGANDO':
            try:
                trabajo = estado['trabajo']
                if trabajo['requiere_input']:
                    extension = Path(trabajo['input']['nombre']).suffix.lower()
                    if extension not in extensiones:
                        raise ErrorWeyland('Extension de input no admitida')
                    nombre = 'datos/input/entrada' + extension
                    destino = confinar(carpeta, nombre)
                    info = trabajo['input']
                    # Un corte puede ocurrir despues del rename y antes de guardar PREPARADO.
                    if not (destino.is_file() and destino.stat().st_size == info['size_bytes']
                            and sha256(destino) == info['sha256']):
                        cliente.descargar(identificador, destino, info)
                    estado['entrada'] = nombre
                else:
                    estado['entrada'] = None
                estado['fase'] = 'PREPARADO'
            except (ErrorRedWeyland, OSError):
                # Mantener DESCARGANDO: recuperar una lectura no repite el negocio.
                raise
            except ErrorHTTPWeyland as error:
                if error.status_code in (408, 429) or error.status_code >= 500:
                    raise
                estado.update(fase='ERROR_PENDIENTE', motivo='INPUT_INVALIDO')
            except Exception:
                estado.update(fase='ERROR_PENDIENTE', motivo='INPUT_INVALIDO')
            guardar_json(journal, estado)
        if estado['fase'] == 'PREPARADO':
            estado['fase'] = 'EJECUTANDO'
            guardar_json(journal, estado)
            try:
                entrada = confinar(carpeta, estado['entrada']) if estado['entrada'] is not None else None
                if entrada is not None and sha256(entrada) != estado['trabajo']['input']['sha256']:
                    raise ErrorWeyland('Input alterado antes del procesamiento')
                resultado = runner(carpeta, entrada, estado['trabajo']['parametros'])
                reporte, metadatos = exportar(carpeta, resultado, estado)
                estado.update(fase='REPORTE_PENDIENTE', reporte=reporte.name if reporte else None,
                              metadatos=metadatos, metadatos_sha256=huella_metadatos(metadatos))
            except Exception:
                estado.update(fase='ERROR_PENDIENTE', motivo='PROCESO_FALLIDO')
            guardar_json(journal, estado)
        if estado['fase'] == 'REPORTE_PENDIENTE':
            metadatos = estado['metadatos']
            if not ids_coinciden(metadatos, identificador):
                raise ErrorWeyland('Metadatos de entrega incompatibles')
            if ('metadatos_sha256' in estado
                    and huella_metadatos(metadatos) != estado['metadatos_sha256']):
                raise ErrorWeyland('Los metadatos cambiaron; no se enviaran')
            if estado.get('reporte') is None:
                if ('metadatos_sha256' not in estado or 'export' not in metadatos
                        or metadatos['export'] is not None):
                    raise ErrorWeyland('Entrega JSON sin checkpoint compatible')
                reporte = None
            else:
                if not re.fullmatch(r'resultado_[0-9]{8}-[0-9]{6}\.zip', str(estado['reporte'])):
                    raise ErrorWeyland('Nombre de reporte incompatible')
                reporte = confinar(carpeta, estado['reporte'])
                if sha256(reporte) != metadatos['export']['sha256']:
                    raise ErrorWeyland('El reporte cambio; no se enviara')
            cliente.entregar(identificador, reporte, estado['metadatos'], estado['claves']['reporte'])
            estado['fase'] = 'COMPLETADA'
            guardar_json(journal, estado)
            avisar(notificar, 'resultados', identificador)
        elif estado['fase'] == 'ERROR_PENDIENTE':
            if estado.get('motivo') not in ('INPUT_INVALIDO', 'PROCESO_FALLIDO'):
                raise ErrorWeyland('Motivo de error incompatible')
            datos = {k: estado[k] for k in ('solicitud_id', 'ejecucion_id', 'ejecucion_bot_id')}
            datos.update(codigo=estado['motivo'], mensaje='No se pudo completar el proceso; revisar su evidencia local.',
                         reintentable=False)
            cliente.fallar(identificador, datos, estado['claves']['error'])
            estado['fase'] = 'ERROR'
            guardar_json(journal, estado)
            avisar(notificar, 'fallas', identificador)
        else:
            raise ErrorWeyland('Fase local desconocida; requiere revision')
        return estado
