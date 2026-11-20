# ======================
# IMPORT
# ======================

"""Orquestador: configuración, notificaciones, automatización y reporte."""

from config.archivos import crear_error_clasificado, es_error

from types import SimpleNamespace

import argparse
import json
import os
import subprocess
import sys

from config.fallos import preparar_consola, registrar_fallo, registrar_fallo_no_capturado
import logging

sys.excepthook = registrar_fallo_no_capturado
import time
from config.archivos import (
    crear_error_weyland,
)
from contextlib import nullcontext
from pathlib import Path

CARGA_INVALIDA = False
try:
    from config import configuracion, correo, base_datos
    from config.archivos import (rutas_ejecucion, bloqueo, confinar, crear_respaldo_historico,
                                guardar_json, EXTENSIONES_EXCEL, buscar_carpeta_ejecucion_web)
    from integraciones.weyland import crear_cliente_api, guardar_maquina_verificada, leer_valores, sesion_cliente
    from integraciones.weyland import crear_proceso_bot, crear_resultado_bot, correo_permitido, ejecutar_cola_configurada
    from integraciones import supermercados, reportes
except (ImportError, RuntimeError, ValueError, OSError) as error:
    registrar_fallo("IMPORT", error)
    CARGA_INVALIDA = True

CODIGOS_INTERRUMPIDOS = frozenset((-2, -9, -15, -1073741510, 3221225786))


# ======================
# ERRORES DE INICIO
# ======================
def crear_error_correo_inicial(mensaje):
    return crear_error_clasificado(mensaje, 'ErrorCorreoInicial')


# ======================
# FUNCIONES DE REINTENTOS Y PREPARACIÓN
# ======================
def ejecutar_modulo(nombre, accion, intentos, *, al_fallar=None):
    """Reintentos de una etapa concreta; nunca envuelve la toma o entrega web."""
    logger = configuracion.obtener_logger('ORQUESTADOR')
    for intento in range(intentos):
        try:
            return accion(intento)
        except Exception as error:
            final = intento + 1 == intentos
            logger.warning('%s: INTENTO %s/%s FALLIDO: %s', nombre, intento + 1, intentos, error)
            if al_fallar is not None:
                al_fallar(error, intento + 1, final)
            if final:
                raise
            time.sleep(min(configuracion.REINTENTOS_PAUSA * (intento + 1), configuracion.REINTENTOS_PAUSA_MAX))
    raise ValueError('Configurar al menos un intento para el modulo')


def preparar_proceso(*, remoto=False):
    """Inicializa logging e ID, revisa dependencias y verifica SQLite."""
    configuracion.configuracion_inicial()
    configuracion.validar_configuracion(configuracion)
    catalogo = supermercados.validar_catalogo()
    configuracion.validar_dependencias(configuracion.RUTA_PROYECTO)
    configuracion.revisar_integridad(configuracion.RUTA_DB_SQLITE)
    configuracion.revisar_base_operativa(configuracion)
    if not remoto:
        supermercados.validar_entradas(configuracion, catalogo)


# ======================
# FUNCIONES DE CORREO INICIAL
# ======================
def enviar_inicio():
    """Envía el aviso inicial habilitado y aplica sus intentos antes de iniciar el negocio."""
    if not (configuracion.MODULO_CORREO and configuracion.MODULO_CORREO_INICIAL):
        return

    def enviar(_intento):
        """Realiza un intento de correo inicial y falla si no se confirma su entrega."""
        if not correo.enviar_correo_inicial():
            raise crear_error_correo_inicial('Correo inicial pendiente; revisar la cola de correo')
        return True

    return ejecutar_modulo('Módulo 1 - Correo inicial', enviar, configuracion.MAX_REINTENTOS_CORREO)


