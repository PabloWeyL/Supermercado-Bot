"""Mensajes publicos y codigos de salida; no incluye cuerpos HTTP ni secretos."""
from dataclasses import dataclass

from integraciones.weyland.errores import ErrorHTTPWeyland, ErrorRedWeyland, ErrorWeyland, SinPendientes


@dataclass(frozen=True)
class Diagnostico:
    codigo: int
    nombre: str
    mensaje: str

    def texto(self):
        return f'[{self.nombre}] {self.mensaje} (salida {self.codigo})'


def diagnosticar_resultado(estado):
    fase = estado.get('fase')
    if fase == 'SIN_TRABAJO':
        return Diagnostico(0, 'SIN_SOLICITUDES', 'No hay solicitudes pendientes. Finalizacion normal; no se proceso trabajo.')
    if fase == 'COMPLETADA':
        return Diagnostico(0, 'COMPLETADA', 'Proceso terminado y resultado confirmado por el backend.')
    if estado.get('etapa_error') == 'input':
        return Diagnostico(6, 'INPUT_INVALIDO', 'La entrada no es compatible o no pudo obtenerse. El fallo fue notificado al backend.')
    return Diagnostico(1, 'PROCESAMIENTO_FALLIDO', 'El procesamiento fallo. El fallo fue notificado al backend; revisar la evidencia privada.')


def diagnosticar_error(error, *, correo=False):
    fase = getattr(error, 'fase_weyland', None)
    status = getattr(error, 'status_code', None)
    if correo:
        return Diagnostico(9, 'CORREO_REQUIERE_REVISION', 'No se completo la gestion de correos. Revisar la cola; no se reinicio el negocio ni la entrega web.')
    if isinstance(error, SinPendientes):
        return diagnosticar_resultado({'fase': 'SIN_TRABAJO'})
    if status in (401, 403):
        mensaje = ('El backend rechazo la autenticacion. Revisar API key o acceso de sesion.' if status == 401
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
    pendientes = sum(resumen.get(estado, 0) for estado in ('PENDIENTE', 'ENVIANDO', 'INCIERTO', 'AGOTADO'))
    if pendientes:
        return Diagnostico(9, 'CORREOS_PENDIENTES', 'Hay destinatarios pendientes o que requieren revision. INCIERTO no se reenvia sin resolucion del operador.')
    return Diagnostico(0, 'CORREOS_AL_DIA', 'La consulta a la cola no presenta destinatarios pendientes.')
