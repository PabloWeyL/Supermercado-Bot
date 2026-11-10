"""Adaptador de negocio: la conexion y el journal viven en integraciones.weyland."""
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from integraciones.weyland import ErrorWeyland, ResultadoBot, ejecutar_solicitud, ejecutar_configurado
from integraciones.weyland.archivos import guardar_json
from integraciones.trazabilidad import actual, contexto


def ejecutar_subproceso(proyecto, comando, raiz, carpeta, entrada, *, notificaciones_coordinador=False):
    datos = carpeta / 'datos'
    salida = datos / 'reportes'
    salida.mkdir(parents=True, exist_ok=True)
    variables = {**os.environ,
        'RUTA_BASE': str(datos), 'RUTA_HISTORICO': str(carpeta / 'historico'),
        'RUTA_LOGS': str(carpeta / 'logs'), 'CARPETA_INPUTS': str(datos / 'input'),
        'EXCEL_ENTRADA': str(entrada or datos / 'input'), 'EXCEL_SALIDA': str(salida / 'resultados.xlsx'),
        'CARPETA_CAPTURAS': str(carpeta / 'capturas'), 'CARPETA_DESCARGAS': str(datos / 'descargas'),
        'DB_PATH': str(datos / 'historial.db'), 'ID_EJECUCION': ''}
    args = [comando]
    if proyecto == 'vencimientos':
        variables['DB_SQLITE'] = 'weyland_' + carpeta.name + '.db'
        args = ['procesar', str(entrada)]
    elif proyecto == 'supermercados' and entrada is None:
        args += ['--productos-json', str(datos / 'parametros' / 'productos.json')]
    if proyecto == 'supermercados':
        variables['WEYLAND_SOLICITUD_ID'] = carpeta.name
        variables['BOT_ACTIVACION_ID'] = actual().get('activacion_id') or ''
        from config.correo import ruta_cola
        variables['CORREO_PENDIENTES_DIR'] = str(ruta_cola())
        if notificaciones_coordinador:
            variables['WEYLAND_CORREO_COORDINADOR'] = 'true'
    with (carpeta / 'proceso.log').open('ab') as log:
        return subprocess.run([sys.executable, str(raiz / 'main.py'), *args], cwd=raiz,
            env=variables, stdout=log, stderr=subprocess.STDOUT, check=False).returncode


def artefactos(carpeta):
    datos = carpeta / 'datos'
    return tuple(p for p in sorted(datos.rglob('*')) if p.is_file() and not p.is_symlink()
        and p.resolve().is_relative_to(datos.resolve())
        and p.suffix.lower() in ('.xlsx', '.xlsm', '.csv', '.json', '.txt')
        and any(c in ('reportes', 'descargas') for c in p.relative_to(datos).parts))


def exportar(carpeta):
    """Utilidad de negocio conservada; la entrega web usa el ZIP del coordinador comun."""
    archivos = artefactos(carpeta)
    if not archivos:
        raise ErrorWeyland('El proceso termino sin generar un export')
    if len(archivos) == 1:
        return archivos[0]
    destino = carpeta / 'resultado.zip'
    with ZipFile(destino, 'w', ZIP_DEFLATED) as paquete:
        for archivo in archivos:
            paquete.write(archivo, archivo.relative_to(carpeta / 'datos').as_posix())
    return destino


def crear_runner(proyecto, comando, raiz, runner=None, *, notificaciones_coordinador=False):
    if proyecto not in ('acme', 'supermercados', 'vencimientos'):
        raise ValueError('Proyecto de negocio no admitido')
    permitidos = ('consultar', 'descargar-reportes', 'procesar') if proyecto == 'acme' else ('procesar',)
    if comando not in permitidos:
        raise ValueError('Comando de negocio no admitido')
    raiz = Path(raiz).resolve()
    ejecutar = runner or ejecutar_subproceso
    def procesar(carpeta, entrada, parametros):
        if entrada is None:
            if proyecto != 'supermercados':
                raise ErrorWeyland('Este bot necesita un archivo de entrada')
            destino = carpeta / 'datos' / 'parametros' / 'productos.json'
            destino.parent.mkdir(parents=True, exist_ok=True)
            productos = parametros.get('productos', [parametros] if parametros.get('producto') else [])
            if isinstance(productos, str):
                productos = json.loads(productos)
            guardar_json(destino, {'productos': productos})
            from integraciones.productos_json import leer_productos_json
            leer_productos_json(destino)
        if runner is None and notificaciones_coordinador:
            codigo_salida = ejecutar_subproceso(proyecto, comando, raiz, carpeta, entrada,
                                                 notificaciones_coordinador=True)
        else:
            codigo_salida = ejecutar(proyecto, comando, raiz, carpeta, entrada)
        if codigo_salida != 0:
            raise ErrorWeyland('El proceso de negocio finalizo con errores')
        # El resumen generico representa una invocacion del proceso, no el conteo de filas.
        return ResultadoBot(artefactos(carpeta), detalles={'proyecto': proyecto, 'comando': comando,
                                                         'trazabilidad': actual()})
    return procesar