# ======================
# FUNCIONES DE EJECUCIÓN LOCAL
# ======================
def ejecutar_local(productos_json=None, *, preparado=False):
    """Flujo de negocio local o subproceso de una solicitud web."""
    with supermercados.sesion_automatizacion():
        if not preparado:
            preparar_proceso(remoto=productos_json is not None)
        logger = configuracion.obtener_logger('PROCESO')
        notifica_web = configuracion.obtener_booleano('WEYLAND_CORREO_COORDINADOR', False)
        logger.info('INICIO DEL PROCESO')
        archivos = []
        try:
            reanudar = configuracion.IDENTIFICADOR_LOCAL.get() if not notifica_web else None
            rutas_reanudables = None
            if reanudar:
                existentes = base_datos.archivos_por_ejecucion(reanudar)
                if not existentes:
                    raise RuntimeError("ID_EJECUCION NO EXISTE EN QUEUE_SUPERMERCADOS")
                rutas_reanudables = {Path(ruta).resolve() for ruta, estado in existentes
                                     if ruta and estado not in ('FINALIZADO', 'ERROR', 'PROCESADO')}
                if not rutas_reanudables:
                    estados = {estado for _, estado in existentes}
                    if 'PROCESADO' in estados and configuracion.MODULO_CORREO:
                        confirmados = correo.obtener_cola().cierres_confirmados()
                        if any(cierre.get('solicitud_id') is None
                               and str(cierre.get('id_ejecucion')) == str(reanudar) for cierre in confirmados):
                            base_datos.confirmar_cierre_por_correo(reanudar)
                            estados = {estado for _, estado in base_datos.archivos_por_ejecucion(reanudar)}
                    logger.info("ID DE EJECUCIÓN SIN TRABAJO REANUDABLE | ESTADOS: %s", ', '.join(sorted(estados)))
                    return {'estado': 'correo_pendiente' if 'PROCESADO' in estados else 'completado'}
            # El aviso inicial pertenece a la ejecución nueva.
            if not notifica_web and not reanudar:
                enviar_inicio()

            # Módulo 2: completar un archivo antes de cargar y consultar el siguiente.
            archivos = ([Path(productos_json)] if productos_json else supermercados.listar_archivos_entrada())
            if rutas_reanudables is not None:
                archivos = [archivo for archivo in archivos if archivo.resolve() in rutas_reanudables]
                if not archivos:
                    raise RuntimeError("EL EXCEL ORIGINAL DE ID_EJECUCION NO ESTÁ DISPONIBLE")
            if not notifica_web:
                pendientes = base_datos.archivos_con_cierre_pendiente()
                omitidos = [archivo for archivo in archivos if archivo.resolve() in pendientes]
                if omitidos:
                    logger.warning('%s ENTRADA(S) ESPERAN LA CONFIRMACIÓN DEL CORREO DE REPORTE', len(omitidos))
                    archivos = [archivo for archivo in archivos if archivo.resolve() not in pendientes]
                    if not archivos:
                        return {'estado': 'correo_pendiente'}
            else:
                omitidos = []
            if not archivos and not notifica_web:
                logger.info("SIN DATOS PARA PROCESAR")
                correo.enviar_correo_sin_datos()
            resumen = {'ok': 0, 'errores': 0, 'fallos': [], 'capturas': [], 'carga': {
                'archivos_leidos': 0, 'archivos_error': 0, 'tareas_agregadas': 0, 'tareas_existentes': 0}}
            with base_datos.ambito_archivos(archivos):
                for numero, archivo in enumerate(archivos, start=1):
                    logger.info('PROCESANDO ARCHIVO %s DE %s: %s', numero, len(archivos), archivo.name)
                    with base_datos.ambito_archivos([archivo]):
                        carga = (supermercados.cargar_productos_json_a_tabla_cola(archivo)
                                 if productos_json else supermercados.cargar_excel_a_tabla_cola(archivo))
                        parcial = (supermercados.procesar_cola_a_resultados(ejecutar_modulo)
                                   if not carga['archivos_error'] else None)
                        base_datos.procesar_archivo(archivo)
                    for campo in resumen['carga']:
                        resumen['carga'][campo] += carga[campo]
                    if parcial:
                        resumen['ok'] += parcial['ok']
                        resumen['errores'] += parcial['errores']
                        resumen['fallos'].extend(parcial.get('fallos', []))
                        resumen['capturas'].extend(parcial.get('capturas', []))
                resumen['capturas'] = list(dict.fromkeys(resumen['capturas']))
                observaciones = min(resumen['errores'], sum(supermercados.es_observacion_busqueda(f[2])
                                                           for f in resumen['fallos']))
                resumen['observaciones'] = observaciones
                resumen['errores_tecnicos'] = resumen['errores'] - observaciones + resumen['carga']['archivos_error']
                resumen['estado'] = ('con_errores' if resumen['errores_tecnicos'] else
                                     'completado_con_observaciones' if observaciones else 'completado')

                # Módulo 3: un único Excel consolidado para todos los archivos de la solicitud.
                ejecutar_modulo('Módulo 3 - Reporte', lambda _: reportes.generar_resultados_excel(),
                               configuracion.MAX_REINTENTOS)
            fuentes = {f"input/{numero}": archivo for numero, archivo in enumerate(archivos, 1)}
            fuentes["reportes"] = Path(configuracion.EXCEL_SALIDA)
            ejecutar_modulo("Módulo 4 - Respaldo", lambda _: crear_respaldo_historico(
                configuracion.RUTA_HISTORICO, configuracion.NOMBRE_PROCESO,
                base_datos.id_ejecucion_operativo(), fuentes,
                configuracion.RETENCION_HISTORICO_MESES), configuracion.MAX_REINTENTOS)
            if resumen['errores_tecnicos']:
                base_datos.registrar_error_ejecucion(archivos)
                if not notifica_web:
                    correo.enviar_correo_error('La ejecución contiene errores técnicos.')
            else:
                correo_confirmado = not configuracion.MODULO_CORREO
                base_datos.cerrar_archivos(archivos, configuracion.EXCEL_SALIDA,
                                          correo_confirmado and not notifica_web)
                if archivos and not notifica_web and configuracion.MODULO_CORREO:
                    correo_confirmado = correo.enviar_correo_reporte(resumen) is True
                    if correo_confirmado:
                        base_datos.confirmar_cierre_por_correo(base_datos.id_ejecucion_operativo())
                if archivos and not notifica_web and not correo_confirmado:
                    logger.error('REPORTE PENDIENTE DE CONFIRMACIÓN SMTP')
                    resumen['estado'] = 'correo_pendiente'
            if omitidos and resumen['estado'] in ('completado', 'completado_con_observaciones'):
                resumen['estado'] = 'correo_pendiente'
            return resumen
        except Exception as error:
            error.id_ejecucion_local = (configuracion.IDENTIFICADOR_LOCAL.get()
                                        or configuracion.actual()['activacion_id'])
            error.fallo_registrado = registrar_fallo("PROCESO LOCAL", error) is not None
            logger.exception('PROCESO INTERRUMPIDO')
            try:
                base_datos.registrar_error_ejecucion(archivos)
            except Exception:
                logger.exception('NO SE PUDO PERSISTIR EL ESTADO ERROR')
            if not notifica_web:
                correo.enviar_correo_error('No se pudo completar el proceso; revisar el diagnóstico local.')
            raise
        finally:
            logger.info('FIN DEL PROCESO')


