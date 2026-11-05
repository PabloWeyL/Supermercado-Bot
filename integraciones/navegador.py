import time
from pathlib import Path

from config.configuracion import CARPETA_CAPTURAS, RPA_HEADLESS, RPA_TIMEOUT, RPA_CHROME_PUERTO, RUTA_TEMPORALES, registrar
from integraciones.chrome import ChromeRobot

r = ChromeRobot(RUTA_TEMPORALES, puerto=RPA_CHROME_PUERTO, timeout=RPA_TIMEOUT, headless=RPA_HEADLESS)


def evaluar_js(cuerpo, reintentos=3, pausa=1.0):
    for intento in range(reintentos):
        try:
            salida = r.dom(cuerpo)
            if salida is not None and str(salida).strip() != "":
                return str(salida).strip()
        except Exception as error:
            registrar("JS intento " + str(intento + 1) + ": " + str(error))
        time.sleep(pausa)
    return ""


def abrir_navegador():
    reutilizada = r.abrir()
    registrar("Chrome: " + ("sesion reutilizada" if reutilizada else "sesion nueva")
              + ("; modo sin interfaz" if RPA_HEADLESS else "; ventana maximizada"), "NAVEGADOR")


def captura_error(nombre_archivo):
    try:
        Path(CARPETA_CAPTURAS).mkdir(parents=True, exist_ok=True)
        destino = Path(CARPETA_CAPTURAS) / (nombre_archivo + ".png")
        time.sleep(3)
        r.snap("page",str(destino))
        return str(destino)
    except Exception as error:
        registrar("No se pudo guardar la captura: " + str(error))
        return None
