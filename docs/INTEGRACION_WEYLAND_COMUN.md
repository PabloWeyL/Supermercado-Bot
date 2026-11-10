# Conexion comun a Weyland Automate

## Nombres de credenciales en .env

El bot de supermercados utiliza `[Administrador]_[Empresa]_[Sistema]_[VM]_[Tipo]`, con mayusculas y guiones bajos. Si administrador y empresa son WEYLAND, se escribe una sola vez. Para el ejemplo se utiliza WEYLAND y MVTESTING; los valores de usuario/password no cambian al renombrar las variables.

```dotenv
CREDENCIALES_EMPRESA=WEYLAND
CREDENCIALES_VM=MVTESTING
WEYLAND_AUTOMATE_MVTESTING_USER=
WEYLAND_AUTOMATE_MVTESTING_PWD=
WEYLAND_AUTOMATE_MVTESTING_API_KEY=
WEYLAND_SMTP_MVTESTING_USER=
WEYLAND_SMTP_MVTESTING_PWD=
WEYLAND_MONITOR_MVTESTING_USER=
WEYLAND_MONITOR_MVTESTING_PWD=
```

AUTOMATE identifica Weyland Automate, SMTP el correo y MONITOR la conexion de Monitor cuando corresponda. SQL se reserva para credenciales DB_USER/DB_PASSWORD si el proyecto las incorpora. USER es el usuario/correo de acceso; PWD la contrasena; API_KEY la clave adicional del bot. En el correo actual, SMTP USER tambien es el remitente. No agregar secretos que el proceso no necesite.

Con una empresa distinta, por ejemplo ACME, la clave es `WEYLAND_ACME_AUTOMATE_MVTESTING_USER`. Al cambiar CREDENCIALES_EMPRESA o CREDENCIALES_VM, renombrar tambien las claves correspondientes; no se toman las credenciales de otra VM automaticamente. Las variables tecnicas (URLs, puertos, rutas, limites y listas de distribucion) mantienen sus nombres.

`config/credenciales.py` adapta estos nombres a los internos que consumen correo y el framework HTTP. Conserva temporalmente los nombres anteriores, como BACKEND_EMAIL, BACKEND_PASSWORD y EMAIL_PASSWORD. Si un nombre anterior y su equivalente nuevo tienen valores diferentes dentro de la misma fuente, se detiene la lectura sin mostrar los valores. El entorno inyectado prevalece sobre .env, incluso si usa el nombre anterior. Los passwords se conservan literalmente, incluidos espacios.

Los ejemplos se mantienen sin secretos. En produccion, inyectar los valores desde el gestor de secretos; no publicar el .env privado. La nomenclatura de credenciales del bot no cambia el JSON de login, los endpoints ni el contrato web. Este ajuste corresponde al supermercado; el paquete comun de los cuatro bots permanece igual.

## Flujo comun

### Funciones explicadas en api_http.py

El archivo conserva el cliente Bearer anterior y ofrece funciones explicitas para el cliente actual de Weyland (sesion Django y CSRF):

- `clasificar_error_weyland(error)`: convierte un error del framework en ErrorAutenticacionAPI (HTTP 401), ErrorTemporalAPI (red, timeout, HTTP 408/429/5xx) o ErrorAPI (permisos, contrato y otros rechazos). No copia mensajes del servidor ni secretos. Un 403 requiere revisar permisos/CSRF; no prueba por si solo que el password sea incorrecto.
- `conectar_weyland()`: abre el cliente configurado en .env, ejecuta GET csrf, POST login con correo/password, GET csrf rotado y GET perfil. Entrega el cliente dentro de un `with`; al salir elimina las cookies locales y cierra la conexion, incluso si falla una peticion. No cierra sesiones de otros usuarios ni ejecuta el negocio.
- `verificar_conexion_weyland()`: utiliza esa conexion exclusivamente para comprobar login/perfil y devolver una confirmacion sin credenciales. El comando `python main.py api-verificar` la utiliza cuando BACKEND_AUTH_MODE=weyland.

