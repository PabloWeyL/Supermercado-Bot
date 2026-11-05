"""CLI del robot de precios; la coordinacion del proceso vive en robot.py."""
import argparse

from config import configuracion
from integraciones import robot
from integraciones.cola import cargar_excel_a_tabla_cola, ver_datos_en_tabla_cola
from integraciones.reportes import generar_resultados_excel


def proceso_por_defecto():
    return robot.ejecutar_todo()


def procesar_productos_en_cola():
    """Alias compatible del mismo coordinador usado por la CLI."""
    return robot.ejecutar_todo()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Robot de precios de supermercados')
    parser.add_argument('comando', nargs='?', default='procesar',
                        choices=('procesar', 'cargar', 'estado', 'reporte'))
    args = parser.parse_args(argv)
    if args.comando in ('procesar', 'cargar') and not configuracion.MODULO_SCRAPING:
        print('MODULO_SCRAPING=false: automatizacion desactivada')
        return 0
    try:
        if args.comando == 'procesar':
            resumen = proceso_por_defecto()
            return 0 if resumen['estado'] in ('completado', 'desactivado') else 1
        configuracion.configuracion_inicial()
        if args.comando == 'cargar':
            carga = cargar_excel_a_tabla_cola()
            return 1 if carga['archivos_error'] else 0
        if args.comando == 'estado':
            ver_datos_en_tabla_cola()
        elif args.comando == 'reporte':
            generar_resultados_excel()
        return 0
    except Exception as error:
        configuracion.obtener_logger('CLI').error('Error: %s', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
