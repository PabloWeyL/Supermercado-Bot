import json
import os
from contextlib import closing
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import unittest
from unittest.mock import call, patch

from openpyxl import Workbook, load_workbook


ROOT = Path(__file__).resolve().parents[1]


def setUpModule():
    # Aislar las rutas y valores de configuracion usados por las pruebas.
    global sandbox, bd, cola, productos, reportes, scraping, supermercados
    sandbox = tempfile.TemporaryDirectory(prefix="rpa-tests-")
    variables = {
        "RUTA_BASE": sandbox.name,
        "DB_PATH": str(Path(sandbox.name) / "inicio.db"),
        "MAX_REINTENTOS": "3", "MAX_REINTENTOS_CORREO": "3",
        "RETENCION_LOGS_MESES": "6",
    }
    with patch.dict(os.environ, variables), patch("dotenv.load_dotenv", return_value=False):
        from config import base_datos as bd
        from integraciones import cola, productos, reportes, scraping, supermercados


def tearDownModule():
    sandbox.cleanup()


def unimarc_card(nombre, oferta="", normal="", condicion=""):
    return (
        '<section id="shelf__vertical--' + nombre + '">' + condicion
        + '<a href="/product/' + nombre + '"><p class="Shelf_nameProduct__hash">'
        + nombre + '</p></a>'
        + ('<p id="listPrice__offerPrice--discountprice-' + nombre + '">' + oferta + '</p>' if oferta else '')
        + ('<p id="listPrice__offerPrice--listprice-' + nombre + '">' + normal + '</p>' if normal else '')
        + '</section>'
    )


def tottus_card_id(producto_id, nombre, precios):
    return (
        '<a id="testId-pod-' + str(producto_id) + '" href="/product/' + str(producto_id) + '">'
        + '<div class="jsx-1901152073 jsx-3413643269">'
        + '<b id="testId-pod-displaySubTitle-' + str(producto_id) + '">' + nombre + '</b>'
        + '<div id="testId-pod-prices-' + str(producto_id) + '">' + precios + '</div></div></a>'
    )


