# Bot de supermercados

Consulta productos en los supermercados habilitados y genera un Excel de precios.
Se conecta a Weyland Automate para recibir una ejecucion y entregar su resultado.

## Preparacion

Requiere Python 3.11 o posterior, Node.js 22 o posterior y Google Chrome.
Desde esta carpeta:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Completar en `.env` la URL del backend, el ID de VM y el codigo de proceso
registrados, correo/password y API key. `MVTESTING` identifica las credenciales
del ejemplo; no reemplaza el ID numerico de la VM. Para produccion usar HTTPS
y un `BACKEND_TRABAJOS_DIR` privado fuera del repositorio.

## Ejecucion

```powershell
.\.venv\Scripts\python.exe main.py
```

Con el entorno activo basta `python main.py`. Tambien puede abrirse `ejecutar.bat`.
El arranque valida configuracion, Chrome/Node.js y SQLite. Luego inicia sesion y
consulta `/siguiente`. Descarga el Excel asignado o usa los productos recibidos
en parametros, procesa y envia el ZIP al backend. Un 204 termina sin trabajo,
con salida 1. No se reutilizan solicitudes antiguas.

El input Excel utiliza `producto` y filtros opcionales `supermercado`, `marca`,
`precio_minimo` y `precio_maximo`. Sin archivo, la web puede enviar
`{"productos": [{"producto": "leche", "supermercado": "unimarc"}]}`.

Otros comandos:

```powershell
python main.py api-verificar
python main.py web-procesar
python main.py procesar
python main.py estado
```

`api-verificar` comprueba login/perfil. `web-procesar` entra directamente al flujo
web. `procesar` ejecuta el modo local con los Excel de `input/`.

## Resultados y correo

Los archivos web se conservan bajo `BACKEND_TRABAJOS_DIR`, separados por ID.
Los reportes locales quedan en `reportes/`. Logs y SQLite permiten revisar fallos.
No borrar checkpoints de entregas inciertas para forzar otro inicio.

Para habilitar SMTP, completar las credenciales y listas antes de activar
`MODULO_CORREO=true`. Inicio y resultados llegan a ambas listas; fallas solo a
`LISTA_DISTRIBUCION_ERROR`. El resultado se avisa despues de la confirmacion web
y adjunta el mismo archivo enviado al backend.

## Publicacion

`.env`, inputs, media, bases, logs, temporales y exportaciones quedan excluidos.
Las plantillas de credenciales se comparten vacias. Activar los controles locales:

```powershell
git config core.hooksPath .githooks
python tools/verificar_publicacion.py
```

El verificador inspecciona los archivos preparados para commit; los hooks y el
workflow de GitHub complementan la revision humana. Esta entrega operativa no
incluye tests, simuladores ni documentacion adicional.
