import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

_ruta_db = None


def configurar_base_datos(ruta_db):
    global _ruta_db
    _ruta_db = Path(ruta_db)
    _ruta_db.parent.mkdir(parents=True, exist_ok=True)
    inicializar_bd()


def obtener_db_conexion_sql3():
    if _ruta_db is None:
        raise RuntimeError('La base de datos no esta configurada.')
    conexion = sqlite3.connect(_ruta_db)
    conexion.execute('PRAGMA foreign_keys = ON')
    return conexion


def _columnas(conexion, tabla):
    return {fila[1] for fila in conexion.execute('PRAGMA table_info(' + tabla + ')')}


def _crear_tablas(conexion, prefijo=''):
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
    copia = _ruta_db.with_name(_ruta_db.name + '.pre_v2_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8] + '.bak')
    with closing(sqlite3.connect(copia)) as destino:
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
    copia = _ruta_db.with_name(_ruta_db.name + '.pre_v3_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8] + '.bak')
    with closing(sqlite3.connect(copia)) as destino:
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
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute('UPDATE details SET estado=?, fecha_fin=?, error=? WHERE id=?', (estado, fecha, error, archivo_id))


def actualizar_cola(id_tarea, estado, reintentos, fecha_fin=None, error=None):
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute('UPDATE cola SET estado=?, reintentos=?, fecha_fin=?, error=? WHERE id=?', (estado, reintentos, fecha_fin, error, id_tarea))


def guardar_resultados(id_tarea, supermercado, producto, resultados, reintentos, fecha):
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
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        cursor = conexion.execute("UPDATE cola SET estado='pendiente', reintentos=0, error=NULL, fecha_fin=NULL WHERE estado='error'")
        return cursor.rowcount
