import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

from integraciones.chrome import ChromeRobot


class SesionChromeTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory(prefix='rpa-chrome-tests-')
        self.addCleanup(self.temporal.cleanup)
        self.robot = ChromeRobot(self.temporal.name)
        self.paginas = [
            {'id': 'pestana-1', 'type': 'page', 'webSocketDebuggerUrl': 'ws://127.0.0.1/page/1'},
            {'id': 'pestana-2', 'type': 'page', 'webSocketDebuggerUrl': 'ws://127.0.0.1/page/2'},
        ]

    def conexion(self, paginas):
        puente = MagicMock()
        puente.stdout.readline.return_value = '{"ready":true}\n'
        return (
            patch.object(self.robot, '_paginas', side_effect=paginas),
            patch('integraciones.chrome.shutil.which', return_value='node'),
            patch('integraciones.chrome.subprocess.run', return_value=MagicMock(stdout='v24.0.0')),
            patch('integraciones.chrome.subprocess.Popen', return_value=puente),
            patch.object(self.robot, 'maximizar'),
        )

    def test_reutiliza_pestana_de_ejecucion_anterior_sin_abrir_chrome(self):
        Path(self.temporal.name, 'sesion_chrome.json').write_text(json.dumps({'target_id': 'pestana-2'}))
        a, b, c, d, e = self.conexion([self.paginas])
        with a, b, c, d as iniciar_puente, e as maximizar, patch.object(self.robot, '_lanzar') as lanzar, patch.object(self.robot, '_http') as http:
            self.assertTrue(self.robot.abrir())
        self.assertEqual(self.robot._target_id, 'pestana-2')
        lanzar.assert_not_called()
        http.assert_not_called()
        maximizar.assert_called_once()
        self.assertEqual(iniciar_puente.call_args.args[0][2], 'ws://127.0.0.1/page/2')

    def test_abre_chrome_solo_si_no_hay_sesion(self):
        a, b, c, d, e = self.conexion([None, self.paginas])
        with a, b, c, d, e as maximizar, patch.object(self.robot, '_lanzar') as lanzar, patch('integraciones.chrome.time.sleep'):
            self.assertFalse(self.robot.abrir())
        lanzar.assert_called_once()
        maximizar.assert_called_once()

    def test_pestana_cerrada_reutiliza_otra_del_robot(self):
        Path(self.temporal.name, 'sesion_chrome.json').write_text('{"target_id":"cerrada"}')
        self.assertEqual(self.robot._elegir_pagina(self.paginas)['id'], 'pestana-1')

    def test_sesion_activa_no_crea_otro_controlador(self):
        self.robot._puente = MagicMock()
        self.robot._puente.poll.return_value = None
        with patch.object(self.robot, 'llamar'), patch.object(self.robot, 'maximizar') as maximizar, patch.object(self.robot, '_lanzar') as lanzar:
            self.assertTrue(self.robot.abrir())
        lanzar.assert_not_called()
        maximizar.assert_called_once()

    def test_maximiza_y_trae_al_frente_la_ventana_correcta(self):
        self.robot._target_id = 'pestana-2'
        with patch.object(self.robot, 'llamar', return_value={'windowId': 17}) as comando:
            self.robot.maximizar()
        self.assertEqual(comando.call_args_list, [
            call('Browser.getWindowForTarget', {'targetId': 'pestana-2'}),
            call('Browser.setWindowBounds', {'windowId': 17, 'bounds': {'windowState': 'normal'}}),
            call('Browser.setWindowBounds', {'windowId': 17, 'bounds': {'windowState': 'maximized'}}),
            call('Page.bringToFront'),
        ])

    def test_headless_no_modifica_estado_de_ventana(self):
        self.robot.headless = True
        with patch.object(self.robot, 'llamar') as comando:
            self.robot.maximizar()
        comando.assert_not_called()

    def test_al_terminar_conserva_chrome_abierto(self):
        puente = self.robot._puente = MagicMock()
        puente.poll.return_value = None
        with patch.object(self.robot, 'llamar') as comando:
            self.robot.close()
        comando.assert_not_called()
        puente.stdin.close.assert_called_once()
        puente.wait.assert_called_once()
        self.assertIsNone(self.robot._puente)

    def test_error_de_conexion_se_informa(self):
        self.robot._puente = MagicMock()
        self.robot._puente.poll.return_value = None
        self.robot._puente.stdout.readline.return_value = '{"id":1,"error":{"message":"Target closed"}}\n'
        with self.assertRaisesRegex(RuntimeError, 'Target closed'):
            self.robot.llamar('Runtime.evaluate')

    def test_sin_node_no_abre_chrome(self):
        with patch('integraciones.chrome.shutil.which', return_value=None), patch.object(self.robot, '_lanzar') as lanzar:
            with self.assertRaisesRegex(RuntimeError, 'Node.js 22'):
                self.robot.abrir()
        lanzar.assert_not_called()


if __name__ == '__main__':
    unittest.main()
