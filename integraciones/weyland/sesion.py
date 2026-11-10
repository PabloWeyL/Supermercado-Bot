"""Transporte HTTP y ciclo de sesion Django/CSRF; sin logica de bots."""
import threading
from urllib.parse import urlsplit

import requests

from .errores import ErrorWeyland, ErrorRedWeyland, ErrorHTTPWeyland

def leer_json(respuesta):
    try:
        datos = respuesta.json()
    except ValueError:
        raise ErrorWeyland('Weyland no devolvio un JSON valido') from None
    if not isinstance(datos, dict):
        raise ErrorWeyland('Respuesta de Weyland incompatible')
    return datos

def exigir_estado(respuesta, codigo=200):
    if respuesta.status_code != codigo:
        raise ErrorHTTPWeyland(respuesta.status_code)


class SesionWeyland:
    def __init__(self, config, session=None):
        self.config = config
        self._session = session if session is not None else requests.Session()
        if session is None:
            self._session.trust_env = False
        self._csrf = None
        self._autenticado = False
        self._cerrado = False
        self._lock = threading.RLock()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()

    def cerrar(self):
        self._autenticado = False
        self._csrf = None
        self._cerrado = True
        self._session.cookies.clear()
        self._session.close()

    def _enviar(self, metodo, ruta, *, login=False, **kwargs):
        try:
            return self._session.request(metodo,
                self.config.login_url if login else self.config.url.rstrip('/') + ruta,
                timeout=(self.config.timeout_conexion, self.config.timeout_lectura), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise ErrorRedWeyland('No se pudo comunicar con Weyland; no repetir una operacion incierta') from None

    def _obtener_csrf(self):
        with self._enviar('GET', '/auth/csrf') as respuesta:
            exigir_estado(respuesta)
            token = leer_json(respuesta).get('csrf')
            if not isinstance(token, str) or not token or any(c.isspace() for c in token):
                raise ErrorWeyland('Respuesta CSRF invalida')
            self._csrf = token

    def _login(self):
        self._autenticado = False
        self._session.cookies.clear()
        self._obtener_csrf()
        with self._enviar('POST', '', login=True,
                json={'correo': self.config.correo, 'password': self.config.password},
                headers={'X-CSRFToken': self._csrf, 'Referer': self.config.login_url}) as respuesta:
            exigir_estado(respuesta)
            perfil = leer_json(respuesta)
            usuario = perfil.get('usuario')
            if (not isinstance(usuario, dict) or str(usuario.get('correo', '')).casefold()
                    != self.config.correo.casefold()):
                raise ErrorWeyland('La identidad recibida no coincide con el correo configurado')
        # Django rota el CSRF al hacer login. Obtener el nuevo antes de otro POST.
        self._obtener_csrf()
        self._autenticado = True

    def _perfil(self):
        if self._cerrado:
            raise ErrorWeyland('Cliente cerrado')
        if not self._autenticado:
            self._login()
        for intento in range(2):
            with self._enviar('GET', '/auth/perfil') as respuesta:
                if respuesta.status_code == 401 and intento == 0:
                    self._login()
                    continue
                if respuesta.status_code != 200:
                    self._autenticado = False
                    exigir_estado(respuesta)
                datos = leer_json(respuesta)
                usuario = datos.get('usuario')
                if (not isinstance(usuario, dict) or
                        str(usuario.get('correo', '')).casefold() != self.config.correo.casefold()):
                    raise ErrorWeyland('Perfil de sesion incompatible')
                return datos

    def verificar(self):
        with self._lock:
            self._perfil()

    def solicitar(self, metodo, ruta, *, datos=None, archivos=None, params=None, stream=False,
                  formulario=None, empresa_id=None, clave_idempotencia=None):
        partes = urlsplit(ruta)
        if (not ruta.startswith('/') or ruta.startswith('//') or partes.scheme or partes.netloc
                or partes.query or partes.fragment or '\\' in ruta
                or any(c.isspace() for c in ruta) or any(p in ('.', '..') for p in ruta.split('/'))):
            raise ValueError('Ruta relativa de API invalida')
        metodo = metodo.upper()
        if clave_idempotencia is not None and (
                not isinstance(clave_idempotencia, str) or not clave_idempotencia
                or len(clave_idempotencia) > 128
                or any(c.isspace() for c in clave_idempotencia)):
            raise ValueError('Clave de idempotencia invalida')
        if (metodo not in ('GET', 'POST') or (datos is not None and archivos is not None)
                or (formulario is not None and (datos is not None or archivos is None))):
            raise ValueError('Metodo o cuerpo no admitidos por el contrato Weyland')
        if empresa_id is not None and (type(empresa_id) is not int or empresa_id < 1 or ruta.startswith('/bot/')):
            raise ValueError('Contexto de empresa invalido')
        posiciones = []
        for campo in (archivos or {}).values():
            contenido = campo[1] if isinstance(campo, (tuple, list)) else campo
            if hasattr(contenido, 'read'):
                posiciones.append((contenido, contenido.tell()))
        with self._lock:
            # El API key no caduca con la cookie. Verificar la sesion humana antes
            # de cada operacion evita continuar con una sesion vencida sin detectarlo.
            self._perfil()
            bot = ruta.startswith('/bot/')
            if bot and not self.config.api_key:
                raise ErrorWeyland('Falta BACKEND_BOT_API_KEY para la API del bot')
            for intento in range(2):
                headers = {'X-Api-Key': self.config.api_key} if bot else {}
                if empresa_id is not None:
                    headers['X-Empresa-Id'] = str(empresa_id)
                if metodo != 'GET':
                    headers.update({'X-CSRFToken': self._csrf, 'Referer': self.config.login_url})
                if clave_idempotencia is not None:
                    headers['Idempotency-Key'] = clave_idempotencia
                for archivo, posicion in posiciones:
                    archivo.seek(posicion)
                cuerpo = {'files': archivos, 'data': formulario} if archivos is not None else {'json': datos}
                respuesta = self._enviar(metodo, ruta, headers=headers, params=params, stream=stream, **cuerpo)
                if 200 <= respuesta.status_code < 300:
                    return respuesta
                codigo = respuesta.status_code
                error = None
                if codigo in (401, 403):
                    try:
                        error = respuesta.json().get('error', {}).get('codigo')
                    except (ValueError, AttributeError):
                        pass
                respuesta.close()
                if intento == 0 and codigo == 401 and (not bot or error == 'SESION_EXPIRADA'):
                    self._login()
                    continue
                if intento == 0 and codigo == 403 and error == 'CSRF_INVALIDO':
                    self._obtener_csrf()
                    continue
                # Una clave invalida no se corrige renovando la sesion.
                raise ErrorHTTPWeyland(codigo)

