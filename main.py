"""Orquestador: configuración, notificaciones, automatización y reporte."""

import argparse
import json
import os
import subprocess
import sys
import time
from config.archivos import ErrorHTTPWeyland, ErrorRedWeyland, ErrorWeyland, SinPendientes
from dataclasses import dataclass
from contextlib import nullcontext
from pathlib import Path

_ERROR_CARGA = None
try:
    from config import configuracion, correo, base_datos
    from config.archivos import RutasEjecucion, bloqueo, confinar, guardar_json, EXTENSIONES_EXCEL
    from config.backend import crear_cliente_api, leer_valores
    from config.ejecuciones import ProcesoBot, ResultadoBot, ejecutar_cola_configurada
    from integraciones import supermercados, reportes
except (ImportError, RuntimeError, ValueError, OSError) as error:
    _ERROR_CARGA = error

CODIGOS_INTERRUMPIDOS = frozenset((-2, -9, -15, -1073741510, 3221225786))


class ErrorCorreoInicial(RuntimeError):
    """No se inicia el negocio si el correo inicial obligatorio no se confirma."""


def ejecutar_modulo(nombre, accion, intentos, *, al_fallar=None):
    """Reintentos de una etapa concreta; nunca envuelve la toma o entrega web."""
    logger = configuracion.obtener_logger('ORQUESTADOR')
    for intento in range(intentos):
        try:
            return accion(intento)
        except Exception as error:
            final = intento + 1 == intentos
            logger.warning('%s: intento %s/%s fallido: %s', nombre, intento + 1, intentos, error)
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


def enviar_inicio():
    """Envía el aviso inicial habilitado y aplica sus intentos antes de iniciar el negocio."""
    if not (configuracion.MODULO_CORREO and configuracion.MODULO_CORREO_INICIAL):
        return

    def enviar(_intento):
        """Realiza un intento de correo inicial y falla si no se confirma su entrega."""
        if not correo.enviar_correo_inicial():
            raise ErrorCorreoInicial('Correo inicial pendiente; revisar la cola de correo')
        return True

    return ejecutar_modulo('Módulo 1 - Correo inicial', enviar, configuracion.MAX_REINTENTOS_CORREO)


def ejecutar_local(productos_json=None, *, preparado=False):
    """Flujo de negocio local o subproceso de una solicitud web."""
    if not preparado:
        preparar_proceso(remoto=productos_json is not None)
    logger = configuracion.obtener_logger('PROCESO')
    notifica_web = configuracion.obtener_booleano('WEYLAND_CORREO_COORDINADOR', False)
    logger.info('INICIO DEL PROCESO')
    try:
        # Módulo 1: una activación conserva el mismo identificador de correo.
        if not notifica_web:
            enviar_inicio()

        # Módulo 2: carga validada y consultas. Los reintentos son por tarea.
        carga = (supermercados.cargar_productos_json_a_tabla_cola(productos_json)
                 if productos_json else supermercados.cargar_excel_a_tabla_cola())
        resumen = supermercados.procesar_cola_a_resultados(ejecutar_modulo)
        resumen = dict(resumen or {'ok': 0, 'errores': 0, 'fallos': [], 'capturas': []}, carga=carga)
        observaciones = min(resumen['errores'], sum(supermercados.es_observacion_busqueda(f[2])
                                                   for f in resumen.get('fallos', [])))
        resumen['observaciones'] = observaciones
        resumen['errores_tecnicos'] = resumen['errores'] - observaciones + carga['archivos_error']
        resumen['estado'] = ('con_errores' if resumen['errores_tecnicos'] else
                             'completado_con_observaciones' if observaciones else 'completado')

        # Módulo 3: reporte. Agregar aquí las siguientes etapas del proceso.
        ejecutar_modulo('Módulo 3 - Reporte', lambda _: reportes.generar_resultados_excel(),
                       configuracion.MAX_REINTENTOS)
        if not notifica_web:
            correo.notificar_resultado_procesamiento(resumen)
        return resumen
    except Exception:
        logger.exception('PROCESO INTERRUMPIDO')
        if not notifica_web:
            correo.enviar_correo_error('No se pudo completar el proceso; revisar el log privado.',
                                      supermercados.obtener_capturas())
        raise
    finally:
        logger.info('FIN DEL PROCESO')


