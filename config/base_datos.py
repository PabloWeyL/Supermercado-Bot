"""Conexión SQLite, esquema y consultas del proceso."""

import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from uuid import uuid4


_ruta_db = None
_archivos_entrada = ContextVar('archivos_entrada', default=None)


@contextmanager
def ambito_archivos(rutas):
    """Limita consultas y reportes a los archivos locales seleccionados, sin borrar historia."""
    token = _archivos_entrada.set(tuple(dict.fromkeys(str(Path(r).resolve()) for r in rutas)))
    try:
        yield
    finally:
        _archivos_entrada.reset(token)


def _filtro_archivos(alias='cola'):
    """Construye el filtro parametrizado que limita las consultas a los inputs del contexto actual."""
    rutas = _archivos_entrada.get()
    if rutas is None:
        return '', ()
    if not rutas:
        return ' AND 0', ()
    marcadores = ','.join('?' for _ in rutas)
    return f' AND {alias}.detail_id IN (SELECT id FROM details WHERE ruta IN ({marcadores}))', rutas


def conectar_sqlite(*args, **kwargs):
    """Abre SQLite con el tiempo límite centralizado, sin duplicar su configuración."""
    from config.configuracion import DB_TIMEOUT
    return sqlite3.connect(*args, timeout=DB_TIMEOUT, **kwargs)


def configurar_base_datos(ruta_db):
    """Selecciona la base operativa, crea su carpeta e inicializa su estructura."""
    global _ruta_db
    _ruta_db = Path(ruta_db)
    _ruta_db.parent.mkdir(parents=True, exist_ok=True)
    inicializar_bd()


def obtener_db_conexion_sql3():
    """Abre la base seleccionada y activa la verificación de claves foráneas."""
    if _ruta_db is None:
        raise RuntimeError('La base de datos no esta configurada.')
    conexion = conectar_sqlite(_ruta_db)
    conexion.execute('PRAGMA foreign_keys = ON')
    return conexion


def _columnas(conexion, tabla):
    """Consulta los nombres de columnas de una tabla durante la validación o migración."""
    return {fila[1] for fila in conexion.execute('PRAGMA table_info(' + tabla + ')')}


def _crear_tablas(conexion, prefijo=''):
    """Crea las tablas de archivos, tareas y resultados con sus restricciones."""
    conexion.execute(f'''
        CREATE TABLE IF NOT EXISTS {prefijo}details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archivo TEXT NOT NULL,
            ruta TEXT UNIQUE,
            estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'leido', 'error')),
            error TEXT,
            fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            fecha_fin TEXT
        )
    ''')
    conexion.execute(f'''
        CREATE TABLE IF NOT EXISTS {prefijo}cola (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detail_id INTEGER REFERENCES details(id),
            producto TEXT NOT NULL,
            supermercado TEXT NOT NULL,
            marca TEXT NOT NULL DEFAULT '',
            precio_minimo REAL,
            precio_maximo REAL,
            estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'leido', 'error')),
            reintentos INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            fecha_fin TEXT
        )
    ''')
    conexion.execute(f'''
        CREATE TABLE IF NOT EXISTS {prefijo}resultados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cola_id INTEGER REFERENCES cola(id),
            supermercado TEXT,
            producto TEXT,
            nombre TEXT,
            precio REAL,
            url TEXT,
            fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    ''')


