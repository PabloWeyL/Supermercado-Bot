"""Entradas seguras, catálogo, navegador y automatización de supermercados."""

from config.archivos import EXTENSIONES_EXCEL
from config.base_datos import (
    actualizar_archivo,
    actualizar_cola,
    consultar_estado_cola,
    guardar_resultados,
    guardar_tareas_entrada,
    reencolar_errores,
    registrar_archivo,
    tareas_con_error,
    tareas_pendientes,
)
from config.configuracion import (
    CARPETA_CAPTURAS,
    EXCEL_ENTRADA,
    ErrorArranque,
    MAX_EXCEL_ENTRADAS_ZIP,
    MAX_PRODUCTOS_POR_BUSQUEDA,
    MAX_REINTENTOS,
    RPA_CAPTURA_PAUSA,
    RPA_CHROME_HOST,
    RPA_CHROME_PUERTO,
    RPA_CHROME_URL,
    RPA_CIERRE_TIMEOUT,
    RPA_DOM_REINTENTOS,
    RPA_ESCRITURA_PAUSA,
    RPA_ESPERA_MAX,
    RPA_HEADLESS,
    RPA_HTTP_TIMEOUT,
    RPA_JS_PAUSA,
    RPA_JS_REINTENTOS,
    RPA_RESULTADOS_INTERVALO,
    RPA_SONDEO_INTERVALO,
    RPA_TIMEOUT,
    RUTA_INPUT,
    RUTA_TEMPORALES,
    ahora,
    obtener_decimal_positivo,
    obtener_entero_positivo,
    obtener_logger,
    obtener_variable_obligatoria,
    registrar,
    slug,
)
from contextlib import closing, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from io import BytesIO
from openpyxl import (
    Workbook,
    load_workbook,
)
from pathlib import Path
from urllib.error import URLError
from urllib.parse import (
    quote,
    urlsplit,
)
from urllib.request import (
    Request,
    urlopen,
)
from xml.parsers import expat
from zipfile import (
    BadZipFile,
    ZIP_DEFLATED,
    ZIP_STORED,
    ZipFile,
)
from zlib import error as ErrorCompresion
import base64
import json
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata


# Catálogo de tiendas: las URLs y esperas se completan desde .env.
CATALOGO_SUPERMERCADOS = {'lider': {'nombre': 'Lider',
           'habilitado': True,
           'extractor': {'tipo': 'lider',
                         'marca': '[data-automation-id=product-brand], '
                                  '.mb1.mt2.b.f6.black.mr1.lh-copy',
                         'tarjeta': 'div[data-item-id]',
                         'boton_nombre': '[data-automation-id=add-to-cart]',
                         'prefijo_nombre': 'Agregar al carro - ',
                         'nombre_respaldo': 'span',
                         'precio': '[data-automation-id=product-price]',
                         'enlace': 'a'},
           'url_busqueda_env': 'SUPERMERCADO_LIDER_URL',
           'espera_env': 'SUPERMERCADO_LIDER_ESPERA'},
 'tottus': {'nombre': 'Tottus',
            'habilitado': True,
            'extractor': {'tipo': 'tottus',
                          'marca': '[id^="testId-pod-displayTitle-"], '
                                   '[data-pod="catalyst-pod-brand"], .pod-title',
                          'tarjeta': 'a[id^="testId-pod-"]',
                          'tarjeta_respaldo': '[data-pod="catalyst-pod"], .pod',
                          'nombre': 'b[id^="testId-pod-displaySubTitle-"]',
                          'nombre_respaldo': '[data-pod="catalyst-pod-title"], .pod-subTitle',
                          'imagen': 'img[alt]',
                          'enlace': 'a[href*="/product/"]',
                          'bloque_precios': '[id^="testId-pod-prices-"]',
                          'prefijo_tarjeta': 'testId-pod-',
                          'prefijo_precios': 'testId-pod-prices-',
                          'precio_publico': '[data-internet-price]',
                          'precio': 'span[class*="line-height-22"]',
                          'precio_principal': '.prices-main-price',
                          'precio_normal': '[data-normal-price], .prices-old-price'},
            'selector_busqueda': "//input[@id='testId-SearchBar-Input']",
            'selector_enviar_busqueda': "//input[@id='testId-SearchBar-Input']/../following-sibling::button",
            'url_inicio_env': 'SUPERMERCADO_TOTTUS_URL',
            'espera_env': 'SUPERMERCADO_TOTTUS_ESPERA',
            'espera_resultados_env': 'SUPERMERCADO_TOTTUS_ESPERA_RESULTADOS'},
 'unimarc': {'nombre': 'Unimarc',
             'habilitado': True,
             'extractor': {'tipo': 'unimarc',
                           'marca': '[class*="Shelf_brandText"]',
                           'tarjeta': 'section[id^="shelf__vertical--"], '
                                      'section[id^="shelf__horizontal--"]',
                           'nombre': '[class*="Shelf_nameProduct"]',
                           'enlace': 'a[href*="/product/"]',
                           'precio_oferta': '[id^="listPrice__offerPrice--discountprice-"]',
                           'precio_normal': '[id^="listPrice__offerPrice--listprice-"]'},
             'selector_busqueda': "//input[@id='search-header__input']",
             'url_inicio_env': 'SUPERMERCADO_UNIMARC_URL',
             'espera_env': 'SUPERMERCADO_UNIMARC_ESPERA',
             'espera_resultados_env': 'SUPERMERCADO_UNIMARC_ESPERA_RESULTADOS'}}


# Puente CDP integrado: Node.js lo ejecuta en memoria mediante -e.
PUENTE_CHROME_JS = r"""
// Puente JSON por stdin/stdout a Chrome. Requiere Node.js 22 o posterior.
const readline = require('node:readline');
const socket = new WebSocket(process.argv[1]);
const timeout = Number(process.argv[2]);
const pending = new Map();
const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
const connectionTimer = setTimeout(() => { emit({bridgeError: 'Timeout al conectar con Chrome'}); socket.close(); }, timeout);
socket.onopen = () => { clearTimeout(connectionTimer); emit({ready: true}); };
socket.onerror = () => { clearTimeout(connectionTimer); emit({bridgeError: 'No se pudo conectar con Chrome'}); };
socket.onmessage = event => {
  const result = JSON.parse(event.data);
  if (pending.has(result.id)) {
    clearTimeout(pending.get(result.id));
    pending.delete(result.id);
    emit(result);
  }
};
socket.onclose = () => {
  for (const [id, timer] of pending) { clearTimeout(timer); emit({id, error: {message: 'Chrome cerro la conexion'}}); }
  pending.clear();
  process.exit(0);
};
const input = readline.createInterface({input: process.stdin});
input.on('line', line => {
  try {
    const request = JSON.parse(line);
    const timer = setTimeout(() => {
      pending.delete(request.id);
      emit({id: request.id, error: {message: 'Timeout: ' + request.method}});
    }, timeout);
    pending.set(request.id, timer);
    socket.send(JSON.stringify(request));
  } catch (error) { emit({bridgeError: error.message}); }
});
input.on('close', () => socket.close());
"""


# Control de Chrome y capturas.
ARCHIVO_SESION_CHROME = 'sesion_chrome.json'