# ======================
# FUNCIONES DE EJECUCIÓN WEB
# ======================
def procesar_solicitud(carpeta, entrada, parametros):
    """Procesa un input, una secuencia de inputs o parámetros JSON y devuelve un único reporte."""
    rutas = rutas_ejecucion(carpeta)
    with configuracion.contexto(json.loads(rutas.contexto.read_text(encoding='utf-8'))):
        argumentos = ['procesar']
        entradas = ([Path(entrada)] if isinstance(entrada, (str, Path)) else
                    list(entrada) if entrada is not None else [])
        if entrada is not None:
            if not entradas or any(not isinstance(p, Path) or p.is_symlink()
                    or not p.resolve().is_relative_to(rutas.input.resolve()) for p in entradas):
                raise crear_error_weyland('Las entradas no pertenecen a la carpeta de la solicitud')
            disponibles = {p.resolve() for p in supermercados.listar_archivos_entrada(rutas.input)}
            if disponibles != {p.resolve() for p in entradas}:
                raise crear_error_weyland('La carpeta contiene entradas no confirmadas por la solicitud')
        if entrada is None:
            productos = parametros.get('productos', [parametros] if parametros.get('producto') else [])
            if isinstance(productos, str):
                productos = json.loads(productos)
            rutas.parametros.mkdir(parents=True, exist_ok=True)
            guardar_json(rutas.productos_json, {'productos': productos})
            supermercados.leer_productos_json(rutas.productos_json)
            argumentos += ['--productos-json', str(rutas.productos_json)]
        rutas.reportes.mkdir(parents=True, exist_ok=True)
        variables = {**os.environ, 'RUTA_BASE': str(rutas.datos),
            'RUTA_HISTORICO': str(rutas.historico), 'RUTA_LOGS': str(rutas.logs),
            'CARPETA_INPUTS': str(rutas.input),
            'EXCEL_ENTRADA': str(entradas[0] if len(entradas) == 1 else rutas.input),
            'EXCEL_SALIDA': str(rutas.reporte_excel),
            'CARPETA_CAPTURAS': str(rutas.capturas), 'CARPETA_DESCARGAS': str(rutas.descargas),
            'DB_PATH': str(rutas.base_datos), 'ID_EJECUCION': '',
            'WEYLAND_SOLICITUD_ID': str(carpeta.name),
            'BOT_ACTIVACION_ID': configuracion.actual()['activacion_id'],
            'WEYLAND_CORREO_COORDINADOR': 'true', 'CORREO_PENDIENTES_DIR': str(correo.ruta_cola())}
        with rutas.log_proceso.open('ab') as log:
            resultado = subprocess.run([sys.executable, str(configuracion.RUTA_PROYECTO / 'main.py'),
                *argumentos], cwd=configuracion.RUTA_PROYECTO, env=variables,
                stdout=log, stderr=subprocess.STDOUT, check=False)
        resumen = reportes.leer_resumen(carpeta)
        if resultado.returncode in CODIGOS_INTERRUMPIDOS:
            raise reportes.crear_error_proceso_bot('El subproceso fue interrumpido.', resumen, 'INTERRUMPIDO')
        if resultado.returncode != 0 or resumen and (resumen['errores'] or resumen['pendientes']):
            raise reportes.crear_error_proceso_bot('El proceso terminó con errores técnicos.', resumen)
        reporte = rutas.reporte_excel
        if (not reporte.is_file() or reporte.is_symlink()
                or not reporte.resolve().is_relative_to(rutas.datos.resolve())):
            raise crear_error_weyland('Falta el reporte Excel válido de la ejecución')
        return crear_resultado_bot((reporte,), resumen=resumen or {'procesados': 1, 'correctos': 1, 'errores': 0},
                            detalles={'trazabilidad': configuracion.actual()}, incluir_metadatos_export=False)


