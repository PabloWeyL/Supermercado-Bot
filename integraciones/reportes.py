"""Reportes y resumen público de resultados del proceso."""

from config.archivos import RutasEjecucion, guardar_json
from config.autenticacion import ErrorWeyland
from config.base_datos import datos_reporte, resumen_ejecucion
from config.configuracion import EXCEL_SALIDA, RUTA_REPORTES, contexto, metadatos
from config.formato_excel import aplicar_formato_salida_excel
from integraciones.supermercados import (
    agregar_fila_literal,
    describir_error_busqueda,
    es_observacion_busqueda,
)
from openpyxl import Workbook
from openpyxl.styles import Alignment
from pathlib import Path
from textwrap import wrap


def generar_resultados_excel():
    """Genera el Excel de resultados e incidencias y su trazabilidad dentro del contexto de ejecución."""
    with contexto():
        encontrados, incidencias = datos_reporte()
        filas = [fila[:6] + ('',) + fila[6:] for fila in encontrados]
        for producto, supermercado, marca, minimo, maximo, estado, error, fecha in incidencias:
            filas.append((producto, None, None, None, fecha, supermercado,
                          describir_error_busqueda(error, estado), marca, minimo, maximo))

        RUTA_REPORTES.mkdir(parents=True, exist_ok=True)
        libro = Workbook()
        hoja = libro.active
        hoja.title = "resultados"
        agregar_fila_literal(hoja, ["producto", "nombre", "precio", "url", "fecha", "supermercado",
                                 "error", "marca_solicitada", "precio_minimo", "precio_maximo"])
        for fila in filas:
            agregar_fila_literal(hoja, fila)
        aplicar_formato_salida_excel(hoja)
        hoja.column_dimensions['G'].width = 80
        for celda in hoja['G'][1:]:
            celda.alignment = Alignment(vertical='top', wrap_text=True)
            if celda.value:
                hoja.row_dimensions[celda.row].height = 15 * max(1, len(wrap(str(celda.value), width=75)))
        datos = metadatos()
        datos['filas_resultado'] = len(filas)
        datos['productos_encontrados'] = len(encontrados)
        datos['consultas_con_incidencias'] = len(incidencias)
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


class ErrorProcesoBot(ErrorWeyland):
    def __init__(self, mensaje, resumen=None, estado_bot='ERROR'):
        """Conserva el resumen público y el estado final de un fallo de automatización."""
        super().__init__(mensaje)
        self.resumen_publico = resumen
        self.estado_bot = estado_bot


def leer_resumen(carpeta):
    """Calcula resultados, observaciones, errores y pendientes desde la base aislada de la ejecución."""
    datos = resumen_ejecucion(RutasEjecucion(carpeta).base_datos)
    if datos is None:
        return None
    tareas = datos['tareas']
    correctos = sum(e == 'leido' for e, _ in tareas)
    observaciones = sum(e == 'error' and es_observacion_busqueda(m) for e, m in tareas)
    fallos = sum(e == 'error' for e, _ in tareas) - observaciones
    pendientes = sum(e == 'pendiente' for e, _ in tareas)
    return {'procesados': correctos + fallos + observaciones, 'correctos': correctos,
            'errores': fallos + datos['archivos_error'], 'observaciones': observaciones,
            'pendientes': pendientes, 'productos_encontrados': datos['productos']}