def procesar_solicitud(carpeta, entrada, parametros):
    """Adaptador de negocio: el cliente backend y los checkpoints están en config/."""
    rutas = RutasEjecucion(carpeta)
    with configuracion.contexto(json.loads(rutas.contexto.read_text(encoding='utf-8'))):
        argumentos = ['procesar']
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
            'EXCEL_ENTRADA': str(entrada or rutas.input), 'EXCEL_SALIDA': str(rutas.reporte_excel),
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
            raise reportes.ErrorProcesoBot('El subproceso fue interrumpido.', resumen, 'INTERRUMPIDO')
        if resultado.returncode != 0 or resumen and (resumen['errores'] or resumen['pendientes']):
            raise reportes.ErrorProcesoBot('El proceso terminó con errores técnicos.', resumen)
        reporte = rutas.reporte_excel
        if (not reporte.is_file() or reporte.is_symlink()
                or not reporte.resolve().is_relative_to(rutas.datos.resolve())):
            raise ErrorWeyland('Falta el reporte Excel válido de la ejecución')
        return ResultadoBot((reporte,), resumen=resumen or {'procesados': 1, 'correctos': 1, 'errores': 0},
                            detalles={'trazabilidad': configuracion.actual()}, incluir_metadatos_export=False)


def notificar_solicitud(evento, estado, carpeta):
    """Envía el aviso de una solicitud usando su contexto y el resultado confirmado por la web."""
    with configuracion.contexto({'solicitud_id': estado['ejecucion_id'],
            'ejecucion_id_web': estado['ejecucion_id'], 'activacion_id': estado['id_externo']}):
        if evento == 'inicio':
            return enviar_inicio()
        final = estado.get('estado_bot_publicado', 'ERROR')
        if evento == 'resultados':
            entrega = confinar(carpeta, estado['entrega']['ruta'])
            return correo.notificar(configuracion.NOMBRE_PROCESO_TEXTO + ' - RESULTADOS',
                'Estado del proceso: ' + final + '. Confirmado por Weyland Automate.' + correo.referencia_web(),
                [entrega], evento='resultados')
        return correo.enviar_correo_error('Estado del proceso: ' + final + '. Confirmado por Weyland Automate.')


def ejecutar_web():
    """Registra el adaptador del proceso y coordina una solicitud cuando el backend está habilitado."""
    if not configuracion.BACKEND_ACTIVO:
        raise ValueError('BACKEND_ACTIVO=false: conexión al backend desactivada')
    valores = leer_valores()
    codigo = str(valores.get('BACKEND_PROCESO_CODIGO') or '').strip()
    if not codigo:
        raise ValueError('Configurar BACKEND_PROCESO_CODIGO registrado en la web')
    proceso = ProcesoBot(procesar_solicitud, requiere_input=None,
                         extensiones=EXTENSIONES_EXCEL, informar_estados=True)
    resultado = ejecutar_cola_configurada(crear_cliente_api, valores, configuracion.RUTA_PROYECTO,
                                          {codigo: proceso}, notificar=notificar_solicitud)
    diagnostico = diagnosticar_resultado(resultado)
    print('Weyland Automate:', resultado['fase'], 'ID:', resultado.get('ejecucion_id', 'SIN_TRABAJO'))
    print(diagnostico.texto())
    return diagnostico.codigo


def gestionar_correos(args):
    """Consulta o recupera destinatarios pendientes sin volver a ejecutar la automatización."""
    cola = correo.obtener_cola()
    if args.comando == 'correos-estado':
        for fila in cola.listar():
            print(fila)
        print('Totales por destinatario:', cola.resumen())
        return 0
    if args.comando == 'correos-resolver':
        if not args.id_correo or not (args.entregado or args.no_entregado):
            raise ValueError('Indicar --id-correo y --entregado o --no-entregado')
        if not cola.resolver_incierto(args.id_correo, args.entregado, args.destinatario):
            raise ValueError('No hay destinatarios inciertos para esa selección')
        print('Resolución registrada. El proceso no se repitió.')
        return 0
    if not configuracion.MODULO_CORREO:
        raise ValueError('MODULO_CORREO=false: no se reenviarán correos')
    if args.id_correo:
        if not cola.resumen(solo=args.id_correo):
            raise ValueError('No existe el correo seleccionado')
        cola.reactivar(args.id_correo)
    diagnostico = diagnosticar_correos(correo.reintentar_pendientes(solo=args.id_correo, propagar_error=True))
    print(diagnostico.texto())
    return diagnostico.codigo


def argumentos(argv=None):
    """Interpreta los comandos y rechaza combinaciones de argumentos incompatibles."""
    parser = argparse.ArgumentParser(description='RPA de supermercados conectado a Weyland Automate')
    parser.add_argument('comando', nargs='?', choices=('procesar', 'web-procesar', 'api-verificar',
        'cargar', 'estado', 'reporte', 'correos-estado', 'correos-reintentar', 'correos-resolver'))
    parser.add_argument('--productos-json', type=Path)
    parser.add_argument('--id-correo')
    parser.add_argument('--destinatario')
    resolucion = parser.add_mutually_exclusive_group()
    resolucion.add_argument('--entregado', action='store_true')
    resolucion.add_argument('--no-entregado', action='store_true')
    args = parser.parse_args(argv)
    if args.productos_json and args.comando not in (None, 'procesar'):
        parser.error('--productos-json solo se admite para procesar')
    if args.comando != 'correos-resolver' and (args.entregado or args.no_entregado or args.destinatario):
        parser.error('La resolución de entrega solo se admite con correos-resolver')
    if args.id_correo and args.comando not in ('correos-reintentar', 'correos-resolver'):
        parser.error('--id-correo solo se admite para gestionar correos')
    return args


