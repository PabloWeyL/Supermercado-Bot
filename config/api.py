from pathlib import Path

from config.weyland import leer_valores

from integraciones.api_http import ClienteAPI, ConfiguracionAPI


def crear_cliente_api(archivo=None, session=None):
    archivo = Path(archivo) if archivo else Path(__file__).resolve().parents[1] / '.env'
    valores = leer_valores(archivo)
    if valores.get('BACKEND_AUTH_MODE') == 'weyland':
        from config.weyland import crear_cliente_weyland
        return crear_cliente_weyland(archivo, session, valores=valores)

    def obligatorio(nombre, conservar=False):
        valor = str(valores.get(nombre) or '')
        if not valor.strip():
            raise ValueError('Falta variable: ' + nombre)
        return valor if conservar else valor.strip()

    config = ConfiguracionAPI(
        url=obligatorio('BACKEND_API_URL'),
        correo=obligatorio('BACKEND_EMAIL'),
        password=obligatorio('BACKEND_PASSWORD', conservar=True),
        login_url=obligatorio('BACKEND_LOGIN_URL'))
    return ClienteAPI(config, session=session)
