# Conexión reutilizable al backend actual de Weyland Automate

Supermercados implementa `cola_actual`: login Django/CSRF, toma de una EN_COLA de la VM, input opcional, negocio una sola vez y reporte multipart. El SDK no conoce scraping, Excel, correo, `.env`, Azure ni rutas de otro bot. Esta guía permite integrar la misma conexión en ACME, vencimientos, Godzilla u otro proyecto sin copiar el negocio del supermercado.

## Qué configurar en supermercados

```dotenv
BACKEND_AUTH_MODE=weyland
BACKEND_CONTRATO=cola_actual
BACKEND_MAQUINA_ID=
BACKEND_PROCESO_CODIGO=
BACKEND_API_URL=http://127.0.0.1:8000/api
BACKEND_PERMITIR_HTTP_LOCAL=true
CREDENCIALES_EMPRESA=WEYLAND
CREDENCIALES_VM=MVTESTING
WEYLAND_AUTOMATE_MVTESTING_USER=
WEYLAND_AUTOMATE_MVTESTING_PWD=
WEYLAND_AUTOMATE_MVTESTING_API_KEY=
```

Completar el ID numérico real de la VM y el código registrado. MVTESTING es parte del nombre de la credencial y no sustituye el ID del servidor. No se asignó una VM ficticia al `.env` privado. En producción usar HTTPS y un directorio BACKEND_TRABAJOS_DIR fuera del repositorio, con permisos exclusivos de la cuenta del bot. BACKEND_EMPRESA_ID es opcional para el contexto de empresa de las consultas de conciliación por sesión.

Conservar los límites de entrada, correo y adjuntos ya configurados. Las credenciales se leen mediante `config/credenciales.py`, que adapta la nomenclatura a los nombres internos del SDK. Los secretos deben inyectarse desde el proveedor autorizado; no se incluyen en archivos de entrega.

## Comandos

```bat
python main.py
ejecutar.bat
python main.py web-procesar
python main.py api-verificar
```

Sin argumentos se mantienen las comprobaciones y toda la batería aislada antes de consultar al backend. `web-procesar` entra directamente al flujo web. En cola_actual no se envía --id-solicitud: el backend entrega el ID oficial. api-verificar comprueba login/perfil y no necesita tomar trabajo.

Un 204 termina con SIN_TRABAJO y salida 1, sin descargar, ejecutar, informar `/error` o reutilizar la última solicitud. Los valores ID_PRUEBA y CODIGO_PRUEBA pertenecen al simulador anterior y no se usan para seleccionar trabajo real en este modo.

## Dónde va cada archivo

| Ubicación | Responsabilidad |
|---|---|
| `integraciones/weyland/` completo | Paquete portable que debe copiarse íntegro o instalarse como SDK. Incluye los módulos previos de sesión, configuración, archivos y ResultadoBot. |
| `integraciones/weyland/cola_actual.py` | Cliente del contrato vigente: siguiente, input, reporte, error y consultas de conciliación. No presupone idempotencia. |
| `integraciones/weyland/ejecutor_cola.py` | ProcesoBot, tabla permitida de adaptadores, bloqueo, checkpoints y recuperación conservadora. |
| `integraciones/weyland/resultado_cola.py` | Snapshot del resultado y generación de ZIP o comprobante JSON multipart. |
| `integraciones/weyland/aplicacion_cola.py` | Entrada configurable, validación de VM y auditoría de journals anteriores antes de tomar trabajo. |
| `config/credenciales.py` | Convierte nombres de credenciales. Es del proyecto, no una dependencia del SDK. |
| `config/weyland.py` y `config/api.py` | Leen `.env`/entorno y seleccionan explícitamente el cliente cola_actual o por_id. |
| `integraciones/weyland_worker.py` | Adaptador del supermercado. Conecta el callback de negocio y la cola de correos al coordinador común. |
| `integraciones/arranque_web.py` y `main.py` | Validación del arranque y comandos. No contienen la lógica HTTP ni la de scraping. |

En un proyecto que instale el paquete como `weyland`, sustituir el prefijo de importación `integraciones.weyland` por `weyland`. No crear otra copia de la lógica de login dentro del nuevo bot.

### Plantilla genérica incluida en la entrega

En `Integracion-Weyland-Automate/plantilla_bot/` ya están colocados los archivos
para comenzar otro bot. No es necesario copiar el worker específico de supermercados.