Ejemplo de uso:

```python
from integraciones.api_http import (
    conectar_weyland, ErrorAutenticacionAPI, ErrorTemporalAPI, ErrorAPI,
)

try:
    with conectar_weyland() as cliente:
        print('Sesion verificada')
        # detalle = cliente.consultar(ID_REAL_DE_LA_SOLICITUD)
except ErrorAutenticacionAPI:
    print('Revisar el acceso')
except ErrorTemporalAPI:
    print('Conservar el checkpoint; no repetir el RPA')
except ErrorAPI:
    print('Revisar permisos o contrato')
```

`python ejemplo_conexion_weyland.py` muestra los pasos con mensajes sencillos. Al ejecutarlo se conecta realmente a la URL configurada para login/perfil, sin tomar solicitudes, ejecutar scraping o enviar exports/correos. Sus pruebas usan respuestas ficticias. Las clases de error heredan el comportamiento de las excepciones de Python; ErrorAPI agrega status_code para inspeccionar el codigo HTTP cuando existe. Los errores de programacion o del negocio no se ocultan como fallos de red.

Estas funciones no incorporan reintentos de negocio. El cliente conserva su renovacion controlada de sesion/CSRF; si persiste el problema, la excepcion llega al llamador. El flujo web completo continua utilizando el coordinador comun y sus journals; la clasificacion de estas funciones se aplica a las operaciones realizadas dentro de `conectar_weyland` y a `api-verificar`. El SDK compartido y el backend no se modifican.

Este bot utiliza la version 2.0.0 del paquete `integraciones/weyland`, identica a la de los otros tres bots. La fuente reutilizable y la guia estan en la carpeta hermana `Integracion-Weyland-Automate/README.md`. Cada proyecto conserva su propia copia para poder instalarse de forma independiente.

Comandos: `ejecutar.bat api-verificar` comprueba login y perfil; `ejecutar.bat web-procesar --id-solicitud 123` procesa solo ese ID. ACME permite ademas `--proceso descargar-reportes`. Los IDs de los ejemplos deben reemplazarse por valores registrados.

La URL, codigo del proceso y proveedor de secretos se configuran en el proyecto. No se requiere un ID de equipo para la conexion. Vencimientos conserva credenciales API en Key Vault, parametros de negocio en Monitor y conexiones independientes.

Funciones comunes: crear_cliente, verificar, consultar, validar, iniciar, descargar, entregar y fallar. La coordinacion, idempotencia, integridad y registro local pertenecen al paquete comun; `integraciones/weyland_worker.py` adapta los comandos/artefactos particulares del negocio. Godzilla utiliza su adaptador `integraciones/worker.py`.

1. Autenticar con correo/password, sesion y CSRF.
2. Consultar GET `/api/bot/ejecuciones/{id}` sin mutar la solicitud.
3. Exigir EN_COLA, ID y codigo correctos; POST `/api/bot/ejecuciones/siguiente` con solicitud_id y ejecucion_id iguales.
4. Esperar EN_PROCESO; descargar input solo si se requiere y verificar tamano y SHA-256.
5. Ejecutar negocio una vez; enviar ZIP y metadatos JSON al reporte del mismo ID, o notificar ERROR.

No existe fallback a la seleccion heredada ni a la ultima solicitud. Las confirmaciones deben conservar ambos IDs y el reporte debe confirmar su hash. Las claves de idempotencia se guardan antes de enviar POST; una entrega pendiente puede reenviarse sin repetir el negocio.

**Pendiente en el servidor:** el backend actual requiere adaptar siguiente a IDs obligatorios, habilitar la consulta del bot por ID e implementar las confirmaciones e idempotencia del contrato. Mientras falte ese soporte, web-procesar se detiene sin tomar otra solicitud. api-verificar por si solo no valida esos requisitos. El backend no se modifica con esta migracion.

