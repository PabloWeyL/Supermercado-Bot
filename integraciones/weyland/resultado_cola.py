"""Checkpoints de resultados y empaquetado del contrato cola_actual."""
from datetime import datetime
import json
from pathlib import Path

from .archivos import confinar, guardar_json, sha256
from .cliente import MAX_BYTES
from .cola_actual import validar_datos_publicables
from .errores import ErrorWeyland
from .worker import ResultadoBot, exportar


def snapshot_resultado(carpeta, resultado):
    if not isinstance(resultado, ResultadoBot) or not isinstance(resultado.resumen, dict):
        raise ErrorWeyland('El adaptador debe devolver ResultadoBot')
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
        if (not archivo.is_file() or any(p in ('input', 'parametros', 'logs') for p in relativo.parts)
                or archivo.name.startswith('.env') or archivo.name in ('contexto.json', 'finalizacion.json')
                or archivo.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.log', '.pem', '.key')):
            raise ErrorWeyland('Archivo de resultado no permitido')
        archivos.append({'ruta': relativo.as_posix(), 'sha256': sha256(archivo), 'size_bytes': archivo.stat().st_size})
    return {'archivos': archivos, 'resumen': resultado.resumen, 'detalles': resultado.detalles,
            'permite_sin_export': resultado.permite_sin_export}


def empaquetar_resultado(carpeta, estado):
    guardado = estado['resultado']
    archivos = []
    for info in guardado['archivos']:
        archivo = confinar(carpeta, info['ruta'])
        if archivo.stat().st_size != info['size_bytes'] or sha256(archivo) != info['sha256']:
            raise ErrorWeyland('Un resultado cambio despues del negocio; requiere revision')
        archivos.append(archivo)
    resultado = ResultadoBot(tuple(archivos), guardado['resumen'],
                             {**guardado['detalles'], 'id_externo': estado['id_externo']}, guardado['permite_sin_export'])
    reporte, metadatos = exportar(carpeta, resultado, estado)
    if reporte is None:
        reporte = carpeta / ('resultado_' + datetime.now().strftime('%d%m%Y-%H%M%S') + '.json')
        guardar_json(reporte, {'ejecucion_id': estado['ejecucion_id'], 'id_externo': estado['id_externo'],
            'proceso_codigo': estado['trabajo']['proceso_codigo'], 'estado_negocio': 'COMPLETADO',
            'tiene_export_negocio': False, 'resumen': resultado.resumen, 'resultado': resultado.detalles})
    if not 1 <= reporte.stat().st_size <= MAX_BYTES:
        raise ErrorWeyland('Resultado fuera del limite de entrega')
    return {'ruta': reporte.relative_to(carpeta).as_posix(), 'size_bytes': reporte.stat().st_size,
            'sha256': sha256(reporte)}
