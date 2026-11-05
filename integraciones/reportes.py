import os

from openpyxl import Workbook

from config.base_datos import obtener_db_conexion_sql3
from config.configuracion import EXCEL_SALIDA, RUTA_REPORTES
from config.formato_excel import aplicar_formato_salida_excel
"""Aca, una vez ingresamos los datos, se sacan de la base de datos resultados y se genera un excel con todo ello. el formato viene de config.formato_excel"""

def generar_resultados_excel():
    conexion = obtener_db_conexion_sql3()
    filas = conexion.execute(
        "SELECT producto, nombre, precio, url, fecha, supermercado "
        "FROM resultados ORDER BY fecha"
    ).fetchall()
    conexion.close()

    RUTA_REPORTES.mkdir(parents=True, exist_ok=True)
    libro = Workbook()
    hoja = libro.active
    hoja.title = "resultados"
    hoja.append(["producto", "nombre", "precio", "url", "fecha", "supermercado"])
    for fila in filas:
        hoja.append(list(fila))
    aplicar_formato_salida_excel(hoja)
    libro.save(EXCEL_SALIDA)
    print("Excel de salida creado: " + EXCEL_SALIDA + " (" + str(len(filas)) + " filas)")


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