class ExtractoresDOMTests(unittest.TestCase):
    def test_diagnostico_dom_cuenta_marcas_y_lee_el_buscador(self):
        config = supermercados.cargar_supermercados()["unimarc"]
        html = '<input id="search-header__input" value="leche">'
        html += unimarc_card("Leche entera", normal="$1.350").replace('</section>', '<p class="Shelf_brandText__vmuWJ">Colun</p></section>')
        html += unimarc_card("Leche sin marca", normal="$1.500")
        with patch.object(scraping, "evaluar_js", return_value="{}") as evaluar:
            scraping.inspeccionar_busqueda(config)
        payload = [{"html": html, "script": evaluar.call_args.args[0]}]
        run = subprocess.run(["node", str(ROOT / "tests" / "browser_dom.cjs")], input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        estado = json.loads(json.loads(run.stdout)[0]["result"])
        self.assertEqual((estado["tarjetas"], estado["tarjetas_con_marca"]), (2, 1))
        self.assertEqual(estado["busqueda_en_campo"], "leche")
        self.assertEqual(estado["muestras"][0]["marca"], "Colun")

    def test_marcas_extraidas_del_dom_y_filtradas_en_tres_supermercados(self):
        from integraciones.filtros import cumple_filtros
        lider = '<div data-item-id="1"><button data-automation-id="add-to-cart" aria-label="Agregar al carro - Leche entera"></button><div data-automation-id="product-price">$1.350</div><div class="mb1 mt2 b f6 black mr1 lh-copy">Colun</div><a href="/product/leche">Leche</a></div>'
        unimarc = unimarc_card("Leche entera", normal="$1.350").replace('</section>', '<p class="Shelf_brandText__vmuWJ">Colun</p></section>')
        tottus = tottus_card_id(1, "Leche entera", '<span class="line-height-22">$1.350</span>').replace('<div id="testId-pod-prices-1">', '<b class="pod-title title-rebrand">Colun</b><div id="testId-pod-prices-1">')
        casos = [("lider", lider), ("unimarc", unimarc), ("tottus", tottus)]
        payload = [{"html": html, "script": scraping.obtener_extractor({"extractor": clave})} for clave, html in casos]
        run = subprocess.run(["node", str(ROOT / "tests" / "browser_dom.cjs")], input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        for (clave, _), resultado in zip(casos, json.loads(run.stdout)):
            with self.subTest(supermercado=clave):
                item = json.loads(resultado["result"])[0]
                self.assertEqual(item["marca"], "Colun")
                self.assertTrue(cumple_filtros(item, 1350, marca="COLÚN"))
                self.assertFalse(cumple_filtros(item, 1350, marca="Soprole"))
                self.assertTrue(cumple_filtros(item, 1350))

    def test_selectores_de_extraccion_se_toman_de_la_configuracion(self):
        config = supermercados.cargar_supermercados()["tottus"]
        config["extractor"].update({
            "tarjeta": "a.producto-configurado", "nombre": ".nombre-configurado", "precio": ".precio-configurado", "marca": ".marca-configurada",
        })
        html = '<a class="producto-configurado" href="/product/ejemplo"><b class="marca-configurada">Soprole</b><b class="nombre-configurado">Leche configurada</b><div id="testId-pod-prices-9"><span class="precio-configurado">$990</span></div></a>'
        payload = [{"html": html, "script": scraping.obtener_extractor(config)}]
        run = subprocess.run(
            ["node", str(ROOT / "tests" / "browser_dom.cjs")], input=json.dumps(payload),
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        resultados = json.loads(json.loads(run.stdout)[0]["result"])
        self.assertEqual(resultados, [{"ok": True, "nombre": "Leche configurada", "precio": "$990", "url": "https://example.test/product/ejemplo", "marca": "Soprole"}])

    def test_enter_nativo_dispara_keydown_y_envia_formulario(self):
        with patch.object(scraping, "r") as robot:
            robot.click.return_value = True
            robot.send.return_value = True
            scraping.enviar_enter("//input[@id='testId-SearchBar-Input']")
        eventos = [json.loads(args.args[0].split("Input.dispatchKeyEvent', ", 1)[1][:-2]) for args in robot.send.call_args_list]
        html = (
            '<form onsubmit="event.preventDefault(); window.enviado=true;">'
            '<input id="testId-SearchBar-Input" value="leche" '
            'onkeydown="window.tecla=event.key;window.confiable=event.isTrusted;" '
            'onkeyup="window.solto=event.key;"><button type="submit">Buscar</button></form>'
        )
        acciones = [{"method": "Runtime.evaluate", "params": {"expression": "document.getElementById('testId-SearchBar-Input').focus()"}}]
        acciones += [{"method": "Input.dispatchKeyEvent", "params": evento} for evento in eventos]
        payload = [{"html": html, "actions": acciones, "script": "return JSON.stringify({tecla:window.tecla,solto:window.solto,enviado:window.enviado,confiable:window.confiable,valor:document.getElementById('testId-SearchBar-Input').value});"}]
        run = subprocess.run(
            ["node", str(ROOT / "tests" / "browser_dom.cjs")], input=json.dumps(payload),
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(json.loads(run.stdout)[0]["result"]), {
            "tecla": "Enter", "solto": "Enter", "enviado": True, "confiable": True, "valor": "leche",
        })

    def test_extractores_en_chrome(self):
        tottus = lambda price: '<div data-pod="catalyst-pod"><a href="/product/leche"><span class="pod-title">Marca</span><span class="pod-subTitle">Leche</span></a>' + price + '</div>'
        cases = [
            ("unimarc", unimarc_card("normal", normal="$1.200"), 1200),
            ("unimarc", unimarc_card("oferta", "$990", "$1.200", "Exclusivo .cl"), 990),
            ("unimarc", unimarc_card("club", "$990", "$1.200", "Club Unimarc"), 1200),
            ("unimarc", unimarc_card("multiple", "6 x $3.000", "$620 c/u", "Club Unimarc"), 620),
            ("unimarc", unimarc_card("tarjeta", "$800", "$1.200", "Tarjeta"), 1200),
            ("unimarc", unimarc_card("solo-club", "$990", condicion="Club Unimarc"), None),
            ("unimarc", unimarc_card("sin-precio"), None),
            ("tottus", tottus('<li data-internet-price="990">$990</li><li data-cmr-price="800">$800 CMR</li>'), 990),
            ("tottus", tottus('<li class="prices-main-price">$1.200</li>'), 1200),
            ("tottus", tottus('<li class="prices-main-price" data-cmr-price="800">$800</li><li data-normal-price="1200">$1.200</li>'), 1200),
            ("tottus", tottus('<li class="prices-main-price">2 x $1.000</li>'), None),
            ("tottus", tottus(''), None),
            ("tottus", tottus_card_id(113152870, "Leche", '<span class="jsx-1901152073 line-height-22">$1.290</span>'), 1290),
            ("tottus", tottus_card_id(113152872, "Leche", '<li><span class="line-height-22">$890</span><img alt="CMR" src="cmr.svg"></li><li><span class="line-height-22">$1.290</span></li>'), 1290),
            ("tottus", tottus_card_id(113152873, "Leche", '<span class="line-height-22" style="text-decoration:line-through">$1.590</span><span class="line-height-22">$1.290</span>'), 1290),
            ("tottus", tottus_card_id(113152874, "Leche", '<span class="line-height-22">2 x $1.000</span>'), None),
            ("tottus", tottus_card_id(113152875, "Leche", ''), None),
            ("lider", '<div data-item-id="1"><button data-automation-id="add-to-cart" aria-label="Agregar al carro - Leche"></button><span data-automation-id="product-price">$1.250</span><a href="/product/leche">Ver</a></div>', 1250),
            ("unimarc", '<form id="challenge-form">Verificación de seguridad en curso</form>', "error"),
            ("lider", '<title>Robot or human?</title><div>Press and hold</div>', "error"),
            ("tottus", '<div role="dialog">Ingresa tu ubicación</div>', "error"),
            ("unimarc", '<div>Ingresa tu ubicación</div>', None),
        ]
        payload = [{"html": html, "script": scraping.obtener_extractor({"extractor": store})} for store, html, expected in cases]
        run = subprocess.run(
            ["node", str(ROOT / "tests" / "browser_dom.cjs")], input=json.dumps(payload),
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        results = json.loads(run.stdout)
        self.assertEqual(len(results), len(cases))
        for (store, html, expected), result in zip(cases, results):
            with self.subTest(store=store, html=html):
                data = json.loads(result["result"])
                if expected == "error":
                    self.assertIn("error", data)
                elif expected is None:
                    self.assertEqual(data, [])
                else:
                    self.assertEqual(len(data), 1)
                    self.assertEqual(scraping.limpiar_precio(data[0]["precio"]), expected)
                    self.assertTrue(data[0]["nombre"])
                    if store == "tottus":
                        self.assertEqual(data[0]["nombre"], "Leche")
                    self.assertTrue(data[0]["url"].startswith("https://example.test/product/"))

    def test_tottus_cinco_productos_con_nombre_precio_y_url_correspondientes(self):
        html = ''.join(
            tottus_card_id(113152870 + i, "Leche " + str(i),
                           '<span class="jsx-dinamica line-height-22">$' + str(1200 + i) + '</span>')
            for i in range(7)
        )
        payload = [{"html": html, "script": scraping.obtener_extractor({"extractor": "tottus"})}]
        run = subprocess.run(
            ["node", str(ROOT / "tests" / "browser_dom.cjs")], input=json.dumps(payload),
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        salida = json.loads(run.stdout)[0]["result"]
        with patch.object(scraping, "evaluar_js", return_value=salida):
            resultados = scraping.esperar_productos({"extractor": "tottus"})
        self.assertEqual(len(resultados), 5)
        for i, producto in enumerate(resultados):
            self.assertEqual(producto["nombre"], "Leche " + str(i))
            self.assertEqual(scraping.limpiar_precio(producto["precio"]), 1200 + i)
            self.assertEqual(producto["url"], "https://example.test/product/" + str(113152870 + i))


class FlujoTests(unittest.TestCase):
    def test_respaldo_de_marca_y_precedencia_del_campo(self):
        from integraciones.filtros import cumple_filtros
        self.assertTrue(cumple_filtros({"nombre": "Leche COLÚN", "marca": "   "}, 1000, marca="colun"))
        self.assertFalse(cumple_filtros({"nombre": "Leche Colun", "marca": "Soprole"}, 1000, marca="colun"))
        self.assertFalse(cumple_filtros({"nombre": "Leche Colunera"}, 1000, marca="colun"))
        self.assertFalse(cumple_filtros({"nombre": "Leche entera"}, 1000, marca="colun"))
        self.assertTrue(cumple_filtros({"nombre": "Leche entera"}, 1000))
        self.assertTrue(cumple_filtros({"nombre": "Leche entera"}, 1000, marca="   "))

    def test_diagnostico_devuelve_estado_sin_cargar_cola(self):
        item = {"nombre": "Leche", "marca": "Colun", "precio": 1350, "url": "url"}
        with patch.object(scraping, "abrir_navegador"), patch.object(scraping.r, "close") as cerrar, patch.object(scraping, "buscar_producto", return_value=[item]) as buscar, patch.object(scraping, "inspeccionar_busqueda", return_value={"tarjetas": 4, "tarjetas_con_marca": 3}), patch.object(scraping, "guardar_resultados") as guardar:
            resumen = scraping.diagnosticar("unimarc", "leche", marca="Colun")
        self.assertEqual(resumen["estado"], "ok")
        self.assertEqual(resumen["dom"]["tarjetas_con_marca"], 3)
        self.assertEqual(buscar.call_args.kwargs["marca"], "Colun")
        guardar.assert_not_called()
        cerrar.assert_called_once()
        self.assertEqual(self.tareas_guardadas(), [])

    def test_distingue_filtros_de_tarjetas_ausentes_y_extractor_invalido(self):
        item = {"nombre": "Leche", "marca": "Soprole", "precio": "$1.200", "url": "url"}
        casos = [
            (json.dumps([item]), {"tarjetas": 1}, "marca_no_coincide"),
            ("[]", {"tarjetas": 0, "url": "inicio"}, "Busqueda sin tarjetas"),
            ("[]", {"tarjetas": 3}, "Hay tarjetas pero el extractor"),
            ("invalido", {}, "interpretar la respuesta"),
        ]
        for salida, estado, mensaje in casos:
            with self.subTest(mensaje=mensaje), patch.object(scraping.time, "monotonic", side_effect=[0, 0, 99]), patch.object(scraping.time, "sleep"), patch.object(scraping.r, "dom"), patch.object(scraping, "evaluar_js", return_value=salida), patch.object(scraping, "inspeccionar_busqueda", return_value=estado):
                with self.assertRaisesRegex(ValueError, mensaje):
                    scraping.esperar_productos({"extractor": "unimarc"}, marca="Colun")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=sandbox.name)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bd.configurar_base_datos(self.root / "test.db")

    def cargar_productos(self):
        ruta = self.root / "productos.xlsx"
        libro = Workbook()
        libro.active.append(["producto"])
        libro.active.append(["leche"])
        libro.active.append(["arroz"])
        libro.save(ruta)
        libro.close()
        self.cargar_archivo(ruta, veces=2)

    def cargar_archivo(self, ruta, veces=1):
        for _ in range(veces):
            cola.cargar_excel_a_tabla_cola(ruta)

    def crear_archivo(self, filas):
        ruta = self.root / "entrada.xlsx"
        libro = Workbook()
        for fila in filas:
            libro.active.append(fila)
        libro.save(ruta)
        libro.close()
        return ruta

    def tareas_guardadas(self):
        with closing(bd.obtener_db_conexion_sql3()) as con:
            return con.execute("SELECT supermercado, producto FROM cola ORDER BY supermercado, producto").fetchall()

    def test_carga_todos_los_excel_de_carpeta_y_no_duplica_al_repetir(self):
        self.crear_archivo([["producto"], ["leche"]]).rename(self.root / "a.xlsx")
        self.crear_archivo([
            [" PRECIO_MAXIMO ", " MARCA ", "supermercado", " PRODUCTO ", "precio_minimo"],
            [2000, "Colun", "Unimarc", "leche", 1000],
        ]).rename(self.root / "b.XLSX")
        self.crear_archivo([["producto", "supermercado"], ["arroz", "Lider"]]).rename(self.root / "c.xlsm")
        with patch.object(cola, "RUTA_INPUT", self.root):
            primera = cola.cargar_excel_a_tabla_cola()
            segunda = cola.cargar_excel_a_tabla_cola()
        self.assertEqual(primera, {"archivos_leidos": 3, "archivos_error": 0, "tareas_agregadas": 5, "tareas_existentes": 0})
        self.assertEqual(segunda, {"archivos_leidos": 3, "archivos_error": 0, "tareas_agregadas": 0, "tareas_existentes": 5})
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM details WHERE estado='leido'").fetchone()[0], 3)
            self.assertEqual(con.execute("SELECT marca, precio_minimo, precio_maximo FROM cola WHERE marca!=''").fetchone(), ("colun", 1000, 2000))

    def test_archivos_invalidos_se_registran_sin_detener_otros_ni_cargar_parcialmente(self):
        self.crear_archivo([["otra_columna"], ["leche"]]).rename(self.root / "a_sin_producto.xlsx")
        self.crear_archivo([
            ["producto", "precio_minimo", "precio_maximo"],
            ["arroz", None, None], ["leche", 2000, 1000],
        ]).rename(self.root / "b_rango_invalido.xlsx")
        (self.root / "c_corrupto.xlsx").write_text("archivo incorrecto")
        self.crear_archivo([["producto", " PRODUCTO "], ["leche", "arroz"]]).rename(self.root / "d_duplicado.xlsx")
        self.crear_archivo([["producto", "supermercado"], ["pan", "Unimarc"]]).rename(self.root / "z_valido.xlsx")
        with patch.object(cola, "RUTA_INPUT", self.root):
            resumen = cola.cargar_excel_a_tabla_cola()
        self.assertEqual((resumen["archivos_leidos"], resumen["archivos_error"]), (1, 4))
        self.assertEqual(self.tareas_guardadas(), [("unimarc", "pan")])
        with closing(bd.obtener_db_conexion_sql3()) as con:
            errores = con.execute("SELECT error FROM details WHERE estado='error'").fetchall()
        self.assertEqual(len(errores), 4)
        self.assertTrue(all(error for (error,) in errores))

    def test_carpeta_ignora_temporales_otros_formatos_y_subcarpetas(self):
        for nombre in ["~$abierto.xlsx", "notas.csv", "antiguo.xls"]:
            (self.root / nombre).write_text("no debe leerse")
        subcarpeta = self.root / "subcarpeta.xlsx"
        subcarpeta.mkdir()
        self.crear_archivo([["producto"], ["leche"]]).rename(subcarpeta / "anidado.xlsx")
        with patch.object(cola, "RUTA_INPUT", self.root):
            resumen = cola.cargar_excel_a_tabla_cola()
        self.assertEqual(resumen["archivos_leidos"], 0)
        self.assertEqual(resumen["archivos_error"], 0)
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM details").fetchone()[0], 0)

    def test_carpeta_inexistente_no_falla(self):
        with patch.object(cola, "RUTA_INPUT", self.root / "faltante"):
            resumen = cola.cargar_excel_a_tabla_cola()
        self.assertEqual(resumen["tareas_agregadas"], 0)

    def test_excel_de_prueba_crea_seis_tareas(self):
        ruta = ROOT / "input" / "productos_prueba.xlsx"
        libro = load_workbook(ruta, read_only=True)
        self.assertEqual(next(libro.active.values), ("supermercado", "producto"))
        self.assertEqual((libro.active.max_column, libro.active.max_row), (2, 5))
        libro.close()
        self.cargar_archivo(ruta, veces=2)
        self.assertEqual(self.tareas_guardadas(), [
            ("lider", "leche"), ("lider", "pan"), ("tottus", "café"),
            ("tottus", "leche"), ("unimarc", "arroz"), ("unimarc", "leche"),
        ])

    def test_seleccion_con_mayusculas_tildes_y_espacios(self):
        ruta = self.crear_archivo([
            ["supermercado", "producto"], ["  LÍDER  ", "pan"],
            [" UnIMaRc ", " arroz "], ["tOtTuS", "café"],
            ["   ", "leche"], ["desconocido", None], [None, "  "],
        ])
        self.cargar_archivo(ruta)
        self.assertEqual(self.tareas_guardadas(), [
            ("lider", "leche"), ("lider", "pan"), ("tottus", "café"),
            ("tottus", "leche"), ("unimarc", "arroz"), ("unimarc", "leche"),
        ])

    def test_supermercado_desconocido_no_inserta_carga_parcial(self):
        ruta = self.crear_archivo([
            ["supermercado", "producto"], ["Unimarc", "arroz"],
            [None, None], ["Unimrac", "pan"],
        ])
        with self.assertRaisesRegex(ValueError, "Fila 4: Supermercado desconocido: Unimrac"):
            self.cargar_archivo(ruta)
        self.assertEqual(self.tareas_guardadas(), [])
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cola").fetchone()[0], 0)

    def test_supermercado_deshabilitado_no_inserta_carga_parcial(self):
        config = supermercados.cargar_supermercados()
        config["tottus"]["habilitado"] = False
        ruta = self.crear_archivo([
            ["supermercado", "producto"], ["Unimarc", "arroz"], ["Tottus", "pan"],
        ])
        with patch.object(cola, "cargar_supermercados", return_value=config):
            with self.assertRaisesRegex(ValueError, "Fila 3: Supermercado deshabilitado: Tottus"):
                self.cargar_archivo(ruta)
        self.assertEqual(self.tareas_guardadas(), [])

    def test_vacio_excluye_deshabilitados(self):
        config = supermercados.cargar_supermercados()
        config["tottus"]["habilitado"] = False
        ruta = self.crear_archivo([["supermercado", "producto"], [None, "leche"]])
        with patch.object(cola, "cargar_supermercados", return_value=config):
            self.cargar_archivo(ruta)
        self.assertEqual(self.tareas_guardadas(), [("lider", "leche"), ("unimarc", "leche")])

    def test_orden_de_columnas_y_duplicados(self):
        ruta = self.crear_archivo([
            ["producto", "supermercado"], ["leche", None], ["leche", "Unimarc"],
        ])
        self.cargar_archivo(ruta, veces=2)
        self.assertEqual(self.tareas_guardadas(), [("lider", "leche"), ("tottus", "leche"), ("unimarc", "leche")])

    def test_plantilla_tiene_filtros_y_no_reemplaza_archivo(self):
        ruta = self.root / "plantilla.xlsx"
        with patch.object(productos, "EXCEL_ENTRADA", str(ruta)), patch.object(productos, "RUTA_INPUT", self.root):
            productos.crear_plantilla_si_no_existe()
            contenido = ruta.read_bytes()
            productos.crear_plantilla_si_no_existe()
        self.assertEqual(ruta.read_bytes(), contenido)
        libro = load_workbook(ruta, read_only=True)
        self.assertEqual(next(libro.active.values), ("supermercado", "producto", "marca", "precio_minimo", "precio_maximo"))
        self.assertEqual(libro.active.max_column, 5)
        libro.close()

    def test_tres_supermercados_sin_duplicar_tareas(self):
        self.cargar_productos()
        with closing(bd.obtener_db_conexion_sql3()) as con:
            rows = con.execute("SELECT supermercado, COUNT(*) FROM cola GROUP BY supermercado").fetchall()
        self.assertEqual(dict(rows), {"lider": 2, "tottus": 2, "unimarc": 2})

    def test_details_registra_archivo_leido_y_cola_queda_pendiente(self):
        self.cargar_productos()
        with closing(bd.obtener_db_conexion_sql3()) as con:
            archivos = con.execute("SELECT archivo, estado, error, fecha_fin FROM details").fetchall()
            tareas = con.execute("SELECT detail_id, estado FROM cola").fetchall()
        self.assertEqual(len(archivos), 1)
        self.assertEqual(archivos[0][:3], ("productos.xlsx", "leido", None))
        self.assertTrue(archivos[0][3])
        self.assertEqual(len(tareas), 6)
        self.assertTrue(all(archivo_id and estado == "pendiente" for archivo_id, estado in tareas))

    def test_error_de_lectura_queda_en_details_sin_productos(self):
        ruta = self.crear_archivo([["columna_incorrecta"], ["leche"]])
        with self.assertRaisesRegex(ValueError, "columna llamada 'producto'"):
            self.cargar_archivo(ruta)
        with closing(bd.obtener_db_conexion_sql3()) as con:
            archivo = con.execute("SELECT estado, error FROM details").fetchone()
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cola").fetchone()[0], 0)
        self.assertEqual(archivo[0], "error")
        self.assertIn("producto", archivo[1])

    def test_archivo_inexistente_queda_registrado_con_error(self):
        with self.assertRaises(FileNotFoundError):
            self.cargar_archivo(self.root / "faltante.xlsx")
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT archivo, estado FROM details").fetchone(), ("faltante.xlsx", "error"))

    def test_archivos_distintos_tienen_tareas_y_resultados_independientes(self):
        ruta = self.crear_archivo([["supermercado", "producto"], ["Unimarc", "leche"]])
        ruta_uno = ruta.rename(self.root / "uno.xlsx")
        self.cargar_archivo(ruta_uno)
        ruta_dos = self.crear_archivo([["supermercado", "producto"], ["Unimarc", "leche"]])
        self.cargar_archivo(ruta_dos)
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM details").fetchone()[0], 2)
            tareas = con.execute("SELECT id FROM cola ORDER BY id").fetchall()
        self.assertEqual(len(tareas), 2)
        item = {"nombre": "Leche", "precio": 1200, "url": "https://example.test/leche"}
        for (tarea_id,) in tareas:
            bd.guardar_resultados(tarea_id, "unimarc", "leche", [item], 0, "2026-10-27 12:00:00")
        bd.actualizar_cola(tareas[0][0], "error", 1, error="Reintento de prueba")
        self.assertEqual(bd.reencolar_errores(), 1)
        bd.guardar_resultados(tareas[0][0], "unimarc", "leche", [{**item, "precio": 1300}], 1, "2026-10-27 12:01:00")
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM resultados").fetchone()[0], 3)
            self.assertEqual(con.execute("SELECT DISTINCT estado FROM cola").fetchall(), [("leido",)])
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_fallo_al_guardar_revierte_el_lote_y_no_marca_leido(self):
        ruta = self.crear_archivo([["supermercado", "producto"], ["Unimarc", "leche"]])
        self.cargar_archivo(ruta)
        with closing(bd.obtener_db_conexion_sql3()) as con:
            tarea_id = con.execute("SELECT id FROM cola").fetchone()[0]
        item = {"nombre": "Leche", "precio": 1200, "url": "https://example.test/leche"}
        with self.assertRaises(sqlite3.Error):
            bd.guardar_resultados(tarea_id, "unimarc", "leche", [item, {**item, "precio": object()}], 0, "2026-10-27 12:00:00")
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM resultados").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT estado FROM cola").fetchone()[0], "pendiente")

    def crear_base_anterior(self, referencia_resultado=11):
        ruta = self.root / "anterior.db"
        with closing(sqlite3.connect(ruta)) as con, con:
            con.executescript("""
                CREATE TABLE cola (id INTEGER PRIMARY KEY, archivo TEXT UNIQUE, estado TEXT,
                    error TEXT, fecha_creacion TEXT, fecha_fin TEXT);
                CREATE TABLE details (id INTEGER PRIMARY KEY, cola_id INTEGER REFERENCES cola(id),
                    producto TEXT, supermercado TEXT, estado TEXT, reintentos INTEGER,
                    error TEXT, fecha_creacion TEXT, fecha_fin TEXT);
                CREATE TABLE resultados (id INTEGER PRIMARY KEY, detail_id INTEGER REFERENCES details(id),
                    supermercado TEXT, producto TEXT, nombre TEXT, precio REAL, url TEXT, fecha TEXT);
                INSERT INTO cola VALUES (10,'uno.xlsx','pendiente',NULL,'2026-10-27',NULL);
                INSERT INTO cola VALUES (20,'dos.xlsx','error','Error anterior','2026-10-27',NULL);
                INSERT INTO details VALUES (11,10,'leche','unimarc','ok',0,NULL,'2026-10-27','2026-10-27');
                INSERT INTO details VALUES (12,10,'arroz','lider','pendiente',0,NULL,'2026-10-27',NULL);
                INSERT INTO details VALUES (13,20,'pan','tottus','error',2,'Bloqueo','2026-10-27','2026-10-27');
            """)
            con.execute("INSERT INTO resultados VALUES (100,?,'unimarc','leche','Leche',1200,'https://example.test/leche','2026-10-27')", (referencia_resultado,))
        return ruta

    def test_migracion_conserva_ids_estados_relaciones_y_respaldo(self):
        ruta = self.crear_base_anterior()
        bd.configurar_base_datos(ruta)
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT id, archivo, estado FROM details ORDER BY id").fetchall(), [(10, "uno.xlsx", "leido"), (20, "dos.xlsx", "error")])
            self.assertEqual(con.execute("SELECT id, detail_id, estado FROM cola ORDER BY id").fetchall(), [(11, 10, "leido"), (12, 10, "pendiente"), (13, 20, "error")])
            self.assertEqual(con.execute("SELECT id, cola_id, precio FROM resultados").fetchall(), [(100, 11, 1200)])
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 3)
        copias = list(self.root.glob("anterior.db.pre_v2_*.bak"))
        self.assertEqual(len(copias), 1)
        with closing(sqlite3.connect(copias[0])) as con:
            self.assertIn("producto", {fila[1] for fila in con.execute("PRAGMA table_info(details)")})
            self.assertEqual(con.execute("SELECT COUNT(*) FROM resultados").fetchone()[0], 1)
        bd.configurar_base_datos(ruta)
        self.assertEqual(len(list(self.root.glob("anterior.db.pre_v2_*.bak"))), 1)

    def test_migracion_invalida_revierte_todas_las_tablas(self):
        ruta = self.crear_base_anterior(referencia_resultado=999)
        with self.assertRaisesRegex(RuntimeError, "referencias invalidas"):
            bd.configurar_base_datos(ruta)
        with closing(sqlite3.connect(ruta)) as con:
            self.assertIn("producto", {fila[1] for fila in con.execute("PRAGMA table_info(details)")})
            self.assertEqual(con.execute("SELECT COUNT(*) FROM details").fetchone()[0], 3)
            self.assertEqual(con.execute("SELECT detail_id FROM resultados").fetchone()[0], 999)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '_v2_%'").fetchone()[0], 0)

    def test_fallo_independiente_y_excel(self):
        self.cargar_productos()
        item = {"nombre": "Leche", "precio": "$1.290", "url": "https://example.test/product/leche"}
        with patch.object(scraping, "abrir_navegador"), patch.object(scraping.r, "url"), patch.object(scraping.r, "present", return_value=True), patch.object(scraping.r, "type", return_value=True), patch.object(scraping, "enviar_enter"), patch.object(scraping.r, "close"), patch.object(scraping.time, "sleep"), patch.object(scraping, "REINTENTOS_MAX", 0), patch.object(scraping, "_capturar", return_value=None), patch.object(scraping, "esperar_productos", side_effect=[[item], ValueError("Acceso bloqueado"), [item], [item], [item], [item]]):
            resumen = scraping.procesar_cola_a_resultados()
        self.assertEqual((resumen["ok"], resumen["errores"]), (5, 1))
        self.assertEqual(resumen["fallos"][0][1:], ("tottus", "Acceso bloqueado"))
        salida = self.root / "resultados.xlsx"
        with patch.object(reportes, "EXCEL_SALIDA", str(salida)), patch.object(reportes, "RUTA_REPORTES", self.root):
            reportes.generar_resultados_excel()
        libro = load_workbook(salida, read_only=True)
        rows = list(libro.active.values)
        libro.close()
        self.assertEqual(rows[0], ("producto", "nombre", "precio", "url", "fecha", "supermercado"))
        self.assertEqual(len(rows), 6)
        self.assertEqual({row[5] for row in rows[1:]}, {"lider", "tottus", "unimarc"})

    def test_espera_filtra_precios_y_duplicados(self):
        item = {"nombre": "Leche", "precio": "$1.200", "url": "https://example.test/leche"}
        lista = [{"nombre": "Sin precio"}, {**item, "precio": "$0"}, item, item]
        lista += [{**item, "nombre": "Leche " + str(i), "url": item["url"] + str(i)} for i in range(8)]
        with patch.object(scraping, "evaluar_js", return_value=json.dumps(lista)):
            resultados = scraping.esperar_productos({"extractor": "unimarc"})
        self.assertEqual(len(resultados), 5)
        self.assertEqual(len({item["url"] for item in resultados}), 5)

    def test_filtros_excel_se_guardan_y_distinguen_tareas(self):
        ruta = self.crear_archivo([
            ["producto", "marca", "precio_maximo", "supermercado", "precio_minimo"],
            ["leche", " COLÚN ", "$2.000", "Unimarc", 1000],
            ["leche", "colun", 2000, "Unimarc", 1000],
            ["leche", "Soprole", None, "Unimarc", None],
            ["leche", None, None, "Unimarc", None],
            ["leche", "colun", 2500, "Unimarc", 1000],
        ])
        self.cargar_archivo(ruta, veces=2)
        with closing(bd.obtener_db_conexion_sql3()) as con:
            filas = con.execute("SELECT marca, precio_minimo, precio_maximo FROM cola ORDER BY id").fetchall()
        self.assertEqual(filas, [("colun", 1000, 2000), ("soprole", None, None), ("", None, None), ("colun", 1000, 2500)])

    def test_filtros_invalidos_rechazan_carga_completa(self):
        for minimo, maximo in [(2000, 1000), (-1, None), ("abc", None), (None, "NaN"), (True, None)]:
            with self.subTest(minimo=minimo, maximo=maximo):
                ruta = self.crear_archivo([
                    ["producto", "precio_minimo", "precio_maximo"],
                    ["arroz", None, None], ["leche", minimo, maximo],
                ])
                with self.assertRaisesRegex(ValueError, "Fila 3:"):
                    self.cargar_archivo(ruta)
                self.assertEqual(self.tareas_guardadas(), [])

    def test_filtra_antes_del_limite_y_respeta_extremos_y_marca(self):
        item = {"nombre": "Leche", "marca": "COLÚN", "precio": "$1.000", "url": "https://example.test/"}
        lista = [{**item, "marca": "Otra"} for _ in range(6)]
        lista += [{**item, "nombre": "Leche Colunera", "marca": "", "url": "falsa"},
                  {**item, "precio": "$999", "url": "barata"},
                  {**item, "precio": "$2.001", "url": "cara"}]
        lista += [{**item, "precio": precio, "url": str(i)}
                  for i, precio in enumerate(["$1.000", "$2.000", "$1.200", "$1.300", "$1.400", "$1.500"])]
        with patch.object(scraping, "evaluar_js", return_value=json.dumps(lista)):
            resultado = scraping.esperar_productos({"extractor": "unimarc"}, marca="colun", precio_minimo=1000, precio_maximo=2000)
        self.assertEqual([r["url"] for r in resultado], ["0", "1", "2", "3", "4"])

    def test_cola_transmite_filtros_a_busqueda_y_guarda_solo_coincidencias(self):
        ruta = self.crear_archivo([
            ["producto", "supermercado", "marca", "precio_minimo", "precio_maximo"],
            ["leche", "Lider", "Colun", 1000, 1500],
        ])
        self.cargar_archivo(ruta)
        items = [{"nombre": "Leche Colun", "precio": "$1.200", "url": "valido"},
                 {"nombre": "Leche Colun", "precio": "$2.000", "url": "caro"}]
        with patch.object(scraping, "abrir_navegador"), patch.object(scraping.r, "close"), patch.object(scraping, "navegar_busqueda", return_value="url") as navegar, patch.object(scraping, "esperar_productos", return_value=items) as extraer:
            resumen = scraping.procesar_cola_a_resultados()
        self.assertEqual(resumen["ok"], 1)
        self.assertEqual(navegar.call_args.args[1], "leche colun")
        self.assertEqual(extraer.call_args.kwargs, dict(max_items=5, marca="colun", precio_minimo=1000, precio_maximo=1500))
        with closing(bd.obtener_db_conexion_sql3()) as con:
            self.assertEqual(con.execute("SELECT precio, url FROM resultados").fetchall(), [(1200, "valido")])

    def test_filtros_opcionales_desde_excel_hasta_resultados(self):
        # DOM simulado; el lector Excel, SQLite, filtros y guardado son reales.
        items = [
            {"nombre": "Leche Colun A", "precio": "$800", "url": "A"},
            {"nombre": "Leche Colun B", "precio": "$1.000", "url": "B"},
            {"nombre": "Leche Soprole C", "precio": "$1.500", "url": "C"},
            {"nombre": "Leche Colun D", "precio": "$2.000", "url": "D"},
            {"nombre": "Leche Soprole E", "precio": "$2.500", "url": "E"},
        ]
        casos = [
            (None, None, None, "ABCDE"),
            (None, 1500, None, "CDE"),
            (None, None, 1500, "ABC"),
            (None, 1000, 2000, "BCD"),
            ("Colun", None, None, "ABD"),
            ("Colun", 1000, None, "BD"),
            ("Colun", None, 1000, "AB"),
            ("Colun", 1000, 2000, "BD"),
        ]
        for indice, (marca, minimo, maximo, esperados) in enumerate(casos):
            with self.subTest(marca=marca, minimo=minimo, maximo=maximo):
                bd.configurar_base_datos(self.root / f"opcional_{indice}.db")
                ruta = self.crear_archivo([
                    ["producto", "supermercado", "marca", "precio_minimo", "precio_maximo"],
                    ["leche", "Unimarc", marca, minimo, maximo],
                ])
                self.cargar_archivo(ruta)
                with patch.object(scraping, "abrir_navegador"), patch.object(scraping.r, "close"), patch.object(scraping.r, "dom"), patch.object(scraping, "navegar_busqueda", return_value="url"), patch.object(scraping, "evaluar_js", return_value=json.dumps(items)), patch.object(scraping.time, "sleep"), patch.object(scraping.time, "monotonic", side_effect=[0, 0, 99]):
                    resumen = scraping.procesar_cola_a_resultados()
                self.assertEqual((resumen["ok"], resumen["errores"]), (1, 0))
                with closing(bd.obtener_db_conexion_sql3()) as con:
                    urls = con.execute("SELECT url FROM resultados ORDER BY id").fetchall()
                self.assertEqual([fila[0] for fila in urls], list(esperados))

    def test_migracion_v2_conserva_resultados_y_permite_distintos_filtros(self):
        ruta = self.root / "v2.db"
        with closing(sqlite3.connect(ruta)) as con, con:
            con.executescript("""
                CREATE TABLE details (id INTEGER PRIMARY KEY, archivo TEXT, ruta TEXT,
                    estado TEXT, error TEXT, fecha_creacion TEXT, fecha_fin TEXT);
                CREATE TABLE cola (id INTEGER PRIMARY KEY AUTOINCREMENT, detail_id INTEGER REFERENCES details(id),
                    producto TEXT, supermercado TEXT, estado TEXT, reintentos INTEGER,
                    error TEXT, fecha_creacion TEXT, fecha_fin TEXT, UNIQUE(detail_id, producto, supermercado));
                CREATE TABLE resultados (id INTEGER PRIMARY KEY, cola_id INTEGER REFERENCES cola(id),
                    supermercado TEXT, producto TEXT, nombre TEXT, precio REAL, url TEXT, fecha TEXT);
                INSERT INTO details VALUES (1,'uno.xlsx',NULL,'leido',NULL,'2026-10-29',NULL);
                INSERT INTO cola VALUES (7,1,'leche','lider','leido',0,NULL,'2026-10-29',NULL);
                INSERT INTO resultados VALUES (9,7,'lider','leche','Leche',1200,'url','2026-10-29');
                PRAGMA user_version=2;
            """)
        bd.configurar_base_datos(ruta)
        bd.configurar_base_datos(ruta)
        with closing(bd.obtener_db_conexion_sql3()) as con, con:
            self.assertEqual(con.execute("SELECT id, estado, marca, precio_minimo, precio_maximo FROM cola").fetchall(), [(7, "leido", "", None, None)])
            con.execute("INSERT INTO cola (detail_id, producto, supermercado, marca) VALUES (1,'leche','lider','colun')")
            self.assertEqual(con.execute("SELECT id, cola_id, precio FROM resultados").fetchall(), [(9, 7, 1200)])
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(len(list(self.root.glob("v2.db.pre_v3_*.bak"))), 1)

    def test_salida_invalida_y_menos_de_cinco_resultados(self):
        item = {"nombre": "Leche", "precio": "$1.200", "url": "https://example.test/leche"}
        with patch.object(scraping.time, "monotonic", side_effect=[0, 0, 1, 2, 99]), patch.object(scraping.time, "sleep"), patch.object(scraping.r, "dom"), patch.object(scraping, "evaluar_js", side_effect=['invalido', '"texto"', json.dumps([item, item])]):
            self.assertEqual(scraping.esperar_productos({"extractor": "unimarc"}), [item])

    def test_bloqueo_explicito(self):
        with patch.object(scraping, "evaluar_js", return_value=json.dumps({"error": "Acceso bloqueado"})):
            with self.assertRaisesRegex(ValueError, "Acceso bloqueado"):
                scraping.esperar_productos({"extractor": "tottus"})

    def test_url_con_tildes_espacios_y_barra(self):
        config = supermercados.cargar_supermercados()["lider"]
        item = {"nombre": "Café", "precio": "$1.200", "url": "https://example.test/cafe"}
        with patch.object(scraping.r, "url") as navegar, patch.object(scraping.time, "sleep"), patch.object(scraping, "esperar_productos", return_value=[item]):
            scraping.buscar_producto(config, "café con/sin azúcar")
        navegar.assert_called_once_with("https://super.lider.cl/search?q=caf%C3%A9%20con%2Fsin%20az%C3%BAcar")

    def test_unimarc_busca_desde_su_campo_configurado(self):
        config = supermercados.cargar_supermercados()["unimarc"]
        with patch.object(scraping, "r") as robot, patch.object(scraping.time, "sleep") as esperar, patch.object(scraping, "enviar_enter") as enter:
            robot.url.return_value = True
            robot.present.return_value = True
            robot.type.return_value = True
            scraping.navegar_busqueda(config, "leche")
        robot.url.assert_called_once_with("https://www.unimarc.cl")
        robot.present.assert_called_once_with("//input[@id='search-header__input']")
        robot.type.assert_called_once_with("//input[@id='search-header__input']", "[clear]leche")
        enter.assert_called_once_with("//input[@id='search-header__input']")
        self.assertEqual(esperar.call_args_list, [call(8.0), call(0.5), call(3.0)])

    def test_tottus_busca_desde_el_input_de_inicio(self):
        config = supermercados.cargar_supermercados()["tottus"]
        selector = "//input[@id='testId-SearchBar-Input']"
        item = {"nombre": "Leche", "precio": "$1.200", "url": "https://example.test/product/leche"}
        with patch.object(scraping, "r") as robot, patch.object(scraping.time, "sleep") as esperar, patch.object(scraping, "esperar_productos", return_value=[item]):
            robot.url.return_value = True
            robot.present.return_value = True
            robot.type.return_value = True
            robot.click.return_value = True
            robot.send.return_value = True
            resultados = scraping.buscar_producto(config, "leche")
        self.assertEqual(robot.mock_calls[:4], [
            call.url("https://www.tottus.cl/tottus-cl"),
            call.present(selector),
            call.type(selector, "[clear]leche"),
            call.click(selector),
        ])
        eventos = [json.loads(args.args[0].split("Input.dispatchKeyEvent', ", 1)[1][:-2]) for args in robot.send.call_args_list]
        self.assertEqual([evento["type"] for evento in eventos], ["keyDown", "keyUp"])
        self.assertTrue(all(evento["key"] == "Enter" and evento["windowsVirtualKeyCode"] == 13 for evento in eventos))
        self.assertEqual(eventos[0]["text"], "\r")
        self.assertEqual(resultados[0]["precio"], 1200)
        self.assertEqual(esperar.call_args_list, [call(8.0), call(0.5), call(3.0)])

    def test_tottus_espera_que_aparezca_el_input(self):
        config = supermercados.cargar_supermercados()["tottus"]
        with patch.object(scraping, "r") as robot, patch.object(scraping.time, "sleep"), patch.object(scraping, "evaluar_js", return_value="{}"):
            robot.present.side_effect = [False, True]
            scraping.navegar_busqueda(config, "café")
        self.assertEqual(robot.present.call_count, 2)
        robot.type.assert_called_once_with(config["selector_busqueda"], "[clear]café")

    def test_fallo_al_enviar_enter_no_extrae_resultados(self):
        config = supermercados.cargar_supermercados()["tottus"]
        with patch.object(scraping, "r") as robot, patch.object(scraping.time, "sleep"), patch.object(scraping, "esperar_productos") as extraer:
            robot.present.return_value = True
            robot.type.return_value = True
            robot.click.return_value = True
            robot.send.return_value = False
            with self.assertRaisesRegex(RuntimeError, "No se pudo presionar Enter"):
                scraping.buscar_producto(config, "leche")
        extraer.assert_not_called()

    def test_tottus_input_ausente_falla_sin_enviar_busqueda(self):
        config = supermercados.cargar_supermercados()["tottus"]
        with patch.object(scraping, "r") as robot, patch.object(scraping.time, "sleep"), patch.object(scraping.time, "monotonic", side_effect=[0, 31]), patch.object(scraping, "evaluar_js", return_value="{}"):
            robot.present.return_value = False
            with self.assertRaisesRegex(TimeoutError, "No se encontro el campo de busqueda"):
                scraping.navegar_busqueda(config, "leche")
        robot.type.assert_not_called()

    def test_tottus_bloqueado_antes_del_input(self):
        config = supermercados.cargar_supermercados()["tottus"]
        with patch.object(scraping, "r") as robot, patch.object(scraping.time, "sleep"), patch.object(scraping, "evaluar_js", return_value='{"error":"Acceso bloqueado"}'):
            robot.present.return_value = False
            with self.assertRaisesRegex(ValueError, "Acceso bloqueado"):
                scraping.navegar_busqueda(config, "leche")
        robot.type.assert_not_called()

    def test_tottus_fallo_al_escribir_no_continua(self):
        config = supermercados.cargar_supermercados()["tottus"]
        with patch.object(scraping, "r") as robot, patch.object(scraping.time, "sleep"), patch.object(scraping, "esperar_productos") as extraer:
            robot.present.return_value = True
            robot.type.return_value = False
            with self.assertRaisesRegex(RuntimeError, "No se pudo escribir o enviar"):
                scraping.buscar_producto(config, "leche")
        extraer.assert_not_called()

    def test_extractor_desconocido(self):
        with self.assertRaisesRegex(ValueError, "Extractor desconocido"):
            scraping.obtener_extractor({"extractor": "inexistente"})
        self.assertEqual(scraping.obtener_extractor({"nombre": "Lider"}), scraping.obtener_extractor({"extractor": "lider"}))


class CoordinacionTests(unittest.TestCase):
    def setUp(self):
        from contextlib import ExitStack
        import main
        from integraciones import robot
        from config import configuracion, correo
        self.main, self.robot, self.config, self.correo = main, robot, configuracion, correo
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(configuracion, 'MODULO_SCRAPING', True))
        stack.enter_context(patch.object(configuracion, 'MODULO_CORREO', True))
        stack.enter_context(patch.object(configuracion, 'MODULO_CORREO_INICIAL', True))
        self.aviso_inicio = stack.enter_context(patch.object(robot, 'enviar_correo_inicial'))
        self.iniciar = stack.enter_context(patch.object(configuracion, 'configuracion_inicial'))
        self.logger = stack.enter_context(patch.object(configuracion, 'obtener_logger'))
        self.cargar = stack.enter_context(patch.object(robot, 'cargar_excel_a_tabla_cola', return_value={'archivos_leidos': 1, 'archivos_error': 0}))
        self.procesar = stack.enter_context(patch.object(robot, 'procesar_cola_a_resultados', return_value={'ok': 1, 'errores': 0}))
        self.reporte = stack.enter_context(patch.object(robot, 'generar_resultados_excel'))
        self.notificar = stack.enter_context(patch.object(robot, 'notificar_resultado_procesamiento'))
        self.error = stack.enter_context(patch.object(robot, 'enviar_correo_error'))
        stack.enter_context(patch.object(robot, 'obtener_capturas', return_value=[]))

    def test_desactivado_no_inicializa_ni_carga_ni_envia_correo(self):
        with patch.object(self.config, 'MODULO_SCRAPING', False):
            self.assertEqual(self.main.main([]), 0)
            self.assertEqual(self.main.main(['cargar']), 0)
            self.assertEqual(self.robot.ejecutar_todo()['estado'], 'desactivado')
        for accion in (self.iniciar, self.cargar, self.procesar, self.reporte, self.notificar, self.error, self.aviso_inicio):
            accion.assert_not_called()

    def test_predeterminado_y_procesar_comparten_coordinador(self):
        for args in ([], ['procesar']):
            self.assertEqual(self.main.main(args), 0)
        self.assertEqual(self.procesar.call_count, 2)
        self.assertEqual(self.notificar.call_count, 2)
        self.assertEqual(self.aviso_inicio.call_count, 2)

    def test_tareas_fallidas_salen_con_error_sin_repetir_pipeline(self):
        self.procesar.return_value = {'ok': 1, 'errores': 1}
        self.assertEqual(self.main.main(['procesar']), 1)
        self.assertEqual(self.procesar.call_count, 1)
        self.assertEqual(self.notificar.call_args.args[0]['estado'], 'con_errores')
        self.logger.return_value.warning.assert_called()

    def test_error_de_entrada_no_se_oculta_si_no_hay_tareas(self):
        self.cargar.return_value = {'archivos_leidos': 0, 'archivos_error': 1}
        self.procesar.return_value = None
        self.assertEqual(self.main.main([]), 1)
        self.assertEqual(self.notificar.call_args.args[0]['carga']['archivos_error'], 1)

    def test_fallo_excel_no_repite_scraping_y_notifica_error(self):
        self.reporte.side_effect = OSError('Disco lleno')
        self.assertEqual(self.main.main([]), 1)
        self.procesar.assert_called_once()
        self.error.assert_called_once()
        self.notificar.assert_not_called()

    def test_error_configuracion_se_informa_con_codigo_uno(self):
        self.iniciar.side_effect = OSError('Sin acceso')
        self.assertEqual(self.main.main([]), 1)
        self.procesar.assert_not_called()

    def test_comandos_cargar_estado_reporte(self):
        with patch.object(self.main, 'cargar_excel_a_tabla_cola', return_value={'archivos_error': 1}) as cargar:
            self.assertEqual(self.main.main(['cargar']), 1)
            cargar.assert_called_once()
        with patch.object(self.main, 'ver_datos_en_tabla_cola') as estado:
            self.assertEqual(self.main.main(['estado']), 0)
            estado.assert_called_once()
        with patch.object(self.main, 'generar_resultados_excel') as reporte:
            self.assertEqual(self.main.main(['reporte']), 0)
            reporte.assert_called_once()
        self.procesar.assert_not_called()

    def test_comando_desconocido_no_ejecuta_nada(self):
        with self.assertRaises(SystemExit) as salida:
            self.main.main(['inexistente'])
        self.assertEqual(salida.exception.code, 2)
        self.iniciar.assert_not_called()

    def test_destinatarios_con_coma_punto_coma_espacios_y_duplicados(self):
        self.assertEqual(self.correo._destinatarios('a@example.test,b@example.test; c@example.test a@example.test'),
                         ['a@example.test', 'b@example.test', 'c@example.test'])
        self.assertEqual(self.correo._destinatarios(''), [])

    def test_lector_compatibilidad_usa_la_implementacion_canonica(self):
        from config import excel
        self.assertIs(excel.leer_productos_excel, productos.leer_productos_excel)
        self.assertIs(excel.crear_plantilla, productos.crear_plantilla_si_no_existe)
        self.assertIn('supermercado', excel.COLUMNAS)

    def test_entero_positivo_respeta_el_defecto(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.config.obtener_entero_positivo('OPCION_AUSENTE', 3), 3)

    def test_importar_configuracion_no_crea_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            codigo = """import os
from pathlib import Path
from unittest.mock import patch
with patch('dotenv.load_dotenv', return_value=False):
    from config import configuracion, base_datos
assert not Path(os.environ['DB_PATH']).exists()
assert base_datos._ruta_db is None
"""
            env = dict(os.environ, RUTA_BASE=tmp, DB_PATH=str(Path(tmp) / 'no_crear.db'),
                       MAX_REINTENTOS='3', MAX_REINTENTOS_CORREO='3', RETENCION_LOGS_MESES='6')
            import sys
            resultado = subprocess.run([sys.executable, '-B', '-c', codigo], cwd=ROOT, env=env,
                                       capture_output=True, text=True, timeout=20)
            self.assertEqual(resultado.returncode, 0, resultado.stderr)


