"""Compatibilidad: el lector y la plantilla canonicos viven en productos."""
from integraciones.productos import (
    COLUMNAS,
    crear_plantilla_si_no_existe as crear_plantilla,
    leer_productos_excel,
)

__all__ = ['COLUMNAS', 'crear_plantilla', 'leer_productos_excel']
