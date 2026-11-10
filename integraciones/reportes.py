import os
from pathlib import Path
from contextlib import closing

from openpyxl import Workbook

from config.base_datos import obtener_db_conexion_sql3
from config.configuracion import EXCEL_SALIDA, RUTA_REPORTES
from config.formato_excel import aplicar_formato_salida_excel
from integraciones.seguridad_excel import agregar_fila_literal
from integraciones.trazabilidad import metadatos, contexto
from integraciones.weyland.archivos import guardar_json
"""Aca, una vez ingresamos los datos, se sacan de la base de datos resultados y se genera un excel con todo ello. el formato viene de config.formato_excel"""

def generar_resultados_excel():
    with contexto():
        return _generar_resultados_excel()


def _generar_resultados_excel():
    with closing(obtener_db_conexion_sql3()) as conexion:
        filas = conexion.execute(
            "SELECT producto, nombre, precio, url, fecha, supermercado FROM resultados ORDER BY fecha"
        ).fetchall()

    RUTA_REPORTES.mkdir(parents=True, exist_ok=True)
    libro = Workbook()
    hoja = libro.active
    hoja.title = "resultados"
    agregar_fila_literal(hoja, ["producto", "nombre", "precio", "url", "fecha", "supermercado"])
    for fila in filas:
        agregar_fila_literal(hoja, fila)
    aplicar_formato_salida_excel(hoja)
    datos = metadatos()
    datos['filas_resultado'] = len(filas)
    auditoria = libro.create_sheet('ejecucion')
    agregar_fila_literal(auditoria, ['campo', 'valor'])
    for campo, valor in datos.items():
        agregar_fila_literal(auditoria, [campo, valor])
    aplicar_formato_salida_excel(auditoria)
    try:
        Path(EXCEL_SALIDA).parent.mkdir(parents=True, exist_ok=True)
        libro.save(EXCEL_SALIDA)
    finally:
        libro.close()
    nombre = 'trazabilidad_' + str(datos.get('activacion_id') or 'sin_activacion') + '.json'
    guardar_json(Path(EXCEL_SALIDA).parent / nombre, datos)
    print("Excel de salida creado: " + str(EXCEL_SALIDA) + " (" + str(len(filas)) + " filas)")


def ver_datos_en_tabla_cola(supermercado=None, limite=20):
    conexion = obtener_db_conexion_sql3()
    if supermercado:
        filas = conexion.execute(
            "SELECT supermercado, producto, nombre, precio, url, fecha "
            "FROM resultados WHERE supermercado=? ORDER BY fecha DESC LIMIT ?",
            (supermercado, limite),
        ).fetchall()
    else:
        filas = conexion.execute(
            "SELECT supermercado, producto, nombre, precio, url, fecha "
            "FROM resultados ORDER BY fecha DESC LIMIT ?",
            (limite,),
        ).fetchall()
    conexion.close()

    if not filas:
        print("no hay resultados, ejecutar python main.py procesar para generarlos")
        return

    print("Supermercado   Producto      Producto encontrado       Precio     Dto")
    print("-" * 80)
    for (super_, producto, nombre, precio, url, fecha) in filas:
        print(
            super_[:13].ljust(14)
            + (producto or "")[:13].ljust(14)
            + (nombre or "")[:28].ljust(29)
        )
    print("resultados listos, verlos en reportes/resultados.xlsx")
