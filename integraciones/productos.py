import os
from openpyxl import Workbook, load_workbook

from config.configuracion import EXCEL_ENTRADA, RUTA_INPUT
from integraciones.filtros import leer_precio, normalizar_marca
from integraciones.productos_json import leer_productos_json
from integraciones.seguridad_excel import validar_contenedor, texto_entrada, MAX_FILAS, MAX_COLUMNAS

COLUMNAS = ("supermercado", "producto", "marca", "precio_minimo", "precio_maximo")


def leer_productos_excel(ruta=None):
    "funcion para leer los productos de excel y procesarlos si es que hay o no"
    ruta = str(ruta if ruta is not None else EXCEL_ENTRADA)
    if not os.path.exists(ruta):
        raise FileNotFoundError("No existe el Excel de entrada: " + ruta)

    copia_validada = validar_contenedor(ruta)
    libro = load_workbook(copia_validada, read_only=True, data_only=False, keep_links=False)
    try:
        hoja = libro.active
        if (hoja.max_row or 0) > MAX_FILAS + 1 or (hoja.max_column or 0) > MAX_COLUMNAS:
            raise ValueError('El Excel supera el limite de filas o columnas')
        hoja.reset_dimensions()
        filas = []
        for numero, fila in enumerate(hoja.iter_rows(max_col=MAX_COLUMNAS + 1), start=1):
            if numero > MAX_FILAS + 1:
                raise ValueError('El Excel supera el limite de filas')
            if fila[-1].value is not None:
                raise ValueError('El Excel supera el limite de columnas')
            if any(celda.data_type == 'f' for celda in fila):
                raise ValueError(f'Fila {numero}: el input contiene formulas; guardar como valores antes de ejecutar')
            # Mantener solo las columnas con contenido; lectura y memoria acotadas.
            valores = [celda.value for celda in fila[:-1]]
            while valores and valores[-1] is None:
                valores.pop()
            filas.append(valores)
    finally:
        libro.close()
        copia_validada.close()

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
        producto = texto_entrada(producto, 'producto', numero_fila, 500, obligatorio=True)
        supermercado = texto_entrada(supermercado, 'supermercado', numero_fila, 100) or None
        marca = normalizar_marca(texto_entrada(valor(fila, 'marca'), 'marca', numero_fila, 200))
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

    if not tareas:
        raise ValueError('El Excel no contiene productos para procesar')
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
