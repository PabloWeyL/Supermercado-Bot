"""Puente comun para CLI; la lectura de .env y secretos pertenece al proyecto."""
import hashlib
from pathlib import Path

from .errores import ErrorWeyland
from .worker import ejecutar, verificar_anteriores
from .cliente import id_positivo
from .configuracion import obligatorio


def ejecutar_configurado(fabrica, valores, raiz, identificador, runner, *,
                         requiere_input=None, extensiones=('.xlsx',), contexto='', notificar=None):
    id_positivo(identificador)
    if str(valores.get('BACKEND_AUTH_MODE') or 'weyland') != 'weyland':
        raise ErrorWeyland('web-procesar requiere BACKEND_AUTH_MODE=weyland')
    if str(valores.get('BACKEND_REUTILIZAR_ULTIMA') or 'false').strip().lower() != 'false':
        raise ValueError('No se permite reutilizar solicitudes automaticamente')
    codigo = obligatorio(valores, 'BACKEND_PROCESO_CODIGO')
    raiz = Path(raiz).resolve()
    base = Path(valores.get('BACKEND_TRABAJOS_DIR') or 'temporales/weyland')
    base = (base if base.is_absolute() else raiz / base).resolve()
    if str(valores.get('ENTORNO') or '').upper() == 'PRODUCCION' and base.is_relative_to(raiz):
        raise ValueError('En produccion BACKEND_TRABAJOS_DIR debe estar fuera del repositorio')
    verificar_anteriores(base, identificador)
    clave = hashlib.sha256(codigo.encode()).hexdigest()[:16]
    with fabrica() as cliente:
        if not cliente.config.api_key:
            raise ErrorWeyland('Falta BACKEND_BOT_API_KEY para ejecutar solicitudes')
        cliente.verificar()
        identidad = hashlib.sha256(cliente.config.correo.casefold().encode()).hexdigest()
        return ejecutar(cliente, identificador, codigo, base / ('v2_' + clave), runner,
            requiere_input=requiere_input, extensiones=extensiones, notificar=notificar,
            contexto=contexto + ':' + identidad)
