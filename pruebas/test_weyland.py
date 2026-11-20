import ast
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dotenv import dotenv_values

with patch.dict(os.environ, {'PYTHON_DOTENV_DISABLED': '1'}):
    plantilla = Path(__file__).resolve().parents[1] / '.env.example'
    with patch.dict(os.environ, {k: v for k, v in dotenv_values(plantilla).items() if v is not None}):
        from integraciones import weyland
        from config.archivos import buscar_carpeta_ejecucion_web


class PruebasWeyland(unittest.TestCase):
    def test_sesion_usa_api_key_y_las_operaciones_usan_bearer(self):
        clave = 'wak_' + '0' * 12 + '_' + 'x' * 43
        opciones = {'BACKEND_API_URL': 'https://automate.example/api', 'BACKEND_MAQUINA_ID': '7'}
        with self.assertRaisesRegex(ValueError, 'BACKEND_API_KEY'):
            weyland.configuracion_desde_valores(opciones)
        config = weyland.configuracion_desde_valores({**opciones, 'BACKEND_API_KEY': clave})
        self.assertNotIn(clave, repr(config))
        http = Mock()
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {'access_token': 'acceso-test', 'refresh_token': 'refresh-test',
                                        'token_type': 'Bearer', 'expires_in': 300, 'sesion_id': 3,
                                        'maquina_id': 11}
        respuesta.__enter__ = Mock(return_value=respuesta)
        respuesta.__exit__ = Mock(return_value=False)
        http.request.side_effect = [respuesta, Mock(status_code=200)]
        sesion = weyland.crear_sesion(config, http)
        weyland.login(sesion)
        weyland.solicitar(sesion, 'GET', '/bot/prueba')
        login, operacion = http.request.call_args_list
        self.assertEqual(login.args, ('POST', 'https://automate.example/api/bot/sesion'))
        self.assertEqual(login.kwargs['json'], {'api_key': clave, 'resolver_maquina_por_ip': True})
        self.assertEqual(sesion._weyland['maquina_id'], 11)
        self.assertEqual(operacion.kwargs['headers'], {'Authorization': 'Bearer acceso-test'})
        self.assertIsNone(operacion.kwargs['json'])

    def test_compatibilidad_con_maquina_explicita_confirmada_en_el_jwt(self):
        clave = 'wak_' + '0' * 12 + '_' + 'x' * 43
        valores = {'BACKEND_API_URL': 'https://automate.example/api', 'BACKEND_API_KEY': clave}
        for maquina, resultado in ((3, 3), (1, None)):
            with self.subTest(maquina=maquina):
                config = weyland.configuracion_desde_valores(valores, maquina_id=maquina)
                http = Mock()
                respuesta = Mock(status_code=200)
                respuesta.json.return_value = {'access_token': 'a.eyJtYXF1aW5hIjozfQ.c',
                    'refresh_token': 'refresh-ficticio', 'token_type': 'Bearer', 'expires_in': 300,
                    'sesion_id': 7}
                respuesta.__enter__ = Mock(return_value=respuesta)
                respuesta.__exit__ = Mock(return_value=False)
                http.request.return_value = respuesta
                sesion = weyland.crear_sesion(config, http)
                if resultado is None:
                    with self.assertRaisesRegex(RuntimeError, 'no coincide'):
                        weyland.login(sesion)
                else:
                    weyland.login(sesion)
                self.assertEqual(sesion._weyland['maquina_id'], resultado)
                http.request.assert_called_once()
                self.assertEqual(http.request.call_args.kwargs['json'], {'api_key': clave, 'maquina_id': maquina})

    def test_maquina_guardada_solo_si_weyland_la_confirma_y_para_el_mismo_backend(self):
        clave = 'wak_' + '0' * 12 + '_' + 'x' * 43
        valores = {'BACKEND_API_URL': 'https://automate.example/api', 'BACKEND_API_KEY': clave}
        cliente = Mock()
        cliente.config = {'url': valores['BACKEND_API_URL'], 'bot_maquina_id': 3}
        cliente.sesion._weyland = {'maquina_id': 1}
        with tempfile.TemporaryDirectory() as temporal, patch.object(weyland, 'leer_valores', return_value=valores), \
                patch.object(weyland, 'consultar_ipv4_publica', return_value='8.8.4.4'):
            raiz = Path(temporal)
            ruta = raiz / '.runtime/maquina_weyland.json'
            archivo = raiz / '.env'
            self.assertIsNone(weyland.crear_cliente_api(archivo).config['bot_maquina_id'])
            with self.assertRaises(RuntimeError):
                weyland.guardar_maquina_verificada(raiz, cliente, 3, "8.8.4.4")
            self.assertFalse(ruta.exists())
            cliente.sesion._weyland['maquina_id'] = 3
            weyland.guardar_maquina_verificada(raiz, cliente, 3, "8.8.4.4")
            self.assertNotIn(clave, ruta.read_text(encoding='utf-8'))
            self.assertEqual(weyland.crear_cliente_api(archivo).config['bot_maquina_id'], 3)
            self.assertEqual(weyland.crear_cliente_api(archivo, maquina_id=7).config['bot_maquina_id'], 7)
            valores['BACKEND_API_URL'] = 'https://otro.example/api'
            with self.assertRaisesRegex(RuntimeError, 'otro backend'):
                weyland.crear_cliente_api(archivo)

    def test_ipv4_incompatible_bloquea_la_toma_y_el_trabajo_de_otra_red(self):
        clave = 'wak_' + '0' * 12 + '_' + 'x' * 43
        config = {'BACKEND_API_URL': 'https://automate.example/api', 'BACKEND_API_KEY': clave}
        transporte = Mock()
        cliente = weyland.crear_cliente_cola(config, session=transporte, maquina_id=3)
        cliente.ipv4_esperada = '8.8.4.4'
        with patch.object(weyland, 'consultar_ipv4_publica', return_value='1.1.1.1'):
            with self.assertRaisesRegex(RuntimeError, 'no coincide'):
                cliente.verificar_ipv4()
        with self.assertRaisesRegex(RuntimeError, 'antes de tomar'):
            cliente.tomar_siguiente(3, 'a' * 32)
        transporte.request.assert_not_called()
        datos = {'ejecucion_id': 40051, 'id_externo': 'a' * 32,
                 'maquina': {'id': 3, 'direccion_ip': '1.1.1.1'},
                 'proceso': {'codigo': 'SUPERMERCADOS'}, 'parametros': {}, 'inputs': []}
        with patch.object(weyland, 'consultar_ipv4_publica', return_value='8.8.4.4'):
            cliente.verificar_ipv4()
        with self.assertRaisesRegex(RuntimeError, 'IPv4 de la asignacion'):
            cliente.validar_asignacion(datos, 3, 'a' * 32, {'SUPERMERCADOS'})
        datos['maquina']['direccion_ip'] = '8.8.4.4'
        self.assertEqual(cliente.validar_asignacion(datos, 3, 'a' * 32, {'SUPERMERCADOS'})['ejecucion_id'], 40051)
        transporte.request.assert_not_called()

    def test_cola_configurada_usa_la_maquina_de_la_sesion_y_conserva_su_carpeta(self):
        valores = {'BACKEND_CONTRATO': 'cola_actual', 'BACKEND_AUTH_MODE': 'weyland',
                   'BACKEND_MAQUINA_ID': '7'}
        cliente = Mock(contrato='cola_actual', config={'bot_auth_mode': 'jwt'})
        procesos = {'Prueba': Mock()}
        with tempfile.TemporaryDirectory() as temporal, patch.object(weyland, 'ejecutar_cola') as ejecutar:
            cliente.verificar.return_value = 11
            revisar = Mock()
            weyland.ejecutar_cola_configurada(lambda: cliente, valores, temporal, procesos,
                                             revisar_trabajos=revisar)
            trabajos = Path(temporal).resolve() / 'temporales/weyland/cola_actual/maquina_11'
            revisar.assert_called_once_with(trabajos)
            ejecutar.assert_called_once_with(cliente, 11, trabajos, procesos, notificar=None)
            ejecutar.reset_mock()
            cliente.verificar.return_value = None
            with self.assertRaises(ValueError):
                weyland.ejecutar_cola_configurada(lambda: cliente, valores, temporal, procesos)
            ejecutar.assert_not_called()

    def test_login_sin_id_de_maquina_no_consulta_la_cola(self):
        clave = 'wak_' + '0' * 12 + '_' + 'x' * 43
        config = weyland.configuracion_desde_valores({'BACKEND_API_URL': 'https://automate.example/api',
                                                     'BACKEND_API_KEY': clave})
        http = Mock()
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {'access_token': 'acceso-test', 'refresh_token': 'refresh-test',
                                        'token_type': 'Bearer', 'expires_in': 300, 'sesion_id': 3}
        respuesta.__enter__ = Mock(return_value=respuesta)
        respuesta.__exit__ = Mock(return_value=False)
        http.request.return_value = respuesta
        sesion = weyland.crear_sesion(config, http)
        with self.assertRaisesRegex(RuntimeError, 'no confirmo la maquina'):
            weyland.verificar(sesion)
        http.request.assert_called_once()
        self.assertEqual(http.request.call_args.args[0], 'POST')
        self.assertIsNone(sesion._weyland['access_token'])

    def test_busca_evidencia_web_por_solicitud_y_rechaza_duplicados(self):
        with tempfile.TemporaryDirectory() as temporal:
            base = Path(temporal).resolve()
            anterior = base / 'cola_actual/maquina_11/ejecuciones/123'
            self.assertIsNone(buscar_carpeta_ejecucion_web(base, 123))
            anterior.mkdir(parents=True)
            self.assertEqual(buscar_carpeta_ejecucion_web(base, 123), anterior)
            (base / 'cola_actual/maquina_12/ejecuciones/123').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'varias maquinas'):
                buscar_carpeta_ejecucion_web(base, 123)

    def test_worker_registra_id_antes_de_ejecutar_y_sube_un_solo_reporte(self):
        for reproceso in (None, 122):
            with self.subTest(reproceso=reproceso), tempfile.TemporaryDirectory() as temporal:
                journal = Path(temporal) / weyland.ARCHIVO_COORDINADOR
                cliente = Mock()
                cliente.config = {'url': 'https://automate.example/api'}
                cliente.validar_asignacion.return_value = {
                    'ejecucion_id': 123, 'proceso_codigo': 'Prueba', 'inputs': [], 'reproceso_de': reproceso,
                }

                def confirmar(ruta, *, json):
                    self.assertEqual(ruta, '/bot/ejecuciones/123/id-ejecucion')
                    valor = json['id_ejecucion']
                    self.assertRegex(valor, r'^[0-9]{8}_[0-9]{6}$')
                    weyland.datetime.strptime(valor, '%d%m%Y_%H%M%S')
                    guardado = weyland.leer(journal)
                    self.assertEqual(guardado['id_ejecucion_bot'], valor)
                    self.assertTrue(guardado['id_ejecucion_bot_pendiente'])
                    respuesta = Mock(status_code=200)
                    respuesta.json.return_value = {'ejecucion_id': 123, 'id_ejecucion_bot': valor}
                    respuesta.__enter__ = Mock(return_value=respuesta)
                    respuesta.__exit__ = Mock(return_value=False)
                    return respuesta

                def generar_reporte(carpeta, entrada, parametros):
                    self.assertTrue(weyland.leer(journal)['id_ejecucion_bot_confirmado'])
                    reporte = carpeta / 'resultado.csv'
                    reporte.write_text('prueba', encoding='utf-8')
                    return weyland.crear_resultado_bot((reporte,))

                cliente.post.side_effect = confirmar
                negocio = cliente.negocio
                negocio.side_effect = generar_reporte
                procesos = {'Prueba': weyland.crear_proceso_bot(negocio, False, informar_estados=True)}
                with patch('config.configuracion.id_ejecucion', create=True):
                    estado = weyland.ejecutar_cola(cliente, 7, temporal, procesos)
                self.assertEqual(estado['fase'], 'COMPLETADA')
                self.assertEqual(estado['ejecucion_id'], 123)
                cliente.tomar_siguiente.assert_called_once_with(7, estado['id_externo'])
                cliente.post.assert_called_once()
                inicio = cliente.informar_estado.call_args_list[1]
                self.assertEqual(inicio.args[:2], (123, 'EN_REPROCESO' if reproceso else 'EN_EJECUCION'))
                cliente.entregar_resultado.assert_called_once()
                self.assertEqual(cliente.entregar_resultado.call_args.args[0], 123)
                llamadas = [llamada[0] for llamada in cliente.method_calls]
                self.assertEqual(llamadas, ['tomar_siguiente', 'validar_asignacion', 'informar_estado',
                                             'post', 'informar_estado', 'negocio', 'entregar_resultado'])
                weyland.registrar_id_ejecucion_bot(cliente, weyland.leer(journal), journal)
                cliente.post.assert_called_once()

    def test_id_incierto_reanuda_checkpoint_anterior_sin_nueva_toma_o_post(self):
        cliente = Mock()
        cliente.config = {'url': 'https://automate.example/api'}
        cliente.post.side_effect = weyland.crear_error_weyland('Tiempo de espera agotado', tipo='RED')
        procesos = {'Prueba': weyland.crear_proceso_bot(cliente.negocio, False, informar_estados=True)}
        anterior = {'version': 1, 'contrato': 'cola_actual', 'backend': cliente.config['url'],
                    'cuenta_sha256': 'a' * 64, 'maquina_id': 7, 'codigos': ['Prueba'],
                    'id_externo': 'b' * 32, 'fase': 'PREPARADO', 'informar_estados': True,
                    'ejecucion_id': 123, 'solicitud_id': 123, 'trabajo': {'proceso_codigo': 'Prueba'}}
        with tempfile.TemporaryDirectory() as temporal:
            journal = Path(temporal) / weyland.ARCHIVO_COORDINADOR
            weyland.guardar_json(journal, anterior)
            with self.assertRaisesRegex(RuntimeError, 'Tiempo de espera agotado'):
                weyland.ejecutar_cola(cliente, 7, temporal, procesos)
            guardado = weyland.leer(journal)
            self.assertEqual(guardado['autenticacion_bot'], 'api_key')
            self.assertNotIn('cuenta_sha256', guardado)
            self.assertEqual(guardado['id_externo'], anterior['id_externo'])
            self.assertEqual(guardado['fase'], 'PREPARADO')
            self.assertTrue(guardado['id_ejecucion_bot_pendiente'])
            with self.assertRaisesRegex(RuntimeError, 'Registro de ID_EJECUCION incierto'):
                weyland.ejecutar_cola(cliente, 7, temporal, procesos)
            self.assertEqual(weyland.leer(journal), guardado)
        cliente.tomar_siguiente.assert_not_called()
        cliente.post.assert_called_once()
        cliente.informar_estado.assert_not_called()
        cliente.negocio.assert_not_called()
        cliente.entregar_resultado.assert_not_called()

    def test_post_incierto_no_se_repite_y_conserva_checkpoint(self):
        cliente = Mock()
        cliente.informar_estado.side_effect = weyland.crear_error_weyland('Tiempo de espera agotado')
        cliente.consultar_ejecucion.return_value = {}
        estado = {'ejecucion_id': 123, 'id_externo': 'solicitud-de-prueba'}
        nuevo = 'EN_EJECUCION'
        with tempfile.TemporaryDirectory() as temporal:
            journal = Path(temporal) / 'estado.json'
            with self.assertRaises(RuntimeError):
                weyland.publicar_estado(cliente, estado, journal, nuevo, 'Prueba')
            guardado = json.loads(journal.read_text(encoding='utf-8'))
            self.assertEqual(guardado['estado_bot_pendiente']['estado'], nuevo)
            with self.assertRaises(RuntimeError):
                weyland.publicar_estado(cliente, guardado, journal, nuevo, 'Prueba')
            cliente.informar_estado.assert_called_once()
            cliente.consultar_ejecucion.assert_called_once_with(123)
            self.assertIn('estado_bot_pendiente', json.loads(journal.read_text(encoding='utf-8')))

    def test_estado_remoto_confirmado_evitar_reenvio(self):
        cliente = Mock()
        nuevo = 'EN_EJECUCION'
        cliente.consultar_ejecucion.return_value = {
            'id_externo': 'solicitud-de-prueba',
            'estado_bot': nuevo,
            'estado': weyland.SISTEMA_POR_ESTADO[nuevo],
        }
        estado = {'ejecucion_id': 123, 'id_externo': 'solicitud-de-prueba',
                  'estado_bot_pendiente': {'estado': nuevo}}
        with tempfile.TemporaryDirectory() as temporal:
            journal = Path(temporal) / 'estado.json'
            weyland.publicar_estado(cliente, estado, journal, nuevo, 'Prueba')
            cliente.informar_estado.assert_not_called()
            guardado = json.loads(journal.read_text(encoding='utf-8'))
            self.assertEqual(guardado['estado_bot_publicado'], nuevo)
            self.assertNotIn('estado_bot_pendiente', guardado)

    def test_codigo_de_produccion_solo_admite_filtro_de_logging_como_clase(self):
        raiz = Path(__file__).resolve().parents[1]
        fuentes = (raiz / 'main.py', *(raiz / 'config').glob('*.py'),
                   *(raiz / 'integraciones').glob('*.py'))
        for fuente in fuentes:
            modulo = ast.parse(fuente.read_text(encoding='utf-8-sig'), filename=str(fuente))
            clases = [nodo for nodo in ast.walk(modulo) if isinstance(nodo, ast.ClassDef)]
            with self.subTest(fuente=fuente.name):
                for clase in clases:
                    self.assertEqual(clase.name, 'FiltroContexto')
                    self.assertEqual([ast.unparse(base) for base in clase.bases], ['logging.Filter'])
