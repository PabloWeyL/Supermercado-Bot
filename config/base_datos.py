"""Conexión SQLite, esquema y consultas del proceso."""

import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from uuid import uuid4


VERSION_ESQUEMA = 6


# Claves estables: el valor operativo pertenece al contexto de cada ejecución.
BASE_DATOS_ACTUAL = ContextVar('base_datos_actual', default=None)
ARCHIVOS_ENTRADA = ContextVar('archivos_entrada', default=None)


# ======================
# SESIÓN DE BASE DE DATOS POR EJECUCIÓN
# ======================


@contextmanager
def ambito_base_datos(ruta=None):
    """Aísla la selección de SQLite y restaura la anterior al salir, incluso ante errores."""
    token = BASE_DATOS_ACTUAL.set(None)
    try:
        if ruta is not None:
            configurar_base_datos(ruta)
        yield
    finally:
        BASE_DATOS_ACTUAL.reset(token)


def obtener_base_datos():
    """Exige una sesión SQLite configurada en el contexto actual."""
    base = BASE_DATOS_ACTUAL.get()
    if base is None:
        raise RuntimeError('La base de datos no esta configurada.')
    return base


# ======================
# FUNCIONES DE ÁMBITO DE ARCHIVOS
# ======================
@contextmanager
def ambito_archivos(rutas):
    """Limita consultas y reportes a los archivos locales seleccionados, sin borrar historia."""
    token = ARCHIVOS_ENTRADA.set(tuple(dict.fromkeys(str(Path(r).resolve()) for r in rutas)))
    try:
        yield
    finally:
        ARCHIVOS_ENTRADA.reset(token)


def filtro_archivos(alias='DETAILS_SUPERMERCADOS'):
    """Construye el filtro parametrizado que limita las consultas a los inputs del contexto actual."""
    rutas = ARCHIVOS_ENTRADA.get()
    if rutas is None:
        return '', ()
    if not rutas:
        return ' AND 0', ()
    condicion, parametros = filtro_queue_rutas(rutas)
    return f' AND {alias}.detail_id IN (SELECT id FROM QUEUE_SUPERMERCADOS WHERE {condicion})', parametros


def filtro_queue_rutas(rutas):
    """Selecciona los archivos de la ejecución indicada o su última versión."""

    from config import configuracion

    identificador = configuracion.IDENTIFICADOR_LOCAL.get()
    marcadores = ','.join('?' for _ in rutas)
    condicion = f'ruta IN ({marcadores})'
    if identificador:
        return condicion + ' AND id_ejecucion=?', (*rutas, identificador)
    return condicion + ' AND id IN (SELECT MAX(id) FROM QUEUE_SUPERMERCADOS GROUP BY ruta)', rutas


# ======================
# FUNCIONES DE CONEXIÓN A LA BASE DE DATOS
# ======================
def conectar_sqlite(*args, **kwargs):
    """Abre SQLite con el tiempo límite centralizado, sin duplicar su configuración."""
    from config.configuracion import DB_TIMEOUT
    return sqlite3.connect(*args, timeout=DB_TIMEOUT, **kwargs)


def configurar_base_datos(ruta_db):
    """Selecciona la base operativa, crea su carpeta e inicializa su estructura."""
    base = Path(ruta_db)
    base.parent.mkdir(parents=True, exist_ok=True)
    token = BASE_DATOS_ACTUAL.set(base)
    try:
        inicializar_bd()
    except Exception:
        BASE_DATOS_ACTUAL.reset(token)
        raise


def obtener_db_conexion_sql3():
    """Abre la base seleccionada y activa la verificación de claves foráneas."""
    conexion = conectar_sqlite(obtener_base_datos())
    conexion.execute('PRAGMA foreign_keys = ON')
    return conexion


# ======================
# FUNCIONES DE ESQUEMA Y MIGRACIONES
# ======================
def obtener_columnas(conexion, tabla):
    """Consulta los nombres de columnas de una tabla durante la validación o migración."""
    return {fila[1] for fila in conexion.execute('PRAGMA table_info(' + tabla + ')')}


