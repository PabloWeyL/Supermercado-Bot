"""Cliente del backend vigente: cola por VM, input opcional y reporte multipart.

Solo depende del paquete portable. No lee .env ni conoce la lógica de un bot.
"""
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import requests

from .archivos import reemplazar_atomico
from .cliente import ClienteWeyland, MAX_BYTES, id_positivo, hash_valido
from .configuracion import configuracion_desde_valores
from .errores import ErrorWeyland, ErrorRedWeyland
from .sesion import exigir_estado, leer_json


def entero_configurado(valores, nombre, defecto=None):
    valor = valores.get(nombre, defecto)
    if isinstance(valor, bool) or not re.fullmatch(r'[0-9]+', str(valor or '')) or int(valor) < 1:
        raise ValueError(f'{nombre} debe ser un entero positivo configurado')
    return int(valor)


def leer_notificar_correo(configuracion):
    """Booleano web opcional. None conserva el interruptor local del bot.

    Se extrae solo esta bandera: nunca persistir el objeto de configuracion,
    que puede incluir datos privados propios de la empresa.
    """
    if configuracion is None:
        return None
    if not isinstance(configuracion, dict):
        raise ErrorWeyland('La configuracion del proceso debe ser un objeto o null')
    if 'notificar_correo' not in configuracion:
        return None
    valor = configuracion['notificar_correo']
    if type(valor) is not bool:
        raise ErrorWeyland('configuracion.notificar_correo debe ser true o false JSON')
    return valor


def validar_datos_publicables(datos, profundidad=0):
    """Evita persistir credenciales accidentales en parámetros o manifiestos."""
    if profundidad > 32:
        raise ErrorWeyland('Datos demasiado anidados')
    prohibidos = {'password', 'pwd', 'secret', 'api_key', 'access_token', 'refresh_token',
                  'authorization', 'cookie', 'cookies', 'token', 'contraseña', 'clave'}
    if isinstance(datos, dict):
        for clave, valor in datos.items():
            if str(clave).casefold() in prohibidos:
                raise ErrorWeyland('El sobre contiene campos de credenciales; usar el gestor de secretos')
            validar_datos_publicables(valor, profundidad + 1)
    elif isinstance(datos, list):
        for valor in datos:
            validar_datos_publicables(valor, profundidad + 1)
    elif isinstance(datos, str) and datos.lstrip().startswith(('{', '[')):
        try:
            interpretado = json.loads(datos)
        except ValueError:
            return
        validar_datos_publicables(interpretado, profundidad + 1)


