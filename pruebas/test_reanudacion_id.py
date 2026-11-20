import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

with patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1"}):
    valores = dotenv_values(Path(__file__).resolve().parents[1]/".env.example")
    with patch.dict(os.environ, {k:v for k,v in valores.items() if v is not None}):
        import main


class PruebasIdYReanudacion(unittest.TestCase):
    def test_nuevo_id_y_misma_fila_con_un_correo_y_reporte_por_ejecucion(self):
        with tempfile.TemporaryDirectory() as temporal, ExitStack() as cambios:
            raiz = Path(temporal)
            entrada = raiz/'entrada.xlsx'
            entrada.write_bytes(b'prueba')
            db = raiz/'prueba.db'
            salida = raiz/'reportes/resultados.xlsx'
            for nombre, valor in {'EXCEL_SALIDA': salida, 'RUTA_HISTORICO': raiz/'historico',
                                  'MODULO_CORREO': True}.items():
                cambios.enter_context(patch.object(main.configuracion, nombre, valor))
            cambios.enter_context(patch.dict(os.environ, {'WEYLAND_CORREO_COORDINADOR': 'false'}))
            cambios.enter_context(patch.object(main.reportes, 'RUTA_REPORTES', salida.parent))
            cambios.enter_context(patch.object(main.reportes, 'EXCEL_SALIDA', salida))
            generar = cambios.enter_context(patch.object(main.reportes, 'generar_resultados_excel',
                                                         wraps=main.reportes.generar_resultados_excel))
            inicio = cambios.enter_context(patch.object(main, 'enviar_inicio'))
            correo = cambios.enter_context(patch.object(main.correo, 'enviar_correo_reporte', return_value=True))
            cambios.enter_context(patch.object(main.supermercados, 'sesion_automatizacion', nullcontext))
            cambios.enter_context(patch.object(main.supermercados, 'listar_archivos_entrada', return_value=[entrada]))

            def cargar(ruta):
                fila = main.base_datos.registrar_archivo(ruta, '2026-11-18')
                nuevas, existentes = main.base_datos.guardar_tareas_entrada(
                    fila, [({'producto': 'leche'}, ['tienda'])], '2026-11-18')
                return dict(archivos_leidos=1, archivos_error=0,
                            tareas_agregadas=nuevas, tareas_existentes=existentes)

            def completar(_):
                id_tarea, *_ = main.base_datos.tareas_pendientes()[0]
                main.base_datos.guardar_resultados(id_tarea, 'tienda', 'leche',
                    [{'nombre': 'prueba', 'precio': 10, 'url': ''}], 1, '2026-11-18')
                return dict(ok=1, errores=0)

            cambios.enter_context(patch.object(main.supermercados, 'cargar_excel_a_tabla_cola', side_effect=cargar))
            proceso = cambios.enter_context(patch.object(main.supermercados, 'procesar_cola_a_resultados'))
            with main.base_datos.ambito_base_datos(db), main.configuracion.ambito_identificacion():
                with main.configuracion.contexto() as primera:
                    proceso.side_effect = KeyboardInterrupt()
                    with self.assertRaises(KeyboardInterrupt):
                        main.ejecutar_local(preparado=True)
                identificador = primera['activacion_id']
                with closing(sqlite3.connect(db)) as con:
                    original, = con.execute("SELECT id,id_ejecucion FROM QUEUE_SUPERMERCADOS").fetchall()
                self.assertEqual(original[1], identificador)
                token = main.configuracion.IDENTIFICADOR_LOCAL.set(identificador)
                try:
                    with main.configuracion.contexto():
                        proceso.side_effect = completar
                        self.assertEqual(main.ejecutar_local(preparado=True)['estado'], 'completado')
                finally:
                    main.configuracion.IDENTIFICADOR_LOCAL.reset(token)
                with closing(sqlite3.connect(db)) as con:
                    self.assertEqual(con.execute("SELECT id,id_ejecucion FROM QUEUE_SUPERMERCADOS").fetchall(), [original])
                inicio.assert_called_once()
                correo.assert_called_once()
                generar.assert_called_once()
                self.assertTrue(salida.is_file())
                with main.configuracion.contexto() as segunda:
                    self.assertEqual(main.ejecutar_local(preparado=True)['estado'], 'completado')
                with closing(sqlite3.connect(db)) as con:
                    filas = con.execute("SELECT id_ejecucion,estado FROM QUEUE_SUPERMERCADOS ORDER BY id").fetchall()
                self.assertEqual(filas, [(identificador,'FINALIZADO'), (segunda['activacion_id'],'FINALIZADO')])
                self.assertEqual(len(list((raiz/'historico').glob('*.zip'))), 2)

    def test_cierre_aceptado_reusa_reporte_sin_generar_ni_enviar(self):
        with tempfile.TemporaryDirectory() as temporal, ExitStack() as cambios:
            raiz = Path(temporal)
            reporte = raiz/'reporte.xlsx'
            reporte.write_bytes(b'excel existente')
            entrada = raiz/'entrada.xlsx'
            identificador = '17112026_080000'
            cambios.enter_context(patch.dict(os.environ, {'WEYLAND_CORREO_COORDINADOR': 'false'}))
            cambios.enter_context(patch.object(main.configuracion, 'MODULO_CORREO', True))
            cambios.enter_context(patch.object(main.supermercados, 'sesion_automatizacion', nullcontext))
            smtp = cambios.enter_context(patch.object(main.correo, 'enviar_correo_reporte'))
            generar = cambios.enter_context(patch.object(main.reportes, 'generar_resultados_excel'))
            inicio = cambios.enter_context(patch.object(main, 'enviar_inicio'))
            cola = cambios.enter_context(patch.object(main.correo, 'obtener_cola')).return_value
            cola.cierres_confirmados.return_value = [{'solicitud_id': None, 'id_ejecucion': identificador}]
            with main.base_datos.ambito_base_datos(raiz/'db.sqlite'), main.configuracion.ambito_identificacion():
                main.configuracion.IDENTIFICADOR_LOCAL.set(identificador)
                with main.configuracion.contexto():
                    fila = main.base_datos.registrar_archivo(entrada, '2026-11-17')
                    main.base_datos.guardar_tareas_entrada(fila, [], '2026-11-17')
                    main.base_datos.procesar_archivo(entrada)
                    main.base_datos.cerrar_archivos([entrada], reporte, False)
                    self.assertEqual(main.ejecutar_local(preparado=True)['estado'], 'completado')
                    self.assertEqual(main.base_datos.archivos_por_ejecucion(identificador),
                                     [(str(entrada.resolve()), 'FINALIZADO')])
            smtp.assert_not_called()
            inicio.assert_not_called()
            generar.assert_not_called()
            self.assertEqual(reporte.read_bytes(), b'excel existente')
