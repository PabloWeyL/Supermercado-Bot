import os
from openpyxl import Workbook, load_workbook

from config.configuracion import EXCEL_ENTRADA, RUTA_INPUT
from integraciones.filtros import leer_precio, normalizar_marca

COLUMNAS = ("supermercado", "producto", "marca", "precio_minimo", "precio_maximo")

def leer_productos_excel(ruta=None):
    "funcion para leer los productos de excel y procesarlos si es que hay o no"
    ruta = str(ruta if ruta is not None else EXCEL_ENTRADA)
    if not os.path.exists(ruta):
        raise FileNotFoundError("No existe el Excel de entrada: " + ruta)

    libro = load_workbook(ruta, read_only=True, data_only=True)
    try:
        hoja = libro.active
        filas = list(hoja.iter_rows(values_only=True))
    finally:
        libro.close()

    if not filas:
        raise ValueError("El Excel de entrada esta vacio")

    encabezados = []
    for celda in filas[0]:
        if celda is None:
            encabezados.append("")
        else:
            encabezados.append(str(celda).strip().lower())
    if "producto" not in encabezados:
        raise ValueError("El excel de entrada necesita una columna llamada 'producto'")
    for nombre in COLUMNAS:
        if encabezados.count(nombre) > 1:
            raise ValueError("El excel tiene un encabezado duplicado: " + nombre)

    idx = {}
    for nombre in COLUMNAS:
        if nombre in encabezados:
            idx[nombre] = encabezados.index(nombre)

    def valor(fila,nombre):
        posicion = idx.get(nombre)
        if posicion is None or posicion >= len(fila):
            return None
        return fila[posicion]

    tareas = []
    for numero_fila, fila in enumerate(filas[1:], start=2):
        producto = valor(fila, "producto")
        if producto is None or str(producto).strip() == "":
            continue
        supermercado = valor(fila, "supermercado")
        marca = normalizar_marca(valor(fila, "marca"))
        minimo = leer_precio(valor(fila, "precio_minimo"), "precio_minimo", numero_fila)
        maximo = leer_precio(valor(fila, "precio_maximo"), "precio_maximo", numero_fila)
        if minimo is not None and maximo is not None and minimo > maximo:
            raise ValueError(f"Fila {numero_fila}: precio_minimo no puede superar precio_maximo")
        tareas.append(
            {
                "producto": str(producto).strip(),
                "supermercado": str(supermercado).strip() if supermercado is not None else None,
                "fila_excel": numero_fila,
                "marca": marca,
                "precio_minimo": minimo,
                "precio_maximo": maximo,
            }
        )

    return tareas


def crear_plantilla_si_no_existe():
    "esta funcion en caso de no existir excel de productos, lo genera"
    RUTA_INPUT.mkdir(parents=True, exist_ok=True)
    if os.path.exists(EXCEL_ENTRADA):
        print("Ya existe " + EXCEL_ENTRADA )
        return
    libro = Workbook()
    hoja = libro.active
    hoja.title = "productos"
    hoja.append(list(COLUMNAS))
    hoja.append([None, "leche"])
    hoja.append(["Unimarc", "arroz"])
    hoja.append(["Tottus", "café"])
    hoja.append(["Líder", "pan"])
    libro.save(EXCEL_ENTRADA)
    libro.close()
    print("Creado " + EXCEL_ENTRADA + " con 4 ejemplos")
