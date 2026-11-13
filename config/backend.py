"""Cliente HTTP del contrato de cola y fábrica de conexión del bot."""

import hashlib
import json
import os
import re
import requests
from config import autenticacion
from config.archivos import (
    ErrorWeyland,
    RUTA_ARCHIVO_EJECUCION,
    RUTA_BOT_ERROR,
    RUTA_BOT_ESTADO,
    RUTA_BOT_INPUT,
    RUTA_BOT_INPUT_API,
    RUTA_BOT_REPORTE,
    RUTA_CATALOGO_ESTADOS,
    RUTA_DETALLE_EJECUCION,
    RUTA_EJECUCIONES,
    RUTA_SIGUIENTE,
    reemplazar_atomico,
)
from config.autenticacion import (
    configuracion_desde_valores,
    exigir_estado,
    leer_json,
)
from pathlib import Path
from tempfile import NamedTemporaryFile


#----------------------------------------------
# FUNCIONES DE VALIDACIÓN DEL CONTRATO
#----------------------------------------------
def id_positivo(valor):
    """Exige un identificador entero positivo y rechaza booleanos u otros tipos."""
    if type(valor) is not int or valor < 1:
        raise ValueError('Se requiere un ID entero positivo')
    return valor


def hash_valido(valor):
    """Comprueba que una huella tenga el formato hexadecimal SHA-256 esperado."""
    return isinstance(valor, str) and re.fullmatch(r'[a-f0-9]{64}', valor) is not None


from config.configuracion import BACKEND_MAX_BYTES as MAX_BYTES, BACKEND_MAX_PAGINAS, BACKEND_MAX_INPUTS


def entero_configurado(valores, nombre, defecto=None):
    """Lee un entero positivo del mapping y rechaza valores ausentes o inválidos."""
    valor = valores.get(nombre, defecto)
    if isinstance(valor, bool) or not re.fullmatch(r'[0-9]+', str(valor or '')) or int(valor) < 1:
        raise ValueError(f'{nombre} debe ser un entero positivo configurado')
    return int(valor)


#----------------------------------------------
# FUNCIONES DE POLÍTICA DE CORREOS Y DATOS PUBLICABLES
#----------------------------------------------
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


