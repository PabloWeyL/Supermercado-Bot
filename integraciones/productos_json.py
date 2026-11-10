"""Validacion de productos JSON con limites configurados, sin acceso a servicios."""
import json
from pathlib import Path

from integraciones.filtros import leer_precio, normalizar_marca
from integraciones.seguridad_excel import MAX_FILAS, MAX_ARCHIVO, texto_entrada


def leer_productos_json(ruta):
    """Productos de parametros web; no requiere ni crea un Excel."""
    if Path(ruta).stat().st_size > MAX_ARCHIVO:
        raise ValueError(f'Los productos JSON superan MAX_ARCHIVO ({MAX_ARCHIVO} bytes)')
    with open(ruta, encoding='utf-8') as f:
        contenido = json.load(f)
    registros = contenido.get('productos') if isinstance(contenido, dict) else contenido
    if not isinstance(registros, list) or not registros:
        raise ValueError('Un proceso de supermercados sin Excel necesita productos en sus parametros')
    if len(registros) > MAX_FILAS:
        raise ValueError('Los productos JSON superan el limite de registros')
    tareas = []
    for numero, item in enumerate(registros, start=1):
        if not isinstance(item, dict) or not isinstance(item.get('producto'), str) or not item['producto'].strip():
            raise ValueError(f'Producto JSON {numero}: falta un nombre de producto valido')
        minimo = leer_precio(item.get('precio_minimo'), 'precio_minimo', numero)
        maximo = leer_precio(item.get('precio_maximo'), 'precio_maximo', numero)
        if minimo is not None and maximo is not None and minimo > maximo:
            raise ValueError(f'Producto JSON {numero}: precio_minimo no puede superar precio_maximo')
        supermercado = item.get('supermercado')
        if supermercado is not None and not isinstance(supermercado, str):
            raise ValueError(f'Producto JSON {numero}: supermercado invalido')
        tareas.append({'producto': texto_entrada(item['producto'], 'producto', numero, 500, obligatorio=True),
                       'supermercado': texto_entrada(supermercado, 'supermercado', numero, 100) or None,
                       'marca': normalizar_marca(texto_entrada(item.get('marca'), 'marca', numero, 200)), 'fila_excel': numero,
                       'precio_minimo': minimo, 'precio_maximo': maximo})
    return tareas

