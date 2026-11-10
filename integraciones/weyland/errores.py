"""Errores publicos de conexion y de disponibilidad de ordenes."""
class ErrorWeyland(RuntimeError):
    pass


class ErrorHTTPWeyland(ErrorWeyland):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f'Weyland rechazo la operacion (HTTP {status_code})')


class ErrorRedWeyland(ErrorWeyland):
    """Operacion incierta: no repetir automaticamente peticiones con efectos."""


class SinPendientes(ErrorWeyland):
    pass


class AsignacionIncompatible(ErrorWeyland):
    """La reserva ya ocurrio en la web, pero este bot no debe ejecutarla."""

    def __init__(self, ejecucion_id):
        self.ejecucion_id = ejecucion_id
        super().__init__(f'La asignacion de la ejecucion #{ejecucion_id} no coincide con '
                         'VM/proceso. Ya fue reservada; requiere revision del operador.')