class ClienteColaWeyland(ClienteWeyland):
    """No reutiliza los POST por ID ni presupone idempotencia del servidor."""

    def __init__(self, config, session=None, *, max_input_bytes=MAX_BYTES, empresa_id=None):
        super().__init__(config, session)
        self.max_input_bytes = entero_configurado({'limite': max_input_bytes}, 'limite')
        self.empresa_id = id_positivo(empresa_id) if empresa_id is not None else None

    def tomar_siguiente(self, maquina_id, id_externo):
        id_positivo(maquina_id)
        if not isinstance(id_externo, str) or not re.fullmatch(r'[a-f0-9]{32}', id_externo):
            raise ValueError('id_externo debe ser la correlacion local persistida')
        with self.solicitar('POST', '/bot/ejecuciones/siguiente',
                datos={'maquina_id': maquina_id, 'id_externo': id_externo}) as respuesta:
            if respuesta.status_code == 204:
                return None
            exigir_estado(respuesta)
            return leer_json(respuesta)

    @staticmethod
    def validar_asignacion(datos, maquina_id, id_externo, codigos):
        if (not isinstance(datos, dict) or type(datos.get('ejecucion_id')) is not int
                or datos['ejecucion_id'] < 1 or datos.get('id_externo') != id_externo
                or not isinstance(datos.get('maquina'), dict)
                or type(datos['maquina'].get('id')) is not int or datos['maquina']['id'] != maquina_id
                or not isinstance(datos.get('proceso'), dict) or datos['proceso'].get('codigo') not in codigos
                or not isinstance(datos.get('parametros'), dict) or 'input' not in datos):
            raise ErrorWeyland('Asignacion incompatible con VM, correlacion, proceso o parametros')
        entrada = datos['input']
        if entrada is not None:
            nombre = entrada.get('nombre') if isinstance(entrada, dict) else None
            if (not isinstance(nombre, str) or not nombre or len(nombre) > 255
                    or any(c in nombre for c in '/\\:\x00\r\n')
                    or entrada.get('descarga') != f'/api/bot/ejecuciones/{datos["ejecucion_id"]}/input'):
                raise ErrorWeyland('Metadatos de input incompatibles')
        validar_datos_publicables(datos['parametros'])
        if len(json.dumps(datos['parametros'], allow_nan=False).encode()) > MAX_BYTES:
            raise ErrorWeyland('Parametros demasiado grandes')
        # No guardar configuracion arbitraria ni la URL firmada de Blob Storage.
        return {'ejecucion_id': datos['ejecucion_id'], 'id_externo': id_externo,
                'maquina_id': maquina_id, 'proceso_codigo': datos['proceso']['codigo'],
                'parametros': datos['parametros'],
                'notificar_correo': leer_notificar_correo(datos.get('configuracion')),
                'input': {'nombre': entrada['nombre'], 'descarga': entrada['descarga']} if entrada else None}

    def _descargar(self, ruta, destino, limite, *, usuario=False):
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = None
        try:
            with self.solicitar('GET', ruta, stream=True,
                    empresa_id=self.empresa_id if usuario else None) as respuesta:
                exigir_estado(respuesta)
                cabecera = respuesta.headers.get('Content-Length')
                esperado = None
                if cabecera is not None and not respuesta.headers.get('Content-Encoding'):
                    if not str(cabecera).isdigit() or not 1 <= int(cabecera) <= limite:
                        raise ErrorWeyland('Tamano de descarga incompatible')
                    esperado = int(cabecera)
                total, digest = 0, hashlib.sha256()
                with NamedTemporaryFile(dir=destino.parent, suffix='.part', delete=False) as archivo:
                    parcial = Path(archivo.name)
                    for bloque in respuesta.iter_content(65536):
                        total += len(bloque)
                        if total > limite:
                            raise ErrorWeyland('La descarga supera el limite configurado')
                        digest.update(bloque)
                        archivo.write(bloque)
                    archivo.flush()
                    os.fsync(archivo.fileno())
                if total < 1 or esperado is not None and total != esperado:
                    raise ErrorRedWeyland('Descarga incompleta; conservar el mismo ID')
            reemplazar_atomico(parcial, destino)
            return {'size_bytes': total, 'sha256': digest.hexdigest()}
        except requests.RequestException:
            raise ErrorRedWeyland('Descarga interrumpida; no ejecutar un parcial') from None
        finally:
            if parcial is not None:
                parcial.unlink(missing_ok=True)

    def descargar_input(self, identificador, destino):
        id_positivo(identificador)
        return self._descargar(f'/bot/ejecuciones/{identificador}/input', destino, self.max_input_bytes)

    def entregar_resultado(self, identificador, reporte, huella, tamano):
        id_positivo(identificador)
        if not hash_valido(huella) or type(tamano) is not int or not 1 <= tamano <= MAX_BYTES:
            raise ErrorWeyland('Faltan las huellas locales de la entrega')
        reporte = Path(reporte)
        mime = 'application/json' if reporte.suffix.lower() == '.json' else 'application/zip'
        with reporte.open('rb') as archivo:
            if os.fstat(archivo.fileno()).st_size != tamano or hashlib.file_digest(archivo, 'sha256').hexdigest() != huella:
                raise ErrorWeyland('La entrega cambio; no se enviara')
            archivo.seek(0)
            with self.solicitar('POST', f'/bot/ejecuciones/{identificador}/reporte',
                    archivos={'archivo': (reporte.name, archivo, mime)}) as respuesta:
                exigir_estado(respuesta)
                confirmado = leer_json(respuesta)
        self.validar_confirmacion(confirmado, identificador, 'COMPLETADA')

    @staticmethod
    def validar_confirmacion(datos, identificador, estado):
        if (not isinstance(datos, dict) or type(datos.get('ejecucion_id')) is not int
                or datos['ejecucion_id'] != identificador or datos.get('estado') != estado):
            raise ErrorWeyland('Confirmacion incompatible; conservar el checkpoint y conciliar')

    def informar_error(self, identificador, mensaje):
        id_positivo(identificador)
        if not isinstance(mensaje, str) or not mensaje.strip() or len(mensaje) > 4000:
            raise ValueError('Mensaje de error invalido')
        with self.solicitar('POST', f'/bot/ejecuciones/{identificador}/error', datos={'mensaje': mensaje}) as respuesta:
            exigir_estado(respuesta)
            self.validar_confirmacion(leer_json(respuesta), identificador, 'ERROR')

    def consultar_ejecucion(self, identificador):
        id_positivo(identificador)
        with self.solicitar('GET', f'/ejecuciones/{identificador}', empresa_id=self.empresa_id) as respuesta:
            exigir_estado(respuesta)
            datos = leer_json(respuesta)
        if type(datos.get('id')) is not int or datos['id'] != identificador:
            raise ErrorWeyland('Detalle de ejecucion incompatible')
        return datos

    def buscar_correlacion(self, maquina_id, id_externo, limite_paginas=100):
        encontrados = []
        for pagina in range(1, limite_paginas + 1):
            with self.solicitar('GET', '/ejecuciones', params={'maquina_id': maquina_id, 'page': pagina},
                    empresa_id=self.empresa_id) as respuesta:
                exigir_estado(respuesta)
                datos = leer_json(respuesta)
            if not isinstance(datos.get('results'), list) or 'next' not in datos:
                raise ErrorWeyland('Listado de conciliacion incompatible')
            encontrados.extend(d for d in datos['results'] if isinstance(d, dict) and d.get('id_externo') == id_externo)
            if datos['next'] is None:
                if len(encontrados) != 1:
                    raise ErrorWeyland('Toma incierta sin coincidencia unica; requiere revision manual')
                identificador = id_positivo(encontrados[0]['id'])
                return self.consultar_ejecucion(identificador)
            if not isinstance(datos['next'], str) or not datos['next']:
                raise ErrorWeyland('Paginacion de conciliacion incompatible')
            # No seguir URLs de next; solo consultar el siguiente numero en el mismo origen.
        raise ErrorWeyland('Conciliacion incompleta por limite de paginas; requiere revision manual')

    def descargar_reporte_confirmado(self, identificador, archivo_id, destino):
        id_positivo(identificador)
        id_positivo(archivo_id)
        return self._descargar(f'/ejecuciones/{identificador}/archivos/{archivo_id}', destino,
                               MAX_BYTES, usuario=True)


def crear_cliente_cola(valores, credenciales=None, session=None):
    empresa_id = entero_configurado(valores, 'BACKEND_EMPRESA_ID') if valores.get('BACKEND_EMPRESA_ID') else None
    return ClienteColaWeyland(configuracion_desde_valores(valores, credenciales), session,
        max_input_bytes=entero_configurado(valores, 'MAX_ARCHIVO', MAX_BYTES), empresa_id=empresa_id)