class CorreosTests(unittest.TestCase):
    def setUp(self):
        from contextlib import ExitStack
        from config import configuracion, correo
        self.config, self.correo = configuracion, correo
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(configuracion, 'MODULO_CORREO', True))
        stack.enter_context(patch.object(configuracion, 'MODULO_CORREO_INICIAL', True))
        stack.enter_context(patch.object(configuracion, 'LISTA_DISTRIBUCION_ERROR', 'operacion@example.test;comun@example.test'))
        stack.enter_context(patch.object(configuracion, 'LISTA_DISTRIBUCION_CORREO', 'resultados@example.test,COMUN@example.test'))
        self.enviar = stack.enter_context(patch.object(correo, 'enviar_email', return_value=True))
        stack.enter_context(patch.object(correo, 'EXCEL_SALIDA', str(ROOT / 'tests/no_existe.xlsx')))

    def test_inicio_ambas_listas_sin_duplicados(self):
        self.assertTrue(self.correo.enviar_correo_inicial())
        self.assertEqual(self.enviar.call_args.args[2], ['operacion@example.test', 'comun@example.test', 'resultados@example.test'])

    def test_falla_solo_lista_error(self):
        self.assertTrue(self.correo.enviar_correo_error('Fallo simulado'))
        self.assertEqual(self.enviar.call_args.args[2], ['operacion@example.test', 'comun@example.test'])

    def test_resultados_ambas_listas_sin_duplicados(self):
        self.assertTrue(self.correo.notificar_resultado_procesamiento({'ok': 2, 'errores': 1, 'fallos': []}))
        self.assertEqual(self.enviar.call_args.args[2], ['operacion@example.test', 'comun@example.test', 'resultados@example.test'])

    def test_inicio_desactivado_y_todo_desactivado(self):
        with patch.object(self.config, 'MODULO_CORREO_INICIAL', False):
            self.assertFalse(self.correo.enviar_correo_inicial())
        self.enviar.assert_not_called()
        with patch.object(self.config, 'MODULO_CORREO', False):
            self.correo.enviar_correo_inicial()
            self.correo.enviar_correo_error('Fallo')
            self.correo.notificar_resultado_procesamiento({'ok': 1, 'errores': 0})
        self.enviar.assert_not_called()

    def test_lista_error_vacia_inicio_llega_a_correo_pero_falla_no(self):
        with patch.object(self.config, 'LISTA_DISTRIBUCION_ERROR', ''):
            self.assertTrue(self.correo.enviar_correo_inicial())
            self.assertEqual(self.enviar.call_args.args[2], ['resultados@example.test', 'COMUN@example.test'])
            self.enviar.reset_mock()
            self.assertFalse(self.correo.enviar_correo_error('Fallo'))
            self.enviar.assert_not_called()
            self.correo.notificar_resultado_procesamiento({'ok': 1, 'errores': 0})
        self.assertEqual(self.enviar.call_args.args[2], ['resultados@example.test', 'COMUN@example.test'])

    def test_fallo_de_envio_no_interrumpe_notificacion_posterior(self):
        self.enviar.side_effect = [OSError('SMTP no disponible'), True]
        self.assertFalse(self.correo.enviar_correo_inicial())
        self.assertTrue(self.correo.notificar_resultado_procesamiento({'ok': 1, 'errores': 0}))
        self.assertEqual(self.enviar.call_count, 2)

    def test_coordinador_envia_inicio_antes_de_cargar_y_resultados_al_final(self):
        from integraciones import robot
        eventos = []
        with patch.object(self.config, 'MODULO_SCRAPING', True), patch.object(self.config, 'configuracion_inicial'), \
             patch.object(robot, 'cargar_excel_a_tabla_cola', side_effect=lambda: eventos.append('carga') or {'archivos_error': 0}), \
             patch.object(robot, 'procesar_cola_a_resultados', return_value={'ok': 1, 'errores': 0}), \
             patch.object(robot, 'generar_resultados_excel'):
            self.enviar.side_effect = lambda titulo, texto, destinatarios, adjuntos: eventos.append('inicio' if 'INICIO' in titulo else 'resultados') or True
            resumen = robot.ejecutar_todo()
        self.assertEqual(resumen['estado'], 'completado')
        self.assertEqual(eventos, ['inicio', 'carga', 'resultados'])
        self.assertEqual(self.enviar.call_args_list[0].args[2], ['operacion@example.test', 'comun@example.test', 'resultados@example.test'])
        self.assertEqual(self.enviar.call_args_list[1].args[2], ['operacion@example.test', 'comun@example.test', 'resultados@example.test'])

    def test_coordinador_falla_no_envia_aviso_de_falla_a_lista_resultados(self):
        from integraciones import robot
        with patch.object(self.config, 'MODULO_SCRAPING', True), patch.object(self.config, 'configuracion_inicial'), \
             patch.object(robot, 'cargar_excel_a_tabla_cola', side_effect=OSError('Entrada inaccesible')), \
             patch.object(robot, 'obtener_capturas', return_value=[]):
            resumen = robot.ejecutar_todo()
        self.assertEqual(resumen['estado'], 'error')
        self.assertEqual(self.enviar.call_count, 2)
        self.assertEqual(self.enviar.call_args_list[0].args[2], ['operacion@example.test', 'comun@example.test', 'resultados@example.test'])
        self.assertEqual(self.enviar.call_args_list[1].args[2], ['operacion@example.test', 'comun@example.test'])


if __name__ == "__main__":
    unittest.main()
