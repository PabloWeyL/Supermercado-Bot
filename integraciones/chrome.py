"""Control de una ventana persistente de Chrome reservada para el robot."""

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ChromeRobot:
    def __init__(self, carpeta, puerto=9222, timeout=20, headless=False):
        self.carpeta = Path(carpeta)
        self.puerto = puerto
        self.timeout = timeout
        self.headless = headless
        self._puente = None
        self._secuencia = 0
        self._target_id = None

    def _http(self, ruta, metodo='GET'):
        with urlopen(Request(f'http://127.0.0.1:{self.puerto}' + ruta, method=metodo), timeout=2) as respuesta:
            return json.load(respuesta)

    def _paginas(self):
        try:
            return self._http('/json/list')
        except (URLError, OSError):
            return None

    def _elegir_pagina(self, paginas):
        pagina_id = None
        archivo = self.carpeta / 'sesion_chrome.json'
        if archivo.exists():
            try:
                pagina_id = json.loads(archivo.read_text(encoding='utf-8')).get('target_id')
            except (OSError, ValueError):
                pass
        return next((p for p in paginas if p.get('type') == 'page' and p['id'] == pagina_id),
                    next((p for p in paginas if p.get('type') == 'page'), None))

    def _lanzar(self):
        candidatos = [os.getenv('CHROME_PATH'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                      str(Path(os.getenv('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe'),
                      shutil.which('google-chrome'), shutil.which('chromium')]
        ejecutable = next((p for p in candidatos if p and Path(p).is_file()), None)
        if not ejecutable:
            raise RuntimeError('No se encontro Chrome; configura CHROME_PATH')
        self.carpeta.mkdir(parents=True, exist_ok=True)
        argumentos = [ejecutable, f'--remote-debugging-port={self.puerto}', '--remote-debugging-address=127.0.0.1',
                      '--user-data-dir=' + str((self.carpeta / 'chrome_robot').resolve()),
                      '--no-first-run', '--no-default-browser-check', '--start-maximized', 'about:blank']
        if self.headless:
            argumentos.append('--headless=new')
        subprocess.Popen(argumentos, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0,
                         start_new_session=os.name != 'nt')

    def abrir(self):
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
        paginas = self._paginas()
        reutilizada = paginas is not None
        if not reutilizada:
            self._lanzar()
            limite = time.monotonic() + self.timeout
            while paginas is None and time.monotonic() < limite:
                time.sleep(0.2)
                paginas = self._paginas()
            if paginas is None:
                raise RuntimeError('Chrome no habilito su conexion de automatizacion')
        pagina = self._elegir_pagina(paginas)
        if pagina is None:
            pagina = self._http('/json/new?' + quote('about:blank', safe=''), 'PUT')
        self._target_id = pagina['id']
        argumentos = [nodo, str(Path(__file__).with_name('chrome_cdp.cjs')),
                      pagina['webSocketDebuggerUrl'], str(int(self.timeout * 1000))]
        self._puente = subprocess.Popen(argumentos, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            if not self._leer().get('ready'):
                raise RuntimeError('Chrome no confirmo la conexion')
            self.maximizar()
            self.carpeta.mkdir(parents=True, exist_ok=True)
            (self.carpeta / 'sesion_chrome.json').write_text(json.dumps({'target_id': self._target_id}), encoding='utf-8')
        except Exception:
            self.close()
            raise
        return reutilizada

    def _leer(self):
        linea = self._puente.stdout.readline()
        if not linea:
            raise RuntimeError('Se perdio la conexion con Chrome')
        respuesta = json.loads(linea)
        if respuesta.get('bridgeError'):
            raise RuntimeError(respuesta['bridgeError'])
        return respuesta

    def llamar(self, metodo, parametros=None):
        if not self._puente or self._puente.poll() is not None:
            raise RuntimeError('No hay sesion conectada con Chrome')
        self._secuencia += 1
        self._puente.stdin.write(json.dumps({'id': self._secuencia, 'method': metodo, 'params': parametros or {}}) + '\n')
        self._puente.stdin.flush()
        respuesta = self._leer()
        if respuesta.get('error'):
            raise RuntimeError('Chrome: ' + respuesta['error'].get('message', str(respuesta['error'])))
        return respuesta.get('result', {})

    def maximizar(self):
        if not self.headless:
            ventana = self.llamar('Browser.getWindowForTarget', {'targetId': self._target_id})
            self.llamar('Browser.setWindowBounds', {'windowId': ventana['windowId'], 'bounds': {'windowState': 'normal'}})
            self.llamar('Browser.setWindowBounds', {'windowId': ventana['windowId'], 'bounds': {'windowState': 'maximized'}})
            self.llamar('Page.bringToFront')

    def dom(self, cuerpo):
        resultado = self.llamar('Runtime.evaluate', {'expression': '(function(){' + cuerpo + '})()',
                                                      'returnByValue': True, 'awaitPromise': True})
        if resultado.get('exceptionDetails'):
            raise RuntimeError('Error JavaScript: ' + resultado['exceptionDetails'].get('text', 'sin detalle'))
        return resultado.get('result', {}).get('value', '')

    @staticmethod
    def _elemento(selector):
        valor = json.dumps(selector)
        if selector.startswith(('/', '(')):
            return f'document.evaluate({valor},document,null,XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue'
        return 'document.querySelector(' + valor + ')'

    def present(self, selector):
        return bool(self.dom('return !!(' + self._elemento(selector) + ');'))

    def click(self, selector):
        return bool(self.dom('var e=' + self._elemento(selector) + '; if(!e)return false; e.focus(); e.click(); return true;'))

    def type(self, selector, texto):
        limpiar = texto.startswith('[clear]')
        if limpiar:
            texto = texto[len('[clear]'):]
        if not self.dom('var e=' + self._elemento(selector) + '; if(!e)return false; e.focus();' +
                        ('e.select();' if limpiar else '') + 'return true;'):
            return False
        self.llamar('Input.insertText', {'text': texto})
        return True

    def send(self, instruccion):
        # Compatibilidad con el evento Enter que utiliza el buscador existente.
        coincidencia = re.fullmatch(r"js chrome_step\('Input.dispatchKeyEvent', (.+)\);", instruccion)
        if not coincidencia:
            raise ValueError('Instruccion de navegador no soportada')
        self.llamar('Input.dispatchKeyEvent', json.loads(coincidencia.group(1)))
        return True

    def url(self, direccion=None):
        if direccion is None:
            return self.dom('return location.href;')
        respuesta = self.llamar('Page.navigate', {'url': direccion})
        if respuesta.get('errorText'):
            raise RuntimeError(respuesta['errorText'])
        return True

    def snap(self, objetivo, ruta):
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
                    puente.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    puente.terminate()
                    puente.wait(timeout=3)
            puente.stdout.close()
        return True
