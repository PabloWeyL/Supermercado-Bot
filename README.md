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
registrados y API key. `MVTESTING` identifica las credenciales
del ejemplo; no reemplaza el ID numerico de la VM. Para produccion usar HTTPS
y un `BACKEND_TRABAJOS_DIR` privado fuera del repositorio.

## Ejecucion

```powershell
.\.venv\Scripts\python.exe main.py
```

Con el entorno activo basta `python main.py`. Tambien puede abrirse `ejecutar.bat`.
El arranque valida configuracion, Chrome/Node.js y SQLite. Luego se autentica con X-Api-Key y
consulta `/siguiente`. Descarga el Excel asignado o usa los productos recibidos
en parametros, procesa y envia el ZIP al backend. Un 204 termina sin trabajo,
con salida 0 (finalizacion normal). No se reutilizan solicitudes antiguas.

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

`api-verificar` comprueba la API key sin tomar una ejecucion. `web-procesar` entra directamente al flujo
web. `procesar` ejecuta el modo local con los Excel de `input/`.

## Resultados y correo

Los archivos web se conservan bajo `BACKEND_TRABAJOS_DIR`, separados por ID.
Los reportes locales quedan en `reportes/`. Logs y SQLite permiten revisar fallos.
No borrar checkpoints de entregas inciertas para forzar otro inicio.

Para habilitar SMTP, completar las credenciales y listas antes de activar
`MODULO_CORREO=true`. Inicio y resultados llegan a ambas listas; fallas solo a
`LISTA_DISTRIBUCION_ERROR`. El resultado se avisa despues de la confirmacion web
y adjunta el mismo archivo enviado al backend.

### Diagnostico y codigos de salida

`python main.py` y `ejecutar.bat` devuelven el mismo codigo:

| Codigo | Significado |
| --- | --- |
| 0 | Ejecucion completada, sin solicitudes o comando de consulta correcto. |
| 1 | Fallo del procesamiento u otro fallo operativo. |
| 2 | Configuracion, dependencias o argumentos invalidos. |
| 3 | Autenticacion o permisos rechazados (HTTP 401/403). |
| 4 | Backend no disponible, peticion rechazada o asignacion sin confirmar. |
| 5 | Proceso, contrato o checkpoint incompatible. |
| 6 | Input invalido o descarga fallida. |
| 7 | Export pendiente o entrega del resultado/fallo sin confirmar. |
| 8 | Procesamiento interrumpido; requiere revision antes de repetirlo. |
| 9 | Gestion de correo fallida o destinatarios pendientes de entrega/revision. |

Los mensajes publicos no muestran credenciales ni cuerpos HTTP. Una entrega
sin confirmacion conserva el checkpoint y no se reenvia automaticamente.
Un resultado confirmado por la web mantiene salida 0 aunque SMTP tenga pendientes;
la recuperacion de correo se gestiona por separado y no repite el negocio.

### Recuperar correos sin repetir el proceso

La cola conserva el mensaje MIME y los adjuntos en SQLite. Aunque se retire el
export original, el correo encolado conserva su copia. Los destinatarios ya
aceptados por SMTP no vuelven a recibir el mensaje durante los reintentos.

```powershell
python main.py correos-estado
python main.py correos-reintentar --id-correo ID_DEL_CORREO
```

El segundo comando reintenta exclusivamente el correo indicado, respeta la espera
de reintento y devuelve 9 si ese mensaje sigue pendiente. Sin `--id-correo`, revisa
la cola general. `AGOTADO` requiere reactivacion explicita mediante el comando
con ID. `INCIERTO` nunca se reenvia automaticamente: primero se comprueba con el
proveedor SMTP si fue aceptado y se registra la resolucion:

```powershell
python main.py correos-resolver --id-correo ID_DEL_CORREO --destinatario persona@ejemplo.test --entregado
python main.py correos-resolver --id-correo ID_DEL_CORREO --destinatario persona@ejemplo.test --no-entregado
```

Son alternativas: `--entregado` cierra la entrega confirmada; `--no-entregado`
habilita su posterior reintento. Estos comandos no consultan solicitudes,
no procesan productos y no vuelven a subir archivos al backend. En modo web,
la politica de notificacion se aplica al encolar el aviso; no se drena la cola
global automaticamente. La recuperacion manual debe respetar las decisiones
del operador sobre los mensajes ya encolados.

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


## Correo controlado desde Weyland

La configuracion de habilitacion de empresa/proceso puede incluir
`{"notificar_correo": false}` para suprimir inicio, resultados y fallas por SMTP.
`true` permite esos avisos si el correo local esta habilitado y configurado.
Si falta el campo, se conserva la configuracion local. Debe ser booleano JSON,
no el texto "false". El envio del resultado/error al backend sigue siendo obligatorio.

El backend actual ya permite guardar ese objeto mediante
`PUT /api/procesos/{proceso_id}/empresas/{empresa_id}` con
`{"activo": true, "configuracion": {"notificar_correo": false}}`, usando permisos
de gestion de procesos. Conservar las otras claves de configuracion al actualizarlo.
No se agrego un endpoint ni un control visual nuevo al backend.

El bot lee la bandera en la respuesta de `/siguiente` y la conserva para esa
ejecucion y sus recuperaciones. Los cambios web se aplican a las siguientes
asignaciones. Los journals antiguos o una toma recuperada sin su configuracion
no autorizan correos automaticos; el negocio y la entrega web pueden continuar.
En modo web no se drena una cola global de correos antes de tomar el trabajo.
Los comandos manuales de correo, donde existan, siguen siendo acciones del operador.

## Autenticacion de este despliegue

Supermercados usa `BACKEND_AUTH_MODE=api_key` y `BACKEND_CONTRATO=cola_actual`.
Solo necesita la API key del bot para los requests `/api/bot/*`; no llama a login,
perfil ni CSRF, y no envia correo/password de acceso al backend. Los otros bots
conservan su modo de autenticacion configurado.

La verificacion usa GET `/api/bot/ejecuciones/siguiente` con la clave y exige
HTTP 405 con codigo `METODO_NO_PERMITIDO`. La API valida la clave antes de rechazar
el metodo: esta peticion no toma trabajo. Los POST de negocio no se repiten
automaticamente ante errores o cortes.

La conciliacion automatica por `/api/ejecuciones/*` requiere sesion de usuario.
En modo solo API key, una toma o entrega incierta conserva su checkpoint y exige
revision; no se intenta un login ni se reenvia el resultado a ciegas.
