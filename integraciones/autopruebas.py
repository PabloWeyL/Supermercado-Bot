"""Runner de tests con configuracion y SQLite temporales; no carga el .env privado."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--resultado', type=Path, required=True)
    args = parser.parse_args(argv)
    raiz = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='supermercados-autopruebas-') as temporal, ExitStack() as stack:
        base = Path(temporal)
        stack.enter_context(patch('dotenv.load_dotenv', return_value=False))
        stack.enter_context(patch.dict(os.environ, {
            'BOT_PRUEBAS_EN_CURSO': '1', 'RUTA_BASE': str(base), 'DB_PATH': str(base / 'pruebas.db'),
            'BACKEND_CONTRATO': 'por_id', 'WEYLAND_CORREO_COORDINADOR': 'false',
            'EXCEL_ENTRADA': str(base / 'input/productos.xlsx'), 'EXCEL_SALIDA': str(base / 'reportes/resultados.xlsx'),
            'CARPETA_CAPTURAS': str(base / 'capturas'), 'ID_EJECUCION': '',
            'CORREO_PENDIENTES_DIR': str(base / 'correos'),
            'MAX_ARCHIVO': '20971520', 'MAX_EXPANDIDO': '104857600',
            'MAX_ADJUNTOS_BYTES': '20971520',
            'MAX_FILAS': '50000', 'MAX_COLUMNAS': '100',
            'MODULO_SCRAPING': 'true', 'MODULO_CORREO': 'false', 'MODULO_CORREO_INICIAL': 'false',
        }))
        suite = unittest.defaultTestLoader.discover(str(raiz / 'tests'))
        resultado = unittest.TextTestRunner(verbosity=2).run(suite)
        aprobadas = (resultado.testsRun > 0 and resultado.wasSuccessful()
                     and not resultado.skipped and not resultado.expectedFailures)
        datos = {'ejecutadas': resultado.testsRun, 'fallas': len(resultado.failures),
                 'errores': len(resultado.errors), 'omitidas': len(resultado.skipped), 'aprobadas': aprobadas}
        args.resultado.write_text(json.dumps(datos, indent=2), encoding='utf-8')
        return 0 if aprobadas else 1


if __name__ == '__main__':
    raise SystemExit(main())