def crear_tablas(conexion, prefijo='', *, estandar=False):
    """Crea las tablas de archivos, tareas y resultados con sus restricciones."""
    tabla_archivos = 'QUEUE_SUPERMERCADOS' if estandar else prefijo + 'details'
    tabla_tareas = 'DETAILS_SUPERMERCADOS' if estandar else prefijo + 'cola'
    tabla_resultados = 'AUX_SUPERMERCADOS_RESULTADOS' if estandar else prefijo + 'resultados'
    referencias_archivos = tabla_archivos if estandar else 'details'
    referencias_tareas = tabla_tareas if estandar else 'cola'

    estado_archivo = ("DEFAULT 'PENDIENTE' CHECK (estado IN "
                      "('PENDIENTE','EN_EJECUCION','PROCESADO','FINALIZADO','ERROR'))"
                      if estandar else "DEFAULT 'pendiente' CHECK (estado IN ('pendiente','leido','error'))")
    estado_tarea = ("DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE','LEIDO','ERROR'))"
                    if estandar else "DEFAULT 'pendiente' CHECK (estado IN ('pendiente','leido','error'))")
    seguimiento = ('id_ejecucion TEXT, modulo_actual TEXT, ultimo_modulo_completado TEXT, ruta_reporte TEXT,'
                   if estandar else '')

    ruta_columna = 'ruta TEXT' if estandar else 'ruta TEXT UNIQUE'
    conexion.execute(f'''
        CREATE TABLE IF NOT EXISTS {tabla_archivos} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archivo TEXT NOT NULL,
            {ruta_columna},
            estado TEXT NOT NULL {estado_archivo},
            {seguimiento}
            error TEXT,
            fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            fecha_fin TEXT
        )
    ''')
    conexion.execute(f'''
        CREATE TABLE IF NOT EXISTS {tabla_tareas} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detail_id INTEGER REFERENCES {referencias_archivos}(id),
            producto TEXT NOT NULL,
            supermercado TEXT NOT NULL,
            marca TEXT NOT NULL DEFAULT '',
            precio_minimo REAL,
            precio_maximo REAL,
            estado TEXT NOT NULL {estado_tarea},
            reintentos INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            fecha_fin TEXT
        )
    ''')
    conexion.execute(f'''
        CREATE TABLE IF NOT EXISTS {tabla_resultados} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cola_id INTEGER REFERENCES {referencias_tareas}(id),
            supermercado TEXT,
            producto TEXT,
            nombre TEXT,
            precio REAL,
            url TEXT,
            fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    ''')


