import json
import unicodedata

from config.configuracion import ARCHIVO_SUPERMERCADOS


def cargar_supermercados():
    with open(ARCHIVO_SUPERMERCADOS, encoding="utf-8") as archivo:
        return json.load(archivo)


def _normalizar_nombre(valor):
    texto = unicodedata.normalize("NFKD", str(valor).strip().casefold())
    return "".join(caracter for caracter in texto if not unicodedata.combining(caracter))


def seleccionar_supermercados(valor, supermercados):
    """Devuelve claves habilitadas; un nombre incorrecto nunca significa todos."""
    nombre = _normalizar_nombre(valor) if valor is not None else ""
    if not nombre:
        return [clave for clave, datos in supermercados.items() if datos.get("habilitado", True)]

    for clave, datos in supermercados.items():
        if nombre in (_normalizar_nombre(clave), _normalizar_nombre(datos.get("nombre", clave))):
            if not datos.get("habilitado", True):
                raise ValueError("Supermercado deshabilitado: " + str(valor).strip())
            return [clave]
    raise ValueError("Supermercado desconocido: " + str(valor).strip())
