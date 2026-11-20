import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import base_datos


class PruebasBaseDatos(unittest.TestCase):
    def test_migracion_v3_y_resumen_compatible(self):
        with tempfile.TemporaryDirectory() as temporal, patch(
            'config.base_datos.conectar_sqlite', side_effect=sqlite3.connect
        ):
            ruta = Path(temporal) / 'supermercados.db'
            entrada = Path(temporal) / 'productos.xlsx'
            conexion = sqlite3.connect(ruta)
            with conexion:
                base_datos.crear_tablas(conexion)
                conexion.execute('INSERT INTO details(id, archivo, ruta) VALUES(1, ?, ?)',
                                 (entrada.name, str(entrada)))
                conexion.execute('INSERT INTO cola(id, detail_id, producto, supermercado) VALUES(2, 1, ?, ?)',
                                 ('leche', 'supermercado-de-prueba'))
                conexion.execute('INSERT INTO resultados(cola_id, nombre) VALUES(2, ?)', ('resultado-de-prueba',))
            conexion.close()

            anterior = base_datos.resumen_ejecucion(ruta)
            with base_datos.ambito_base_datos(ruta), base_datos.ambito_archivos([entrada]):
                self.assertEqual(base_datos.tareas_pendientes()[0][0], 2)
                self.assertEqual(base_datos.datos_reporte()[0][0][1], 'resultado-de-prueba')
            self.assertEqual(base_datos.resumen_ejecucion(ruta), anterior)
            with base_datos.ambito_base_datos(ruta):
                self.assertEqual(base_datos.tareas_pendientes()[0][0], 2)

    def test_cierre_finaliza_solo_tras_confirmar_entrega(self):
        with tempfile.TemporaryDirectory() as temporal, patch(
            'config.base_datos.conectar_sqlite', side_effect=sqlite3.connect
        ):
            base = Path(temporal) / 'supermercados.db'
            entrada = Path(temporal) / 'productos.xlsx'
            reporte = Path(temporal) / 'reporte.xlsx'
            with base_datos.ambito_base_datos(base), base_datos.ambito_archivos([entrada]):
                from config import configuracion

                with configuracion.contexto() as contexto:
                    identificador = contexto['activacion_id']
                    fila = base_datos.registrar_archivo(entrada, '2026-11-18')
                    self.assertEqual(base_datos.obtener_estado_fallo(identificador)[0], 'PENDIENTE')
                    base_datos.guardar_tareas_entrada(
                        fila, [({'producto': 'leche'}, ['tienda-de-prueba'])], '2026-11-18'
                    )
                    self.assertEqual(base_datos.obtener_estado_fallo(identificador),
                                     ('EN_EJECUCION', [('PENDIENTE', 1)]))
                    base_datos.procesar_archivo(entrada)
                    self.assertEqual(base_datos.obtener_estado_fallo(identificador)[0], 'EN_EJECUCION')
                    tarea = base_datos.tareas_pendientes()[0][0]
                    base_datos.guardar_resultados(tarea, 'tienda-de-prueba', 'leche',
                                                 [{'nombre': 'prueba', 'precio': 10, 'url': ''}], 1, '2026-11-18')
                    base_datos.procesar_archivo(entrada)
                    base_datos.cerrar_archivos([entrada], reporte, False)
                    self.assertEqual(base_datos.obtener_estado_fallo(identificador),
                                     ('PROCESADO', [('LEIDO', 1)]))
                    self.assertEqual(base_datos.archivos_con_cierre_pendiente(), {entrada.resolve()})
                    base_datos.confirmar_cierre_por_correo(identificador)
                    self.assertEqual(base_datos.obtener_estado_fallo(identificador)[0], 'FINALIZADO')
                    self.assertEqual(base_datos.archivos_con_cierre_pendiente(), set())
