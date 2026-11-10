"""CLI del robot de precios; la coordinacion del proceso vive en robot.py."""
import argparse
from pathlib import Path

_ERROR_CARGA = None
try:
    from config import configuracion
    from integraciones import robot
    from integraciones.cola import cargar_excel_a_tabla_cola, ver_datos_en_tabla_cola
    from integraciones.reportes import generar_resultados_excel
    from integraciones.arranque import ejecutar_automatico
except (ImportError, RuntimeError, ValueError, OSError) as error:
    _ERROR_CARGA = error


def proceso_por_defecto(configurado=False):
    return robot.ejecutar_todo(configurado=True) if configurado else robot.ejecutar_todo()


def procesar_productos_en_cola():
    """Alias compatible del mismo coordinador usado por la CLI."""
    return robot.ejecutar_todo()


def id_positivo(valor):
    numero = int(valor)
    if numero < 1:
        raise argparse.ArgumentTypeError('El id_solicitud debe ser un entero positivo')
    return numero


def main(argv=None):
    parser = argparse.ArgumentParser(description='Robot de precios de supermercados')
    parser.add_argument('comando', nargs='?', default=None,
                        choices=('procesar', 'cargar', 'estado', 'reporte', 'api-verificar', 'web-procesar',
                                 'correos-estado', 'correos-reintentar', 'correos-resolver'))
    parser.add_argument('--productos-json', type=Path, help='Productos en JSON para procesar sin Excel')
    parser.add_argument('--id-solicitud', type=id_positivo, help='ID seleccionado por el operador; es tambien el ID de ejecucion web')
    parser.add_argument('--id-correo', help='Identificador del correo en la cola persistente')
    parser.add_argument('--destinatario', help='Destinatario concreto al resolver un envio incierto')
    resolucion = parser.add_mutually_exclusive_group()
    resolucion.add_argument('--entregado', action='store_true', help='Confirmar entrega revisada con el proveedor SMTP')
    resolucion.add_argument('--no-entregado', action='store_true', help='Confirmar que el proveedor no entrego el correo incierto')
    args = parser.parse_args(argv)
    automatico = args.comando is None and args.productos_json is None
    args.comando = args.comando or 'procesar'
    if args.id_correo and args.comando not in ('correos-reintentar', 'correos-resolver'):
        parser.error('--id-correo solo se admite para gestionar correos')
    if args.comando == 'correos-resolver':
        if not args.id_correo or not (args.entregado or args.no_entregado):
            parser.error('correos-resolver requiere --id-correo y --entregado o --no-entregado')
    elif args.entregado or args.no_entregado:
        parser.error('La confirmacion de entrega solo se admite con correos-resolver')
    if args.destinatario and args.comando != 'correos-resolver':
        parser.error('--destinatario solo se admite con correos-resolver')
    if args.productos_json and args.comando != 'procesar':
        parser.error('--productos-json solo se admite con procesar')
    contrato = configuracion.obtener_variable_opcional('BACKEND_CONTRATO', 'por_id').lower() if _ERROR_CARGA is None else 'por_id'
    if args.comando == 'web-procesar' and contrato == 'cola_actual' and args.id_solicitud is not None:
        parser.error('cola_actual toma el ID del backend; no admite --id-solicitud')
    if args.comando == 'web-procesar' and contrato != 'cola_actual' and args.id_solicitud is None:
        parser.error('web-procesar requiere --id-solicitud con el ID seleccionado por el operador')
    if args.id_solicitud is not None and args.comando != 'web-procesar':
        parser.error('--id-solicitud solo se admite con web-procesar')
    if _ERROR_CARGA is not None:
        print(f'ERROR: no se pudo cargar la configuracion o las dependencias: {_ERROR_CARGA}')
        return 1
    if args.comando in ('procesar', 'cargar') and not configuracion.MODULO_SCRAPING:
        print('MODULO_SCRAPING=false: automatizacion desactivada')
        return 0
    try:
        if args.comando.startswith('correos-'):
            from config.correo import obtener_cola, reintentar_pendientes
            cola = obtener_cola()
            if args.comando == 'correos-estado':
                for fila in cola.listar():
                    print(fila)
                print('Totales por destinatario:', cola.resumen())
            elif args.comando == 'correos-resolver':
                cantidad = cola.resolver_incierto(args.id_correo, args.entregado, args.destinatario)
                if not cantidad:
                    raise ValueError('No hay destinatarios inciertos que coincidan con esa seleccion')
                print('Resolucion registrada. No se ha repetido el scraping.')
            else:
                if not configuracion.MODULO_CORREO:
                    raise ValueError('MODULO_CORREO=false: no se reenviaran correos')
                if args.id_correo:
                    cola.reactivar(args.id_correo)
                print('Estado de correos:', reintentar_pendientes())
            return 0
        if args.comando == 'web-procesar':
            if not configuracion.MODULO_SCRAPING:
                raise ValueError('MODULO_SCRAPING=false: no se tomaran ejecuciones web')
            from integraciones.weyland_worker import ejecutar_desde_config
            return ejecutar_desde_config('supermercados', configuracion.RUTA_PROYECTO,
                                         solicitud_id=args.id_solicitud)
        if args.comando == 'api-verificar':
            from config.api import crear_cliente_api
            if configuracion.obtener_variable_opcional('BACKEND_AUTH_MODE') == 'weyland':
                from integraciones.api_http import verificar_conexion_weyland
                verificar_conexion_weyland(crear_cliente_api)
            else:
                with crear_cliente_api() as cliente:
                    cliente.verificar()
            print('Conexion API autenticada correctamente.')
            return 0
        if args.comando == 'procesar':
            if automatico:
                modo = configuracion.obtener_variable_opcional('ARRANQUE_MODO', 'web').lower()
                if modo == 'web':
                    from integraciones.arranque_web import ejecutar_web_sin_argumentos
                    return ejecutar_web_sin_argumentos(ejecutar_automatico)
                if modo != 'local':
                    raise ValueError('ARRANQUE_MODO debe ser web o local')
                resumen = ejecutar_automatico(lambda: proceso_por_defecto(configurado=True))
            else:
                resumen = (robot.ejecutar_todo(productos_json=args.productos_json) if args.productos_json
                           else proceso_por_defecto())
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
        from integraciones.trazabilidad import contexto
        with contexto(getattr(error, 'contexto_trazabilidad', None)):
            configuracion.obtener_logger('CLI').error('Error: %s', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
