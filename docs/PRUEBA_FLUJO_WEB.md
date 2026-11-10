# Arranque automatico y prueba de conexion web

`python main.py` ejecuta las comprobaciones y los tests, y despues consulta la solicitud web cuyo ID esta en BACKEND_SOLICITUD_ID. No es necesario escribir el ID en la terminal. El operador sigue siendo responsable de seleccionar y habilitar esa solicitud en la plataforma.

## Configuracion del modo real

Completar en el .env privado o entorno:

```dotenv
ARRANQUE_MODO=web
BACKEND_AUTH_MODE=weyland
BACKEND_SOLICITUD_ID=
BACKEND_PROCESO_CODIGO=
BACKEND_API_URL=http://127.0.0.1:8000/api
BACKEND_LOGIN_URL=http://127.0.0.1:8000/api/auth/login
BACKEND_PERMITIR_HTTP_LOCAL=true
BACKEND_EMAIL=
BACKEND_PASSWORD=
BACKEND_BOT_API_KEY=
```

Completar ID, codigo y credenciales reales antes de ejecutar. HTTP en loopback es solo para desarrollo; produccion requiere HTTPS. No copiar el ID ficticio del simulador a una configuracion del backend real.

El bot inicia sesion, consulta GET /api/bot/ejecuciones/{id}, exige EN_COLA y el codigo correcto y notifica el inicio mediante POST /api/bot/ejecuciones/siguiente con solicitud_id y ejecucion_id iguales. Espera EN_PROCESO y descarga el archivo asignado con verificacion de tamano y SHA-256, si se requiere. Si no requiere archivo, utiliza sus parametros.

No se cargan los Excel ni la cola de otro proceso local para reemplazar una solicitud no autorizada. No se reutiliza la ultima solicitud. El negocio utiliza una base y carpetas exclusivas de esa ejecucion web.

## Resultado y correo

El negocio genera su Excel. Si el correo esta habilitado y configurado, envia inicio y resultados a LISTA_DISTRIBUCION_ERROR y LISTA_DISTRIBUCION_CORREO; las fallas del negocio van a LISTA_DISTRIBUCION_ERROR. Los correos del trabajo incluyen el ID de solicitud y ejecucion web. El correo final adjunta el Excel generado.

El adaptador envia el reporte ZIP con ese Excel y los metadatos JSON al endpoint de reporte del mismo ID. La web confirma IDs, estado COMPLETADA y hash; ante un fallo de negocio se informa ERROR. Un fallo SMTP no provoca repetir el scraping ni impide por si solo la entrega web. La confirmacion web no demuestra que un correo haya sido entregado por el servidor SMTP.

El backend debe implementar el contrato por ID, los permisos y la idempotencia. Esta implementacion del bot no modifica el backend. Si falta la consulta por ID o se rechaza la solicitud, se detiene antes del negocio.

## Parametros obligatorios del simulador en .env

Agregar al .env privado:

```dotenv
ID_PRUEBA=900001
CODIGO_PRUEBA='SUPERMERCADOS_PRUEBA'
```

El simulador lee ambos valores exclusivamente de ese archivo. No estan fijados en el codigo ni tienen valores por defecto. Si falta el archivo, falta una variable, el codigo esta vacio o el ID no es un entero positivo, termina con error antes de crear el servidor o iniciar la suite.

Estas variables solo configuran la prueba aislada. No reemplazan BACKEND_SOLICITUD_ID ni BACKEND_PROCESO_CODIGO del modo real.

## Prueba repetible con los valores configurados

Desde la raiz del proyecto, ejecutar sin argumentos:

```powershell
.\probar_flujo_web.bat
```

Alternativa con el entorno virtual activo: `python probar_flujo_web.py`.

El simulador crea un servidor HTTP en 127.0.0.1 con puerto libre, credenciales ficticias y una unica solicitud con ID_PRUEBA y CODIGO_PRUEBA del .env. Proporciona un Excel de prueba y recibe el ZIP final. Tambien intercepta SMTP en memoria: construye los correos reales del codigo, pero no los envia. El scraping se sustituye por un resultado controlado. No consulta tiendas reales, no escribe solicitudes en el backend real y no cambia .env.

La prueba recorre el mismo arranque sin argumentos, ejecuta la suite completa, valida la base, hace login por HTTP, consulta el ID, informa inicio, descarga el input, carga una cola SQLite, guarda resultados y genera el Excel. Finalmente comprueba la entrega web y ambos correos simulados, sus destinatarios, el ID en sus textos y la igualdad exacta de los bytes del Excel en correo y ZIP.

La evidencia queda en temporales/prueba_web_<identificador>: logs y JSON de tests, bases de prueba, archivos descargados, reporte ZIP y resultado_prueba.json. Todos son datos ficticios y estan excluidos de Git. Un exito del simulador prueba el cliente y la integracion con el contrato previsto, no la disponibilidad ni los permisos del backend real ni la entrega real de correos.

## Archivos que intervienen

| Archivo | Funcion |
|---|---|
| main.py | Selecciona el modo web/local para el arranque sin argumentos. |
| integraciones/arranque.py | Comprobaciones y tests previos; en modo remoto omite las entradas locales. |
| integraciones/arranque_web.py | Lee el ID configurado y coordina el paso hacia el worker web. |
| integraciones/weyland_worker.py | Adapta el input y los reportes al proceso de supermercados. |
| integraciones/weyland/ | Cliente HTTP y coordinador reutilizable, sin cambios en su contrato. |
| config/correo.py | Correo del proceso con referencia al ID web. |
| probar_flujo_web.py y probar_flujo_web.bat | Lanzadores de la prueba aislada. |
| tests/simulador_web.py | Servidor de contrato, SMTP simulado y verificaciones de la prueba. |
