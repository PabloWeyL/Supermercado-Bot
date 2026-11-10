"""Alias de importacion anterior. El contrato se implementa una vez en el paquete comun."""
from .weyland import ClienteWeyland as ClienteSolicitudes, ErrorWeyland

SolicitudNoEjecutable = ErrorWeyland