def ejecutar_uno(cliente, proyecto, comando, raiz, trabajos, codigo, runner=None, *, solicitud_id):
    resultado = ejecutar_solicitud(cliente, solicitud_id, codigo, trabajos,
        crear_runner(proyecto, comando, raiz, runner), requiere_input=None if proyecto == 'supermercados' else True,
        extensiones=('.xlsx', '.xlsm', '.csv') if proyecto == 'vencimientos' else ('.xlsx', '.xlsm'),
        contexto=proyecto + ':' + comando)
    return 0 if resultado['fase'] == 'COMPLETADA' else 1


def ejecutar_desde_config(proyecto, raiz, comando='procesar', solicitud_id=None):
    from config.api import crear_cliente_api
    from config.weyland import leer_valores
    valores = leer_valores()
    if str(valores.get('BACKEND_CONTRATO') or 'por_id').strip().lower() == 'cola_actual':
        if solicitud_id is not None:
            raise ValueError('cola_actual recibe el ID desde siguiente; no enviar --id-solicitud')
        return ejecutar_contrato_actual(proyecto, raiz, comando, valores)
    with contexto(solicitud_id=solicitud_id):
        resultado = ejecutar_configurado(crear_cliente_api, valores, raiz, solicitud_id,
            crear_runner(proyecto, comando, raiz), requiere_input=None if proyecto == 'supermercados' else True,
            extensiones=('.xlsx', '.xlsm', '.csv') if proyecto == 'vencimientos' else ('.xlsx', '.xlsm'),
            contexto=proyecto + ':' + comando)
    return 0 if resultado['fase'] == 'COMPLETADA' else 1


def ejecutar_contrato_actual(proyecto, raiz, comando, valores, runner=None, *, fabrica=None):
    """Adaptador fino: el SDK gestiona HTTP/checkpoints; aquí vive el negocio."""
    from config.api import crear_cliente_api
    from integraciones.weyland.aplicacion_cola import ejecutar_cola_configurada
    from integraciones.weyland.ejecutor_cola import ProcesoBot
    from config import correo
    codigo = str(valores.get('BACKEND_PROCESO_CODIGO') or '').strip()
    if not codigo:
        raise ValueError('Configurar BACKEND_PROCESO_CODIGO registrado en la web')
    negocio = crear_runner(proyecto, comando, raiz, runner, notificaciones_coordinador=True)

    def procesar(carpeta, entrada, parametros):
        datos = json.loads((carpeta / 'contexto.json').read_text(encoding='utf-8'))
        with contexto(datos):
            return negocio(carpeta, entrada, parametros)

    def notificar(evento, estado, carpeta):
        with contexto({'solicitud_id': estado['ejecucion_id'], 'ejecucion_id_web': estado['ejecucion_id'],
                       'activacion_id': estado['id_externo']}):
            if evento == 'inicio':
                return correo.enviar_correo_inicial()
            if evento == 'resultados':
                from integraciones.weyland.archivos import confinar
                entrega = confinar(carpeta, estado['entrega']['ruta'])
                return correo.notificar(correo.NOMBRE_PROCESO_TEXTO + ' - RESULTADOS',
                    'Proceso terminado y confirmado por Weyland Automate.' + correo.referencia_web(),
                    [entrega], evento='resultados')
            return correo.enviar_correo_error('Proceso con error confirmado por Weyland Automate.')

    extensiones = ('.xlsx', '.xlsm', '.csv') if proyecto == 'vencimientos' else ('.xlsx', '.xlsm')
    proceso = ProcesoBot(procesar, None if proyecto == 'supermercados' else True, extensiones)
    resultado = ejecutar_cola_configurada(fabrica or crear_cliente_api, valores, raiz, {codigo: proceso}, notificar=notificar)
    print('Weyland Automate:', resultado['fase'], 'ID:', resultado.get('ejecucion_id', 'SIN_TRABAJO'), flush=True)
    from integraciones.diagnostico import diagnosticar_resultado
    diagnostico = diagnosticar_resultado(resultado)
    print(diagnostico.texto(), flush=True)
    return diagnostico.codigo