def notificar_solicitud(evento, estado, carpeta):
    """Envía el aviso de una solicitud usando su contexto y el resultado confirmado por la web."""
    with configuracion.contexto({'solicitud_id': estado['ejecucion_id'],
            'ejecucion_id_web': estado['ejecucion_id'], 'activacion_id': estado['id_externo']}):
        if evento == 'inicio':
            return enviar_inicio()
        final = estado.get('estado_bot_publicado', 'ERROR')
        enviar = correo_permitido(estado) and configuracion.MODULO_CORREO
        if evento == 'resultados':
            rutas = rutas_ejecucion(carpeta)
            confirmado = not enviar
            if enviar:
                entrega = confinar(carpeta, estado['entrega']['ruta'])
                confirmado = correo.enviar_correo_reporte(
                    mensaje=f'Estado del proceso: {final}. Confirmado por Weyland Automate.' + correo.referencia_web(),
                    adjuntos=[entrega]) is True
            if rutas.base_datos.is_file():
                with base_datos.ambito_base_datos(rutas.base_datos):
                    base_datos.cerrar_archivos(None, rutas.reporte_excel, confirmado)
            return confirmado
        if enviar:
            return correo.enviar_correo_error(f'Estado del proceso confirmado por Weyland Automate: {final}')
        return False


def ejecutar_web(maquina_id=None):
    """Registra el adaptador del proceso y coordina una solicitud cuando el backend está habilitado."""
    if not configuracion.BACKEND_ACTIVO:
        raise ValueError('BACKEND_ACTIVO=false: conexión al backend desactivada')
    valores = leer_valores()
    codigo = str(valores.get('BACKEND_PROCESO_CODIGO') or '').strip()
    if not codigo:
        raise ValueError('Configurar BACKEND_PROCESO_CODIGO registrado en la web')
    proceso = crear_proceso_bot(procesar_solicitud, requiere_input=None,
                         extensiones=EXTENSIONES_EXCEL, informar_estados=True)
    resultado = ejecutar_cola_configurada(lambda: crear_cliente_api(maquina_id=maquina_id),
                                          valores, configuracion.RUTA_PROYECTO,
                                          {codigo: proceso}, notificar=notificar_solicitud)
    diagnostico = diagnosticar_resultado(resultado)
    logger = configuracion.obtener_logger('WEYLAND', resultado.get('ejecucion_id'))
    logger.info('WEYLAND AUTOMATE: %s', resultado['fase'])
    logger.log(logging.INFO if diagnostico.codigo == 0 else logging.ERROR, diagnostico.texto.upper())
    return diagnostico.codigo