#----------------------------------------------
# CLIENTE DE LA COLA DEL BACKEND
#----------------------------------------------
class ClienteColaWeyland:
    """No reutiliza los POST por ID ni presupone idempotencia del servidor."""

    def __init__(self, config, session=None, *, max_input_bytes=MAX_BYTES, empresa_id=None):
        """Inicializa el transporte, el límite de descarga y el contexto opcional de empresa."""
        self.max_input_bytes = entero_configurado({'limite': max_input_bytes}, 'limite')
        self.empresa_id = id_positivo(empresa_id) if empresa_id is not None else None
        self.config = config
        self.sesion = autenticacion.crear_sesion(config, session=session)

    #----------------------------------------------
    # SESIÓN JWT Y MÉTODOS HTTP PÚBLICOS
    #----------------------------------------------
    def __enter__(self):
        """Entrega el cliente dentro de un bloque with."""
        return self

    def __exit__(self, *_):
        """Cierra la sesión al salir del contexto."""
        self.cerrar()

    def login(self):
        """Inicia o reutiliza JWT mediante el módulo funcional."""
        return autenticacion.login(self.sesion)

    def refrescar(self):
        """Renueva JWT sin exponer los tokens al negocio."""
        return autenticacion.refrescar(self.sesion)

    def cerrar(self):
        """Revoca y limpia la sesión HTTP y sus tokens."""
        return autenticacion.cerrar(self.sesion)

    def verificar(self):
        """Comprueba el acceso JWT sin tomar solicitudes."""
        return autenticacion.verificar(self.sesion)

    @property
    def cierre_remoto_confirmado(self):
        """Indica si el backend confirmó el cierre de esta sesión."""
        return self.sesion._weyland['cierre_remoto_confirmado']

    def solicitar(self, metodo, ruta, **opciones):
        """Delega validación, Bearer y renovación en el transporte funcional común."""
        return autenticacion.solicitar(self.sesion, metodo, ruta, **opciones)

    def get(self, ruta, *, params=None, **opciones):
        """Consulta una ruta relativa y devuelve la respuesta HTTP para cerrar con with."""
        return self.solicitar('GET', ruta, params=params, **opciones)

    def post(self, ruta, *, json=None, files=None, data=None, params=None, **opciones):
        """Envía JSON o archivos multipart mediante el transporte autenticado común."""
        return self.solicitar('POST', ruta, datos=json, archivos=files, formulario=data, params=params, **opciones)

    def put(self, ruta, *, json=None, files=None, data=None, params=None, **opciones):
        """Envía una sustitución a una ruta que el backend admita mediante PUT."""
        return self.solicitar('PUT', ruta, datos=json, archivos=files, formulario=data, params=params, **opciones)

    def patch(self, ruta, *, json=None, files=None, data=None, params=None, **opciones):
        """Envía una actualización parcial a una ruta habilitada para PATCH."""
        return self.solicitar('PATCH', ruta, datos=json, archivos=files, formulario=data, params=params, **opciones)

    #----------------------------------------------
    # FUNCIONES DE ESTADOS Y ASIGNACIÓN
    #----------------------------------------------
    def consultar_estados(self):
        """Consulta explícita del catálogo cuando el despliegue lo habilite.

        Devuelve el catálogo web (listas ejecucion y bot). No se invoca para
        tomar trabajo ni se guarda la respuesta completa en el checkpoint.
        """
        with self.get(RUTA_CATALOGO_ESTADOS, empresa_id=self.empresa_id) as respuesta:
            exigir_estado(respuesta)
            try:
                datos = respuesta.json()
            except ValueError:
                raise ErrorWeyland('El catalogo de estados no devolvio JSON valido') from None
        if not isinstance(datos, (dict, list)):
            raise ErrorWeyland('Catalogo de estados incompatible; revisar el contrato')
        return datos

    def tomar_siguiente(self, maquina_id, id_externo):
        """Toma como máximo una solicitud con la máquina y la correlación persistida."""
        id_positivo(maquina_id)
        if not isinstance(id_externo, str) or not re.fullmatch(r'[a-f0-9]{32}', id_externo):
            raise ValueError('id_externo debe ser la correlacion local persistida')
        with self.post(RUTA_SIGUIENTE,
                json={'maquina_id': maquina_id, 'id_externo': id_externo,
                      'soporta_multiples_inputs': True}) as respuesta:
            if respuesta.status_code == 204:
                return None
            exigir_estado(respuesta)
            return leer_json(respuesta)

    @staticmethod
    def validar_asignacion(datos, maquina_id, id_externo, codigos):
        """Valida la identidad y el input recibido y conserva únicamente los datos publicables necesarios."""
        if (not isinstance(datos, dict) or type(datos.get('ejecucion_id')) is not int
                or datos['ejecucion_id'] < 1 or datos.get('id_externo') != id_externo
                or not isinstance(datos.get('maquina'), dict)
                or type(datos['maquina'].get('id')) is not int or datos['maquina']['id'] != maquina_id
                or not isinstance(datos.get('proceso'), dict) or datos['proceso'].get('codigo') not in codigos
                or not isinstance(datos.get('parametros'), dict)
                or not any(k in datos for k in ('input', 'inputs'))):
            raise ErrorWeyland('Asignacion incompatible con VM, correlacion, proceso o parametros')
        multiples = 'inputs' in datos
        entradas = datos['inputs'] if multiples else ([datos['input']] if datos['input'] is not None else [])
        if not isinstance(entradas, list) or len(entradas) > BACKEND_MAX_INPUTS:
            raise ErrorWeyland('Lista de inputs invalida o superior al limite configurado')
        normalizadas, vistos = [], set()
        for entrada in entradas:
            nombre = entrada.get('nombre') if isinstance(entrada, dict) else None
            archivo_id = entrada.get('archivo_id') if isinstance(entrada, dict) else None
            ruta = RUTA_BOT_INPUT_API.format(identificador=datos['ejecucion_id'])
            if multiples:
                if type(archivo_id) is not int or archivo_id < 1 or archivo_id in vistos:
                    raise ErrorWeyland('ID de archivo de input invalido o repetido')
                vistos.add(archivo_id)
                ruta += '?archivo_id=' + str(archivo_id)
            if (not isinstance(nombre, str) or not nombre or len(nombre) > 255
                    or any(c in nombre for c in '/\\:\x00\r\n')
                    or entrada.get('descarga') != ruta):
                raise ErrorWeyland('Metadatos de input incompatibles')
            item = {'nombre': nombre, 'descarga': ruta}
            if multiples:
                item['archivo_id'] = archivo_id
            normalizadas.append(item)
        if multiples and not entradas and datos.get('input') is not None:
            raise ErrorWeyland('El input legacy contradice la lista vacia de inputs')
        validar_datos_publicables(datos['parametros'])
        if len(json.dumps(datos['parametros'], allow_nan=False).encode()) > MAX_BYTES:
            raise ErrorWeyland('Parametros demasiado grandes')
        # No guardar configuracion arbitraria ni la URL firmada de Blob Storage.
        reproceso = datos.get('reproceso_de')
        if reproceso is not None:
            id_positivo(reproceso)
        return {'ejecucion_id': datos['ejecucion_id'], 'id_externo': id_externo,
                'reproceso_de': reproceso,
                'maquina_id': maquina_id, 'proceso_codigo': datos['proceso']['codigo'],
                'parametros': datos['parametros'],
                'notificar_correo': leer_notificar_correo(datos.get('configuracion')),
                'input': normalizadas[0] if len(normalizadas) == 1 else None,
                'inputs': normalizadas}

    #----------------------------------------------
    # FUNCIONES DE DESCARGA DE INPUTS
    #----------------------------------------------
    def descargar(self, ruta, destino, limite, *, usuario=False, params=None):
        """Descarga en un archivo parcial, verifica su tamaño y lo publica mediante sustitución atómica."""
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = None
        try:
            with self.get(ruta, stream=True, params=params,
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
                    raise ErrorWeyland('Descarga incompleta; conservar el mismo ID', tipo='RED')
            reemplazar_atomico(parcial, destino)
            return {'size_bytes': total, 'sha256': digest.hexdigest()}
        except requests.RequestException:
            raise ErrorWeyland('Descarga interrumpida; no ejecutar un parcial', tipo='RED') from None
        finally:
            if parcial is not None:
                parcial.unlink(missing_ok=True)

    def descargar_input(self, identificador, destino, *, archivo_id=None):
        """Descarga un input concreto mediante su ID o el primero en el contrato legacy."""
        id_positivo(identificador)
        params = {'archivo_id': id_positivo(archivo_id)} if archivo_id is not None else None
        return self.descargar(RUTA_BOT_INPUT.format(identificador=identificador), destino,
                               self.max_input_bytes, params=params)

    #----------------------------------------------
    # FUNCIONES DE ENTREGA DE RESULTADOS
    #----------------------------------------------
    def entregar_resultado(self, identificador, reporte, huella, tamano, *, estado_bot=None, mensaje='', resumen=None):
        """Verifica el reporte, lo sube con su estado final y exige una confirmación compatible."""
        id_positivo(identificador)
        if not hash_valido(huella) or type(tamano) is not int or not 1 <= tamano <= MAX_BYTES:
            raise ErrorWeyland('Faltan las huellas locales de la entrega')
        reporte = Path(reporte)
        mime = 'application/json' if reporte.suffix.lower() == '.json' else 'application/zip'
        formulario = None
        if estado_bot is not None:
            from config.ejecuciones import validar_estado_bot
            if estado_bot not in ('FINALIZADO', 'FINALIZADO_CON_OBSERVACIONES'):
                raise ValueError('El reporte requiere un estado final exitoso')
            validar_estado_bot(estado_bot, mensaje, resumen)
            formulario = {'estado': estado_bot, 'mensaje': mensaje}
            if resumen is not None:
                formulario['resumen'] = json.dumps(resumen, allow_nan=False)
        with reporte.open('rb') as archivo:
            if os.fstat(archivo.fileno()).st_size != tamano or hashlib.file_digest(archivo, 'sha256').hexdigest() != huella:
                raise ErrorWeyland('La entrega cambio; no se enviara')
            archivo.seek(0)
            with self.post(RUTA_BOT_REPORTE.format(identificador=identificador),
                    files={'archivo': (reporte.name, archivo, mime)}, data=formulario) as respuesta:
                exigir_estado(respuesta)
                confirmado = leer_json(respuesta)
        self.validar_confirmacion(confirmado, identificador, 'COMPLETADA')
        if estado_bot is not None:
            from config.ejecuciones import validar_confirmacion_estado
            validar_confirmacion_estado(confirmado, identificador, estado_bot)

    #----------------------------------------------
    # FUNCIONES DE ACTUALIZACIÓN DE ESTADOS
    #----------------------------------------------
    def informar_estado(self, identificador, estado, mensaje='', resumen=None):
        """Valida y publica un estado del bot y comprueba el ID y el cierre confirmados."""
        from config.ejecuciones import validar_estado_bot, validar_confirmacion_estado
        id_positivo(identificador)
        validar_estado_bot(estado, mensaje, resumen)
        datos = {'estado': estado, 'mensaje': mensaje}
        if resumen is not None:
            datos['resumen'] = resumen
        with self.post(RUTA_BOT_ESTADO.format(identificador=identificador), json=datos) as respuesta:
            exigir_estado(respuesta)
            validar_confirmacion_estado(leer_json(respuesta), identificador, estado)

    @staticmethod
    def validar_confirmacion(datos, identificador, estado):
        """Comprueba que el backend confirme la ejecución y el estado de sistema esperados."""
        if (not isinstance(datos, dict) or type(datos.get('ejecucion_id')) is not int
                or datos['ejecucion_id'] != identificador or datos.get('estado') != estado):
            raise ErrorWeyland('Confirmacion incompatible; conservar el checkpoint y conciliar')

    def informar_error(self, identificador, mensaje):
        """Publica un fallo mediante el endpoint compatible y exige confirmación de ERROR."""
        id_positivo(identificador)
        if not isinstance(mensaje, str) or not mensaje.strip() or len(mensaje) > 4000:
            raise ValueError('Mensaje de error invalido')
        with self.post(RUTA_BOT_ERROR.format(identificador=identificador), json={'mensaje': mensaje}) as respuesta:
            exigir_estado(respuesta)
            self.validar_confirmacion(leer_json(respuesta), identificador, 'ERROR')

    #----------------------------------------------
    # FUNCIONES DE CONSULTA Y CONCILIACIÓN
    #----------------------------------------------
    def consultar_ejecucion(self, identificador):
        """Normaliza el código BOT del detalle web, sin usar su etiqueta visible."""
        id_positivo(identificador)
        with self.get(RUTA_DETALLE_EJECUCION.format(identificador=identificador), empresa_id=self.empresa_id) as respuesta:
            exigir_estado(respuesta)
            datos = leer_json(respuesta)
        if type(datos.get('id')) is not int or datos['id'] != identificador:
            raise ErrorWeyland('Detalle de ejecucion incompatible')
        estado_bot = datos.get('estado_bot')
        if isinstance(estado_bot, dict):
            codigo = estado_bot.get('codigo')
            if not isinstance(codigo, str) or not re.fullmatch(r'[A-Z0-9_]{2,30}', codigo):
                raise ErrorWeyland('El detalle no contiene un codigo de estado BOT valido')
            datos = {**datos, 'estado_bot': codigo}
        return datos

    def buscar_correlacion(self, maquina_id, id_externo, limite_paginas=BACKEND_MAX_PAGINAS, *, permitir_ausente=False):
        """Busca en todas las páginas; una ausencia no autoriza repetir una toma incierta.

        permitir_ausente se reserva a una conciliación explícita del operador.
        El coordinador conserva el modo estricto y no reintenta POST por ausencia.
        """
        id_positivo(maquina_id)
        if not isinstance(id_externo, str) or not re.fullmatch(r'[a-f0-9]{32}', id_externo):
            raise ValueError('Correlacion local invalida')
        if type(limite_paginas) is not int or limite_paginas < 1 or type(permitir_ausente) is not bool:
            raise ValueError('Opciones de conciliacion invalidas')
        encontrados = []
        for pagina in range(1, limite_paginas + 1):
            with self.get(RUTA_EJECUCIONES, params={'maquina_id': maquina_id, 'page': pagina},
                    empresa_id=self.empresa_id) as respuesta:
                exigir_estado(respuesta)
                datos = leer_json(respuesta)
            if isinstance(datos.get('data'), list):
                filas = datos['data']
                total, actual, tamano = (datos.get(k) for k in ('total', 'page', 'page_size'))
                if (type(total) is not int or total < 0 or type(actual) is not int or actual != pagina
                        or type(tamano) is not int or tamano < 1 or len(filas) > tamano
                        or not filas and pagina * tamano < total):
                    raise ErrorWeyland('Paginacion de conciliacion incompatible')
                final = pagina * tamano >= total
            elif isinstance(datos.get('results'), list) and 'next' in datos:
                filas = datos['results']
                siguiente = datos['next']
                if siguiente is not None and (not isinstance(siguiente, str) or not siguiente):
                    raise ErrorWeyland('Paginacion de conciliacion incompatible')
                final = siguiente is None
            else:
                raise ErrorWeyland('Listado de conciliacion incompatible')
            if any(not isinstance(fila, dict) for fila in filas):
                raise ErrorWeyland('Listado de conciliacion con filas incompatibles')
            encontrados.extend(d for d in filas if d.get('id_externo') == id_externo)
            if final:
                if not encontrados and permitir_ausente:
                    return None
                if len(encontrados) != 1:
                    raise ErrorWeyland('Toma incierta sin coincidencia unica; requiere revision manual')
                identificador = id_positivo(encontrados[0]['id'])
                return self.consultar_ejecucion(identificador)
            # No seguir URLs de next; solo consultar el siguiente numero en el mismo origen.
        raise ErrorWeyland('Conciliacion incompleta por limite de paginas; requiere revision manual')

    def descargar_reporte_confirmado(self, identificador, archivo_id, destino):
        """Solicita el reporte del detalle web para conciliar su tamaño y huella con la entrega local."""
        id_positivo(identificador)
        id_positivo(archivo_id)
        return self.descargar(RUTA_ARCHIVO_EJECUCION.format(identificador=identificador, archivo_id=archivo_id), destino,
                               MAX_BYTES, usuario=True)


#----------------------------------------------
# FUNCIONES DE CONFIGURACIÓN Y CREACIÓN DEL CLIENTE
#----------------------------------------------
def crear_cliente_cola(valores, credenciales=None, session=None):
    """Construye el cliente del contrato de cola con sus credenciales y límites de entrada."""
    empresa_id = entero_configurado(valores, 'BACKEND_EMPRESA_ID') if valores.get('BACKEND_EMPRESA_ID') else None
    return ClienteColaWeyland(configuracion_desde_valores(valores, credenciales), session,
        max_input_bytes=entero_configurado(valores, 'MAX_ARCHIVO', MAX_BYTES), empresa_id=empresa_id)


def leer_valores(archivo=None):
    """Combina el archivo privado con el entorno y normaliza los nombres de las credenciales."""
    from dotenv import dotenv_values
    from config.configuracion import combinar_configuracion
    from config.archivos import ARCHIVO_ENV
    archivo = Path(archivo) if archivo else ARCHIVO_ENV
    return combinar_configuracion(dotenv_values(archivo), os.environ)


def crear_cliente_api(archivo=None, session=None):
    """Exige el contrato y el modo JWT del bot antes de construir su cliente."""
    valores = leer_valores(archivo)
    if valores.get('BACKEND_CONTRATO', 'cola_actual') != 'cola_actual':
        raise ValueError('Este bot utiliza BACKEND_CONTRATO=cola_actual')
    if valores.get('BACKEND_AUTH_MODE', 'weyland') != 'weyland':
        raise ValueError('Este bot utiliza BACKEND_AUTH_MODE=weyland')
    if valores.get('BACKEND_BOT_AUTH_MODE', 'jwt') != 'jwt':
        raise ValueError('Este backend requiere BACKEND_BOT_AUTH_MODE=jwt')
    return crear_cliente_cola({**valores, 'BACKEND_BOT_AUTH_MODE': 'jwt'}, session=session)

