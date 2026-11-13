"""Estilos de los reportes Excel."""

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


RELLENO_ENCABEZADO = PatternFill("solid", fgColor="1F4E78")


FUENTE_ENCABEZADO = Font(bold=True, color="FFFFFF")


ALINEACION_ENCABEZADO = Alignment(horizontal="center", vertical="center")


#----------------------------------------------
# FUNCIONES DE FORMATO DE EXCEL
#----------------------------------------------
def aplicar_formato_salida_excel(hoja, congelar=True, autofiltro=True, ancho_max=45):
    "formato de excel para generar los resultados"
    if hoja.max_row < 1:
        return
    for celda in hoja[1]:
        celda.fill = RELLENO_ENCABEZADO
        celda.font = FUENTE_ENCABEZADO
        celda.alignment = ALINEACION_ENCABEZADO
    for columna in range(1, hoja.max_column + 1):
        letra = get_column_letter(columna)
        ancho = 0
        for celda in hoja[letra]:
            if celda.value is not None:
                ancho = max(ancho, len(str(celda.value)))
        hoja.column_dimensions[letra].width = min(ancho+ 3, ancho_max)

    if congelar: 
        hoja.freeze_panes = "A2"
    if autofiltro:
        hoja.auto_filter.ref = hoja.dimensions
