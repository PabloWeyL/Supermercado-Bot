"""Comprobaciones del arranque automatico, previas a cualquier scraping o correo."""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from integraciones.weyland.archivos import bloqueo
from integraciones.trazabilidad import contexto


class ErrorArranque(RuntimeError):
    pass


def _probar_directorio(ruta):
    ruta = Path(ruta)
    ruta.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=ruta, prefix='verificacion_', suffix='.tmp'):
        pass


def validar_configuracion(config):
    for nombre in ('RPA_TIMEOUT', 'RPA_ESPERA_MAX', 'MAX_REINTENTOS', 'MAX_REINTENTOS_CORREO', 'RETENCIONES_LOGS_MESES'):
        valor = getattr(config, nombre)
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or valor <= 0:
            raise ErrorArranque(f'{nombre} debe ser un numero positivo y finito')
    if config.REINTENTOS_MAX < 0:
        raise ErrorArranque('REINTENTOS_MAX no puede ser negativo')
    if config.ID_EJECUCION_ENV and (not config.ID_EJECUCION_ENV.isdecimal() or int(config.ID_EJECUCION_ENV) < 1):
        raise ErrorArranque('ID_EJECUCION debe ser un entero positivo o estar vacio')
    for nombre in ('MODULO_SCRAPING', 'MODULO_CORREO', 'MODULO_CORREO_INICIAL', 'RPA_HEADLESS'):
        valor = os.getenv(nombre)
        if valor is not None and valor.strip() and valor.strip().upper() not in (
                'TRUE', 'SI', '1', 'YES', 'Y', 'FALSE', 'NO', '0', 'N'):
            raise ErrorArranque(f'{nombre} contiene un booleano invalido')
    for ruta in (config.RUTA_LOGS, config.RUTA_REPORTES, config.RUTA_TEMPORALES,
                 Path(config.RUTA_DB_SQLITE).parent, Path(config.EXCEL_SALIDA).parent,
                 Path(config.CARPETA_CAPTURAS)):
        _probar_directorio(ruta)
    if config.MODULO_CORREO:
        for nombre in ('smtp_server', 'email_remitente', 'email_password'):
            if not config.obtener_variable_opcional(nombre):
                raise ErrorArranque(f'Correo habilitado pero falta {nombre}; configurar o desactivar MODULO_CORREO')
        try:
            puerto = int(config.obtener_variable_opcional('smtp_port', '587'))
        except ValueError:
            raise ErrorArranque('smtp_port debe ser un entero') from None
        if not 1 <= puerto <= 65535 or not config.LISTA_DISTRIBUCION_ERROR.strip():
            raise ErrorArranque('Revisar smtp_port y LISTA_DISTRIBUCION_ERROR del correo habilitado')
    with Path(config.ARCHIVO_SUPERMERCADOS).open(encoding='utf-8') as archivo:
        catalogo = json.load(archivo)
    if not isinstance(catalogo, dict) or not catalogo:
        raise ErrorArranque('El catalogo de supermercados esta vacio o no es un objeto JSON')
    habilitados = {}
    for clave, datos in catalogo.items():
        if not isinstance(datos, dict) or type(datos.get('habilitado', True)) is not bool:
            raise ErrorArranque(f'Configuracion invalida del supermercado {clave}')
        if not datos.get('habilitado', True):
            continue
        url = datos.get('url_busqueda') or datos.get('url_inicio')
        if not isinstance(url, str) or urlsplit(url).scheme != 'https' or not urlsplit(url).hostname:
            raise ErrorArranque(f'Falta URL HTTPS del supermercado {clave}')
        if not datos.get('nombre') or not datos.get('extractor'):
            raise ErrorArranque(f'Falta nombre o extractor del supermercado {clave}')
        habilitados[clave] = datos
    if not habilitados:
        raise ErrorArranque('No hay supermercados habilitados')
    return catalogo


