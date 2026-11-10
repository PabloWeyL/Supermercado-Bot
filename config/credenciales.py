import os
import re


# Los nombres internos se conservan para no acoplar el framework HTTP a una empresa/VM.
CAMPOS = {
    'BACKEND_EMAIL': ('AUTOMATE', 'USER'),
    'BACKEND_PASSWORD': ('AUTOMATE', 'PWD'),
    'BACKEND_BOT_API_KEY': ('AUTOMATE', 'API_KEY'),
    'EMAIL_REMITENTE': ('SMTP', 'USER'),
    'EMAIL_PASSWORD': ('SMTP', 'PWD'),
    'MONITOR_USER': ('MONITOR', 'USER'),
    'MONITOR_PASSWORD': ('MONITOR', 'PWD'),
    'DB_USER': ('SQL', 'USER'),
    'DB_PASSWORD': ('SQL', 'PWD'),
}
IDENTIDAD = ('CREDENCIALES_EMPRESA', 'CREDENCIALES_VM')


def nombres_estandar(valores):
    empresa = str(valores.get('CREDENCIALES_EMPRESA') or '').strip().upper()
    vm = str(valores.get('CREDENCIALES_VM') or '').strip().upper()
    if not empresa and not vm:
        return {}
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*', empresa) or not re.fullmatch(r'MV[A-Z0-9]+', vm):
        raise ValueError('Configurar CREDENCIALES_EMPRESA y CREDENCIALES_VM con identificadores validos')
    prefijo = 'WEYLAND' if empresa == 'WEYLAND' else 'WEYLAND_' + empresa
    return {interno: f'{prefijo}_{sistema}_{vm}_{tipo}'
            for interno, (sistema, tipo) in CAMPOS.items()}


def normalizar_credenciales(valores):
    """Acepta nombres anteriores; alias contradictorios fallan sin exponer secretos."""
    datos = {}
    for nombre, valor in valores.items():
        clave = nombre.upper()
        if clave in datos and datos[clave] != valor and clave in CAMPOS:
            raise ValueError('Nombres duplicados de credencial: ' + clave)
        datos[clave] = valor
    for interno, nuevo in nombres_estandar(datos).items():
        if nuevo not in datos:
            continue
        anterior, actual = datos.get(interno), datos.pop(nuevo)
        if anterior not in (None, '') and actual not in (None, '') and anterior != actual:
            raise ValueError(f'Credenciales incompatibles entre {interno} y {nuevo}')
        datos[interno] = actual if actual not in (None, '') else anterior
    return datos


def combinar_configuracion(archivo, entorno):
    """La inyeccion por entorno prevalece, tambien al cambiar de nombre de variable."""
    archivo = {k.upper(): v for k, v in archivo.items()}
    entorno = {k.upper(): v for k, v in entorno.items()}
    identidad = {k: entorno.get(k, archivo.get(k)) for k in IDENTIDAD}
    return {**normalizar_credenciales({**archivo, **identidad}),
            **normalizar_credenciales({**entorno, **identidad})}


def leer_credencial(nombre):
    """Retorna el valor literal; los passwords nunca se recortan."""
    return normalizar_credenciales(os.environ).get(nombre.upper())
