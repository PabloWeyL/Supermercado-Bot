"""Operaciones del contrato por ID; sin dependencias del bot o proveedor de secretos."""
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import requests

from .sesion import SesionWeyland, exigir_estado, leer_json
from .errores import ErrorWeyland, ErrorHTTPWeyland, ErrorRedWeyland
from .archivos import reemplazar_atomico

MAX_BYTES = 20 * 1024 * 1024


def id_positivo(valor):
    if type(valor) is not int or valor < 1:
        raise ValueError('Se requiere un ID entero positivo')
    return valor


def ids_coinciden(datos, identificador):
    return isinstance(datos, dict) and all(type(datos.get(campo)) is int
        and datos[campo] == identificador for campo in ('solicitud_id', 'ejecucion_id'))


def hash_valido(valor):
    return isinstance(valor, str) and re.fullmatch(r'[a-f0-9]{64}', valor) is not None


def validar_clave(clave):
    if not isinstance(clave, str) or not clave or len(clave) > 128 or any(c.isspace() for c in clave):
        raise ValueError('Se requiere una clave de idempotencia valida')


class ClienteWeyland(SesionWeyland):
    """API publica comun: verificar, consultar, validar, iniciar, descargar, entregar, fallar."""

    def consultar(self, identificador):
        id_positivo(identificador)
        try:
            with self.solicitar('GET', f'/bot/ejecuciones/{identificador}') as respuesta:
                exigir_estado(respuesta)
                return leer_json(respuesta)
        except ErrorHTTPWeyland as error:
            if error.status_code == 404:
                raise ErrorWeyland('Solicitud no visible o contrato por ID no habilitado. '
                                   'No se llamo a siguiente ni se inicio el bot.') from None
            raise

    @staticmethod
    def validar(datos, identificador, codigo, estado):
        id_positivo(identificador)
        if (not isinstance(codigo, str) or not codigo.strip() or not ids_coinciden(datos, identificador)
                or datos.get('estado') != estado or not isinstance(datos.get('proceso'), dict)
                or datos['proceso'].get('codigo') != codigo
                or type(datos.get('requiere_input')) is not bool
                or not isinstance(datos.get('parametros'), dict) or 'input' not in datos):
            raise ErrorWeyland('La solicitud no coincide con ID, proceso, estado o parametros')
        if datos['requiere_input']:
            info = datos['input']
            if (not isinstance(info, dict) or not isinstance(info.get('nombre'), str)
                    or not info['nombre'] or type(info.get('size_bytes')) is not int
                    or not 1 <= info['size_bytes'] <= MAX_BYTES or not hash_valido(info.get('sha256'))
                    or info.get('descarga') != f'/api/bot/ejecuciones/{identificador}/input'):
                raise ErrorWeyland('Input sin metadatos de integridad compatibles')
        elif datos['input'] is not None:
            raise ErrorWeyland('Un proceso sin input no puede recibir un archivo')

    def iniciar(self, identificador, codigo, clave):
        id_positivo(identificador)
        validar_clave(clave)
        with self.solicitar('POST', '/bot/ejecuciones/siguiente',
                datos={'solicitud_id': identificador, 'ejecucion_id': identificador},
                clave_idempotencia=clave) as respuesta:
            exigir_estado(respuesta)
            trabajo = leer_json(respuesta)
        self.validar(trabajo, identificador, codigo, 'EN_PROCESO')
        return trabajo

    def descargar(self, identificador, destino, info):
        """No sigue URLs del input: descarga del backend y verifica bytes antes de procesar."""
        id_positivo(identificador)
        if (not isinstance(info, dict) or not hash_valido(info.get('sha256'))
                or type(info.get('size_bytes')) is not int or not 1 <= info['size_bytes'] <= MAX_BYTES):
            raise ErrorWeyland('Falta tamano o hash valido del input')
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = None
        try:
            with self.solicitar('GET', f'/bot/ejecuciones/{identificador}/input', stream=True) as respuesta:
                exigir_estado(respuesta)
                cabecera = respuesta.headers.get('X-Content-SHA256')
                if cabecera is not None and cabecera != info['sha256']:
                    raise ErrorWeyland('Hash del encabezado de input incompatible')
                total, digest = 0, hashlib.sha256()
                with NamedTemporaryFile(dir=destino.parent, suffix='.part', delete=False) as archivo:
                    parcial = Path(archivo.name)
                    for bloque in respuesta.iter_content(65536):
                        total += len(bloque)
                        if total > info['size_bytes'] or total > MAX_BYTES:
                            raise ErrorWeyland('Input supera el tamano anunciado')
                        digest.update(bloque)
                        archivo.write(bloque)
                    archivo.flush()
                    os.fsync(archivo.fileno())
                if total != info['size_bytes'] or digest.hexdigest() != info['sha256']:
                    raise ErrorWeyland('El input no coincide con su tamano o SHA-256')
            reemplazar_atomico(parcial, destino)
        except requests.RequestException:
            raise ErrorRedWeyland('Descarga interrumpida') from None
        finally:
            if parcial is not None:
                parcial.unlink(missing_ok=True)

    def entregar(self, identificador, reporte, metadatos, clave):
        id_positivo(identificador)
        validar_clave(clave)
        if not ids_coinciden(metadatos, identificador) or metadatos.get('estado') != 'COMPLETADA':
            raise ErrorWeyland('Metadatos de reporte incompatibles')
        if reporte is None:
            if 'export' not in metadatos or metadatos['export'] is not None:
                raise ErrorWeyland('La finalizacion sin archivo debe declarar export=null')
            if len(json.dumps(metadatos, allow_nan=False).encode('utf-8')) > MAX_BYTES:
                raise ErrorWeyland('Resultado JSON supera el limite de entrega')
            with self.solicitar('POST', f'/bot/ejecuciones/{identificador}/reporte',
                    datos=metadatos, clave_idempotencia=clave) as respuesta:
                exigir_estado(respuesta)
                confirmado = leer_json(respuesta)
            if (not ids_coinciden(confirmado, identificador) or confirmado.get('estado') != 'COMPLETADA'
                    or 'reporte' not in confirmado or confirmado['reporte'] is not None):
                raise ErrorWeyland('Finalizacion JSON sin confirmacion compatible; no repetir el proceso')
            return
        reporte = Path(reporte)
        with reporte.open('rb') as archivo:
            digest = hashlib.file_digest(archivo, 'sha256').hexdigest()
            info = metadatos.get('export', {})
            tamano = os.fstat(archivo.fileno()).st_size
            if (info.get('sha256') != digest or info.get('size_bytes') != tamano
                    or not 1 <= tamano <= MAX_BYTES or info.get('nombre') != reporte.name):
                raise ErrorWeyland('El reporte no coincide con sus metadatos')
            archivo.seek(0)
            with self.solicitar('POST', f'/bot/ejecuciones/{identificador}/reporte',
                    archivos={'archivo': (reporte.name, archivo, 'application/zip')},
                    formulario={'metadatos': json.dumps(metadatos)}, clave_idempotencia=clave) as respuesta:
                exigir_estado(respuesta)
                confirmado = leer_json(respuesta)
        if (not ids_coinciden(confirmado, identificador) or confirmado.get('estado') != 'COMPLETADA'
                or not isinstance(confirmado.get('reporte'), dict)
                or confirmado['reporte'].get('sha256') != digest):
            raise ErrorWeyland('Entrega sin confirmacion compatible; no repetir el proceso')

    def fallar(self, identificador, datos, clave):
        id_positivo(identificador)
        validar_clave(clave)
        if not ids_coinciden(datos, identificador):
            raise ErrorWeyland('IDs de error incompatibles')
        with self.solicitar('POST', f'/bot/ejecuciones/{identificador}/error',
                            datos=datos, clave_idempotencia=clave) as respuesta:
            exigir_estado(respuesta)
            confirmado = leer_json(respuesta)
        if not ids_coinciden(confirmado, identificador) or confirmado.get('estado') != 'ERROR':
            raise ErrorWeyland('Fallo sin confirmacion compatible; revisar la web')

    # Nombres descriptivos para nuevos proyectos; usan exactamente las mismas implementaciones.
    consultar_solicitud = consultar
    iniciar_solicitud = iniciar
    descargar_input = descargar
    enviar_reporte = entregar
    enviar_error = fallar