def main(argv=None):
    """Selecciona el modo, valida el arranque, impide ejecuciones simultáneas y devuelve el código de salida."""
    args = argumentos(argv)
    if _ERROR_CARGA is not None:
        print('[CONFIGURACION_INVALIDA] Revisar .env y dependencias. No se inició el proceso.')
        return 2
    try:
        if args.comando == 'api-verificar':
            if not configuracion.BACKEND_ACTIVO:
                print('BACKEND_ACTIVO=false: no se realizarán peticiones al backend.')
                return 0
            with crear_cliente_api() as cliente:
                cliente.verificar()
            print('Conexión JWT BOT autenticada, sin cookies ni CSRF. No se tomó trabajo.')
            return 0
        if args.comando and args.comando.startswith('correos-'):
            return gestionar_correos(args)
        if (not configuracion.MODULO_AUTOMATIZACION_SUPERMERCADOS
                and args.comando in (None, 'procesar', 'web-procesar', 'cargar')):
            print('MODULO_AUTOMATIZACION_SUPERMERCADOS=false: automatización desactivada')
            return 0
        if not configuracion.BACKEND_ACTIVO:
            if args.comando == 'web-procesar' or args.productos_json is not None:
                raise ValueError('BACKEND_ACTIVO=false: ejecutar sin parámetros para leer solo CARPETA_INPUTS')
        web = configuracion.BACKEND_ACTIVO and (
            args.comando == 'web-procesar' or args.comando is None and not args.productos_json)
        local_input = not web and args.productos_json is None and args.comando in (None, 'procesar', 'cargar')
        ambito = (base_datos.ambito_archivos(supermercados.listar_archivos_entrada())
                  if local_input else nullcontext())
        raiz_lock = configuracion.RUTA_BASE / '.runtime'
        raiz_lock.mkdir(parents=True, exist_ok=True)
        with configuracion.contexto(), bloqueo(raiz_lock / 'arranque.lock'), ambito:
            if args.comando in ('estado', 'reporte', 'cargar'):
                configuracion.configuracion_inicial()
            else:
                preparar_proceso(remoto=web or args.productos_json is not None)
            if web:
                return ejecutar_web()
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
        if isinstance(error, ErrorCorreoInicial):
            diagnostico = Diagnostico(1, 'CORREO_INICIAL_FALLIDO', 'No se confirmó el correo inicial; el negocio no se inició.')
        else:
            diagnostico = diagnosticar_error(error, correo=bool(args.comando and args.comando.startswith('correos-')))
        with configuracion.contexto(getattr(error, 'contexto_trazabilidad', None)):
            configuracion.obtener_logger('ORQUESTADOR').error(diagnostico.texto())
        return diagnostico.codigo




@dataclass(frozen=True)
class Diagnostico:
    codigo: int
    nombre: str
    mensaje: str

    def texto(self):
        """Formatea el diagnóstico con su nombre, explicación y código de salida."""
        return f'[{self.nombre}] {self.mensaje} (salida {self.codigo})'


def diagnosticar_resultado(estado):
    """Traduce la fase final del coordinador a un diagnóstico operativo."""
    fase = estado.get('fase')
    if fase == 'SIN_TRABAJO':
        return Diagnostico(0, 'SIN_SOLICITUDES', 'No hay solicitudes pendientes. Finalizacion normal; no se proceso trabajo.')
    if fase == 'COMPLETADA':
        mensaje = ('Proceso finalizado con observaciones; reporte confirmado por el backend.'
                   if estado.get('estado_bot_publicado') == 'FINALIZADO_CON_OBSERVACIONES'
                   else 'Proceso terminado y resultado confirmado por el backend.')
        return Diagnostico(0, 'COMPLETADA', mensaje)
    if fase == 'CANCELADO':
        return Diagnostico(1, 'CANCELADO', 'Cancelacion confirmada por el backend; no se repetira el trabajo.')
    if fase == 'INTERRUMPIDO':
        return Diagnostico(1, 'INTERRUMPIDO', 'Interrupcion confirmada por el backend; requiere un reproceso autorizado.')
    if estado.get('etapa_error') == 'input':
        return Diagnostico(6, 'INPUT_INVALIDO', 'La entrada no es compatible o no pudo obtenerse. El fallo fue notificado al backend.')
    return Diagnostico(1, 'PROCESAMIENTO_FALLIDO', 'El procesamiento fallo. El fallo fue notificado al backend; revisar la evidencia privada.')