| Archivo de la plantilla | Qué implementar o configurar |
|---|---|
| `config/configuracion.py` | Lectura de valores, REQUIERE_INPUT y EXTENSIONES_ENTRADA del nuevo proceso. |
| `config/api.py` | Fábrica del cliente y punto para adaptar el proveedor de secretos. |
| `integraciones/worker.py` | Callback que convierte el resultado del negocio en ResultadoBot y selecciona el contrato. |
| `integraciones/weyland/` | Paquete completo ya copiado; conservar sus funciones HTTP y checkpoints. |
| `recursos/proceso.py` | Sustituir la demostración TXT por el negocio real, validando su entrada. |
| `main.py` y `ejecutar.bat` | CLI genérica: procesar local, api-verificar y web-procesar. |

La plantilla recibe los nombres internos BACKEND_EMAIL, BACKEND_PASSWORD y
BACKEND_BOT_API_KEY desde su proveedor. Si se usa la nomenclatura corporativa,
adaptarla en config/api.py mediante config/credenciales.py o un proveedor equivalente;
no cambiar los nombres internos ni agregar secretos al SDK.

En esa plantilla ejecutar `python main.py web-procesar`, con contrato cola_actual,
para consultar la cola; no necesita un ID manual. El arranque sin argumentos y la
batería completa automática pertenecen al supermercado y no se trasladan a otros
bots al copiar solamente el SDK. Cada proyecto debe conservar sus comprobaciones
y conectar sus notificaciones mediante el callback notificar si corresponde.

## Ejemplo mínimo para otro bot

```python
from pathlib import Path
from integraciones.weyland.cola_actual import crear_cliente_cola
from integraciones.weyland.ejecutor_cola import ProcesoBot
from integraciones.weyland.aplicacion_cola import ejecutar_cola_configurada
from integraciones.weyland.worker import ResultadoBot

def negocio(carpeta, entrada, parametros):
    # Validar la entrada específica y ejecutar el negocio propio del proyecto.
    salida = carpeta / "resultado.txt"
    salida.write_text("Resultado del proceso", encoding="utf-8")
    return ResultadoBot((salida,), resumen={"procesados": 1, "correctos": 1, "errores": 0})

def ejecutar_bot(valores, credenciales, raiz):
    codigo = valores["BACKEND_PROCESO_CODIGO"]
    procesos = {codigo: ProcesoBot(negocio, requiere_input=False, extensiones=())}
    return ejecutar_cola_configurada(
        lambda: crear_cliente_cola(valores, credenciales),
        valores, Path(raiz), procesos,
    )
```

Este ejemplo es una plantilla; no inventa un código registrado ni crea solicitudes. El callback devuelve ResultadoBot y nunca realiza los POST de estado por su cuenta. Para input obligatorio usar requiere_input=True y declarar extensiones; para ambas modalidades usar None. Los validadores de bytes, estructura, filas y columnas pertenecen al adaptador que lee Excel/CSV.

Si un proceso está diseñado para terminar sin export, devolver `ResultadoBot((), detalles={...}, permite_sin_export=True)`. El SDK genera un comprobante JSON y lo sube como archivo. Omitir esa bandera cuando el export sea obligatorio, para que su ausencia sea un error y no un éxito silencioso.

Una VM puede utilizar una tabla con varios códigos y callbacks fijos. No convertir códigos recibidos en nombres de scripts o comandos. Solo se deben liberar en esa VM procesos soportados por su coordinador. El backend no ofrece filtro de código de proceso en siguiente: un código distinto queda bloqueado como ASIGNACION_INCOMPATIBLE para revisión.

## Requests y respuestas reales

1. CSRF, login con correo/password, CSRF rotado y perfil, en una sesión con cookies.
2. `POST /api/bot/ejecuciones/siguiente` con `{"maquina_id": 1, "id_externo": "correlacion_uuid"}` y X-Api-Key. El ID 1 es ilustrativo. Un 200 ya cambió el estado a EN_PROCESO.
3. Si input es objeto, `GET /api/bot/ejecuciones/{id}/input`; si es null, el callback recibe entrada=None. No se siguen URLs firmadas externas.
4. `POST /api/bot/ejecuciones/{id}/reporte`, multipart con un único campo archivo. El archivo es un ZIP o un comprobante JSON; no se manda export=null como cuerpo HTTP.
5. Confirmación esperada: `{"ejecucion_id": 123, "estado": "COMPLETADA"}`. No se exige un hash que el servidor no devuelve.
6. Ante fallo confirmado de negocio, `POST /api/bot/ejecuciones/{id}/error` con `{"mensaje": "Descripción saneada"}`. Confirmación: mismo ID y estado ERROR.

Los parámetros de la asignación deben ser un objeto. La configuración arbitraria de empresa y las URLs firmadas no se persisten. Campos de credenciales en parámetros o detalles se rechazan: resolver secretos en el proyecto mediante el proveedor autorizado. La API key actual es global; el login y las validaciones del cliente no sustituyen una autorización por empresa/proceso del servidor.