No borrar los journals anteriores: una ejecucion incierta bloquea la migracion hasta revision. El codigo nuevo no reanuda negocio desde EJECUTANDO ni sobrescribe un registro incompatible. En produccion, configurar BACKEND_TRABAJOS_DIR fuera del repositorio y permisos de acceso exclusivos de la cuenta operativa.

## Entrega final y recuperacion

El cierre es una peticion adicional al inicio: `POST /api/bot/ejecuciones/{id}/reporte`, con la misma sesion autenticada, CSRF, X-Api-Key e Idempotency-Key persistida para la entrega. Ambos IDs son el ID de solicitud. No basta con que termine el subproceso local: se espera una confirmacion HTTP 200 con JSON compatible antes de guardar COMPLETADA.

**Con exportacion:** se envia multipart/form-data con `archivo` (ZIP que contiene los exports y el manifiesto de ejecucion) y `metadatos` (texto JSON). El JSON contiene solicitud_id, ejecucion_id, ejecucion_bot_id, estado=COMPLETADA, resumen y export con nombre, content_type, size_bytes y sha256. El supermercado mantiene esta modalidad y exige generar su reporte; no se convierte un export faltante en una finalizacion exitosa.

Confirmacion del servidor con archivo:

```json
{
  "solicitud_id": 123,
  "ejecucion_id": 123,
  "estado": "COMPLETADA",
  "reporte": {"sha256": "SHA256_DEL_ZIP_RECIBIDO"}
}
```

**Sin exportacion, solo cuando el bot lo declara expresamente:** devolver `ResultadoBot((), resumen=..., detalles=..., permite_sin_export=True)`. El framework envia application/json al mismo endpoint; no fabrica un archivo vacio. El UUID y los IDs del ejemplo son ilustrativos:

```json
{
  "solicitud_id": 123,
  "ejecucion_id": 123,
  "ejecucion_bot_id": "0123456789abcdef0123456789abcdef",
  "estado": "COMPLETADA",
  "resumen": {"procesados": 2, "correctos": 2, "errores": 0},
  "export": null,
  "resultado": {"actualizados": ["A", "B"]}
}
```

Confirmacion del servidor sin archivo:

```json
{"solicitud_id": 123, "ejecucion_id": 123, "estado": "COMPLETADA", "reporte": null}
```

El backend debe aceptar ambas representaciones, validar la pertenencia y estado de la solicitud y persistir atomicamente resultado, referencia del export y estado final. Debe validar tamano/hash del ZIP y aplicar idempotencia durable: misma clave y contenido devuelve la confirmacion original; misma clave con contenido distinto se rechaza. Estas capacidades estan implementadas en el cliente y probadas con un servidor de contrato local; el backend real no se modifica y requiere habilitarlas.

Antes de enviar se guarda REPORTE_PENDIENTE con los datos y clave definitivos. Los metadatos nuevos incluyen una huella local para detectar cambios entre intentos; los ZIP se sincronizan a disco antes del checkpoint. Ante corte de subida, timeout, HTTP 503, respuesta invalida o confirmacion con ID/hash/estado incorrectos, no se repite el negocio ni se marca COMPLETADA. Al volver a ejecutar con el mismo ID y directorio, se reenvian los mismos datos/ZIP y clave. Las solicitudes ya confirmadas no se entregan otra vez. Los journals anteriores con ZIP siguen siendo compatibles.

Pruebas: `python -B -m unittest discover -s tests -p test_weyland_http.py -v` comprueba peticiones HTTP reales contra un servidor local ficticio, incluida la subida interrumpida y la confirmacion perdida para JSON/ZIP. `python -B -m unittest discover -s tests -p test_finalizacion.py -v` comprueba checkpoints, cambios de metadatos, export obligatorio y compatibilidad anterior. No se envian correos ni se modifica el backend real.