def _migrar_estructura_anterior(conexion):
    """Respalda y convierte el esquema antiguo dentro de una transacción recuperable."""
    copia = _ruta_db.with_name(_ruta_db.name + '.pre_v2_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8] + '.bak')
    with closing(conectar_sqlite(copia)) as destino:
        conexion.backup(destino)
    conexion.execute('PRAGMA foreign_keys = OFF')
    try:
        conexion.execute('BEGIN IMMEDIATE')
        _crear_tablas(conexion, '_v2_')
        archivo_id = 'cola_id' if 'cola_id' in _columnas(conexion, 'details') else 'NULL'
        conexion.execute('''
            INSERT INTO _v2_details (id, archivo, estado, error, fecha_creacion, fecha_fin)
            SELECT id, archivo, CASE WHEN estado='error' THEN 'error' ELSE 'leido' END,
                error, fecha_creacion, COALESCE(fecha_fin, fecha_creacion) FROM cola
        ''')
        conexion.execute(f'''
            INSERT INTO _v2_cola (id, detail_id, producto, supermercado, estado, reintentos, error, fecha_creacion, fecha_fin)
            SELECT id, {archivo_id}, producto, supermercado,
                CASE WHEN estado='ok' THEN 'leido' ELSE estado END,
                reintentos, error, fecha_creacion, fecha_fin FROM details
        ''')
        columnas_resultados = _columnas(conexion, 'resultados')
        if columnas_resultados:
            tarea_id = next((nombre for nombre in ('detail_id', 'details_id') if nombre in columnas_resultados), 'NULL')
            conexion.execute(f'''
                INSERT INTO _v2_resultados (id, cola_id, supermercado, producto, nombre, precio, url, fecha)
                SELECT id, {tarea_id}, supermercado, producto, nombre, precio, url, fecha FROM resultados
            ''')
            conexion.execute('DROP TABLE resultados')
        conexion.execute('DROP TABLE details')
        conexion.execute('DROP TABLE cola')
        for tabla in ('details', 'cola', 'resultados'):
            conexion.execute('ALTER TABLE _v2_' + tabla + ' RENAME TO ' + tabla)
        if conexion.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('La migracion encontro referencias invalidas; se conserva la estructura anterior.')
        conexion.execute('PRAGMA user_version = 2')
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.execute('PRAGMA foreign_keys = ON')


def _migrar_filtros(conexion):
    """Respalda y migra las tareas para incorporar marca y rango de precios."""
    copia = _ruta_db.with_name(_ruta_db.name + '.pre_v3_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8] + '.bak')
    with closing(conectar_sqlite(copia)) as destino:
        conexion.backup(destino)
    conexion.execute('PRAGMA foreign_keys = OFF')
    try:
        conexion.execute('BEGIN IMMEDIATE')
        conexion.execute('''CREATE TABLE cola_filtros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detail_id INTEGER REFERENCES details(id),
            producto TEXT NOT NULL, supermercado TEXT NOT NULL,
            marca TEXT NOT NULL DEFAULT '', precio_minimo REAL, precio_maximo REAL,
            estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'leido', 'error')),
            reintentos INTEGER NOT NULL DEFAULT 0, error TEXT,
            fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime')), fecha_fin TEXT
        )''')
        columnas = 'id, detail_id, producto, supermercado, estado, reintentos, error, fecha_creacion, fecha_fin'
        conexion.execute(f'INSERT INTO cola_filtros ({columnas}) SELECT {columnas} FROM cola')
        conexion.execute('DROP TABLE cola')
        conexion.execute('ALTER TABLE cola_filtros RENAME TO cola')
        if conexion.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('La migracion de filtros encontro referencias invalidas')
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.execute('PRAGMA foreign_keys = ON')


def inicializar_bd():
    """Detecta migraciones pendientes y asegura el esquema, los índices y las restricciones."""
    with closing(obtener_db_conexion_sql3()) as conexion:
        if 'producto' in _columnas(conexion, 'details') and 'archivo' in _columnas(conexion, 'cola'):
            _migrar_estructura_anterior(conexion)
        if _columnas(conexion, 'cola') and 'marca' not in _columnas(conexion, 'cola'):
            _migrar_filtros(conexion)
        with conexion:
            _crear_tablas(conexion)
            for sql in (
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_cola_filtros ON cola(detail_id, producto, supermercado, marca, COALESCE(precio_minimo, -1), COALESCE(precio_maximo, -1))",
                'CREATE INDEX IF NOT EXISTS idx_details_estado ON details(estado)',
                'CREATE INDEX IF NOT EXISTS idx_cola_estado ON cola(estado)',
                'CREATE INDEX IF NOT EXISTS idx_cola_detail ON cola(detail_id)',
                'CREATE INDEX IF NOT EXISTS idx_resultados_busqueda ON resultados(producto, supermercado, fecha)',
                'CREATE INDEX IF NOT EXISTS idx_resultados_cola ON resultados(cola_id)',
            ):
                conexion.execute(sql)
            conexion.execute('PRAGMA user_version = 3')


def registrar_archivo(ruta, fecha):
    """Registra o recupera el archivo por su ruta y reinicia su estado de lectura."""
    ruta = Path(ruta).resolve()
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        fila = conexion.execute('SELECT id FROM details WHERE ruta=?', (str(ruta),)).fetchone()
        if not fila:
            fila = conexion.execute('SELECT id FROM details WHERE ruta IS NULL AND archivo=? ORDER BY id LIMIT 1', (ruta.name,)).fetchone()
        if fila:
            archivo_id = fila[0]
            conexion.execute("UPDATE details SET ruta=?, estado='pendiente', error=NULL, fecha_fin=NULL WHERE id=?", (str(ruta), archivo_id))
        else:
            archivo_id = conexion.execute("INSERT INTO details (archivo, ruta, estado, fecha_creacion) VALUES (?, ?, 'pendiente', ?)", (ruta.name, str(ruta), fecha)).lastrowid
    return archivo_id


def actualizar_archivo(archivo_id, estado, fecha, error=None):
    """Actualiza el estado, fecha y error de un archivo de entrada."""
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute('UPDATE details SET estado=?, fecha_fin=?, error=? WHERE id=?', (estado, fecha, error, archivo_id))


def actualizar_cola(id_tarea, estado, reintentos, fecha_fin=None, error=None):
    """Actualiza el estado y los intentos de una tarea concreta."""
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute('UPDATE cola SET estado=?, reintentos=?, fecha_fin=?, error=? WHERE id=?', (estado, reintentos, fecha_fin, error, id_tarea))


def guardar_resultados(id_tarea, supermercado, producto, resultados, reintentos, fecha):
    """Guarda los productos y marca la tarea como leída en la misma transacción."""
    if not resultados:
        raise ValueError('No hay resultados para guardar')
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.executemany(
            'INSERT INTO resultados (cola_id, supermercado, producto, nombre, precio, url, fecha) VALUES (?, ?, ?, ?, ?, ?, ?)',
            [(id_tarea, supermercado, producto, dato['nombre'], dato['precio'], dato['url'], fecha) for dato in resultados],
        )
        cursor = conexion.execute("UPDATE cola SET estado='leido', reintentos=?, fecha_fin=?, error=NULL WHERE id=?", (reintentos, fecha, id_tarea))
        if cursor.rowcount != 1:
            raise ValueError('La tarea no existe en cola: ' + str(id_tarea))


def reencolar_errores():
    """Rehabilita las tareas fallidas del ámbito actual para volver a consultarlas."""
    filtro, parametros = _filtro_archivos()
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        cursor = conexion.execute("UPDATE cola SET estado='pendiente', reintentos=0, error=NULL, fecha_fin=NULL WHERE estado='error'" + filtro, parametros)
        return cursor.rowcount


def guardar_tareas_entrada(archivo_id, tareas_validadas, fecha):
    """Inserta las tareas validadas sin duplicarlas y confirma la lectura del archivo."""
    agregadas = omitidas = 0
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        for tarea, destinos in tareas_validadas:
            for clave in destinos:
                cursor = conexion.execute(
                    "INSERT OR IGNORE INTO cola "
                    "(detail_id, producto, supermercado, marca, precio_minimo, precio_maximo, estado, reintentos, fecha_creacion) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pendiente', 0, ?)",
                    (archivo_id, tarea['producto'], clave, tarea.get('marca', ''),
                     tarea.get('precio_minimo'), tarea.get('precio_maximo'), fecha))
                if cursor.rowcount:
                    agregadas += 1
                else:
                    omitidas += 1
        conexion.execute("UPDATE details SET estado='leido', error=NULL, fecha_fin=? WHERE id=?", (fecha, archivo_id))
    return agregadas, omitidas


def tareas_pendientes():
    """Obtiene las consultas pendientes de los archivos incluidos en el ámbito actual."""
    filtro, parametros = _filtro_archivos()
    with closing(obtener_db_conexion_sql3()) as conexion:
        return conexion.execute(
            "SELECT id, supermercado, producto, marca, precio_minimo, precio_maximo "
            "FROM cola WHERE estado='pendiente'" + filtro + " ORDER BY id", parametros).fetchall()


def tareas_con_error():
    """Obtiene las incidencias de las tareas del ámbito actual."""
    filtro, parametros = _filtro_archivos()
    with closing(obtener_db_conexion_sql3()) as conexion:
        return conexion.execute("SELECT producto, supermercado, error FROM cola WHERE estado='error'" + filtro + " ORDER BY id", parametros).fetchall()


def datos_reporte():
    """Reúne productos e incidencias para el reporte, respetando el ámbito de entrada."""
    filtro, parametros = _filtro_archivos('c')
    with closing(obtener_db_conexion_sql3()) as conexion:
        encontrados = conexion.execute(
            "SELECT r.producto, r.nombre, r.precio, r.url, r.fecha, r.supermercado, "
            "c.marca, c.precio_minimo, c.precio_maximo "
            "FROM resultados r LEFT JOIN cola c ON c.id=r.cola_id WHERE 1=1" + filtro + " ORDER BY r.fecha, r.id", parametros).fetchall()
        incidencias = conexion.execute(
            "SELECT c.producto, c.supermercado, c.marca, c.precio_minimo, c.precio_maximo, "
            "c.estado, c.error, COALESCE(c.fecha_fin, c.fecha_creacion) FROM cola c "
            "WHERE (c.estado<>'leido' OR NOT EXISTS "
            "(SELECT 1 FROM resultados r WHERE r.cola_id=c.id))" + filtro + " ORDER BY c.id", parametros).fetchall()
    return encontrados, incidencias


def resumen_ejecucion(ruta):
    """Lee los contadores de una base existente en modo de solo lectura."""
    ruta = Path(ruta)
    if not ruta.is_file():
        return None
    with closing(conectar_sqlite(ruta.resolve().as_uri() + '?mode=ro', uri=True)) as conexion:
        return {
            'tareas': conexion.execute('SELECT estado,error FROM cola').fetchall(),
            'productos': conexion.execute('SELECT COUNT(*) FROM resultados').fetchone()[0],
            'archivos_error': conexion.execute("SELECT COUNT(*) FROM details WHERE estado='error'").fetchone()[0],
        }


def consultar_estado_cola():
    """Consulta el total por estado y una muestra de los errores recientes."""
    with closing(obtener_db_conexion_sql3()) as conexion:
        estados = conexion.execute('SELECT estado, COUNT(*) FROM cola GROUP BY estado').fetchall()
        errores = conexion.execute(
            "SELECT id, supermercado, producto, reintentos, error, fecha_fin "
            "FROM cola WHERE estado='error' ORDER BY id DESC LIMIT 10").fetchall()
    return estados, errores


def supermercados_por_validar():
    """Obtiene las tiendas de tareas pendientes o fallidas del ámbito actual."""
    filtro, parametros = _filtro_archivos()
    with closing(obtener_db_conexion_sql3()) as conexion:
        return conexion.execute("SELECT DISTINCT supermercado FROM cola WHERE estado IN ('pendiente','error')" + filtro, parametros).fetchall()
