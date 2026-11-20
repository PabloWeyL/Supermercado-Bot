import io
import logging
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dotenv import dotenv_values

with patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1"}):
    plantilla = Path(__file__).resolve().parents[1] / ".env.example"
    with patch.dict(os.environ, {k: v for k, v in dotenv_values(plantilla).items() if v is not None}):
        import main
        from config import fallos


class PruebasIdCerrado(unittest.TestCase):
    def test_id_olvidado_detiene_dos_arranques_sin_mutar_queue(self):
        with tempfile.TemporaryDirectory() as temporal, ExitStack() as cambios:
            raiz = Path(temporal)
            db = raiz / "prueba.db"
            identificador = "17112026_080000"
            with closing(sqlite3.connect(db)) as conexion, conexion:
                conexion.execute("CREATE TABLE QUEUE_SUPERMERCADOS (id_ejecucion TEXT, estado TEXT)")
                conexion.execute("INSERT INTO QUEUE_SUPERMERCADOS VALUES (?, ?)", (identificador, "FINALIZADO"))
            antes = db.read_bytes()
            logger = Mock()
            for nombre, valor in {
                "RUTA_BASE": raiz, "RUTA_DB_SQLITE": db,
                "ID_EJECUCION_ENV": identificador, "BACKEND_ACTIVO": True,
            }.items():
                cambios.enter_context(patch.object(main.configuracion, nombre, valor))
            cambios.enter_context(patch.object(main.configuracion, "obtener_logger", return_value=logger))
            cambios.enter_context(patch.object(main.configuracion, "configurar_logging"))
            bloqueados = [cambios.enter_context(patch.object(main, nombre)) for nombre in (
                "preparar_proceso", "ejecutar_web", "ejecutar_local", "enviar_inicio")]
            bloqueados.append(cambios.enter_context(patch.object(main.supermercados, "listar_archivos_entrada")))
            comandos = ([], ["procesar"], ["cargar"], ["reporte"], ["web-procesar"])
            for comando in comandos:
                for _ in range(2):
                    self.assertEqual(main.main(comando), 0)
                self.assertEqual(db.read_bytes(), antes)
            self.assertEqual(logger.info.call_args_list[-1].args,
                             ("LA EJECUCIÓN %s YA ESTÁ %s", identificador, "FINALIZADO"))
            with closing(sqlite3.connect(db)) as conexion, conexion:
                conexion.execute("UPDATE QUEUE_SUPERMERCADOS SET estado=?", ("ERROR",))
            antes = db.read_bytes()
            for comando in comandos:
                self.assertEqual(main.main(comando), 0)
                self.assertEqual(db.read_bytes(), antes)
            self.assertEqual(logger.info.call_args_list[-1].args,
                             ("LA EJECUCIÓN %s YA ESTÁ %s", identificador, "ERROR"))
            for mock in bloqueados:
                mock.assert_not_called()

    def test_fallo_solo_incluye_contexto_permitido(self):
        with tempfile.TemporaryDirectory() as temporal, patch.object(fallos, "RUTA_BASE", Path(temporal)):
            error = RuntimeError("mensaje privado con token")
            error.id_ejecucion_local = datetime.now().strftime("%d%m%Y_%H%M%S")
            ruta = fallos.registrar_fallo("ACME", error, 2, {
                "extension_entrada": ".xlsx",
                "estado_queue": "ERROR",
                "conteo_details": {"ERROR": 1, "evento privado": 2},
                "respuesta_externa": "cuerpo privado",
            }, ["texto privado"])
            texto = ruta.read_text(encoding="utf-8")
            self.assertIn("Duración:", texto)
            self.assertIn("Extensión de entrada: .xlsx", texto)
            self.assertIn("Estado de Queue: ERROR", texto)
            self.assertIn("Details ERROR: 1", texto)
            self.assertIn("Reproducción controlada", texto)
            self.assertNotIn("privad", texto)

    def test_diagnostico_y_logger_sanitizan_traza_y_extracto(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            ruta_log = raiz / "bot.log"
            identificador = "19112026_110000"
            centinela = "persona@example.test token=secreto-prueba C:/Users/UsuarioPrivado/factura.xlsx"
            registros = [
                f"2026-11-19 11:00:{segundo:02d} [ERROR] [BOT] [ID:{identificador}] [PRUEBA] {centinela}"
                for segundo in range(31)
            ]
            registros.append("2026-11-19 11:01:00 [ERROR] [BOT] [ID:OTRA] [PRUEBA] otro mensaje")
            ruta_log.write_text("\n".join(registros), encoding="utf-8")
            ruta_codigo = raiz / "integraciones/proceso.py"
            ajustes = SimpleNamespace(NOMBRE_PROCESO="BOT", RUTA_LOG_ACTUAL=ruta_log)
            salida = io.StringIO()
            try:
                exec(compile("raise RuntimeError(centinela)", str(ruta_codigo), "exec"), {"centinela": centinela})
            except RuntimeError as error:
                error.id_ejecucion_local = identificador
                with patch.object(fallos, "RUTA_BASE", raiz), patch.dict(
                        "sys.modules", {"config.configuracion": ajustes, "config.base_datos": None}):
                    informe = fallos.registrar_fallo("PRUEBA", error, 1, {"estado_queue": "ERROR"})
                    handler = logging.StreamHandler(salida)
                    handler.addFilter(fallos.filtrar_excepcion)
                    logger = logging.Logger("BOT", logging.INFO)
                    logger.addHandler(handler)
                    logger.exception("FALLO CONTROLADO")
            self.assertIsNotNone(informe)
            contenido = informe.read_text(encoding="utf-8")
            self.assertIn("integraciones/proceso.py:1", contenido)
            self.assertIn("Estado de Queue: ERROR", contenido)
            self.assertEqual(contenido.count(f"[ERROR] [ID:{identificador}]"), 30)
            self.assertNotIn("11:00:00 [ERROR]", contenido)
            for protegido in ("persona@example.test", "secreto-prueba", "UsuarioPrivado",
                              "factura.xlsx", "otro mensaje", str(raiz)):
                self.assertNotIn(protegido, contenido)
                self.assertNotIn(protegido, salida.getvalue())
            self.assertIn("TRAZA SANITIZADA: RuntimeError", salida.getvalue())