def migrar_estructura_anterior(conexion):
    """Respalda y convierte el esquema antiguo dentro de una transacción recuperable."""
    ruta_db = obtener_base_datos()
    copia = ruta_db.with_name(ruta_db.name + '.pre_v2_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8] + '.bak')
    with closing(conectar_sqlite(copia)) as destino:
        conexion.backup(destino)
    conexion.execute('PRAGMA foreign_keys = OFF')
    try:
        conexion.execute('BEGIN IMMEDIATE')
        crear_tablas(conexion, '_v2_')
        archivo_id = 'cola_id' if 'cola_id' in obtener_columnas(conexion, 'details') else 'NULL'
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
        columnas_resultados = obtener_columnas(conexion, 'resultados')
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


def migrar_filtros(conexion):
    """Respalda y migra las tareas para incorporar marca y rango de precios."""
    ruta_db = obtener_base_datos()
    copia = ruta_db.with_name(ruta_db.name + '.pre_v3_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8] + '.bak')
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


def migrar_estados_operativos(conexion):
    """Migra las tablas operativas sin perder estados, tareas ni resultados."""

    grupo_v3 = ('details', 'cola', 'resultados')
    grupo_v4 = ('QUEUE_SUPERMERCADOS', 'DETAILS_SUPERMERCADOS', 'AUX_SUPERMERCADOS_RESULTADOS')
    hay_v3 = bool(obtener_columnas(conexion, grupo_v3[0]))
    columnas = obtener_columnas(conexion, grupo_v4[0])
    if hay_v3 and columnas:
        raise RuntimeError('SQLite contiene dos esquemas operativos')
    version = conexion.execute('PRAGMA user_version').fetchone()[0]
    if not (hay_v3 or columnas) or ('id_ejecucion' in columnas and version >= 6):
        return
    origen = grupo_v3 if hay_v3 else grupo_v4
    for anterior, temporal in zip(origen, ('_migracion_archivos', '_migracion_tareas', '_migracion_productos')):
        if obtener_columnas(conexion, anterior):
            conexion.execute(f'ALTER TABLE {anterior} RENAME TO {temporal}')
    crear_tablas(conexion, estandar=True)

    if 'id_ejecucion' in columnas:
        conexion.execute("""
            INSERT INTO QUEUE_SUPERMERCADOS
            (id, archivo, ruta, estado, error, fecha_creacion, fecha_fin, id_ejecucion,
             modulo_actual, ultimo_modulo_completado, ruta_reporte)
            SELECT id, archivo, ruta, estado, error, fecha_creacion, fecha_fin, id_ejecucion,
                   modulo_actual, ultimo_modulo_completado, ruta_reporte FROM _migracion_archivos
        """)
    else:
        conexion.execute("""
            INSERT INTO QUEUE_SUPERMERCADOS (id, archivo, ruta, estado, error, fecha_creacion, fecha_fin)
            SELECT id, archivo, ruta,
                CASE estado WHEN 'leido' THEN 'PROCESADO' ELSE UPPER(estado) END,
                error, fecha_creacion, fecha_fin FROM _migracion_archivos
        """)
    conexion.execute("""
        INSERT INTO DETAILS_SUPERMERCADOS
        (id, detail_id, producto, supermercado, marca, precio_minimo, precio_maximo,
         estado, reintentos, error, fecha_creacion, fecha_fin)
        SELECT id, detail_id, producto, supermercado, marca, precio_minimo, precio_maximo,
               UPPER(estado), reintentos, error, fecha_creacion, fecha_fin FROM _migracion_tareas
    """)
    if obtener_columnas(conexion, '_migracion_productos'):
        conexion.execute("""
            INSERT INTO AUX_SUPERMERCADOS_RESULTADOS
            (id, cola_id, supermercado, producto, nombre, precio, url, fecha)
            SELECT id, cola_id, supermercado, producto, nombre, precio, url, fecha FROM _migracion_productos
        """)
    if conexion.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError('La migración de estados encontró relaciones inválidas')
    for tabla in ('_migracion_productos', '_migracion_tareas', '_migracion_archivos'):
        conexion.execute(f'DROP TABLE IF EXISTS {tabla}')


def inicializar_bd():
    """Detecta migraciones pendientes y asegura el esquema, los índices y las restricciones."""
    with closing(obtener_db_conexion_sql3()) as conexion:
        if 'producto' in obtener_columnas(conexion, 'details') and 'archivo' in obtener_columnas(conexion, 'cola'):
            migrar_estructura_anterior(conexion)
        if obtener_columnas(conexion, 'cola') and 'marca' not in obtener_columnas(conexion, 'cola'):
            migrar_filtros(conexion)
        with conexion:
            conexion.execute('BEGIN IMMEDIATE')
            migrar_estados_operativos(conexion)
            crear_tablas(conexion, estandar=True)
            for sql in (
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_cola_filtros ON DETAILS_SUPERMERCADOS(detail_id, producto, supermercado, marca, COALESCE(precio_minimo, -1), COALESCE(precio_maximo, -1))",
                'CREATE INDEX IF NOT EXISTS idx_details_estado ON QUEUE_SUPERMERCADOS(estado)',
                'CREATE INDEX IF NOT EXISTS idx_cola_estado ON DETAILS_SUPERMERCADOS(estado)',
                'CREATE INDEX IF NOT EXISTS idx_cola_detail ON DETAILS_SUPERMERCADOS(detail_id)',
                'CREATE INDEX IF NOT EXISTS idx_resultados_busqueda ON AUX_SUPERMERCADOS_RESULTADOS(producto, supermercado, fecha)',
                'CREATE INDEX IF NOT EXISTS idx_resultados_cola ON AUX_SUPERMERCADOS_RESULTADOS(cola_id)',
            ):
                conexion.execute(sql)
            conexion.execute('CREATE INDEX IF NOT EXISTS idx_queue_ejecucion ON QUEUE_SUPERMERCADOS(id_ejecucion)')
            conexion.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_ejecucion_ruta '
                             'ON QUEUE_SUPERMERCADOS(id_ejecucion, ruta)')
            conexion.execute('CREATE INDEX IF NOT EXISTS idx_queue_ruta ON QUEUE_SUPERMERCADOS(ruta)')
            conexion.execute(f'PRAGMA user_version = {VERSION_ESQUEMA}')


# ======================
# FUNCIONES DE REGISTRO Y ACTUALIZACIÓN DE ESTADOS
# ======================
def id_ejecucion_operativo():
    """Resuelve el ID local o la correlación de la base web aislada."""

    from config import configuracion

    contexto = configuracion.actual()
    if contexto.get('solicitud_id') is None:
        return configuracion.IDENTIFICADOR_LOCAL.get() or contexto['activacion_id']
    return contexto['activacion_id']


def registrar_archivo(ruta, fecha):
    """Registra o recupera el archivo por su ruta y reinicia su estado de lectura."""
    ruta = Path(ruta).resolve()
    identificador = id_ejecucion_operativo()
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute('BEGIN IMMEDIATE')
        fila = conexion.execute(
            'SELECT id, id_ejecucion, estado FROM QUEUE_SUPERMERCADOS WHERE ruta=? AND id_ejecucion IS ?',
            (str(ruta), identificador),
        ).fetchone()
        if not fila:
            fila = conexion.execute(
                'SELECT id, id_ejecucion, estado FROM QUEUE_SUPERMERCADOS '
                'WHERE id_ejecucion IS NULL AND (ruta=? OR (ruta IS NULL AND archivo=?)) ORDER BY id DESC LIMIT 1',
                (str(ruta), ruta.name),
            ).fetchone()
        if fila:
            archivo_id, id_anterior, estado = fila
            if identificador and str(id_anterior) == str(identificador) and estado in ('FINALIZADO', 'ERROR', 'PROCESADO'):
                raise RuntimeError(f"LA EJECUCIÓN {identificador} YA ESTÁ {estado}")
            conexion.execute("""
                UPDATE QUEUE_SUPERMERCADOS SET ruta=?, id_ejecucion=?, estado='PENDIENTE',
                    error=NULL, fecha_fin=NULL, modulo_actual='CARGA',
                    ultimo_modulo_completado=NULL, ruta_reporte=NULL WHERE id=?
            """, (str(ruta), identificador, archivo_id))
        else:
            archivo_id = conexion.execute("""
                INSERT INTO QUEUE_SUPERMERCADOS
                (archivo, ruta, id_ejecucion, estado, fecha_creacion, modulo_actual)
                VALUES (?, ?, ?, 'PENDIENTE', ?, 'CARGA')
            """, (ruta.name, str(ruta), identificador, fecha)).lastrowid
    return archivo_id


def actualizar_archivo(archivo_id, estado, fecha, error=None):
    """Actualiza el estado, fecha y error de un archivo de entrada."""
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute('UPDATE QUEUE_SUPERMERCADOS SET estado=?, fecha_fin=?, error=? WHERE id=?', (estado.upper(), fecha, error, archivo_id))


def actualizar_cola(id_tarea, estado, reintentos, fecha_fin=None, error=None):
    """Actualiza el estado y los intentos de una tarea concreta."""
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute('UPDATE DETAILS_SUPERMERCADOS SET estado=?, reintentos=?, fecha_fin=?, error=? WHERE id=?', (estado.upper(), reintentos, fecha_fin, error, id_tarea))


def procesar_archivo(ruta):
    """Pasa a PROCESADO un archivo cuya búsqueda terminó sin ítems pendientes."""

    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute("""
            UPDATE QUEUE_SUPERMERCADOS AS q SET estado='PROCESADO',
                modulo_actual='REPORTE', ultimo_modulo_completado='CONSULTA'
            WHERE q.ruta=? AND q.id_ejecucion IS ? AND q.estado='EN_EJECUCION' AND NOT EXISTS (
                SELECT 1 FROM DETAILS_SUPERMERCADOS WHERE detail_id=q.id AND estado='PENDIENTE'
            )
        """, (str(Path(ruta).resolve()), id_ejecucion_operativo()))


def cerrar_archivos(rutas, reporte, confirmado):
    """Persiste el reporte y cierra solo los archivos con entrega confirmada."""

    reporte = str(Path(reporte).resolve())
    estado = 'FINALIZADO' if confirmado else 'PROCESADO'
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        consulta = """
            UPDATE QUEUE_SUPERMERCADOS SET estado=?, ruta_reporte=?, modulo_actual='CIERRE',
                ultimo_modulo_completado='REPORTE',
                fecha_fin=CASE WHEN ? THEN datetime('now', 'localtime') ELSE NULL END
            WHERE estado='PROCESADO'
        """
        if rutas is None:  # SQLite aislado de una única solicitud web.
            conexion.execute(consulta, (estado, reporte, confirmado))
        else:
            identificador = id_ejecucion_operativo()
            conexion.executemany(consulta + ' AND ruta=? AND id_ejecucion IS ?',
                                 [(estado, reporte, confirmado, str(Path(ruta).resolve()), identificador)
                                  for ruta in rutas])


def archivos_por_ejecucion(identificador):
    """Consulta los archivos y estados previamente asociados al ID."""

    with closing(obtener_db_conexion_sql3()) as conexion:
        return conexion.execute("""
            SELECT ruta, estado FROM QUEUE_SUPERMERCADOS WHERE id_ejecucion=? ORDER BY id
        """, (identificador,)).fetchall()


def archivos_con_cierre_pendiente():
    """Identifica entradas que esperan confirmación y no se deben reprocesar."""

    with closing(obtener_db_conexion_sql3()) as conexion:
        return {Path(ruta) for ruta, in conexion.execute("""
            SELECT ruta FROM QUEUE_SUPERMERCADOS
            WHERE estado='PROCESADO' AND ruta_reporte IS NOT NULL AND ruta IS NOT NULL
        """)}


def confirmar_cierre_por_correo(identificador):
    """Recupera el cierre cuando el correo pendiente ya tiene confirmación."""

    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.execute("""
            UPDATE QUEUE_SUPERMERCADOS SET estado='FINALIZADO', fecha_fin=datetime('now', 'localtime')
            WHERE id_ejecucion=? AND estado='PROCESADO' AND ruta_reporte IS NOT NULL
        """, (identificador,))


def obtener_estado_fallo(id_ejecucion):
    """Obtiene los estados de archivos y conteos de búsquedas para el diagnóstico."""

    with closing(obtener_db_conexion_sql3()) as conexion:
        estados = conexion.execute("""
            SELECT DISTINCT estado FROM QUEUE_SUPERMERCADOS WHERE id_ejecucion=? ORDER BY estado
        """, (id_ejecucion,)).fetchall()
        detalles = conexion.execute("""
            SELECT d.estado, COUNT(*) FROM DETAILS_SUPERMERCADOS d
            JOIN QUEUE_SUPERMERCADOS q ON q.id=d.detail_id
            WHERE q.id_ejecucion=? GROUP BY d.estado
        """, (id_ejecucion,)).fetchall()
    return ', '.join(estado for estado, in estados) or 'NO_ENCONTRADA', detalles


def registrar_error_ejecucion(rutas):
    """Marca fallos críticos sin cambiar el estado de búsquedas ya confirmadas."""

    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.executemany("""
            UPDATE QUEUE_SUPERMERCADOS SET estado='ERROR', error='Fallo crítico del proceso',
                fecha_fin=datetime('now', 'localtime')
            WHERE ruta=? AND id_ejecucion IS ? AND estado IN ('PENDIENTE', 'EN_EJECUCION', 'PROCESADO')
        """, [(str(Path(ruta).resolve()), id_ejecucion_operativo()) for ruta in rutas])


# ======================
# FUNCIONES DE RESULTADOS Y REPROCESO
# ======================
def guardar_resultados(id_tarea, supermercado, producto, resultados, reintentos, fecha):
    """Guarda los productos y marca la tarea como leída en la misma transacción."""
    if not resultados:
        raise ValueError('No hay resultados para guardar')
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        conexion.executemany(
            'INSERT INTO AUX_SUPERMERCADOS_RESULTADOS (cola_id, supermercado, producto, nombre, precio, url, fecha) VALUES (?, ?, ?, ?, ?, ?, ?)',
            [(id_tarea, supermercado, producto, dato['nombre'], dato['precio'], dato['url'], fecha) for dato in resultados],
        )
        cursor = conexion.execute("UPDATE DETAILS_SUPERMERCADOS SET estado='LEIDO', reintentos=?, fecha_fin=?, error=NULL WHERE id=?", (reintentos, fecha, id_tarea))
        if cursor.rowcount != 1:
            raise ValueError('La tarea no existe en cola: ' + str(id_tarea))


def reencolar_errores():
    """Rehabilita las tareas fallidas del ámbito actual para volver a consultarlas."""
    filtro, parametros = filtro_archivos()
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        cursor = conexion.execute("UPDATE DETAILS_SUPERMERCADOS SET estado='PENDIENTE', reintentos=0, error=NULL, fecha_fin=NULL WHERE estado='ERROR'" + filtro, parametros)
        return cursor.rowcount


# ======================
# FUNCIONES DE CARGA Y CONSULTA DE TAREAS
# ======================
def guardar_tareas_entrada(archivo_id, tareas_validadas, fecha):
    """Inserta las tareas validadas sin duplicarlas y confirma la lectura del archivo."""
    agregadas = omitidas = 0
    with closing(obtener_db_conexion_sql3()) as conexion, conexion:
        for tarea, destinos in tareas_validadas:
            for clave in destinos:
                cursor = conexion.execute(
                    "INSERT OR IGNORE INTO DETAILS_SUPERMERCADOS "
                    "(detail_id, producto, supermercado, marca, precio_minimo, precio_maximo, estado, reintentos, fecha_creacion) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'PENDIENTE', 0, ?)",
                    (archivo_id, tarea['producto'], clave, tarea.get('marca', ''),
                     tarea.get('precio_minimo'), tarea.get('precio_maximo'), fecha))
                if cursor.rowcount:
                    agregadas += 1
                else:
                    omitidas += 1
        conexion.execute("""
            UPDATE QUEUE_SUPERMERCADOS SET estado='EN_EJECUCION',
                modulo_actual='CONSULTA', ultimo_modulo_completado='CARGA', error=NULL, fecha_fin=NULL
            WHERE id=?
        """, (archivo_id,))
    return agregadas, omitidas


def tareas_pendientes():
    """Obtiene las consultas pendientes de los archivos incluidos en el ámbito actual."""
    filtro, parametros = filtro_archivos()
    with closing(obtener_db_conexion_sql3()) as conexion:
        return conexion.execute(
            "SELECT id, supermercado, producto, marca, precio_minimo, precio_maximo "
            "FROM DETAILS_SUPERMERCADOS WHERE estado='PENDIENTE'" + filtro + " ORDER BY id", parametros).fetchall()


def tareas_con_error():
    """Obtiene las incidencias de las tareas del ámbito actual."""
    filtro, parametros = filtro_archivos()
    with closing(obtener_db_conexion_sql3()) as conexion:
        return conexion.execute("SELECT producto, supermercado, error FROM DETAILS_SUPERMERCADOS WHERE estado='ERROR'" + filtro + " ORDER BY id", parametros).fetchall()


# ======================
# FUNCIONES DE REPORTES Y DIAGNÓSTICO
# ======================
def datos_reporte():
    """Reúne productos e incidencias para el reporte, respetando el ámbito de entrada."""
    filtro, parametros = filtro_archivos('c')
    with closing(obtener_db_conexion_sql3()) as conexion:
        encontrados = conexion.execute(
            "SELECT r.producto, r.nombre, r.precio, r.url, r.fecha, r.supermercado, "
            "c.marca, c.precio_minimo, c.precio_maximo "
            "FROM AUX_SUPERMERCADOS_RESULTADOS r LEFT JOIN DETAILS_SUPERMERCADOS c ON c.id=r.cola_id WHERE 1=1" + filtro + " ORDER BY r.fecha, r.id", parametros).fetchall()
        incidencias = conexion.execute(
            "SELECT c.producto, c.supermercado, c.marca, c.precio_minimo, c.precio_maximo, "
            "LOWER(c.estado), c.error, COALESCE(c.fecha_fin, c.fecha_creacion) FROM DETAILS_SUPERMERCADOS c "
            "WHERE (c.estado<>'LEIDO' OR NOT EXISTS "
            "(SELECT 1 FROM AUX_SUPERMERCADOS_RESULTADOS r WHERE r.cola_id=c.id))" + filtro + " ORDER BY c.id", parametros).fetchall()
    return encontrados, incidencias


def datos_auditoria_reporte():
    """Obtiene los archivos y el detalle operacional del ámbito del reporte."""

    filtro, parametros = filtro_archivos('d')
    rutas = ARCHIVOS_ENTRADA.get()
    condicion, parametros_queue = filtro_queue_rutas(rutas) if rutas is not None else ('1=1', ())
    with closing(obtener_db_conexion_sql3()) as conexion:
        queue = conexion.execute("""
            SELECT id_ejecucion, archivo, estado, modulo_actual, ultimo_modulo_completado,
                   fecha_creacion, fecha_fin, error FROM QUEUE_SUPERMERCADOS
        """ + ' WHERE ' + condicion + ' ORDER BY id', parametros_queue).fetchall()
        detalles = conexion.execute("""
            SELECT q.id_ejecucion, d.producto, d.supermercado, d.estado, d.reintentos,
                   d.error, d.fecha_fin FROM DETAILS_SUPERMERCADOS d
            LEFT JOIN QUEUE_SUPERMERCADOS q ON q.id=d.detail_id WHERE 1=1
        """ + filtro + ' ORDER BY d.id', parametros).fetchall()
    return queue, detalles


def resumen_ejecucion(ruta):
    """Lee los contadores de una base existente en modo de solo lectura."""
    ruta = Path(ruta)
    if not ruta.is_file():
        return None
    with closing(conectar_sqlite(ruta.resolve().as_uri() + '?mode=ro', uri=True)) as conexion:
        if obtener_columnas(conexion, 'DETAILS_SUPERMERCADOS'):
            archivos, tareas, resultados = ('QUEUE_SUPERMERCADOS', 'DETAILS_SUPERMERCADOS', 'AUX_SUPERMERCADOS_RESULTADOS')
        else:
            archivos, tareas, resultados = ('details', 'cola', 'resultados')
        return {
            'tareas': conexion.execute(f'SELECT LOWER(estado),error FROM {tareas}').fetchall(),
            'productos': conexion.execute(f'SELECT COUNT(*) FROM {resultados}').fetchone()[0],
            'archivos_error': conexion.execute(f"SELECT COUNT(*) FROM {archivos} WHERE UPPER(estado)='ERROR'").fetchone()[0],
        }


def consultar_estado_cola():
    """Consulta el total por estado y una muestra de los errores recientes."""
    with closing(obtener_db_conexion_sql3()) as conexion:
        estados = conexion.execute('SELECT estado, COUNT(*) FROM DETAILS_SUPERMERCADOS GROUP BY estado').fetchall()
        errores = conexion.execute(
            "SELECT id, supermercado, producto, reintentos, error, fecha_fin "
            "FROM DETAILS_SUPERMERCADOS WHERE estado='ERROR' ORDER BY id DESC LIMIT 10").fetchall()
    return estados, errores


def supermercados_por_validar():
    """Obtiene las tiendas de tareas pendientes o fallidas del ámbito actual."""
    filtro, parametros = filtro_archivos()
    with closing(obtener_db_conexion_sql3()) as conexion:
        return conexion.execute("SELECT DISTINCT supermercado FROM DETAILS_SUPERMERCADOS WHERE estado IN ('PENDIENTE','ERROR')" + filtro, parametros).fetchall()


def estados_id_local(ruta, identificador):
    """Consulta la Queue sin inicializar SQLite ni modificar registros anteriores."""

    ruta = Path(ruta).resolve()
    if not ruta.is_file():
        raise RuntimeError("ID_EJECUCION REQUIERE UNA BASE OPERATIVA EXISTENTE")
    with closing(conectar_sqlite(ruta.as_uri() + '?mode=ro', uri=True)) as con:
        if 'id_ejecucion' in obtener_columnas(con, 'QUEUE_SUPERMERCADOS'):
            estados = [fila[0] for fila in con.execute(
                "SELECT DISTINCT estado FROM QUEUE_SUPERMERCADOS WHERE id_ejecucion=?",
                (identificador,),
            )]
        else:
            estados = []
    if not estados:
        raise RuntimeError("ID_EJECUCION NO EXISTE EN QUEUE_SUPERMERCADOS")
    return estados
