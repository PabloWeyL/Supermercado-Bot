"""Ejemplo de lectura: python ejemplo_conexion_weyland.py desde el bot.

Usa las credenciales del .env para login/perfil. No inicia solicitudes ni envia
exports o correos. No muestra usuarios, passwords, cookies ni claves de API.
"""
from integraciones.api_http import (
    conectar_weyland, ErrorAPI, ErrorTemporalAPI, ErrorAutenticacionAPI,
)


def main():
    print('1. Abriendo conexion: obtener CSRF, iniciar sesion y verificar perfil.')
    try:
        with conectar_weyland() as cliente:
            print('2. Sesion y perfil verificados correctamente.')
            # Aqui se pueden realizar operaciones autenticadas, por ejemplo:
            # detalle = cliente.consultar(ID_REAL_DE_LA_SOLICITUD)
            # No iniciar un proceso desde este ejemplo de conexion.
            print('3. El cliente esta disponible para consultar la API.')
        print('4. Conexion cerrada y cookies locales eliminadas.')
        return 0
    except ErrorAutenticacionAPI as error:
        print('Acceso rechazado:', error)
    except ErrorTemporalAPI as error:
        print('Fallo temporal:', error)
        print('Conservar los checkpoints; este ejemplo no reintenta el proceso.')
    except ErrorAPI as error:
        print('Revisar permisos o contrato:', error)
    except ValueError:
        print('Configuracion invalida: revisar nombres, URLs y variables obligatorias del .env.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
