"""Validacion del input y filtros comunes a los supermercados."""

import math
import re
import unicodedata


def normalizar_marca(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", texto))


def leer_precio(valor, columna, fila):
    if valor is None or str(valor).strip() == "":
        return None
    # En Excel se prefieren celdas numericas; como texto se acepta formato CLP.
    texto = str(valor).strip().replace("$", "").replace(" ", "")
    if isinstance(valor, str):
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", texto):
            texto = texto.replace(".", "")
        texto = texto.replace(",", ".")
    try:
        precio = float(texto)
    except (ValueError, TypeError):
        precio = float("nan")
    if isinstance(valor, bool) or not math.isfinite(precio) or precio < 0:
        raise ValueError(f"Fila {fila}: {columna} debe ser un numero no negativo en pesos chilenos")
    return precio


def cumple_filtros(item, precio, marca=None, precio_minimo=None, precio_maximo=None):
    return motivo_descarte(item, precio, marca, precio_minimo, precio_maximo) is None


def motivo_descarte(item, precio, marca=None, precio_minimo=None, precio_maximo=None):
    if precio_minimo is not None and precio < precio_minimo:
        return "precio_bajo_minimo"
    if precio_maximo is not None and precio > precio_maximo:
        return "precio_sobre_maximo"
    marca = normalizar_marca(marca)
    if marca:
        # Algunos sitios incluyen la marca en el nombre; otros tienen campo propio.
        texto = normalizar_marca(item.get("marca")) or normalizar_marca(item.get("nombre"))
        if " " + normalizar_marca(marca) + " " not in " " + texto + " ":
            return "marca_no_coincide" if normalizar_marca(item.get("marca")) else "marca_no_verificable_en_nombre"
    return None