# ======================
# FUNCIONES DE RECUPERACIÓN DE CORREOS
# ======================
def aplicar_cierres_de_correo(cola):
    """Finaliza las ejecuciones locales o web cuyo reporte ya fue aceptado."""

    valores = None
    for cierre in cola.cierres_confirmados():
        solicitud = cierre.get('solicitud_id')
        if solicitud is None:
            base = Path(configuracion.RUTA_DB_SQLITE)
        else:
            if valores is None:
                valores = leer_valores()
            if type(solicitud) is not int:
                continue
            trabajos = Path(valores.get('BACKEND_TRABAJOS_DIR') or 'temporales/weyland')
            trabajos = (configuracion.RUTA_PROYECTO / trabajos).resolve()
            carpeta = buscar_carpeta_ejecucion_web(trabajos, solicitud)
            if carpeta is None:
                continue
            base = rutas_ejecucion(carpeta).base_datos
        if base.is_file():
            with base_datos.ambito_base_datos(base):
                base_datos.confirmar_cierre_por_correo(cierre['id_ejecucion'])


def gestionar_correos(args):
    """Consulta o recupera destinatarios pendientes sin volver a ejecutar la automatización."""
    logger = configuracion.obtener_logger('CORREOS')
    cola = correo.obtener_cola()
    if args.comando == 'correos-estado':
        for fila in cola.listar():
            logger.info("MENSAJE %s | ESTADO %s | INTENTOS %s | CÓDIGO %s",
                        fila["mensaje_id"], fila["estado"], fila["intentos"], fila["codigo"] or "-")
        logger.info('TOTALES POR ESTADO DE CORREO: %s', cola.resumen())
        return 0
    if args.comando == 'correos-resolver':
        if not args.id_correo or not (args.entregado or args.no_entregado):
            raise ValueError('Indicar --id-correo y --entregado o --no-entregado')
        if not cola.resolver_incierto(args.id_correo, args.entregado, args.destinatario):
            raise ValueError('No hay destinatarios inciertos para esa selección')
        if args.entregado:
            aplicar_cierres_de_correo(cola)
        logger.info('RESOLUCIÓN DE CORREO REGISTRADA')
        return 0
    if not configuracion.MODULO_CORREO:
        raise ValueError('MODULO_CORREO=false: no se reenviarán correos')
    if args.id_correo:
        if not cola.resumen(solo=args.id_correo):
            raise ValueError('No existe el correo seleccionado')
        cola.reactivar(args.id_correo)
    diagnostico = diagnosticar_correos(correo.reintentar_pendientes(solo=args.id_correo, propagar_error=True))
    aplicar_cierres_de_correo(cola)
    logger.info(diagnostico.texto.upper())
    return diagnostico.codigo


# ======================
# ARGUMENTOS Y PUNTO DE ENTRADA
# ======================
def argumentos(argv=None):
    """Interpreta los comandos y rechaza combinaciones de argumentos incompatibles."""
    parser = argparse.ArgumentParser(description='RPA de supermercados conectado a Weyland Automate')
    parser.add_argument('comando', nargs='?', choices=('procesar', 'web-procesar', 'api-verificar',
        'cargar', 'estado', 'reporte', 'correos-estado', 'correos-reintentar', 'correos-resolver'))
    parser.add_argument('--productos-json', type=Path)
    parser.add_argument('--maquina-id', type=int, help='ID explícito de Weyland para esta corrida')
    parser.add_argument('--guardar-maquina', action='store_true', help='Guarda la máquina tras verificarla sin tomar trabajo')
    parser.add_argument('--ipv4-publica', help='IPv4 pública registrada para esta máquina; usada con --guardar-maquina')
    parser.add_argument('--id-correo')
    parser.add_argument('--destinatario')
    resolucion = parser.add_mutually_exclusive_group()
    resolucion.add_argument('--entregado', action='store_true')
    resolucion.add_argument('--no-entregado', action='store_true')
    args = parser.parse_args(argv)
    if args.maquina_id is not None and (args.maquina_id < 1 or args.productos_json
            or args.comando not in (None, 'web-procesar', 'api-verificar')):
        parser.error('--maquina-id requiere un ID positivo y un comando web')
    if args.guardar_maquina and (args.comando != 'api-verificar'
            or args.maquina_id is None or not args.ipv4_publica):
        parser.error('--guardar-maquina requiere api-verificar, --maquina-id y --ipv4-publica')
    if args.ipv4_publica and not args.guardar_maquina:
        parser.error('--ipv4-publica solo se admite con --guardar-maquina')
    if args.productos_json and args.comando not in (None, 'procesar'):
        parser.error('--productos-json solo se admite para procesar')
    if args.comando != 'correos-resolver' and (args.entregado or args.no_entregado or args.destinatario):
        parser.error('La resolución de entrega solo se admite con correos-resolver')
    if args.id_correo and args.comando not in ('correos-reintentar', 'correos-resolver'):
        parser.error('--id-correo solo se admite para gestionar correos')
    return args


