"""Punto de integración con Azure Key Vault; aún no activado en este proyecto."""


# ======================
# Cliente de Key Vault
# ======================

def crear_cliente_key_vault(nombre):
    """Crea el cliente usando la identidad administrada de la VM."""

    if not nombre or not str(nombre).strip():
        raise ValueError("KEYVAULT_NAME es obligatorio")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as error:
        raise RuntimeError("Instalar azure-identity y azure-keyvault-secrets antes de activar Key Vault") from error
    url = f"https://{str(nombre).strip()}.vault.azure.net/"
    return SecretClient(vault_url=url, credential=DefaultAzureCredential())


def obtener_secreto(cliente, nombre):
    """Obtiene un secreto no vacío sin registrar su contenido."""

    if cliente is None or not nombre or not str(nombre).strip():
        raise ValueError("La referencia del secreto es obligatoria")
    valor = cliente.get_secret(str(nombre).strip()).value
    if not valor:
        raise RuntimeError("El secreto solicitado no existe o está vacío")
    return valor
