from pathlib import Path
from contextlib import closing

from config.base_datos import actualizar_archivo, obtener_db_conexion_sql3, registrar_archivo
from config.configuracion import RUTA_INPUT, ahora, obtener_logger
from integraciones.productos import leer_productos_excel
from integraciones.supermercados import cargar_supermercados, seleccionar_supermercados


def cargar_excel_a_tabla_cola(ruta=None):
    """Sin ruta, carga todos los Excel validos de la carpeta input."""
    if ruta is not None:
        return _cargar_archivo_a_cola(Path(ruta))
    logger = obtener_logger("DETAILS")
    carpeta = Path(RUTA_INPUT)
    resumen = {"archivos_leidos": 0, "archivos_error": 0, "tareas_agregadas": 0, "tareas_existentes": 0}
    if not carpeta.is_dir():
        logger.warning("No existe la carpeta de entrada: " + str(carpeta))
        return resumen
    archivos = sorted((p for p in carpeta.iterdir()
                       if p.is_file() and p.suffix.lower() in (".xlsx", ".xlsm")
                       and not p.name.startswith("~$")), key=lambda p: p.name.casefold())
    if not archivos:
        logger.info("No hay archivos Excel para cargar en " + str(carpeta))
    for archivo in archivos:
        try:
            carga = _cargar_archivo_a_cola(archivo)
        except Exception as error:
            resumen["archivos_error"] += 1
            logger.warning("Archivo omitido: " + archivo.name + "; " + str(error))
            continue
        resumen["archivos_leidos"] += 1
        resumen["tareas_agregadas"] += carga["tareas_agregadas"]
        resumen["tareas_existentes"] += carga["tareas_existentes"]
    logger.info("Carga de carpeta finalizada: " + str(resumen))
    return resumen


def _cargar_archivo_a_cola(ruta):
    logger = obtener_logger("DETAILS")
    archivo_id = registrar_archivo(ruta, ahora())
    agregadas = 0
    omitidas = 0
    try:
        tareas = leer_productos_excel(ruta)
        supermercados = cargar_supermercados()
        tareas_validadas = []
        for tarea in tareas:
            try:
                destinos = seleccionar_supermercados(tarea.get("supermercado"), supermercados)
            except ValueError as error:
                raise ValueError("Fila " + str(tarea["fila_excel"]) + ": " + str(error)) from error
            tareas_validadas.append((tarea, destinos))

        with closing(obtener_db_conexion_sql3()) as conexion, conexion:
            for tarea, destinos in tareas_validadas:
                for clave in destinos:
                    cursor = conexion.execute(
                        "INSERT OR IGNORE INTO cola "
                        "(detail_id, producto, supermercado, marca, precio_minimo, precio_maximo, estado, reintentos, fecha_creacion) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'pendiente', 0, ?)",
                        (archivo_id, tarea["producto"], clave, tarea.get("marca", ""),
                         tarea.get("precio_minimo"), tarea.get("precio_maximo"), ahora()),
                    )
                    if cursor.rowcount:
                        agregadas += 1
                    else:
                        omitidas += 1
            conexion.execute(
                "UPDATE details SET estado='leido', error=NULL, fecha_fin=? WHERE id=?",
                (ahora(), archivo_id),
            )
    except Exception as error:
        actualizar_archivo(archivo_id, "error", ahora(), str(error))
        raise

    logger.info("Archivo leido: " + ruta.name + "; agregadas " + str(agregadas)
                + " tareas a cola; " + str(omitidas) + " ya existian")
    return {"tareas_agregadas": agregadas, "tareas_existentes": omitidas}


def ver_datos_en_tabla_cola():
    conexion = obtener_db_conexion_sql3()
    filas = conexion.execute(
        "SELECT estado, COUNT(*) FROM cola GROUP BY estado"
    ).fetchall()
    errores = conexion.execute(
        "SELECT id, supermercado, producto, reintentos, error, fecha_fin "
        "FROM cola WHERE estado='error' ORDER BY id DESC LIMIT 10"
    ).fetchall()
    conexion.close()

    if not filas:
        print("La cola está vacía. Usa: python main.py cargar")
        return

    print("Estado de la cola:")
    estados_presentes = []
    for estado, cantidad in filas:
        estados_presentes.append(estado)
        print("  " + estado.ljust(12) + str(cantidad))

    if errores:
        print("\nÚltimas tareas con error:")
        for (id_, super_, producto, reintentos, error, fecha) in errores:
            print("  id " + str(id_).ljust(4) + super_.ljust(13) + producto[:30] +
                  " reintentos: " + str(reintentos) + " " + (fecha or ""))
            if error:
                print("      " + str(error)[:100])
    elif "error" not in estados_presentes:
        print("\nNo hay errores.")
