import json
import re
import time
from urllib.parse import quote

from config.base_datos import actualizar_cola, guardar_resultados, obtener_db_conexion_sql3, reencolar_errores
from config.configuracion import REINTENTOS_MAX, RPA_ESPERA_MAX, ahora, obtener_logger, registrar, slug
from integraciones.navegador import abrir_navegador, captura_error, evaluar_js, r
from integraciones.extractores import (
    COMPROBAR_ACCESO_JS,
    COMPROBAR_UBICACION_JS,
    EXTRACTOR_TOTTUS_JS,
    EXTRACTOR_UNIMARC_JS,
)
from integraciones.supermercados import cargar_supermercados
from integraciones.filtros import cumple_filtros, motivo_descarte
_capturas = []

def _capturar(nombre_archivo):
    ruta = captura_error(nombre_archivo)
    if ruta:
        _capturas.append(ruta)
    return ruta

def obtener_capturas():
    return list(_capturas)


def limpiar_precio(texto):
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


def obtener_selectores(datos_supermercado):
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
        return JSON.stringify({url: location.href, titulo: document.title, busqueda_en_campo: campo ? campo.value : null,
            tarjetas: cards.length, tarjetas_con_marca: marcas, muestras: muestras});
    """
    salida = evaluar_js(" ".join(cuerpo.split()), reintentos=1)
    try:
        datos = json.loads(salida)
    except (ValueError, TypeError):
        return {"error": "No se pudo leer el estado del DOM"}
    return datos if isinstance(datos, dict) else {"error": "Respuesta DOM inesperada"}


def esperar_productos(datos_supermercado, max_items=5, marca=None, precio_minimo=None, precio_maximo=None):
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
        salida = evaluar_js(extractor, reintentos=2)
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
        r.dom("return window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
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
        raise ValueError("Busqueda sin tarjetas de productos; comprobar envio, carga o selector de tarjeta: " + json.dumps(estado, ensure_ascii=False))
    return resultados


def enviar_enter(selector):
    """Envia una pulsacion Enter al navegador conectado."""
    if not r.click(selector):
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
        if not r.send(instruccion):
            raise RuntimeError("No se pudo presionar Enter en el buscador")


def navegar_busqueda(datos_supermercado, termino):
    """Abre la busqueda por URL o utiliza el campo configurado del sitio."""
    espera = float(datos_supermercado.get("espera", 6))
    selector = datos_supermercado.get("selector_busqueda")
    if not selector:
        url = datos_supermercado["url_busqueda"].replace("{termino}", quote(termino, safe=""))
        r.url(url)
        time.sleep(espera)
        return url

    url = datos_supermercado["url_inicio"]
    if not r.url(url):
        raise RuntimeError("No se pudo abrir la pagina de " + datos_supermercado["nombre"])
    time.sleep(espera)
    fin = time.monotonic() + RPA_ESPERA_MAX
    while not r.present(selector):
        salida = evaluar_js(COMPROBAR_ACCESO_JS + "return JSON.stringify({});", reintentos=1)
        try:
            estado = json.loads(salida)
        except (TypeError, ValueError):
            estado = None
        if isinstance(estado, dict) and estado.get("error"):
            raise ValueError(estado["error"])
        if time.monotonic() >= fin:
            raise TimeoutError("No se encontro el campo de busqueda: " + selector)
        time.sleep(0.5)

    if not r.type(selector, "[clear]" + termino):
        raise RuntimeError("No se pudo escribir o enviar la busqueda en " + datos_supermercado["nombre"])
    time.sleep(0.5)
    enviar_enter(selector)
    time.sleep(float(datos_supermercado.get("espera_resultados", espera)))
    return url


def buscar_producto(datos_supermercado, termino, max_items=5, marca=None, precio_minimo=None, precio_maximo=None):
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
        if not cumple_filtros(datos, precio, marca, precio_minimo, precio_maximo):
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


def procesar_tarea(datos_supermercado, termino, id_tarea, clave_super, producto, max_items=5,
                   marca=None, precio_minimo=None, precio_maximo=None):
    reintento = 0
    while reintento <= REINTENTOS_MAX:
        try:
            productos = buscar_producto(datos_supermercado, termino, max_items=max_items,
                                        marca=marca, precio_minimo=precio_minimo, precio_maximo=precio_maximo)
            guardar_resultados(id_tarea, clave_super, producto, productos, reintento, ahora())
            for datos in productos:
                print("   OK: " + datos["nombre"] + " -> " + str(datos["precio"]))
            registrar("Guardados " + str(len(productos)) + " resultados de '" + producto + "'", "SCRAPING")
            return True
        except Exception as error:
            reintento += 1
            registrar("ERROR intento " + str(reintento) + " " + datos_supermercado["nombre"]
                      + " " + producto + ": " + str(error), "SCRAPING")
            if reintento <= REINTENTOS_MAX:
                actualizar_cola(id_tarea, "pendiente", reintento)
                time.sleep(2 * reintento)
            else:
                _capturar(slug(clave_super) + "_" + slug(producto))
                actualizar_cola(id_tarea, "error", reintento, ahora(), error=str(error))
                print("   Error: " + str(error))
    return False


def procesar_cola_a_resultados():
    logger = obtener_logger("SCRAPING")
    _capturas.clear()
    reencolados = reencolar_errores()
    if reencolados:
        logger.info("Reencoladas " + str(reencolados) + "tareas con error a reintentar")
    
    conexion = obtener_db_conexion_sql3()
    pendientes = conexion.execute(
        "SELECT id, supermercado, producto, marca, precio_minimo, precio_maximo FROM cola WHERE estado='pendiente' "
        "ORDER BY id"
    ).fetchall()
    conexion.close()

    if not pendientes:
        logger.info("No hay tareas pendientes. Usa: python main.py cargar")
        return None

    supermercados = cargar_supermercados()
    ok = 0
    errores = 0
    abrir_navegador()
    try:
        for (id_tarea, clave_super, producto, marca, minimo, maximo) in pendientes:
            datos_supermercado = supermercados.get(clave_super)
            if not datos_supermercado or not datos_supermercado.get("habilitado", True):
                _capturar(slug(clave_super) + "_" + slug(producto))
                actualizar_cola(id_tarea, "error", 0, ahora(),
                                error="supermercado desconocido o deshabilitado")
                errores += 1
                continue

            print("Buscando '" + producto + "' en " + datos_supermercado["nombre"])
            if procesar_tarea(datos_supermercado, producto, id_tarea, clave_super, producto,
                              marca=marca, precio_minimo=minimo, precio_maximo=maximo):
                ok += 1
            else:
                errores += 1
    finally:
        try:
            r.close()
        except Exception:
            pass

    print("Fin del procesamiento. OK: " + str(ok) + " | Errores: " + str(errores))
    conexion = obtener_db_conexion_sql3()
    fallos = conexion.execute(
        "SELECT producto, supermercado, error FROM cola WHERE estado='error' ORDER BY id"
    ).fetchall()
    conexion.close()
    return {"ok": ok, "errores": errores, "fallos": fallos, "capturas": obtener_capturas()}


def diagnosticar(clave, termino="leche", marca=None, precio_minimo=None, precio_maximo=None):
    supermercados = cargar_supermercados()
    datos = supermercados.get(clave)
    if not datos:
        print("Supermercado desconocido: " + clave)
        print("Opciones: " + ", ".join(supermercados.keys()))
        return

    print("Diagnosticando '" + clave + "' con el término '" + termino + "'")

    abrir_navegador()
    try:
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
            r.close()
        except Exception:
            pass
