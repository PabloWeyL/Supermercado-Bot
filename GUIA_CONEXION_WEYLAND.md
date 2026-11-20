# Conexión a Weyland Automate

Guía breve para reutilizar la conexión JWT en otro bot. El backend entrega una solicitud; el bot recibe archivos y/o parámetros, ejecuta su lógica y devuelve el resultado. Los ejemplos usan datos ficticios.

## 1. Dónde integrar cada archivo

Copiar la carpeta `config/` del ZIP conservando sus imports. Integrar con cuidado los ajustes si el proyecto ya tiene configuración propia.

| Archivo | Responsabilidad |
|---|---|
| `integraciones/weyland.py` | Sesión JWT, endpoints, estados, entrega, contratos y checkpoints. |
| `config/archivos.py` | Rutas, integridad, escritura atómica y excepción `ErrorWeyland`. |
| `config/configuracion.py` | Variables, credenciales y contextos de ejecución. |
| `config/base_datos.py` | SQLite local de apoyo; no accede a la BD del backend. |
| `integraciones/mi_proceso.py` | Adaptador donde se implementa la lógica del nuevo bot. |
| `integraciones/validacion_parametros.py` | Ejemplo de validación de parámetros y rutas; adaptar al contrato propio. |

El cliente organiza los endpoints; la autenticación usa funciones que reciben una sesión HTTP explícita, sin tokens globales. El orquestador delimita `ambito_base_datos()`, `ambito_identificacion()` y `contexto()`. Los recursos del negocio, como un navegador, deben tener su propio ámbito.

## 2. Configuración mínima

Usar Python 3.11 o posterior e instalar `requirements.txt`. Copiar `.env.example` como `.env` y completar las credenciales y registros reales:

```dotenv
CREDENCIALES_EMPRESA=WEYLAND
CREDENCIALES_VM=MVTESTING
BACKEND_API_KEY=
BACKEND_API_URL=https://automate.example/api
BACKEND_ACTIVO=true
BACKEND_AUTH_MODE=weyland
BACKEND_BOT_AUTH_MODE=jwt
BACKEND_CONTRATO=cola_actual
BACKEND_PROCESO_CODIGO=MI_PROCESO
BACKEND_REFRESH_ACTIVO=true
BACKEND_REFRESH_MARGEN_SEGUNDOS=60
BACKEND_REUTILIZAR_ULTIMA=false
```

Se requieren una API key activa en el entorno privado, una máquina registrada y el código exacto del proceso. La plantilla completa incluye rutas, tiempos y límites de archivos. En producción usar HTTPS y un directorio persistente de trabajos fuera del repositorio. HTTP local requiere permiso explícito en la configuración.

## 3. Interfaz pública

```python
from integraciones.weyland import crear_cliente_api

with crear_cliente_api() as cliente:
    cliente.login()
    cliente.verificar()  # Verifica acceso sin tomar trabajo.
    # cliente.refrescar() permite renovar explícitamente.
# El contexto cierra la sesión y limpia los tokens.
```

Para usar solo las funciones de autenticación, sin crear el cliente de endpoints:

```python
from integraciones.weyland import leer_valores
from integraciones.weyland import configuracion_desde_valores, sesion_jwt, login, verificar

config = configuracion_desde_valores(leer_valores())
with sesion_jwt(config) as sesion:
    login(sesion)
    verificar(sesion)
```

`refrescar(sesion)` renueva explícitamente; `solicitar(sesion, metodo, ruta, ...)` envía requests. El contexto llama a `cerrar(sesion)`. Usar `sesion_jwt()` para garantizar la limpieza, sin imprimir el estado privado de la sesión.

Las operaciones comunes son `get(ruta, params=...)`, `post(ruta, json=...)`, `put(...)` y `patch(...)`. Para multipart: `post(ruta, files=archivos, data=campos)`; no combinar esos campos con `json`. La ruta es relativa, por ejemplo `/bot/ejecuciones/123/estado`, sin repetir `/api`. Cerrar las respuestas HTTP con `with`.

La autenticación y renovación son automáticas antes de los requests cuando corresponde. `login()` reutiliza una sesión vigente; `refrescar()` exige una sesión iniciada. PUT/PATCH requieren endpoints compatibles: el flujo actual utiliza GET/POST.

## 4. Flujo y datos intercambiados

| Paso | Endpoint y datos principales |
|---|---|
| Login | POST `/api/bot/sesion`: envía `api_key` y `resolver_maquina_por_ip: true`. La respuesta incluye el ID de la máquina. Recibe `access_token`, `refresh_token`, `token_type`, `expires_in` y `sesion_id`. |
| Renovación | POST `/api/bot/sesion/renovar`: envía `{"refresh_token":"VALOR_EN_MEMORIA"}` y recibe tokens nuevos. Los requests usan `Authorization: Bearer ...`. |
| Tomar trabajo | POST `/api/bot/ejecuciones/siguiente`: envía `{"maquina_id":1,"id_externo":"0123456789abcdef0123456789abcdef","soporta_multiples_inputs":true}`. HTTP 204 significa fin normal sin trabajo. |
| Recibir solicitud | HTTP 200 entrega `ejecucion_id`, correlación `id_externo`, máquina, código de proceso, `parametros` e `inputs`. El bot valida que la asignación le corresponda. |
| Descargar | GET `/api/bot/ejecuciones/123/input?archivo_id=501`: devuelve bytes. Se descargan todos los inputs con límite de tamaño, archivo parcial y SHA-256. |
| Actualizar estado | POST `/api/bot/ejecuciones/123/estado`: `{"estado":"EN_EJECUCION","mensaje":"Iniciando trabajo","resumen":null}`. El cliente omite `resumen` cuando no hay datos. |
| Entregar resultado | POST `/api/bot/ejecuciones/123/reporte`, multipart: `archivo` ZIP, `estado`, `mensaje` y `resumen` como texto JSON. Confirma HTTP 200 con `ejecucion_id`, `estado:"COMPLETADA"` y `estado_bot`. |
| Cerrar | POST `/api/bot/sesion/cerrar`, con Bearer y `{}`. Espera HTTP 204 y limpia los tokens locales. |