def verificar_id_configurado(args, web):
    """Comprueba el ID antes del correo, la carga, el reporte o la toma web."""

    valor = configuracion.ID_EJECUCION_ENV
    if not valor or args.comando == 'estado':
        return False
    identificador = configuracion.interpretar_id_ejecucion(valor)
    configuracion.configurar_logging()
    logger = configuracion.obtener_logger("INICIO", identificador)
    estados = base_datos.estados_id_local(configuracion.RUTA_DB_SQLITE, identificador)
    if all(estado in ("FINALIZADO", "ERROR") for estado in estados):
        for estado in sorted(estados):
            logger.info("LA EJECUCIÓN %s YA ESTÁ %s", identificador, estado)
        return True
    if web or args.comando in ('cargar', 'reporte'):
        raise RuntimeError("USA PROCESAR PARA REANUDAR EL ID_EJECUCION LOCAL")
    return False


def main(argv=None):
    """Selecciona el modo, valida el arranque, impide ejecuciones simultáneas y devuelve el código de salida."""
    args = argumentos(argv)
    if CARGA_INVALIDA:
        logger = logging.getLogger('SUPERMERCADOS')
        preparar_consola(logger)
        logger.error('CONFIGURACIÓN INVÁLIDA. REVISAR .ENV Y DEPENDENCIAS')
        return 2
    try:
        with base_datos.ambito_base_datos(), configuracion.ambito_identificacion():
            if args.comando == 'api-verificar':
                if not configuracion.BACKEND_ACTIVO:
                    configuracion.obtener_logger('API').info('BACKEND DESACTIVADO POR CONFIGURACIÓN')
                    return 0
                with sesion_cliente(crear_cliente_api(maquina_id=args.maquina_id)) as cliente:
                    maquina = cliente.verificar()
                    if args.guardar_maquina:
                        guardar_maquina_verificada(configuracion.RUTA_PROYECTO, cliente, maquina, args.ipv4_publica)
                    elif cliente.ipv4_esperada is not None:
                        cliente.verificar_ipv4()
                        configuracion.obtener_logger('API').info('IPV4 PÚBLICA CONFIRMADA')
                    if args.guardar_maquina:
                        configuracion.obtener_logger('API').info('MÁQUINA %s GUARDADA PARA ARRANQUES SIN ARGUMENTOS', maquina)
                configuracion.obtener_logger('API').info('CONEXIÓN JWT AUTENTICADA PARA MÁQUINA %s. NO SE TOMÓ TRABAJO', maquina)
                return 0
            if args.comando and args.comando.startswith('correos-'):
                return gestionar_correos(args)
            web = configuracion.BACKEND_ACTIVO and (
                args.comando == 'web-procesar' or args.comando is None and not args.productos_json)
            local_input = not web and args.productos_json is None and args.comando in (None, 'procesar', 'cargar')
            raiz_lock = configuracion.RUTA_BASE / '.runtime'
            raiz_lock.mkdir(parents=True, exist_ok=True)
            with configuracion.contexto(), bloqueo(raiz_lock / 'arranque.lock'):
                if verificar_id_configurado(args, web):
                    return 0
                if (not configuracion.MODULO_AUTOMATIZACION_SUPERMERCADOS
                        and args.comando in (None, 'procesar', 'web-procesar', 'cargar')):
                    configuracion.obtener_logger('INICIO').info('MÓDULO SUPERMERCADOS DESACTIVADO POR CONFIGURACIÓN')
                    return 0
                if not configuracion.BACKEND_ACTIVO:
                    if args.comando == 'web-procesar' or args.productos_json is not None or args.maquina_id is not None:
                        raise ValueError('BACKEND_ACTIVO=false: ejecutar sin parámetros para leer solo CARPETA_INPUTS')
                ambito = (base_datos.ambito_archivos(supermercados.listar_archivos_entrada())
                          if local_input else nullcontext())
                with ambito:
                    if args.comando in ('estado', 'reporte', 'cargar'):
                        configuracion.configuracion_inicial()
                    else:
                        preparar_proceso(remoto=web or args.productos_json is not None)
                    if web:
                        return ejecutar_web(args.maquina_id)
                    if args.comando == 'cargar':
                        return 1 if supermercados.cargar_excel_a_tabla_cola()['archivos_error'] else 0
                    if args.comando == 'estado':
                        supermercados.ver_datos_en_tabla_cola()
                        return 0
                    if args.comando == 'reporte':
                        reportes.generar_resultados_excel()
                        return 0
                    resumen = ejecutar_local(args.productos_json, preparado=True)
                    return 0 if resumen['estado'] in ('completado', 'completado_con_observaciones') else 1
    except Exception as error:
        if es_error(error, 'ErrorCorreoInicial'):
            diagnostico = crear_diagnostico(1, 'CORREO_INICIAL_FALLIDO', 'No se confirmó el correo inicial; el negocio no se inició.')
        else:
            diagnostico = diagnosticar_error(error, correo=bool(args.comando and args.comando.startswith('correos-')))
        if not getattr(error, "fallo_registrado", False):
            registrar_fallo("ORQUESTADOR", error)
        with configuracion.contexto(getattr(error, 'contexto_trazabilidad', None)):
            configuracion.obtener_logger('ORQUESTADOR', getattr(error, 'id_ejecucion_local', None)).exception(diagnostico.texto)
        return diagnostico.codigo




