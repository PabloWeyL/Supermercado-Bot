"""Limites de entrada y escritura literal de textos en XLSX."""
from pathlib import Path
from io import BytesIO
import re
from xml.parsers import expat
from zipfile import ZipFile, BadZipFile, ZIP_STORED, ZIP_DEFLATED
from zlib import error as ErrorCompresion
from config.configuracion import obtener_entero_positivo

MAX_ARCHIVO = obtener_entero_positivo('MAX_ARCHIVO')
MAX_EXPANDIDO = obtener_entero_positivo('MAX_EXPANDIDO')
MAX_FILAS = obtener_entero_positivo('MAX_FILAS')
MAX_COLUMNAS = obtener_entero_positivo('MAX_COLUMNAS')


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
        _validar_zip(copia)
    except (BadZipFile, expat.ExpatError, ErrorCompresion, EOFError, RuntimeError, NotImplementedError) as error:
        raise ValueError('El Excel esta corrupto, cifrado o contiene XML invalido') from error
    copia.seek(0)
    return copia


def _validar_zip(copia):
    with ZipFile(copia) as archivo:
        entradas = archivo.infolist()
        if len(entradas) > 2000 or sum(f.file_size for f in entradas) > MAX_EXPANDIDO:
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
            parser = _parser_xml_seguro() if entrada.filename.casefold().endswith(('.xml', '.rels')) else None
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


def _parser_xml_seguro():
    parser = expat.ParserCreate(namespace_separator='}')

    def rechazar(*args):
        raise ValueError('El XML del Excel contiene DTD o entidades no permitidas')

    def elemento(nombre, atributos):
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


def texto_entrada(valor, campo, fila, limite, obligatorio=False):
    original = '' if valor is None else str(valor)
    texto = original.strip()
    if len(texto) > limite or re.search(r'[\x00-\x1f\x7f]', original):
        raise ValueError(f'Fila {fila}: {campo} excede el limite o contiene caracteres de control')
    if obligatorio and not texto:
        raise ValueError(f'Fila {fila}: falta {campo}')
    return texto


def agregar_fila_literal(hoja, valores):
    hoja.append(list(valores))
    for celda in hoja[hoja.max_row]:
        if isinstance(celda.value, str):
            # XLSX inline string: conserva el valor sin crear una formula ni alterar precios numericos.
            celda.data_type = 's'
