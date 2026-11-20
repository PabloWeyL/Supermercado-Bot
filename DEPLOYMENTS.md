# Registro de despliegues

Una fila por versión y una columna por VM. Se actualiza con una sola PR
`docs/deploy-vX.Y.Z` al terminar el recorrido completo, no una PR por VM.

| Versión | Fecha | VM 1 | VM 2 | VM 3 | VM 4 | Responsable |
|---|---|---|---|---|---|---|
| Pendiente | - | - | - | - | - | - |

## Procedimiento

1. Verificar el tag anotado `vX.Y.Z` en `main`.
2. En cada VM, hacer checkout del tag con la deploy key de solo lectura.
3. Actualizar `.venv` si cambió `requirements.txt`.
4. Ejecutar la validación controlada del proceso.
5. Registrar el resultado de cada VM en la tabla.