# ======================
# DIAGNÓSTICOS DE EJECUCIÓN
# ======================
def crear_diagnostico(codigo, nombre, mensaje):
    """Prepara el diagnóstico y su texto de salida."""

    return SimpleNamespace(
        codigo=codigo, nombre=nombre, mensaje=mensaje,
        texto=f"[{nombre}] {mensaje} (salida {codigo})",
    )


def diagnosticar_resultado(estado):
    """Traduce la fase final del coordinador a un diagnóstico operativo."""
    fase = estado.get('fase')
    if fase == 'SIN_TRABAJO':
        return crear_diagnostico(0, 'SIN_SOLICITUDES', 'No hay solicitudes pendientes. Finalizacion normal; no se proceso trabajo.')
    if fase == 'COMPLETADA':
        mensaje = ('Proceso finalizado con observaciones; reporte confirmado por el backend.'
                   if estado.get('estado_bot_publicado') == 'FINALIZADO_CON_OBSERVACIONES'
                   else 'Proceso terminado y resultado confirmado por el backend.')
        return crear_diagnostico(0, 'COMPLETADA', mensaje)
    if fase == 'CANCELADO':
        return crear_diagnostico(1, 'CANCELADO', 'Cancelacion confirmada por el backend; no se repetira el trabajo.')
    if fase == 'INTERRUMPIDO':
        return crear_diagnostico(1, 'INTERRUMPIDO', 'Interrupcion confirmada por el backend; requiere un reproceso autorizado.')
    if estado.get('etapa_error') == 'input':
        return crear_diagnostico(6, 'INPUT_INVALIDO', 'La entrada no es compatible o no pudo obtenerse. El fallo fue notificado al backend.')
    return crear_diagnostico(1, 'PROCESAMIENTO_FALLIDO', 'El procesamiento fallo. El fallo fue notificado al backend; revisar la evidencia privada.')