El coordinador publica `PENDIENTE`, después `EN_EJECUCION` o `EN_REPROCESO` y finalmente `FINALIZADO` o `FINALIZADO_CON_OBSERVACIONES`. También admite `ERROR`, `CANCELADO` e `INTERRUMPIDO`. Enviar códigos de estado, no etiquetas visibles. Los errores técnicos deben propagarse como fallos; las observaciones funcionales se cuentan en el resumen.

El ID recibido identifica la solicitud/ejecución. `id_externo` es una correlación diferente, generada y persistida por el coordinador. La comprobación `verificar()` usa GET de `/siguiente` y espera HTTP 405 autenticado; tomar trabajo requiere POST.

### Parámetros con y sin archivos

Fragmento de una solicitud sin archivos:

```json
{
  "parametros": {"fecha_desde":"2026-11-01", "fecha_hasta":"2026-11-13", "limite":100},
  "input": null, "inputs": []
}
```

Fragmento con un archivo y parámetros adicionales:

```json
{
  "parametros": {"fecha_desde":"2026-11-01", "fecha_hasta":"2026-11-13", "limite":100},
  "inputs": [{"archivo_id":501, "nombre":"entrada.xlsx",
              "descarga":"/api/bot/ejecuciones/123/input?archivo_id=501"}]
}
```

`procesar(carpeta, entrada, parametros)` recibe `None` sin archivos, un `Path` con uno o una tupla de `Path` con varios. Los parámetros siempre llegan como diccionario. Validar claves permitidas, campos obligatorios, tipos, fechas y rangos; no ejecutar comandos ni SQL recibidos en ellos. El ejemplo incluido rechaza fechas imposibles, períodos invertidos y valores fuera de rango. Validar además bytes y columnas de cada Excel.

## 5. Implementar el bot receptor

1. Completar el `.env` privado. `python verificar_jwt.py` prueba solo las funciones de autenticación; `python main.py` verifica mediante el cliente. Ninguno toma solicitudes.
2. Implementar `integraciones/mi_proceso.py`. La plantilla valida entradas y parámetros, pero mantiene `NotImplementedError` hasta incorporar el negocio.
3. Procesar los archivos uno a uno y consolidar una sola salida dentro de la carpeta de ejecución.
4. Devolver `ResultadoBot((reporte,), resumen=..., incluir_metadatos_export=False)`. El ZIP incluirá el reporte sin añadir el JSON de ejecución. Sin exportación, usar `ResultadoBot((), permite_sin_export=True)` para una entrega JSON.
5. Registrar `ProcesoBot(procesar, requiere_input=None, informar_estados=True)` y ejecutar `python main.py --ejecutar`. Usar `True` si los archivos son obligatorios o `False` si no se admiten.

El coordinador y los helpers de endpoints conservan las validaciones de IDs, integridad y confirmación; la interfaz HTTP genérica no las sustituye. Los inputs se guardan en `BACKEND_TRABAJOS_DIR/cola_actual/maquina_ID/ejecuciones/ID/datos/input/`, con nombres independientes. El checkpoint permite recuperar descargas y entregas sin repetir trabajo confirmado. Si se desactiva el backend, el bot receptor debe implementar su flujo local. Los correos se integran mediante el callback opcional `notificar(evento, estado, carpeta)`.

## 6. Errores y seguridad

Capturar una sola clase: `ErrorWeyland`. Revisar `error.tipo`: `HTTP`, `RED`, `CONTRATO`, `ASIGNACION_INCOMPATIBLE` o `SIN_PENDIENTES`. También conserva `status_code` y `ejecucion_id` cuando corresponden. Un 401 permite una renovación y un reenvío; otros rechazos o escrituras inciertas requieren revisión según el checkpoint.

Mantener tokens solo en memoria y credenciales fuera de Git. No hace falta desencriptar el JWT. El cliente utiliza exclusivamente `/api/bot/*`; el token BOT no sirve para consultar la API del frontend. Ante cortes en toma, renovación o entrega, conservar la evidencia y no repetir POST indiscriminadamente.

La toma de la solicitud conserva su ID numérico y su checkpoint. Antes de publicar `EN_EJECUCION` o `EN_REPROCESO`, el bot envía `{"id_ejecucion":"19112026_093507"}` a `POST /api/bot/ejecuciones/{id}/id-ejecucion`. El valor se genera con la hora local del equipo en formato `DDMMYYYY_HHMMSS`. Ante una respuesta incierta, el bot conserva la solicitud y el valor en el checkpoint. Verifica ese ID en Weyland Automate antes de conciliar el checkpoint.