def validar_dependencias(raiz):
    if sys.version_info < (3, 11):
        raise ErrorArranque('Se requiere Python 3.11 o posterior')
    nodo = shutil.which('node')
    if not nodo:
        raise ErrorArranque('Falta Node.js 22 o posterior en PATH')
    resultado = subprocess.run([nodo, '--version'], capture_output=True, text=True, timeout=10, check=True)
    if int(resultado.stdout.strip().lstrip('v').split('.')[0]) < 22:
        raise ErrorArranque('Se requiere Node.js 22 o posterior')
    candidatos = [os.getenv('CHROME_PATH'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        str(Path(os.getenv('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe'),
        shutil.which('google-chrome'), shutil.which('chromium')]
    chrome = next((p for p in candidatos if p and Path(p).is_file()), None)
    if not chrome:
        raise ErrorArranque('No se encontro Chrome; configurar CHROME_PATH')
    return str(Path(chrome).resolve())


def revisar_integridad(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        return
    # mode=ro revisa la base existente sin crearla ni migrarla.
    with closing(sqlite3.connect(ruta.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conexion:
        if conexion.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ErrorArranque('La base SQLite no supera quick_check; restaurar o revisar antes de ejecutar')
        if conexion.execute('PRAGMA foreign_key_check').fetchall():
            raise ErrorArranque('La base SQLite contiene referencias invalidas')


def revisar_base_operativa(config):
    from config import base_datos as bd
    revisar_integridad(config.RUTA_DB_SQLITE)
    requeridas = {
        'details': {'id', 'archivo', 'ruta', 'estado', 'error', 'fecha_creacion', 'fecha_fin'},
        'cola': {'id', 'detail_id', 'producto', 'supermercado', 'marca', 'precio_minimo',
                 'precio_maximo', 'estado', 'reintentos', 'error', 'fecha_creacion', 'fecha_fin'},
        'resultados': {'id', 'cola_id', 'supermercado', 'producto', 'nombre', 'precio', 'url', 'fecha'},
    }
    with closing(bd.obtener_db_conexion_sql3()) as conexion:
        if conexion.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise ErrorArranque('Version de esquema SQLite incompatible')
        for tabla, columnas in requeridas.items():
            presentes = {fila[1] for fila in conexion.execute('PRAGMA table_info(' + tabla + ')')}
            if not columnas <= presentes:
                raise ErrorArranque(f'La tabla {tabla} no tiene todas sus columnas requeridas')
        indices = {fila[0] for fila in conexion.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        if not {'idx_cola_filtros', 'idx_details_estado', 'idx_cola_estado', 'idx_cola_detail',
                'idx_resultados_busqueda', 'idx_resultados_cola'} <= indices:
            raise ErrorArranque('Faltan indices de la base operativa')
        # Comprobar bloqueo de escritura sin insertar, borrar ni cambiar tareas.
        conexion.execute('BEGIN IMMEDIATE')
        conexion.rollback()


def validar_entradas(config, catalogo):
    from config import base_datos as bd
    from integraciones.productos import leer_productos_excel
    from integraciones.supermercados import seleccionar_supermercados
    carpeta = Path(config.RUTA_INPUT)
    archivos = sorted(p for p in carpeta.iterdir() if p.is_file()
        and p.suffix.lower() in ('.xlsx', '.xlsm') and not p.name.startswith('~$')) if carpeta.is_dir() else []
    for ruta in archivos:
        try:
            for tarea in leer_productos_excel(ruta):
                seleccionar_supermercados(tarea.get('supermercado'), catalogo)
        except Exception as error:
            raise ErrorArranque(f'Entrada invalida: {ruta.name}. {error}') from None
    with closing(bd.obtener_db_conexion_sql3()) as conexion:
        pendientes = conexion.execute("SELECT DISTINCT supermercado FROM cola WHERE estado IN ('pendiente','error')").fetchall()
    for (supermercado,) in pendientes:
        seleccionar_supermercados(supermercado, catalogo)
    if not archivos and not pendientes:
        raise ErrorArranque('No hay Excel en input/ ni tareas pendientes o con error en la cola')
    return len(archivos), len(pendientes)


def ejecutar_automatico(proceso, *, remoto=False):
    from config import configuracion as config
    raiz_lock = Path(config.RUTA_PROYECTO) / '.runtime'
    raiz_lock.mkdir(exist_ok=True)
    with contexto(), bloqueo(raiz_lock / 'arranque.lock'):
        print('[1/4] Verificando configuracion, dependencias y base existente', flush=True)
        catalogo = validar_configuracion(config)
        validar_dependencias(config.RUTA_PROYECTO)
        revisar_integridad(config.RUTA_DB_SQLITE)
        print('[2/4] Inicializando y verificando la base operativa', flush=True)
        config.configuracion_inicial()
        revisar_base_operativa(config)
        from config.correo import reintentar_pendientes
        reintentar_pendientes()
        if remoto:
            print('[3/4] Entradas controladas por la solicitud web; no se cargara la cola local', flush=True)
        else:
            print('[3/4] Validando todos los Excel y la cola pendiente', flush=True)
            validar_entradas(config, catalogo)
        print('[4/4] Comprobaciones aprobadas. Iniciando el flujo autorizado', flush=True)
        return proceso()