def diagnosticar_error(error, *, correo=False):
    """Clasifica el fallo y conserva las restricciones de recuperación ante operaciones inciertas."""
    fase = getattr(error, 'fase_weyland', None)
    status = getattr(error, 'status_code', None)
    if correo:
        return crear_diagnostico(9, 'CORREO_REQUIERE_REVISION', 'No se completo la gestion de correos. Revisar la cola; no se reinicio el negocio ni la entrega web.')
    if es_error(error, 'ErrorWeyland') and error.tipo == 'SIN_PENDIENTES':
        return diagnosticar_resultado({'fase': 'SIN_TRABAJO'})
    if status in (401, 403):
        mensaje = ('El backend rechazo la autenticacion. Revisar BACKEND_API_KEY y la sesion BOT.' if status == 401
                   else 'El backend rechazo los permisos de la operacion. Revisar la autorizacion del bot.')
        if fase in ('TOMA_INCIERTA', 'ENTREGA_INCIERTA', 'ERROR_INCIERTO'):
            mensaje += ' Se conserva el checkpoint; revisar la web antes de reenviar.'
        return crear_diagnostico(3, 'AUTENTICACION_RECHAZADA' if status == 401 else 'PERMISOS_RECHAZADOS', mensaje)
    if fase in ('ENTREGA_INCIERTA', 'ERROR_INCIERTO'):
        return crear_diagnostico(7, 'ENTREGA_SIN_CONFIRMAR', 'El backend no confirmo la entrega del resultado o del fallo. Se conserva el checkpoint; verificar antes de reenviar.')
    if fase == 'TOMA_INCIERTA':
        return crear_diagnostico(4, 'ASIGNACION_SIN_CONFIRMAR', 'No se confirmo la toma de trabajo. Conservar el checkpoint y verificar la web antes de otra asignacion.')
    if fase == 'ASIGNACION_INCOMPATIBLE':
        return crear_diagnostico(5, 'PROCESO_INCOMPATIBLE', 'La asignacion no coincide con la maquina, proceso o contrato. Puede estar tomada en la web; requiere revision.')
    if fase == 'DESCARGANDO':
        return crear_diagnostico(6, 'DESCARGA_FALLIDA', 'No se completo la descarga del input. Se conserva la solicitud para recuperacion; no se procesara un archivo parcial.')
    if fase == 'EJECUTANDO':
        return crear_diagnostico(8, 'PROCESO_INTERRUMPIDO', 'Existe un procesamiento interrumpido. Se conserva la evidencia y no se repite automaticamente.')
    if fase in ('EXPORT_PENDIENTE', 'ENTREGA_LISTA'):
        return crear_diagnostico(7, 'EXPORT_PENDIENTE', 'El resultado no pudo prepararse o validarse para entrega. Se conserva el trabajo; no se repite el procesamiento.')
    if es_error(error, 'ErrorWeyland') and error.tipo == 'RED':
        return crear_diagnostico(4, 'BACKEND_NO_DISPONIBLE', 'No se pudo comunicar con el backend. Revisar conectividad; no repetir operaciones inciertas.')
    if (es_error(error, 'ErrorWeyland') and error.tipo == 'HTTP') or status is not None:
        return crear_diagnostico(4, 'BACKEND_RECHAZO', 'El backend rechazo la peticion. Revisar disponibilidad y contrato de la API.')
    if es_error(error, 'ErrorWeyland'):
        return crear_diagnostico(5, 'CONTRATO_O_CHECKPOINT_INVALIDO', 'La respuesta o el checkpoint no cumplen el contrato. Conservar los archivos y revisar antes de continuar.')
    if isinstance(error, (ValueError, ImportError)) or es_error(error, 'ErrorArranque'):
        return crear_diagnostico(2, 'CONFIGURACION_INVALIDA', 'Revisar .env, dependencias y validaciones de arranque. No se completara el flujo hasta corregir la configuracion.')
    return crear_diagnostico(1, 'FALLO_OPERATIVO', 'El proceso no pudo completarse. Revisar configuracion y evidencia privada antes de reejecutar.')


def diagnosticar_correos(resumen):
    """Indica si la cola conserva destinatarios pendientes o que requieren revisión."""
    pendientes = sum(resumen.get(estado, 0) for estado in ('PENDIENTE', 'ENVIANDO', 'INCIERTO', 'AGOTADO'))
    if pendientes:
        return crear_diagnostico(9, 'CORREOS_PENDIENTES', 'Hay destinatarios pendientes o que requieren revision. INCIERTO no se reenvia sin resolucion del operador.')
    return crear_diagnostico(0, 'CORREOS_AL_DIA', 'La consulta a la cola no presenta destinatarios pendientes.')


if __name__ == "__main__":
    raise SystemExit(main())