def diagnosticar_error(error, *, correo=False):
    """Clasifica el fallo y conserva las restricciones de recuperación ante operaciones inciertas."""
    fase = getattr(error, 'fase_weyland', None)
    status = getattr(error, 'status_code', None)
    if correo:
        return Diagnostico(9, 'CORREO_REQUIERE_REVISION', 'No se completo la gestion de correos. Revisar la cola; no se reinicio el negocio ni la entrega web.')
    if isinstance(error, SinPendientes):
        return diagnosticar_resultado({'fase': 'SIN_TRABAJO'})
    if status in (401, 403):
        mensaje = ('El backend rechazo la autenticacion. Revisar acceso JWT o sesion configurada.' if status == 401
                   else 'El backend rechazo los permisos de la operacion. Revisar la autorizacion del bot.')
        if fase in ('TOMA_INCIERTA', 'ENTREGA_INCIERTA', 'ERROR_INCIERTO'):
            mensaje += ' Se conserva el checkpoint; revisar la web antes de reenviar.'
        return Diagnostico(3, 'AUTENTICACION_RECHAZADA' if status == 401 else 'PERMISOS_RECHAZADOS', mensaje)
    if fase in ('ENTREGA_INCIERTA', 'ERROR_INCIERTO'):
        return Diagnostico(7, 'ENTREGA_SIN_CONFIRMAR', 'El backend no confirmo la entrega del resultado o del fallo. Se conserva el checkpoint; verificar antes de reenviar.')
    if fase == 'TOMA_INCIERTA':
        return Diagnostico(4, 'ASIGNACION_SIN_CONFIRMAR', 'No se confirmo la toma de trabajo. Conservar el checkpoint y verificar la web antes de otra asignacion.')
    if fase == 'ASIGNACION_INCOMPATIBLE':
        return Diagnostico(5, 'PROCESO_INCOMPATIBLE', 'La asignacion no coincide con la maquina, proceso o contrato. Puede estar tomada en la web; requiere revision.')
    if fase == 'DESCARGANDO':
        return Diagnostico(6, 'DESCARGA_FALLIDA', 'No se completo la descarga del input. Se conserva la solicitud para recuperacion; no se procesara un archivo parcial.')
    if fase == 'EJECUTANDO':
        return Diagnostico(8, 'PROCESO_INTERRUMPIDO', 'Existe un procesamiento interrumpido. Se conserva la evidencia y no se repite automaticamente.')
    if fase in ('EXPORT_PENDIENTE', 'ENTREGA_LISTA'):
        return Diagnostico(7, 'EXPORT_PENDIENTE', 'El resultado no pudo prepararse o validarse para entrega. Se conserva el trabajo; no se repite el procesamiento.')
    if isinstance(error, ErrorRedWeyland):
        return Diagnostico(4, 'BACKEND_NO_DISPONIBLE', 'No se pudo comunicar con el backend. Revisar conectividad; no repetir operaciones inciertas.')
    if isinstance(error, ErrorHTTPWeyland) or status is not None:
        return Diagnostico(4, 'BACKEND_RECHAZO', 'El backend rechazo la peticion. Revisar disponibilidad y contrato de la API.')
    if isinstance(error, ErrorWeyland):
        return Diagnostico(5, 'CONTRATO_O_CHECKPOINT_INVALIDO', 'La respuesta o el checkpoint no cumplen el contrato. Conservar los archivos y revisar antes de continuar.')
    if isinstance(error, (ValueError, ImportError)) or type(error).__name__ == 'ErrorArranque':
        return Diagnostico(2, 'CONFIGURACION_INVALIDA', 'Revisar .env, dependencias y validaciones de arranque. No se completara el flujo hasta corregir la configuracion.')
    return Diagnostico(1, 'FALLO_OPERATIVO', 'El proceso no pudo completarse. Revisar configuracion y evidencia privada antes de reejecutar.')


def diagnosticar_correos(resumen):
    """Indica si la cola conserva destinatarios pendientes o que requieren revisión."""
    pendientes = sum(resumen.get(estado, 0) for estado in ('PENDIENTE', 'ENVIANDO', 'INCIERTO', 'AGOTADO'))
    if pendientes:
        return Diagnostico(9, 'CORREOS_PENDIENTES', 'Hay destinatarios pendientes o que requieren revision. INCIERTO no se reenvia sin resolucion del operador.')
    return Diagnostico(0, 'CORREOS_AL_DIA', 'La consulta a la cola no presenta destinatarios pendientes.')


if __name__ == "__main__":
    raise SystemExit(main())
