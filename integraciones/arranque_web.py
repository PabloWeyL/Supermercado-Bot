"""Arranque por ID configurado; nunca selecciona otro trabajo ni cae al modo local."""
from config import configuracion
from integraciones.weyland_worker import ejecutar_desde_config
from integraciones.trazabilidad import contexto


def leer_solicitud_configurada():
    valor = configuracion.obtener_variable_opcional('BACKEND_SOLICITUD_ID', '')
    if not valor.isdecimal() or int(valor) < 1:
        raise ValueError('Configurar BACKEND_SOLICITUD_ID con el ID real seleccionado en la web; '
                         'no se ejecutara un ID de prueba contra el backend real')
    if configuracion.obtener_variable_opcional('BACKEND_AUTH_MODE', '') != 'weyland':
        raise ValueError('El arranque web requiere BACKEND_AUTH_MODE=weyland')
    return int(valor)


def ejecutar_web_sin_argumentos(puerta_arranque):
    contrato = configuracion.obtener_variable_opcional('BACKEND_CONTRATO', 'por_id').lower()
    if contrato == 'cola_actual':
        from config.weyland import leer_valores
        from integraciones.weyland.cola_actual import entero_configurado
        valores = leer_valores()
        maquina = entero_configurado(valores, 'BACKEND_MAQUINA_ID')
        if valores.get('BACKEND_AUTH_MODE') != 'weyland' or not valores.get('BACKEND_PROCESO_CODIGO'):
            raise ValueError('Configurar autenticacion Weyland y codigo de proceso antes del arranque')
        print(f'Contrato backend actual; consultar trabajo de la VM registrada {maquina}', flush=True)
        return puerta_arranque(lambda: ejecutar_desde_config('supermercados', configuracion.RUTA_PROYECTO), remoto=True)
    if contrato != 'por_id':
        raise ValueError('BACKEND_CONTRATO debe ser por_id o cola_actual')
    identificador = leer_solicitud_configurada()
    print(f'Solicitud web seleccionada: {identificador}', flush=True)
    with contexto(solicitud_id=identificador):
        return puerta_arranque(lambda: ejecutar_desde_config('supermercados', configuracion.RUTA_PROYECTO,
                                                             solicitud_id=identificador), remoto=True)
