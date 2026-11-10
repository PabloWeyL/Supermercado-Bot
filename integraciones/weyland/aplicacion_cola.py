"""Adaptador de arranque configurable; no lee entorno, rutas o secretos implícitos."""
import json
from pathlib import Path

from .cola_actual import entero_configurado, ClienteColaWeyland
from .ejecutor_cola import ejecutar_cola
from .errores import ErrorWeyland


def verificar_migracion(base):
    """No ocultar trabajos del contrato anterior al estrenar un namespace."""
    for ruta in base.rglob('estado.json'):
        try:
            datos = json.loads(ruta.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise ErrorWeyland('Hay un journal anterior ilegible; revisar antes de migrar') from None
        if not isinstance(datos, dict) or datos.get('fase') not in ('COMPLETADA', 'ERROR'):
            raise ErrorWeyland('Hay una ejecucion pendiente del contrato anterior; revisar antes de migrar')
    for ruta in base.rglob('activo.json'):
        raise ErrorWeyland('Existe un journal heredado; revisar antes de habilitar cola_actual')


def ejecutar_cola_configurada(fabrica, valores, raiz, procesos, *, notificar=None):
    if valores.get('BACKEND_CONTRATO') != 'cola_actual' or valores.get('BACKEND_AUTH_MODE') != 'weyland':
        raise ValueError('Configurar BACKEND_CONTRATO=cola_actual y BACKEND_AUTH_MODE=weyland')
    if str(valores.get('BACKEND_REUTILIZAR_ULTIMA') or 'false').lower() != 'false':
        raise ValueError('No se reutilizan solicitudes anteriores')
    maquina = entero_configurado(valores, 'BACKEND_MAQUINA_ID')
    raiz = Path(raiz).resolve()
    ruta = Path(valores.get('BACKEND_TRABAJOS_DIR') or 'temporales/weyland')
    base = (ruta if ruta.is_absolute() else raiz / ruta).resolve()
    if str(valores.get('ENTORNO') or '').upper() == 'PRODUCCION' and base.is_relative_to(raiz):
        raise ValueError('En produccion BACKEND_TRABAJOS_DIR debe estar fuera del repositorio')
    verificar_migracion(base)
    with fabrica() as cliente:
        if not isinstance(cliente, ClienteColaWeyland):
            raise ErrorWeyland('La fabrica no corresponde al contrato cola_actual')
        if not cliente.config.api_key:
            raise ErrorWeyland('Falta la API key del bot; no se tomara trabajo')
        cliente.verificar()
        return ejecutar_cola(cliente, maquina, base / 'cola_actual' / ('maquina_' + str(maquina)),
                              procesos, notificar=notificar)
