# Cómo contribuir

## Ramas
- `main`: solo releases. No se hace push directo.
- `dev`: integración. Todo entra por PR y siempre debe funcionar.
- Trabajo: `feat/`, `fix/`, `refactor/`, `docs/`, creadas desde `dev`.
- Se eliminan al mergear. Vida máxima: 5 días.

## Commits
Formato: `tipo(alcance): descripción` en imperativo.
Tipos válidos: feat, fix, refactor, docs.

## Pull requests
- 1 aprobación de alguien distinto al autor.
- Merge a `dev`: squash. Merge a `main`: merge commit, solo el Tech Lead.
- `dev` debe quedar funcionando después de cada merge.
- Los cambios relevantes llevan su entrada en `Sin publicar` de CHANGELOG.md.

## Prueba
Se prueba la rama en el clon `-test` del PC, con `.env` de desarrollo
y cuenta de prueba, antes de abrir la PR.

## Releases
Desde `dev`, tag `vX.Y.Z` en `main`, cuando hay cambio probado.

## Despliegue
Manual, por escritorio remoto. Deploy key de solo lectura por VM.
Se despliega un tag, nunca archivos.
Cada recorrido se registra en DEPLOYMENTS.md.
