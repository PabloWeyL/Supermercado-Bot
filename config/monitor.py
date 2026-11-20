"""Contrato funcional recibido desde Weyland Automate."""


_solicitud = None


# ======================
# Solicitud y parametros
# ======================

def configurar_monitor(solicitud):
    """Registra la solicitud validada que reemplaza al Monitor SQL en este bot."""

    global _solicitud
    if not isinstance(solicitud, dict):
        raise ValueError("La solicitud de Weyland Automate debe ser un objeto JSON")
    parametros = solicitud.get("parametros")
    if parametros is not None and not isinstance(parametros, dict):
        raise ValueError("Los parametros de la solicitud deben ser un objeto JSON")
    _solicitud = dict(solicitud)


def obtener_parametros_monitor():
    """Retorna una copia de los parámetros funcionales de la solicitud."""

    if _solicitud is None:
        raise RuntimeError("La solicitud todavía no fue configurada")
    return dict(_solicitud.get("parametros") or {})


def obtener_lista_distribucion():
    """Retorna destinatarios opcionales entregados por la solicitud."""

    if _solicitud is None:
        raise RuntimeError("La solicitud todavía no fue configurada")
    correos = _solicitud.get("lista_distribucion") or []
    if not isinstance(correos, list) or any(not isinstance(correo, str) for correo in correos):
        raise ValueError("lista_distribucion debe ser una lista de correos")
    return tuple(correos)