## Input de supermercados

Con archivo, se descarga a datos/input/entrada.xlsx en la carpeta del ID y el lector actual valida los bytes y la estructura antes de cargar la cola. No se escanean inputs generales de otro trabajo.

Sin archivo, se esperan productos en parámetros, por ejemplo:

```json
{"productos": [{"producto": "leche", "supermercado": "unimarc"}]}
```

Se admiten los filtros de marca y precio que ya valida productos_json.py. Un input ausente sin productos válidos produce un error del mismo ID; no se carga un Excel antiguo.

## Resultados y correo

La entrega queda congelada antes del POST. En el modo actual, el subproceso recibe WEYLAND_CORREO_COORDINADOR=true y no envía por su cuenta correos de inicio, resultado o falla. El padre prepara inicio tras la asignación y resultado/falla tras la confirmación web. Se mantienen ambas listas para inicio/resultados y solo ERROR para fallas.

El correo final adjunta exactamente el archivo entregado a la web. Su cola y Message-ID estable permiten retomar notificaciones sin repetir la entrega web. La trazabilidad conserva el ID oficial y la correlación local; los passwords y API keys no se guardan en el journal ni en el export.

## Checkpoints y recuperación

El directorio es BACKEND_TRABAJOS_DIR/cola_actual/maquina_ID. coordinador.json conserva la intención o el trabajo activo; ejecuciones/ID contiene contexto, datos, reportes y finalización. La carpeta de trabajo y la cuenta deben permanecer iguales al retomar. No borrar registros para forzar otro inicio.

| Fase | Acción al volver a ejecutar |
|---|---|
| TOMA_INCIERTA | Buscar una correlación única mediante la API de usuario paginada y recuperar el detalle. No repetir siguiente. Sin permisos o coincidencia única se requiere revisión. |
| DESCARGANDO | Descargar de nuevo el mismo ID; nunca procesar un parcial. |
| PREPARADO | Validar el snapshot local antes de comenzar el negocio. |
| EJECUTANDO | Detener para revisión; no repetir un negocio incierto. |
| EXPORT_PENDIENTE | Verificar los archivos del resultado persistido y empaquetarlos, sin ejecutar el negocio otra vez. |
| ENTREGA_LISTA | Realizar el primer intento de entrega del archivo congelado. |
| ENTREGA_INCIERTA | Consultar detalle y descargar el reporte web para comparar tamaño y SHA-256. Confirmar solo si coincide. Un estado EN_PROCESO o un 409 no autorizan un reenvío. |
| ERROR_INCIERTO | Consultar el mismo ID y confirmar ERROR; no reenviar a ciegas. |
| COMPLETADA o ERROR | Revisar la notificación pendiente y permitir un nuevo ciclo de toma; nunca reutilizar la ejecución terminada. |

Los GET de conciliación usan sesión y permisos de lectura. BACKEND_EMPRESA_ID permite indicar el contexto cuando corresponde. La API key no habilita esos permisos. Los POST de este modo no envían Idempotency-Key: el servidor actual no la implementa.

El framework no ofrece un comando para forzar a ciegas un reenvío incierto. Si el servidor aún muestra EN_PROCESO, hay que resolver con el operador si el intento anterior sigue activo o no persistió. La automatización completa de ese caso requiere un contrato idempotente o una confirmación adicional del servidor.

Antes de migrar se revisan journals estado.json del contrato anterior. Los pendientes bloquean el modo nuevo. Por_id permanece disponible solo de forma explícita; no hay fallback automático si una ruta devuelve 404.

## Pruebas y reutilización

```bat
python -B -m unittest discover -s tests -p test_cola_actual.py -v
python -B -m unittest discover -s tests -p test_supermercados_cola_actual.py -v
python -B -m integraciones.autopruebas --resultado temporales/pruebas.json
```

El servidor de tests reproduce el contrato actual, sin idempotencia. Se prueban 204, input opcional, recuperación de toma, permisos de lectura, fallo/ZIP/JSON, descarga interrumpida y confirmación perdida. La prueba de negocio usa SQLite y genera el Excel real, mientras extracción y SMTP son ficticios. No consume solicitudes del backend real ni envía correos reales.

Para otro bot: copiar/instalar el paquete completo, implementar su callback y proveedor de configuración, registrar su código y VM y ejecutar primero las pruebas contra el contrato local. Los demás bots no cambian de modo automáticamente al recibir estos módulos: deben adaptar también su CLI, notificaciones y entrada.