#----------------------------------------------
# CONTROL DEL NAVEGADOR CHROME
#----------------------------------------------
class ChromeRobot:
    def __init__(self, carpeta, puerto=None, timeout=RPA_TIMEOUT, headless=RPA_HEADLESS):
        """Inicializa el controlador de Chrome con las rutas y opciones del entorno."""
        self.carpeta = Path(carpeta)
        self.puerto = RPA_CHROME_PUERTO if puerto is None else puerto
        self.host = RPA_CHROME_HOST
        host_url = f'[{self.host}]' if ':' in self.host else self.host
        self.url_base = RPA_CHROME_URL if puerto is None else f'http://{host_url}:{self.puerto}'
        self.timeout = timeout
        self.headless = headless
        self._puente = None
        self._secuencia = 0
        self._target_id = None

    def consultar_http(self, ruta, metodo='GET'):
        """Consulta un endpoint local de Chrome y devuelve su respuesta JSON."""
        with urlopen(Request(self.url_base + ruta, method=metodo), timeout=RPA_HTTP_TIMEOUT) as respuesta:
            return json.load(respuesta)

    def paginas(self):
        """Obtiene las pestañas disponibles o indica que Chrome todavía no responde."""
        try:
            return self.consultar_http('/json/list')
        except (URLError, OSError):
            return None

    def elegir_pagina(self, paginas):
        """Recupera la pestaña de la sesión anterior o selecciona una página disponible."""
        pagina_id = None
        archivo = self.carpeta / ARCHIVO_SESION_CHROME
        if archivo.exists():
            try:
                pagina_id = json.loads(archivo.read_text(encoding='utf-8')).get('target_id')
            except (OSError, ValueError):
                pass
        return next((p for p in paginas if p.get('type') == 'page' and p['id'] == pagina_id),
                    next((p for p in paginas if p.get('type') == 'page'), None))

    def lanzar(self):
        """Localiza Chrome y lo inicia con el puerto de automatización y perfil del bot."""
        candidatos = [os.getenv('CHROME_PATH'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                      str(Path(os.getenv('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe'),
                      shutil.which('google-chrome'), shutil.which('chromium')]
        ejecutable = next((p for p in candidatos if p and Path(p).is_file()), None)
        if not ejecutable:
            raise RuntimeError('No se encontro Chrome; configura CHROME_PATH')
        self.carpeta.mkdir(parents=True, exist_ok=True)
        argumentos = [ejecutable, f'--remote-debugging-port={self.puerto}', f'--remote-debugging-address={self.host}',
                      '--user-data-dir=' + str((self.carpeta / 'chrome_robot').resolve()),
                      '--no-first-run', '--no-default-browser-check', '--start-maximized', 'about:blank']
        if self.headless:
            argumentos.append('--headless=new')
        subprocess.Popen(argumentos, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0,
                         start_new_session=os.name != 'nt')

    def abrir(self):
        """Conecta con Chrome, ejecuta el puente integrado y prepara una pestaña para automatizar."""
        if self._puente and self._puente.poll() is None:
            try:
                self.llamar('Runtime.evaluate', {'expression': '1'})
                self.maximizar()
                return True
            except RuntimeError:
                self.close()
        elif self._puente:
            self.close()
        nodo = shutil.which('node')
        if not nodo:
            raise RuntimeError('Se necesita Node.js 22 o posterior para conectar con Chrome')
        version = subprocess.run([nodo, '--version'], capture_output=True, text=True, check=True).stdout
        if int(version.lstrip('v').split('.')[0]) < 22:
            raise RuntimeError('Se necesita Node.js 22 o posterior para conectar con Chrome')
        paginas = self.paginas()
        reutilizada = paginas is not None
        if not reutilizada:
            self.lanzar()
            limite = time.monotonic() + self.timeout
            while paginas is None and time.monotonic() < limite:
                time.sleep(RPA_SONDEO_INTERVALO)
                paginas = self.paginas()
            if paginas is None:
                raise RuntimeError('Chrome no habilito su conexion de automatizacion')
        pagina = self.elegir_pagina(paginas)
        if pagina is None:
            pagina = self.consultar_http('/json/new?' + quote('about:blank', safe=''), 'PUT')
        self._target_id = pagina['id']
        argumentos = [nodo, '-e', PUENTE_CHROME_JS,
                      pagina['webSocketDebuggerUrl'], str(int(self.timeout * 1000))]
        self._puente = subprocess.Popen(argumentos, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            if not self.leer().get('ready'):
                raise RuntimeError('Chrome no confirmo la conexion')
            self.maximizar()
            self.carpeta.mkdir(parents=True, exist_ok=True)
            (self.carpeta / ARCHIVO_SESION_CHROME).write_text(json.dumps({'target_id': self._target_id}), encoding='utf-8')
        except Exception:
            self.close()
            raise
        return reutilizada

    def leer(self):
        """Lee una respuesta del puente CDP y detecta desconexiones o errores."""
        linea = self._puente.stdout.readline()
        if not linea:
            raise RuntimeError('Se perdio la conexion con Chrome')
        respuesta = json.loads(linea)
        if respuesta.get('bridgeError'):
            raise RuntimeError(respuesta['bridgeError'])
        return respuesta

    def llamar(self, metodo, parametros=None):
        """Envía un comando CDP a Chrome y devuelve el resultado correspondiente."""
        if not self._puente or self._puente.poll() is not None:
            raise RuntimeError('No hay sesion conectada con Chrome')
        self._secuencia += 1
        self._puente.stdin.write(json.dumps({'id': self._secuencia, 'method': metodo, 'params': parametros or {}}) + '\n')
        self._puente.stdin.flush()
        respuesta = self.leer()
        if respuesta.get('error'):
            raise RuntimeError('Chrome: ' + respuesta['error'].get('message', str(respuesta['error'])))
        return respuesta.get('result', {})

    def maximizar(self):
        """Maximiza y trae al frente la ventana cuando Chrome tiene interfaz visible."""
        if not self.headless:
            ventana = self.llamar('Browser.getWindowForTarget', {'targetId': self._target_id})
            self.llamar('Browser.setWindowBounds', {'windowId': ventana['windowId'], 'bounds': {'windowState': 'normal'}})
            self.llamar('Browser.setWindowBounds', {'windowId': ventana['windowId'], 'bounds': {'windowState': 'maximized'}})
            self.llamar('Page.bringToFront')

    def dom(self, cuerpo):
        """Ejecuta JavaScript en la página y devuelve su resultado serializable."""
        resultado = self.llamar('Runtime.evaluate', {'expression': '(function(){' + cuerpo + '})()',
                                                      'returnByValue': True, 'awaitPromise': True})
        if resultado.get('exceptionDetails'):
            raise RuntimeError('Error JavaScript: ' + resultado['exceptionDetails'].get('text', 'sin detalle'))
        return resultado.get('result', {}).get('value', '')

    @staticmethod
    def elemento(selector):
        """Construye una expresión de búsqueda de un elemento por CSS o XPath."""
        valor = json.dumps(selector)
        if selector.startswith(('/', '(')):
            return f'document.evaluate({valor},document,null,XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue'
        return 'document.querySelector(' + valor + ')'

    def present(self, selector):
        """Indica si el selector corresponde a un elemento presente en la página."""
        return bool(self.dom('return !!(' + self.elemento(selector) + ');'))

    def click(self, selector):
        """Enfoca y pulsa el elemento seleccionado cuando existe."""
        return bool(self.dom('var e=' + self.elemento(selector) + '; if(!e)return false; e.focus(); e.click(); return true;'))

    def type(self, selector, texto):
        """Introduce texto en el elemento y permite reemplazar su contenido."""
        limpiar = texto.startswith('[clear]')
        if limpiar:
            texto = texto[len('[clear]'):]
        if not self.dom('var e=' + self.elemento(selector) + '; if(!e)return false; e.focus();' +
                        ('e.select();' if limpiar else '') + 'return true;'):
            return False
        self.llamar('Input.insertText', {'text': texto})
        return True

    def send(self, instruccion):
        # Compatibilidad con el evento Enter que utiliza el buscador existente.
        """Convierte el evento de teclado admitido al comando CDP equivalente."""
        coincidencia = re.fullmatch(r"js chrome_step\('Input.dispatchKeyEvent', (.+)\);", instruccion)
        if not coincidencia:
            raise ValueError('Instruccion de navegador no soportada')
        self.llamar('Input.dispatchKeyEvent', json.loads(coincidencia.group(1)))
        return True

    def url(self, direccion=None):
        """Consulta la dirección actual o navega hacia la dirección recibida."""
        if direccion is None:
            return self.dom('return location.href;')
        respuesta = self.llamar('Page.navigate', {'url': direccion})
        if respuesta.get('errorText'):
            raise RuntimeError(respuesta['errorText'])
        return True

    def snap(self, objetivo, ruta):
        """Captura la página mediante CDP y guarda la imagen en la ruta indicada."""
        resultado = self.llamar('Page.captureScreenshot', {'format': 'png'})
        Path(ruta).write_bytes(base64.b64decode(resultado['data']))
        return True

    def close(self):
        """Desconecta el controlador y conserva Chrome para la siguiente ejecucion."""
        puente, self._puente = self._puente, None
        if puente:
            try:
                puente.stdin.close()
            except OSError:
                pass
            if puente.poll() is None:
                try:
                    puente.wait(timeout=RPA_CIERRE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    puente.terminate()
                    puente.wait(timeout=RPA_CIERRE_TIMEOUT)
            puente.stdout.close()
        return True


# Clave estable; no se construye un navegador ni se comparten capturas al importar.
SESION_AUTOMATIZACION = ContextVar('sesion_automatizacion', default=None)


#----------------------------------------------
# SESIÓN DE AUTOMATIZACIÓN POR EJECUCIÓN
#----------------------------------------------
class SesionAutomatizacion:
    def __init__(self, navegador=None):
        """Agrupa el controlador y las evidencias exclusivas de una automatización."""
        self._navegador = navegador
        self.capturas = []

    @property
    def navegador(self):
        """Crea Chrome de forma diferida y conserva el mismo controlador dentro de la sesión."""
        if self._navegador is None:
            self._navegador = ChromeRobot(RUTA_TEMPORALES, timeout=RPA_TIMEOUT, headless=RPA_HEADLESS)
        return self._navegador

    def cerrar(self):
        """Desconecta el controlador si existe, sin cerrar la ventana reutilizable de Chrome."""
        navegador, self._navegador = self._navegador, None
        if navegador is not None:
            try:
                navegador.close()
            except Exception:
                pass


def obtener_sesion():
    """Obtiene la sesión del contexto o crea una para el uso directo de las funciones."""
    sesion = SESION_AUTOMATIZACION.get()
    if sesion is None:
        sesion = SesionAutomatizacion()
        SESION_AUTOMATIZACION.set(sesion)
    return sesion


@contextmanager
def sesion_automatizacion(sesion=None):
    """Delimita navegador y evidencias, cierra recursos y restaura el contexto anterior."""
    actual = sesion if sesion is not None else SesionAutomatizacion()
    token = SESION_AUTOMATIZACION.set(actual)
    try:
        yield actual
    finally:
        try:
            actual.cerrar()
        finally:
            SESION_AUTOMATIZACION.reset(token)


#----------------------------------------------
# FUNCIONES DE JAVASCRIPT Y APERTURA DEL NAVEGADOR
#----------------------------------------------
def evaluar_js(cuerpo, reintentos=RPA_JS_REINTENTOS, pausa=RPA_JS_PAUSA):
    """Evalúa JavaScript con intentos y pausas limitados por la configuración."""
    for intento in range(reintentos):
        try:
            salida = obtener_sesion().navegador.dom(cuerpo)
            if salida is not None and str(salida).strip() != "":
                return str(salida).strip()
        except Exception as error:
            registrar("JS intento " + str(intento + 1) + ": " + str(error))
        time.sleep(pausa)
    return ""


def abrir_navegador():
    """Abre o reutiliza Chrome y registra el modo de conexión."""
    reutilizada = obtener_sesion().navegador.abrir()
    registrar("Chrome: " + ("sesion reutilizada" if reutilizada else "sesion nueva")
              + ("; modo sin interfaz" if RPA_HEADLESS else "; ventana maximizada"), "NAVEGADOR")


#----------------------------------------------
# FUNCIONES DE CAPTURA DE ERRORES
#----------------------------------------------
def captura_error(nombre_archivo):
    """Guarda una captura de evidencia y registra cualquier fallo de captura."""
    try:
        Path(CARPETA_CAPTURAS).mkdir(parents=True, exist_ok=True)
        destino = Path(CARPETA_CAPTURAS) / (nombre_archivo + ".png")
        time.sleep(RPA_CAPTURA_PAUSA)
        obtener_sesion().navegador.snap("page",str(destino))
        return str(destino)
    except Exception as error:
        registrar("No se pudo guardar la captura: " + str(error))
        return None


# Validación, lectura de entradas y catálogo.
#----------------------------------------------
# FUNCIONES DE CONFIGURACIÓN DEL CATÁLOGO
#----------------------------------------------
def resolver_catalogo(catalogo):
    """Completa URLs y esperas del catálogo con las variables de entorno."""
    if not isinstance(catalogo, dict):
        raise RuntimeError('El catálogo de supermercados debe ser un objeto JSON')
    for clave, datos in catalogo.items():
        if not isinstance(datos, dict):
            raise RuntimeError(f'Catálogo inválido: {clave}')
        if not datos.get('habilitado', True):
            continue
        for campo in ('url_inicio', 'url_busqueda', 'espera', 'espera_resultados'):
            variable = datos.get(campo + '_env')
            if not variable:
                continue
            if campo.startswith('url_'):
                valor = obtener_variable_obligatoria(variable)
                url = urlsplit(valor)
                if (url.scheme != 'https' or not url.hostname or url.username is not None
                        or url.password is not None or url.fragment):
                    raise RuntimeError(f'{variable} debe ser una URL HTTPS sin credenciales ni fragmento')
            else:
                valor = obtener_decimal_positivo(variable, None)
            datos[campo] = valor
    return catalogo


#----------------------------------------------
# FUNCIONES DE MARCAS, PRECIOS Y FILTROS
#----------------------------------------------
def normalizar_marca(valor):
    """Normaliza acentos y espacios para comparar marcas sin diferencias de formato."""
    texto = unicodedata.normalize("NFKD", str(valor or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", texto))


def leer_precio(valor, columna, fila):
    """Interpreta un precio del input y rechaza valores negativos o no numéricos."""
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


def motivo_descarte(item, precio, marca=None, precio_minimo=None, precio_maximo=None):
    """Devuelve la razón por la que un producto no cumple los filtros."""
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


MOTIVOS_DESCARTE = {
    'precio_bajo_minimo': 'Precio inferior al mínimo solicitado',
    'precio_sobre_maximo': 'Precio superior al máximo solicitado',
    'marca_no_coincide': 'La marca no coincide con la solicitada',
    'marca_no_verificable_en_nombre': 'No se pudo verificar la marca solicitada',
    'precio_no_valido': 'El sitio no proporcionó un precio válido',
}


#----------------------------------------------
# FUNCIONES DE CLASIFICACIÓN DE OBSERVACIONES Y ERRORES
#----------------------------------------------
def es_observacion_busqueda(error):
    """Solo descartes funcionales demostrados; fallos de carga/comunicacion son tecnicos."""
    texto = str(error or '')
    if texto.startswith('Busqueda sin coincidencias confirmada: '):
        try:
            evidencia = json.loads(texto.split(': ', 1)[1])
        except (ValueError, TypeError):
            return False
        return (isinstance(evidencia, dict) and evidencia.get('sin_resultados') is True
                and type(evidencia.get('tarjetas')) is int and evidencia['tarjetas'] == 0)
    prefijo = 'Productos descartados por filtros o precio: '
    if texto.startswith(prefijo):
        try:
            datos = json.loads(texto[len(prefijo):])
        except (ValueError, TypeError):
            return False
        motivos = {'precio_bajo_minimo', 'precio_sobre_maximo', 'marca_no_coincide',
                   'marca_no_verificable_en_nombre'}
        return (isinstance(datos, dict) and bool(datos) and set(datos) <= motivos
                and all(type(n) is int and n > 0 for n in datos.values()))
    return 'sin resultados que cumplan los filtros' in texto


def describir_error_busqueda(error, estado='error'):
    """Traducir evidencia ya persistida; no inventar precios ni inexistencia."""
    if estado == 'pendiente':
        return 'Consulta pendiente: el producto todavía no se ha buscado.'
    if estado == 'leido':
        return 'La consulta terminó sin productos guardados; requiere revisión.'
    texto = str(error or '')
    if texto.startswith('Busqueda sin coincidencias confirmada: ') and es_observacion_busqueda(texto):
        return 'Producto no encontrado: el supermercado confirmó que no hay coincidencias para la búsqueda.'
    prefijo = 'Productos descartados por filtros o precio: '
    if texto.startswith(prefijo):
        try:
            descartados = json.loads(texto[len(prefijo):])
        except (ValueError, TypeError):
            descartados = {}
        if isinstance(descartados, dict):
            motivos = [f'{descripcion} ({descartados[clave]})'
                       for clave, descripcion in MOTIVOS_DESCARTE.items()
                       if type(descartados.get(clave)) is int and descartados[clave] > 0]
            if motivos:
                return 'Sin productos válidos: ' + '; '.join(motivos) + '.'
        return 'No se encontraron productos que cumplan los filtros solicitados.'
    if 'Busqueda sin tarjetas de productos' in texto:
        return ('No se obtuvieron productos en la búsqueda. No se pudo confirmar si '
                'no hay coincidencias o si falló la carga o el envío de la búsqueda.')
    if 'Hay tarjetas pero el extractor' in texto or 'respuesta del extractor DOM' in texto:
        return 'No se pudieron leer los datos de los productos del sitio.'
    if 'requiere seleccionar una ubicacion' in texto:
        return 'El supermercado requiere seleccionar una ubicación para mostrar productos.'
    if 'Acceso bloqueado' in texto or 'captcha' in texto.casefold():
        return 'El supermercado bloqueó el acceso o solicitó una verificación.'
    if 'sin resultados que cumplan los filtros' in texto:
        return 'No se encontraron productos que cumplan los filtros solicitados.'
    if 'precio no reconocido' in texto:
        return 'No se pudo reconocer un precio válido para el producto.'
    if 'supermercado desconocido o deshabilitado' in texto:
        return 'El supermercado no está configurado o está deshabilitado.'
    if any(x in texto for x in ('No se pudo abrir', 'No se pudo escribir',
            'No se pudo enviar', 'No se pudo presionar', 'No se pudo enfocar',
            'No se encontro el campo de busqueda')):
        return 'No se pudo abrir el sitio o completar el envío de la búsqueda.'
    return 'No se pudo completar la consulta; revisar el registro del proceso.'


MAX_ARCHIVO = obtener_entero_positivo('MAX_ARCHIVO')


MAX_EXPANDIDO = obtener_entero_positivo('MAX_EXPANDIDO')


MAX_FILAS = obtener_entero_positivo('MAX_FILAS')


MAX_COLUMNAS = obtener_entero_positivo('MAX_COLUMNAS')


#----------------------------------------------
# FUNCIONES DE SEGURIDAD DEL EXCEL
#----------------------------------------------
def validar_contenedor(ruta):
    """Devuelve una copia validada: el lector debe consumir estos mismos bytes."""
    ruta = Path(ruta)
    if ruta.is_symlink() or not ruta.is_file():
        raise ValueError('El Excel debe ser un archivo regular, sin enlace simbolico')
    if ruta.stat().st_size > MAX_ARCHIVO:
        raise ValueError(f'El Excel supera MAX_ARCHIVO ({MAX_ARCHIVO} bytes)')
    with ruta.open('rb') as origen:
        contenido = origen.read(MAX_ARCHIVO + 1)
    if len(contenido) > MAX_ARCHIVO:
        raise ValueError(f'El Excel supera MAX_ARCHIVO ({MAX_ARCHIVO} bytes)')
    if not contenido.startswith(b'PK\x03\x04'):
        raise ValueError('Firma de bytes invalida: se requiere un Excel OOXML, no XLS ni otro formato')
    copia = BytesIO(contenido)
    try:
        validar_zip(copia)
    except (BadZipFile, expat.ExpatError, ErrorCompresion, EOFError, RuntimeError, NotImplementedError) as error:
        raise ValueError('El Excel esta corrupto, cifrado o contiene XML invalido') from error
    copia.seek(0)
    return copia


def validar_zip(copia):
    """Revisa expansión, entradas y contenido activo del contenedor Excel."""
    with ZipFile(copia) as archivo:
        entradas = archivo.infolist()
        if len(entradas) > MAX_EXCEL_ENTRADAS_ZIP or sum(f.file_size for f in entradas) > MAX_EXPANDIDO:
            raise ValueError('El Excel supera el limite de contenido expandido o archivos internos')
        if any(f.flag_bits & 1 for f in entradas):
            raise ValueError('No se admiten archivos Excel cifrados')
        nombres = set()
        for entrada in entradas:
            nombre = entrada.filename
            normalizado = nombre.casefold()
            if (normalizado in nombres or '\\' in nombre or ':' in nombre or nombre.startswith('/')
                    or any(parte in ('.', '..') for parte in nombre.split('/'))
                    or entrada.orig_filename != nombre
                    or (entrada.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('El Excel contiene rutas internas ambiguas o inseguras')
            nombres.add(normalizado)
            if (any(parte in normalizado for parte in ('vbaproject', 'activex/', 'embeddings/',
                    'externallinks/', 'macrosheets/', 'dialogsheets/', 'querytables/', 'connections.xml'))
                    or normalizado.endswith(('.bin', '.exe', '.dll', '.js', '.vbs', '.ps1'))):
                raise ValueError('El Excel contiene macros, objetos incrustados o conexiones no permitidas')
            if entrada.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                raise ValueError('Compresion interna de Excel no permitida')
        requeridos = {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels'}
        if not requeridos.issubset({e.filename for e in entradas}):
            raise ValueError('Faltan componentes obligatorios del Excel OOXML')
        total = 0
        for entrada in entradas:
            parser = parser_xml_seguro() if entrada.filename.casefold().endswith(('.xml', '.rels')) else None
            with archivo.open(entrada) as origen:
                while True:
                    bloque = origen.read(min(65536, MAX_EXPANDIDO - total + 1))
                    if not bloque:
                        break
                    total += len(bloque)
                    if total > MAX_EXPANDIDO:
                        raise ValueError('El Excel supera el limite de contenido expandido')
                    if parser is not None:
                        parser.Parse(bloque, False)
            if parser is not None:
                parser.Parse(b'', True)


def parser_xml_seguro():
    """Crea un lector XML que rechaza fórmulas, entidades y construcciones inseguras."""
    parser = expat.ParserCreate(namespace_separator='}')

    def rechazar(*args):
        """Interrumpe la lectura al detectar una construcción XML no permitida."""
        raise ValueError('El XML del Excel contiene DTD o entidades no permitidas')

    def elemento(nombre, atributos):
        """Valida cada etiqueta XML y rechaza las fórmulas de las celdas."""
        local = nombre.rsplit('}', 1)[-1]
        if local == 'Relationship' and atributos.get('TargetMode', '').casefold() == 'external':
            raise ValueError('El Excel contiene enlaces externos no permitidos')
        if local in ('Default', 'Override'):
            tipo = atributos.get('ContentType', '').casefold()
            if any(valor in tipo for valor in ('macroenabled', 'vba', 'activex', 'oleobject')):
                raise ValueError('El Excel contiene macros u objetos no permitidos')
        if nombre == 'http://schemas.openxmlformats.org/spreadsheetml/2006/main}f':
            raise ValueError('El input contiene formulas; guardar como valores antes de ejecutar')

    parser.StartDoctypeDeclHandler = rechazar
    parser.EntityDeclHandler = rechazar
    parser.ExternalEntityRefHandler = rechazar
    parser.StartElementHandler = elemento
    return parser


#----------------------------------------------
# FUNCIONES DE VALIDACIÓN Y ESCRITURA DE TEXTOS
#----------------------------------------------
def texto_entrada(valor, campo, fila, limite, obligatorio=False):
    """Valida y normaliza un texto del input, su obligatoriedad y longitud."""
    original = '' if valor is None else str(valor)
    texto = original.strip()
    if len(texto) > limite or re.search(r'[\x00-\x1f\x7f]', original):
        raise ValueError(f'Fila {fila}: {campo} excede el limite o contiene caracteres de control')
    if obligatorio and not texto:
        raise ValueError(f'Fila {fila}: falta {campo}')
    return texto


def agregar_fila_literal(hoja, valores):
    """Escribe una fila como valores literales para evitar fórmulas en el reporte."""
    hoja.append(list(valores))
    for celda in hoja[hoja.max_row]:
        if isinstance(celda.value, str):
            # XLSX inline string: conserva el valor sin crear una formula ni alterar precios numericos.
            celda.data_type = 's'


#----------------------------------------------
# FUNCIONES DE LECTURA DE ENTRADAS JSON Y EXCEL
#----------------------------------------------
def leer_productos_json(ruta):
    """Productos de parametros web; no requiere ni crea un Excel."""
    if Path(ruta).stat().st_size > MAX_ARCHIVO:
        raise ValueError(f'Los productos JSON superan MAX_ARCHIVO ({MAX_ARCHIVO} bytes)')
    with open(ruta, encoding='utf-8') as f:
        contenido = json.load(f)
    registros = contenido.get('productos') if isinstance(contenido, dict) else contenido
    if not isinstance(registros, list) or not registros:
        raise ValueError('Un proceso de supermercados sin Excel necesita productos en sus parametros')
    if len(registros) > MAX_FILAS:
        raise ValueError('Los productos JSON superan el limite de registros')
    tareas = []
    for numero, item in enumerate(registros, start=1):
        if not isinstance(item, dict) or not isinstance(item.get('producto'), str) or not item['producto'].strip():
            raise ValueError(f'Producto JSON {numero}: falta un nombre de producto valido')
        minimo = leer_precio(item.get('precio_minimo'), 'precio_minimo', numero)
        maximo = leer_precio(item.get('precio_maximo'), 'precio_maximo', numero)
        if minimo is not None and maximo is not None and minimo > maximo:
            raise ValueError(f'Producto JSON {numero}: precio_minimo no puede superar precio_maximo')
        supermercado = item.get('supermercado')
        if supermercado is not None and not isinstance(supermercado, str):
            raise ValueError(f'Producto JSON {numero}: supermercado invalido')
        tareas.append({'producto': texto_entrada(item['producto'], 'producto', numero, 500, obligatorio=True),
                       'supermercado': texto_entrada(supermercado, 'supermercado', numero, 100) or None,
                       'marca': normalizar_marca(texto_entrada(item.get('marca'), 'marca', numero, 200)), 'fila_excel': numero,
                       'precio_minimo': minimo, 'precio_maximo': maximo})
    return tareas


COLUMNAS = ("supermercado", "producto", "marca", "precio_minimo", "precio_maximo")


def leer_productos_excel(ruta=None):
    "funcion para leer los productos de excel y procesarlos si es que hay o no"
    ruta = str(ruta if ruta is not None else EXCEL_ENTRADA)
    if not os.path.exists(ruta):
        raise FileNotFoundError("No existe el Excel de entrada: " + ruta)

    copia_validada = validar_contenedor(ruta)
    libro = load_workbook(copia_validada, read_only=True, data_only=False, keep_links=False)
    try:
        hoja = libro.active
        if (hoja.max_row or 0) > MAX_FILAS + 1 or (hoja.max_column or 0) > MAX_COLUMNAS:
            raise ValueError('El Excel supera el limite de filas o columnas')
        hoja.reset_dimensions()
        filas = []
        for numero, fila in enumerate(hoja.iter_rows(max_col=MAX_COLUMNAS + 1), start=1):
            if numero > MAX_FILAS + 1:
                raise ValueError('El Excel supera el limite de filas')
            if fila[-1].value is not None:
                raise ValueError('El Excel supera el limite de columnas')
            if any(celda.data_type == 'f' for celda in fila):
                raise ValueError(f'Fila {numero}: el input contiene formulas; guardar como valores antes de ejecutar')
            # Mantener solo las columnas con contenido; lectura y memoria acotadas.
            valores = [celda.value for celda in fila[:-1]]
            while valores and valores[-1] is None:
                valores.pop()
            filas.append(valores)
    finally:
        libro.close()
        copia_validada.close()

    if not filas:
        raise ValueError("El Excel de entrada esta vacio")

    encabezados = []
    for celda in filas[0]:
        if celda is None:
            encabezados.append("")
        else:
            encabezados.append(str(celda).strip().lower())
    if "producto" not in encabezados:
        raise ValueError("El excel de entrada necesita una columna llamada 'producto'")
    for nombre in COLUMNAS:
        if encabezados.count(nombre) > 1:
            raise ValueError("El excel tiene un encabezado duplicado: " + nombre)

    idx = {}
    for nombre in COLUMNAS:
        if nombre in encabezados:
            idx[nombre] = encabezados.index(nombre)

    def valor(fila,nombre):
        """Acumula el texto XML controlando su tamaño permitido."""
        posicion = idx.get(nombre)
        if posicion is None or posicion >= len(fila):
            return None
        return fila[posicion]

    tareas = []
    for numero_fila, fila in enumerate(filas[1:], start=2):
        producto = valor(fila, "producto")
        if producto is None or str(producto).strip() == "":
            continue
        supermercado = valor(fila, "supermercado")
        producto = texto_entrada(producto, 'producto', numero_fila, 500, obligatorio=True)
        supermercado = texto_entrada(supermercado, 'supermercado', numero_fila, 100) or None
        marca = normalizar_marca(texto_entrada(valor(fila, 'marca'), 'marca', numero_fila, 200))
        minimo = leer_precio(valor(fila, "precio_minimo"), "precio_minimo", numero_fila)
        maximo = leer_precio(valor(fila, "precio_maximo"), "precio_maximo", numero_fila)
        if minimo is not None and maximo is not None and minimo > maximo:
            raise ValueError(f"Fila {numero_fila}: precio_minimo no puede superar precio_maximo")
        tareas.append(
            {
                "producto": str(producto).strip(),
                "supermercado": str(supermercado).strip() if supermercado is not None else None,
                "fila_excel": numero_fila,
                "marca": marca,
                "precio_minimo": minimo,
                "precio_maximo": maximo,
            }
        )

    if not tareas:
        raise ValueError('El Excel no contiene productos para procesar')
    return tareas


def crear_plantilla_si_no_existe():
    "esta funcion en caso de no existir excel de productos, lo genera"
    RUTA_INPUT.mkdir(parents=True, exist_ok=True)
    if os.path.exists(EXCEL_ENTRADA):
        print("Ya existe " + EXCEL_ENTRADA )
        return
    libro = Workbook()
    hoja = libro.active
    hoja.title = "productos"
    hoja.append(list(COLUMNAS))
    hoja.append([None, "leche"])
    hoja.append(["Unimarc", "arroz"])
    hoja.append(["Tottus", "café"])
    hoja.append(["Líder", "pan"])
    libro.save(EXCEL_ENTRADA)
    libro.close()
    print("Creado " + EXCEL_ENTRADA + " con 4 ejemplos")


#----------------------------------------------
# FUNCIONES DE SELECCIÓN DE SUPERMERCADOS
#----------------------------------------------
def cargar_supermercados():
    """Devuelve una copia del catálogo integrado con las URLs y esperas del entorno."""
    return resolver_catalogo(deepcopy(CATALOGO_SUPERMERCADOS))


def normalizar_nombre(valor):
    """Normaliza un nombre de supermercado para resolver su clave de catálogo."""
    texto = unicodedata.normalize("NFKD", str(valor).strip().casefold())
    return "".join(caracter for caracter in texto if not unicodedata.combining(caracter))


def seleccionar_supermercados(valor, supermercados):
    """Devuelve claves habilitadas; un nombre incorrecto nunca significa todos."""
    nombre = normalizar_nombre(valor) if valor is not None else ""
    if not nombre:
        return [clave for clave, datos in supermercados.items() if datos.get("habilitado", True)]

    for clave, datos in supermercados.items():
        if nombre in (normalizar_nombre(clave), normalizar_nombre(datos.get("nombre", clave))):
            if not datos.get("habilitado", True):
                raise ValueError("Supermercado deshabilitado: " + str(valor).strip())
            return [clave]
    raise ValueError("Supermercado desconocido: " + str(valor).strip())


#----------------------------------------------
# FUNCIONES DE CARGA DE ARCHIVOS Y TAREAS
#----------------------------------------------
def listar_archivos_entrada(carpeta=None):
    """Lista todos los Excel compatibles de la carpeta y omite archivos de bloqueo."""
    carpeta = Path(RUTA_INPUT if carpeta is None else carpeta)
    return sorted((p for p in carpeta.iterdir()
                   if p.is_file() and p.suffix.lower() in EXTENSIONES_EXCEL
                   and not p.name.startswith('~$')), key=lambda p: p.name.casefold()) if carpeta.is_dir() else []


def cargar_excel_a_tabla_cola(ruta=None):
    """Carga un Excel concreto o todos los de input y devuelve sus contadores de lectura."""
    logger = obtener_logger("DETAILS")
    carpeta = Path(RUTA_INPUT)
    resumen = {"archivos_leidos": 0, "archivos_error": 0, "tareas_agregadas": 0, "tareas_existentes": 0}
    if ruta is None and not carpeta.is_dir():
        logger.warning("No existe la carpeta de entrada: " + str(carpeta))
        return resumen
    archivos = [Path(ruta)] if ruta is not None else listar_archivos_entrada(carpeta)
    if not archivos:
        logger.info("No hay archivos Excel para cargar en " + str(carpeta))
    for archivo in archivos:
        try:
            carga = cargar_archivo_a_cola(archivo)
        except Exception as error:
            resumen["archivos_error"] += 1
            logger.warning("Archivo omitido: " + archivo.name + "; " + str(error))
            continue
        resumen["archivos_leidos"] += 1
        resumen["tareas_agregadas"] += carga["tareas_agregadas"]
        resumen["tareas_existentes"] += carga["tareas_existentes"]
    logger.info("Carga de carpeta finalizada: " + str(resumen))
    return resumen


def cargar_productos_json_a_tabla_cola(ruta):
    """Carga las tareas de una entrada JSON validada en la cola SQLite."""
    carga = cargar_archivo_a_cola(Path(ruta), lector=leer_productos_json)
    return dict(carga, archivos_leidos=1, archivos_error=0)


def cargar_archivo_a_cola(ruta, lector=None):
    """Registra el archivo, valida sus tareas y evita insertar tareas duplicadas."""
    logger = obtener_logger("DETAILS")
    archivo_id = registrar_archivo(ruta, ahora())
    agregadas = 0
    omitidas = 0
    try:
        tareas = (lector or leer_productos_excel)(ruta)
        supermercados = cargar_supermercados()
        tareas_validadas = []
        for tarea in tareas:
            try:
                destinos = seleccionar_supermercados(tarea.get("supermercado"), supermercados)
            except ValueError as error:
                raise ValueError("Fila " + str(tarea["fila_excel"]) + ": " + str(error)) from error
            tareas_validadas.append((tarea, destinos))

        agregadas, omitidas = guardar_tareas_entrada(archivo_id, tareas_validadas, ahora())
    except Exception as error:
        actualizar_archivo(archivo_id, "error", ahora(), str(error))
        raise

    logger.info("Archivo leido: " + ruta.name + "; agregadas " + str(agregadas)
                + " tareas a cola; " + str(omitidas) + " ya existian")
    return {"tareas_agregadas": agregadas, "tareas_existentes": omitidas}


#----------------------------------------------
# FUNCIONES DE DIAGNÓSTICO Y VALIDACIÓN DE ENTRADAS
#----------------------------------------------
def ver_datos_en_tabla_cola():
    """Muestra el resumen y los errores registrados en la cola local."""
    filas, errores = consultar_estado_cola()

    if not filas:
        print("La cola está vacía. Usa: python main.py cargar")
        return

    print("Estado de la cola:")
    estados_presentes = []
    for estado, cantidad in filas:
        estados_presentes.append(estado)
        print("  " + estado.ljust(12) + str(cantidad))

    if errores:
        print("\nÚltimas tareas con error:")
        for (id_, super_, producto, reintentos, error, fecha) in errores:
            print("  id " + str(id_).ljust(4) + super_.ljust(13) + producto[:30] +
                  " reintentos: " + str(reintentos) + " " + (fecha or ""))
            if error:
                print("      " + str(error)[:100])
    elif "error" not in estados_presentes:
        print("\nNo hay errores.")


def validar_entradas(config, catalogo):
    """Comprueba los archivos y supermercados de las tareas antes de iniciar el negocio."""
    from config import base_datos as bd
    carpeta = Path(config.RUTA_INPUT)
    archivos = listar_archivos_entrada(carpeta)
    for ruta in archivos:
        try:
            for tarea in leer_productos_excel(ruta):
                seleccionar_supermercados(tarea.get('supermercado'), catalogo)
        except Exception as error:
            raise ErrorArranque(f'Entrada invalida: {ruta.name}. {error}') from None
    pendientes = bd.supermercados_por_validar()
    for (supermercado,) in pendientes:
        seleccionar_supermercados(supermercado, catalogo)
    if not archivos and not pendientes:
        raise ErrorArranque('No hay Excel en input/ ni tareas pendientes o con error en la cola')
    return len(archivos), len(pendientes)


def validar_catalogo():
    """Comprueba tiendas habilitadas, URLs del entorno y selectores antes de procesar."""
    catalogo = cargar_supermercados()
    if not isinstance(catalogo, dict) or not catalogo:
        raise ErrorArranque('El catalogo de supermercados esta vacio o no es un objeto JSON')
    habilitados = {}
    for clave, datos in catalogo.items():
        if not isinstance(datos, dict) or type(datos.get('habilitado', True)) is not bool:
            raise ErrorArranque(f'Configuracion invalida del supermercado {clave}')
        if not datos.get('habilitado', True):
            continue
        url = datos.get('url_busqueda') or datos.get('url_inicio')
        if not isinstance(url, str) or urlsplit(url).scheme != 'https' or not urlsplit(url).hostname:
            raise ErrorArranque(f'Falta URL HTTPS del supermercado {clave}')
        if not datos.get('nombre') or not datos.get('extractor'):
            raise ErrorArranque(f'Falta nombre o extractor del supermercado {clave}')
        habilitados[clave] = datos
    if not habilitados:
        raise ErrorArranque('No hay supermercados habilitados')
    return catalogo


# Búsqueda, extracción y procesamiento de tareas.
COMPROBAR_ACCESO_JS = r"""
    var textoPagina = document.body ? document.body.innerText : '';
    if (/robot or human|just a moment|un momento/i.test(document.title) ||
        document.querySelector('#challenge-running, #cf-challenge-running, #challenge-form') ||
        /verificaci[oó]n de seguridad en curso|verifying you are human|verify you are human|access denied|acceso denegado/i.test(textoPagina)) {
        return JSON.stringify({error: 'Acceso bloqueado por verificacion de seguridad del sitio'});
    }
    if (location.protocol === 'chrome-error:') {
        return JSON.stringify({error: 'No se pudo cargar la pagina del supermercado'});
    }
"""


EXTRACTOR_TOTTUS_JS = r"""
    var out = [];
    var cards = document.querySelectorAll(selectores.tarjeta);
    if (!cards.length) {
        cards = document.querySelectorAll(selectores.tarjeta_respaldo);
    }
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var nombre = card.querySelector(selectores.nombre) || card.querySelector(selectores.nombre_respaldo);
        var imagen = card.querySelector(selectores.imagen);
        var textoNombre = nombre ? nombre.textContent.trim() :
            (card.getAttribute('title') || (imagen ? imagen.getAttribute('alt') : '')).trim();
        var link = card.matches('a[href]') ? card : card.querySelector(selectores.enlace);
        var idProducto = (card.id || '').slice(selectores.prefijo_tarjeta.length);
        var bloquePrecios = card.querySelector(selectores.bloque_precios) ||
            document.getElementById(selectores.prefijo_precios + idProducto) || card;
        var precio = bloquePrecios.querySelector(selectores.precio_publico);
        if (!precio) {
            var candidatos = bloquePrecios.querySelectorAll(selectores.precio);
            var precioNormal = null;
            for (var j = 0; j < candidatos.length; j++) {
                var candidato = candidatos[j];
                var contenido = candidato.textContent.trim();
                var filaPrecio = candidato.closest('li') || candidato.parentElement;
                var condicionado = /cmr|tarjeta|club|membres[ií]a|\d+\s*[x×]\s*\$/i.test(filaPrecio.textContent) ||
                    filaPrecio.querySelector('[data-cmr-price], .price-cmr, img[alt*="CMR"], img[src*="cmr"]') ||
                    candidato.matches('[data-cmr-price], .price-cmr');
                if (!/^\$\s*\d[\d.,]*(?:\s*c\s*\/\s*u)?\s*$/i.test(contenido) || condicionado) {
                    continue;
                }
                if (candidato.closest('s, del') ||
                    /line-through/.test(getComputedStyle(candidato).textDecorationLine) ||
                    /line-through/.test(getComputedStyle(filaPrecio).textDecorationLine)) {
                    precioNormal = precioNormal || candidato;
                    continue;
                }
                precio = candidato;
                break;
            }
            precio = precio || precioNormal;
        }
        if (!precio) {
            var principal = card.querySelector(selectores.precio_principal);
            if (principal && !principal.matches('[data-cmr-price], .price-cmr') &&
                !principal.querySelector('[data-cmr-price], .price-cmr') &&
                !/cmr|tarjeta|club|\d+\s*[x×]\s*\$/i.test(principal.textContent)) {
                precio = principal;
            }
        }
        if (!precio) {
            precio = card.querySelector(selectores.precio_normal);
        }
        var texto = precio ? precio.textContent.trim() : '';
        if (!textoNombre || !link || !/\$\s*\d/.test(texto) ||
            /cmr|tarjeta|club|\d+\s*[x×]\s*\$/i.test(texto)) {
            continue;
        }
        out.push({ok: true, nombre: textoNombre, precio: texto, url: link.href});
        var marca = selectores.marca ? card.querySelector(selectores.marca) : null;
        if (marca) { out[out.length - 1].marca = marca.textContent.trim(); }
    }
    return JSON.stringify(out);
"""


EXTRACTOR_UNIMARC_JS = r"""
    var out = [];
    var cards = document.querySelectorAll(selectores.tarjeta);
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var nombre = card.querySelector(selectores.nombre);
        var link = card.querySelector(selectores.enlace);
        var oferta = card.querySelector(selectores.precio_oferta);
        var normal = card.querySelector(selectores.precio_normal);
        var textoCard = card.innerText || card.textContent;
        var condicionado = /club\s+unimarc|tarjeta|membres[ií]a|\d+\s*[x×]\s*\$/i.test(textoCard);
        var precio = condicionado ? normal : (oferta || normal);
        var texto = precio ? precio.textContent.trim() : '';
        if (!nombre || !link || !/^\$\s*\d[\d.,]*(?:\s*c\s*\/\s*u)?\s*$/i.test(texto)) {
            continue;
        }
        out.push({ok: true, nombre: nombre.textContent.trim(), precio: texto, url: link.href});
        var marca = selectores.marca ? card.querySelector(selectores.marca) : null;
        if (marca) { out[out.length - 1].marca = marca.textContent.trim(); }
    }
    return JSON.stringify(out);
"""


COMPROBAR_UBICACION_JS = r"""
    var dialogos = document.querySelectorAll('[role="dialog"], dialog[open]');
    for (var i = 0; i < dialogos.length; i++) {
        if (dialogos[i].getClientRects().length &&
            /(?:ingresa|selecciona|elige|indica).{0,40}(?:ubicaci[oó]n|comuna|direcci[oó]n)/i.test(dialogos[i].innerText)) {
            return JSON.stringify({error: 'El sitio requiere seleccionar una ubicacion para continuar'});
        }
    }
    return JSON.stringify([]);
"""


#----------------------------------------------
# FUNCIONES DE EVIDENCIAS DEL PROCESO
#----------------------------------------------
def capturar(nombre_archivo):
    """Registra la captura de una consulta fallida como evidencia del proceso."""
    ruta = captura_error(nombre_archivo)
    if ruta:
        obtener_sesion().capturas.append(ruta)
    return ruta


def obtener_capturas():
    """Devuelve las capturas acumuladas en el procesamiento actual."""
    return list(obtener_sesion().capturas)


#----------------------------------------------
# FUNCIONES DE NORMALIZACIÓN DE PRECIOS
#----------------------------------------------
def limpiar_precio(texto):
    """Convierte el precio publicado por la tienda a un valor numérico comparable."""
    if not texto:
        return None
    texto = re.split(r"[a-zA-Z]", str(texto))[0]
    coincidencia = re.search(r"[0-9][0-9.,]*", texto)
    if not coincidencia:
        return None
    numero = coincidencia.group(0).strip(".,")
    if not numero:
        return None
    if "," in numero and "." in numero:
        if numero.rfind(",") > numero.rfind("."):
            numero = numero.replace(".", "").replace(",", ".")
        else:
            numero = numero.replace(",", "")
    elif "," in numero:
        numero = numero.replace(".", "").replace(",", ".")
    else:
        partes = numero.split(".")
        if len(partes) > 1:
            son_miles = True
            for parte in partes[1:]:
                if len(parte) != 3:
                    son_miles = False
                    break
            if son_miles:
                numero = "".join(partes)
    try:
        return float(numero)
    except ValueError:
        return None


EXTRACTOR_JS = """
    var out = [];
    var cards = document.querySelectorAll(selectores.tarjeta);
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var btn = card.querySelector(selectores.boton_nombre);
        var nombre = btn ? (btn.getAttribute('aria-label') || '').replace(selectores.prefijo_nombre, '') : '';
        if (!nombre) {
            var spans = card.querySelectorAll(selectores.nombre_respaldo);
            for (var j = 0; j < spans.length; j++) {
                var texto = (spans[j].textContent || '').trim();
                if (texto.length > 2 && texto.indexOf('$') < 0 && texto != 'Agregar') {
                    nombre = texto;
                    break;
                }
            }
        }
        var precio = card.querySelector(selectores.precio);
        var link = card.querySelector(selectores.enlace);
        out.push({
            ok: true,
            nombre: nombre,
            precio: precio ? precio.textContent.trim() : '',
            precio_antes: '',
            url: link ? link.href : location.href
        });
        var marca = selectores.marca ? card.querySelector(selectores.marca) : null;
        if (marca) { out[out.length - 1].marca = marca.textContent.trim(); }
    }
    return JSON.stringify(out);
"""


#----------------------------------------------
# FUNCIONES DE EXTRACCIÓN DE PRODUCTOS
#----------------------------------------------
def obtener_selectores(datos_supermercado):
    """Combina los selectores y la estrategia de extracción de la tienda."""
    configuracion = datos_supermercado.get("extractor")
    clave = configuracion.get("tipo") if isinstance(configuracion, dict) else configuracion
    if not clave:
        clave = datos_supermercado.get("nombre", "lider").strip().lower()
    if clave not in ("lider", "tottus", "unimarc"):
        raise ValueError("Extractor desconocido: " + str(clave))
    selectores = dict(cargar_supermercados()[clave]["extractor"])
    if isinstance(configuracion, dict):
        selectores.update(configuracion)
    return clave, selectores


def obtener_extractor(datos_supermercado):
    """Construye el JavaScript de extracción correspondiente al supermercado."""
    extractores = {"lider": EXTRACTOR_JS, "tottus": EXTRACTOR_TOTTUS_JS, "unimarc": EXTRACTOR_UNIMARC_JS}
    clave, selectores = obtener_selectores(datos_supermercado)
    js = COMPROBAR_ACCESO_JS + "var selectores = " + json.dumps(selectores) + "; " + extractores[clave].replace(
        "return JSON.stringify(out);",
        "if (out.length) { return JSON.stringify(out); } " + COMPROBAR_UBICACION_JS,
    )
    return " ".join(js.split())


def inspeccionar_busqueda(datos_supermercado):
    """Lee el estado y campos de las tarjetas sin modificar la pagina."""
    _, selectores = obtener_selectores(datos_supermercado)
    cuerpo = COMPROBAR_ACCESO_JS + "var s = " + json.dumps(selectores) + "; var selectorBusqueda = " + json.dumps(datos_supermercado.get("selector_busqueda")) + "; " + """
        var cards = document.querySelectorAll(s.tarjeta);
        if (!cards.length && s.tarjeta_respaldo) cards = document.querySelectorAll(s.tarjeta_respaldo);
        var marcas = 0, muestras = [];
        for (var i = 0; i < cards.length; i++) {
            var marca = s.marca ? cards[i].querySelector(s.marca) : null;
            if (marca && marca.textContent.trim()) marcas++;
            if (muestras.length < 5) muestras.push({marca: marca ? marca.textContent.trim() : '',
                texto: (cards[i].innerText || cards[i].textContent || '').trim().slice(0, 350)});
        }
        var campo = selectorBusqueda ? document.evaluate(selectorBusqueda, document, null,
            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue : null;
        var textoPagina = document.body ? document.body.innerText : '';
        var avisoVacio = textoPagina.match(/no se encontraron resultados|no encontramos (?:resultados|productos|coincidencias)|no hay resultados/i);
        return JSON.stringify({url: location.href, titulo: document.title, busqueda_en_campo: campo ? campo.value : null,
            tarjetas: cards.length, tarjetas_con_marca: marcas, muestras: muestras,
            sin_resultados: cards.length === 0 && !!avisoVacio,
            mensaje_sin_resultados: avisoVacio ? avisoVacio[0] : null});
    """
    salida = evaluar_js(" ".join(cuerpo.split()), reintentos=1)
    try:
        datos = json.loads(salida)
    except (ValueError, TypeError):
        return {"error": "No se pudo leer el estado del DOM"}
    return datos if isinstance(datos, dict) else {"error": "Respuesta DOM inesperada"}


def esperar_productos(datos_supermercado, max_items=MAX_PRODUCTOS_POR_BUSQUEDA, marca=None, precio_minimo=None, precio_maximo=None):
    """Espera resultados, aplica filtros y reúne productos hasta el límite configurado."""
    extractor = obtener_extractor(datos_supermercado)
    if not extractor:
        return []
    fin = time.monotonic() + RPA_ESPERA_MAX
    resultados = []
    vistos = set()
    descartados = {}
    observados = set()
    respuestas_validas = 0
    while time.monotonic() < fin and len(resultados) < max_items:
        salida = evaluar_js(extractor, reintentos=RPA_DOM_REINTENTOS)
        if salida:
            try:
                datos = json.loads(salida)
            except ValueError:
                datos = None
            if isinstance(datos, (list, dict)):
                respuestas_validas += 1
            if isinstance(datos, dict) and datos.get("error"):
                raise ValueError(datos["error"])
            if datos:
                if isinstance(datos, dict):
                    lista = [datos] if datos.get("ok") and datos.get("nombre") else []
                else:
                    lista = datos if isinstance(datos, list) else []
                for item in lista:
                    if not isinstance(item, dict) or not item.get("nombre"):
                        continue
                    precio = limpiar_precio(item.get("precio"))
                    motivo = "precio_no_valido" if precio is None or precio <= 0 else motivo_descarte(
                        item, precio, marca, precio_minimo, precio_maximo)
                    observacion = (item.get("nombre"), item.get("url"), item.get("marca"), str(item.get("precio")))
                    if motivo:
                        if observacion not in observados:
                            descartados[motivo] = descartados.get(motivo, 0) + 1
                            observados.add(observacion)
                        continue
                    clave = (item.get("nombre","").strip(), item.get("url",""))
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    resultados.append(item)
                    if len(resultados) >= max_items:
                        break
        if len(resultados) >= max_items:
            break
        obtener_sesion().navegador.dom("return window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(RPA_RESULTADOS_INTERVALO)
    registrar("Lectura de productos: aceptados=" + str(len(resultados)) + "; descartados="
              + json.dumps(descartados, ensure_ascii=False), "SCRAPING")
    if not resultados:
        if not respuestas_validas:
            raise ValueError("No se pudo interpretar la respuesta del extractor DOM; comprobar conexion con Chrome")
        if descartados:
            raise ValueError("Productos descartados por filtros o precio: " + json.dumps(descartados, ensure_ascii=False))
        estado = inspeccionar_busqueda(datos_supermercado)
        if estado.get("error"):
            raise ValueError(estado["error"])
        if estado.get("tarjetas", 0):
            raise ValueError("Hay tarjetas pero el extractor no reconoce nombre, enlace o precio publico: " + json.dumps(estado, ensure_ascii=False))
        if estado.get('sin_resultados') is True and estado.get('tarjetas') == 0:
            raise ValueError('Busqueda sin coincidencias confirmada: ' + json.dumps(estado, ensure_ascii=False))
        raise ValueError("Busqueda sin tarjetas de productos; comprobar envio, carga o selector de tarjeta: " + json.dumps(estado, ensure_ascii=False))
    return resultados


#----------------------------------------------
# FUNCIONES DE NAVEGACIÓN Y BÚSQUEDA
#----------------------------------------------
def enviar_enter(selector):
    """Envia una pulsacion Enter al navegador conectado."""
    if not obtener_sesion().navegador.click(selector):
        raise RuntimeError("No se pudo enfocar el buscador antes de presionar Enter")
    for tipo in ("keyDown", "keyUp"):
        evento = {
            "type": tipo,
            "key": "Enter",
            "code": "Enter",
            "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
        }
        if tipo == "keyDown":
            evento.update(text="\r", unmodifiedText="\r")
        instruccion = "js chrome_step('Input.dispatchKeyEvent', " + json.dumps(evento) + ");"
        if not obtener_sesion().navegador.send(instruccion):
            raise RuntimeError("No se pudo presionar Enter en el buscador")


def navegar_busqueda(datos_supermercado, termino):
    """Abre la busqueda por URL o utiliza el campo configurado del sitio."""
    espera = float(datos_supermercado.get("espera", 6))
    selector = datos_supermercado.get("selector_busqueda")
    if not selector:
        url = datos_supermercado["url_busqueda"].replace("{termino}", quote(termino, safe=""))
        obtener_sesion().navegador.url(url)
        time.sleep(espera)
        return url

    url = datos_supermercado["url_inicio"]
    if not obtener_sesion().navegador.url(url):
        raise RuntimeError("No se pudo abrir la pagina de " + datos_supermercado["nombre"])
    time.sleep(espera)
    fin = time.monotonic() + RPA_ESPERA_MAX
    while not obtener_sesion().navegador.present(selector):
        salida = evaluar_js(COMPROBAR_ACCESO_JS + "return JSON.stringify({});", reintentos=1)
        try:
            estado = json.loads(salida)
        except (TypeError, ValueError):
            estado = None
        if isinstance(estado, dict) and estado.get("error"):
            raise ValueError(estado["error"])
        if time.monotonic() >= fin:
            raise TimeoutError("No se encontro el campo de busqueda: " + selector)
        time.sleep(RPA_ESCRITURA_PAUSA)

    if not obtener_sesion().navegador.type(selector, "[clear]" + termino):
        raise RuntimeError("No se pudo escribir o enviar la busqueda en " + datos_supermercado["nombre"])
    time.sleep(RPA_ESCRITURA_PAUSA)
    boton = datos_supermercado.get("selector_enviar_busqueda")
    if boton:
        if not obtener_sesion().navegador.click(boton):
            raise RuntimeError("No se pudo enviar la busqueda en " + datos_supermercado["nombre"])
    else:
        enviar_enter(selector)
    time.sleep(float(datos_supermercado.get("espera_resultados", espera)))
    return url


def buscar_producto(datos_supermercado, termino, max_items=MAX_PRODUCTOS_POR_BUSQUEDA, marca=None, precio_minimo=None, precio_maximo=None):
    """Busca un producto y devuelve resultados normalizados que cumplen los filtros."""
    consulta = termino + " " + marca if marca else termino
    url = navegar_busqueda(datos_supermercado, consulta)

    resultados = esperar_productos(datos_supermercado, max_items=max_items,
                                   marca=marca, precio_minimo=precio_minimo, precio_maximo=precio_maximo)
    if not resultados:
        raise ValueError("sin resultados que cumplan los filtros para: " + consulta)

    productos = []
    for datos in resultados:
        precio = limpiar_precio(datos.get("precio"))
        if precio is None:
            continue
        if motivo_descarte(datos, precio, marca, precio_minimo, precio_maximo) is not None:
            continue
        productos.append(
            {
                "nombre": (datos.get("nombre") or "").strip(),
                "precio": precio,
                "url": datos.get("url") or url,
            }
        )
        if datos.get("marca"):
            productos[-1]["marca"] = datos["marca"].strip()
    if not productos:
        raise ValueError("precio no reconocido para: " + termino)

    return productos


#----------------------------------------------
# FUNCIONES DE PROCESAMIENTO Y REINTENTOS
#----------------------------------------------
def procesar_tarea(datos_supermercado, termino, id_tarea, clave_super, producto, max_items=MAX_PRODUCTOS_POR_BUSQUEDA,
                   marca=None, precio_minimo=None, precio_maximo=None, *, ejecutar_con_reintentos):
    """Ejecuta los reintentos de una consulta y guarda su éxito o su fallo final."""
    def consultar(intento):
        """Realiza un intento de búsqueda y guarda los productos encontrados."""
        productos = buscar_producto(datos_supermercado, termino, max_items=max_items,
                                    marca=marca, precio_minimo=precio_minimo, precio_maximo=precio_maximo)
        guardar_resultados(id_tarea, clave_super, producto, productos, intento, ahora())
        for datos in productos:
            print("   OK: " + datos["nombre"] + " -> " + str(datos["precio"]))
        registrar("Guardados " + str(len(productos)) + " resultados de '" + producto + "'", "SCRAPING")
        return True

    def registrar_fallo(error, numero, final):
        """Actualiza la tarea y captura evidencia si se agotaron los intentos."""
        if final:
            capturar(slug(clave_super) + "_" + slug(producto))
            actualizar_cola(id_tarea, "error", numero, ahora(), error=str(error))
        else:
            actualizar_cola(id_tarea, "pendiente", numero)

    try:
        return ejecutar_con_reintentos("Consulta " + clave_super + " - " + producto,
            consultar, MAX_REINTENTOS, al_fallar=registrar_fallo)
    except Exception:
        return False


def procesar_cola_a_resultados(ejecutar_con_reintentos):
    """Procesa las tareas pendientes y desconecta el navegador al finalizar."""
    logger = obtener_logger("SCRAPING")
    obtener_sesion().capturas.clear()
    reencolados = reencolar_errores()
    if reencolados:
        logger.info("Reencoladas " + str(reencolados) + "tareas con error a reintentar")

    pendientes = tareas_pendientes()

    if not pendientes:
        logger.info("No hay tareas pendientes. Usa: python main.py cargar")
        return None

    supermercados = cargar_supermercados()
    ok = 0
    errores = 0
    try:
        abrir_navegador()
        for (id_tarea, clave_super, producto, marca, minimo, maximo) in pendientes:
            datos_supermercado = supermercados.get(clave_super)
            if not datos_supermercado or not datos_supermercado.get("habilitado", True):
                capturar(slug(clave_super) + "_" + slug(producto))
                actualizar_cola(id_tarea, "error", 0, ahora(),
                                error="supermercado desconocido o deshabilitado")
                errores += 1
                continue

            print("Buscando '" + producto + "' en " + datos_supermercado["nombre"])
            if procesar_tarea(datos_supermercado, producto, id_tarea, clave_super, producto,
                              marca=marca, precio_minimo=minimo, precio_maximo=maximo,
                              ejecutar_con_reintentos=ejecutar_con_reintentos):
                ok += 1
            else:
                errores += 1
    finally:
        try:
            obtener_sesion().cerrar()
        except Exception:
            pass

    print("Fin del procesamiento. OK: " + str(ok) + " | Errores: " + str(errores))
    fallos = tareas_con_error()
    return {"ok": ok, "errores": errores, "fallos": fallos, "capturas": obtener_capturas()}


#----------------------------------------------
# FUNCIONES DE DIAGNÓSTICO DEL NAVEGADOR
#----------------------------------------------
def diagnosticar(clave, termino="leche", marca=None, precio_minimo=None, precio_maximo=None):
    """Ejecuta una búsqueda de diagnóstico y muestra su resultado e inspección del DOM."""
    with sesion_automatizacion():
        supermercados = cargar_supermercados()
        datos = supermercados.get(clave)
        if not datos:
            print("Supermercado desconocido: " + clave)
            print("Opciones: " + ", ".join(supermercados.keys()))
            return

        print("Diagnosticando '" + clave + "' con el término '" + termino + "'")

        try:
            abrir_navegador()
            try:
                productos = buscar_producto(datos, termino, marca=marca,
                                            precio_minimo=precio_minimo, precio_maximo=precio_maximo)
                resumen = {"estado": "ok", "productos": productos}
            except Exception as error:
                resumen = {"estado": "error", "error": str(error)}
            resumen["dom"] = inspeccionar_busqueda(datos)
            print(json.dumps(resumen, ensure_ascii=False, indent=2))
            return resumen
        finally:
            try:
                obtener_sesion().cerrar()
            except Exception:
                pass
