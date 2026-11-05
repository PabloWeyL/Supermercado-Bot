"""Coordinador del flujo de precios; la CLI y las llamadas Python lo comparten."""
from config import configuracion
from config.correo import enviar_correo_inicial, enviar_correo_error, notificar_resultado_procesamiento
from integraciones.cola import cargar_excel_a_tabla_cola
from integraciones.reportes import generar_resultados_excel
from integraciones.scraping import procesar_cola_a_resultados, obtener_capturas


def ejecutar_todo(titulo="Resumen de scraping", mensaje=None):
    """Devuelve un resultado explicito y envia una sola notificacion final.

    Los reintentos de tareas pertenecen al scraper; no se repite el flujo entero.
    """
    if not configuracion.MODULO_SCRAPING:
        return {'estado': 'desactivado', 'ok': 0, 'errores': 0}
    logger = configuracion.obtener_logger('PROCESO')
    try:
        configuracion.configuracion_inicial()
        logger.info('INICIO DEL PROCESO')
        if configuracion.MODULO_CORREO and configuracion.MODULO_CORREO_INICIAL:
            enviar_correo_inicial()
        carga = cargar_excel_a_tabla_cola()
        resumen = procesar_cola_a_resultados() or {'ok': 0, 'errores': 0, 'fallos': [], 'capturas': []}
        resumen = dict(resumen, carga=carga)
        resumen['estado'] = 'con_errores' if resumen['errores'] or carga['archivos_error'] else 'completado'
        generar_resultados_excel()
        notificar_resultado_procesamiento(resumen, titulo=titulo, mensaje=mensaje)
        if resumen['estado'] == 'completado':
            logger.info('PROCESO FINALIZADO CORRECTAMENTE')
        else:
            logger.warning('PROCESO FINALIZADO CON ERRORES')
        return resumen
    except Exception as error:
        logger.exception('PROCESO INTERRUMPIDO')
        enviar_correo_error(str(error), obtener_capturas())
        return {'estado': 'error', 'ok': 0, 'errores': 1, 'error': str(error)}
