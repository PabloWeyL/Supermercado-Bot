# RPA de supermercados

Consulta productos, genera un reporte Excel y se conecta a Weyland Automate mediante JWT. Se ejecuta con `python main.py` o `ejecutar.bat`.

## Arquitectura

El proyecto tiene 11 archivos Python organizados por responsabilidad:

```text
main.py                       Orquestación, módulos, reintentos y comandos
config/
  configuracion.py            .env, settings, flags, logs, ID y validaciones
  base_datos.py               SQLite, esquema y consultas
  correo.py                   SMTP y cola persistente de notificaciones
  formato_excel.py            Estilos del reporte
  archivos.py                 Constantes, rutas, huellas y persistencia atómica
  autenticacion.py            JWT BOT, renovación y cierre
  backend.py                  Endpoints, asignación, input y entrega HTTP
  ejecuciones.py              Estados, checkpoints y recuperación
integraciones/
  supermercados.py           Entradas, catálogo, Chrome y búsqueda de productos
  reportes.py                 Excel de salida y resumen del proceso
input/
procesados/
reportes/
temporales/
logs/
recursos/                     Puente de Chrome, imágenes y templates
```

`config/` no importa módulos de negocio. `main.py` conecta los servicios con la automatización y los reportes. No se incluyen módulos vacíos de Monitor ni Key Vault porque este bot no los utiliza. El catálogo, los selectores, la lectura de entradas, el controlador de Chrome y su puente CDP están integrados en `integraciones/supermercados.py`.

## Instalación

Requiere Python 3.11 o posterior, Node.js 22 o posterior y Google Chrome. Desde la raíz:

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
```

Completar el `.env` privado. Los valores del entorno prevalecen sobre el archivo. Las rutas configurables, los límites y las credenciales mantienen sus nombres existentes.

## Flujo y módulos

1. `main.py` inicia el contexto y llama a `configuracion_inicial()`: logging mensual, ID y SQLite. Valida configuración, integridad, Node.js y Chrome antes de procesar.
2. En modo web, autentica, recupera una ejecución incompleta o toma una solicitud EN_COLA mediante `siguiente`. Verifica máquina y código de proceso y descarga el input si existe.
3. Módulo 1: correo inicial, si está habilitado. `main.py` aplica `MAX_REINTENTOS_CORREO`; si no se confirma, no inicia el negocio. En modo web se informa ERROR al backend. La cola conserva el aviso para recuperación.
4. Módulo 2: valida y carga las entradas; ejecuta las consultas. El loop de reintentos está en `main.py` y se aplica por tarea mediante `MAX_REINTENTOS`. No repite consultas ya guardadas correctamente.
5. Módulo 3: genera el reporte. El orquestador incluye el punto donde agregar otras etapas.
6. En modo web, entrega el reporte y su estado final juntos. Después notifica resultados o fallas, respetando la bandera web de correo.
7. Registra FIN. El navegador se cierra en `finally`, incluso si falla su apertura o una búsqueda.

Los POST de toma, estado y entrega no se reintentan con el loop del negocio. Una respuesta incierta se concilia con el backend para evitar duplicados. Si el trabajo ya terminó, un fallo de correo final no vuelve a ejecutar el negocio ni cambia un cierre web confirmado.

## Configuración principal

| Variables | Uso |
|---|---|
| `ID_EJECUCION`, `NOMBRE_PROCESO`, `NOMBRE_PROCESO_TEXTO` | Identificación y logs. El ID web siempre lo entrega el backend. |
| `BACKEND_ACTIVO` | `true`: solicita trabajo al backend con JWT; `false`: procesa solo los Excel locales de `CARPETA_INPUTS`. |
| `CARPETA_INPUTS` | Carpeta local de entrada; `input` por defecto, admite ruta absoluta o relativa a `RUTA_BASE`. |
| `MODULO_AUTOMATIZACION_SUPERMERCADOS` | Activa la automatización. Se admite `MODULO_SCRAPING` como alias anterior. |
| `MODULO_CORREO`, `MODULO_CORREO_INICIAL` | Activan notificaciones. |
| `MAX_REINTENTOS`, `MAX_REINTENTOS_CORREO` | Intentos máximos por consulta y correo inicial. |
| `RETENCION_LOGS_MESES` | Retención de los logs mensuales. |
| `DB_PATH` | Ruta SQLite; el modo web utiliza una base aislada por ejecución. |
| `MAX_ARCHIVO`, `MAX_EXPANDIDO`, `MAX_FILAS`, `MAX_COLUMNAS` | Límites de seguridad de la entrada. |
| `MAX_ADJUNTOS_BYTES` | Límite de adjuntos de correo. |

## Conexión al backend

```dotenv
BACKEND_AUTH_MODE=weyland
BACKEND_BOT_AUTH_MODE=jwt
BACKEND_CONTRATO=cola_actual
BACKEND_API_URL=https://automate.example.com/api
BACKEND_BOT_LOGIN_RUTA=/bot/sesion
BACKEND_BOT_REFRESH_RUTA=/bot/sesion/renovar
BACKEND_BOT_CERRAR_RUTA=/bot/sesion/cerrar
BACKEND_MAQUINA_ID=1
BACKEND_PROCESO_CODIGO=SUPERMERCADOS
BACKEND_TRABAJOS_DIR=temporales/weyland
BACKEND_REUTILIZAR_ULTIMA=false
BACKEND_REFRESH_ACTIVO=true
BACKEND_REFRESH_MARGEN_SEGUNDOS=60
```

El ID y código del ejemplo deben corresponder a registros reales. Para producción, usar HTTPS, `ENTORNO=PRODUCCION` y un directorio operativo fuera del repositorio. HTTP se admite solo en loopback con `BACKEND_PERMITIR_HTTP_LOCAL=true`.

Las credenciales estandarizadas son `WEYLAND_AUTOMATE_MVTESTING_USER` y `WEYLAND_AUTOMATE_MVTESTING_PWD`; empresa y VM se resuelven mediante `CREDENCIALES_EMPRESA` y `CREDENCIALES_VM`. No se utiliza API key en el arranque actual.

El bot inicia sesión directamente con `POST /api/bot/sesion`, enviando correo, contraseña y el ID de máquina configurado. Conserva `access_token`, `refresh_token` y su vencimiento únicamente en memoria; envía `Authorization: Bearer <access_token>` a `/api/bot/*`. El cliente utiliza exclusivamente la sesión JWT BOT. El refresh rota ambos tokens; si su respuesta se pierde, no se reutiliza el anterior. Al cerrar el cliente se intenta cerrar la sesión BOT y siempre se limpian sus tokens.

El contrato actual toma una solicitud por invocación. Un HTTP 204 termina sin trabajo. No se reutiliza la última solicitud ni se inventan IDs. Se admiten `input: null` y productos JSON en parámetros cuando corresponda.

## Entradas y reportes

El modo local lee todos los `.xlsx` y `.xlsm` de `input/`, sin exigir un nombre. Columna obligatoria: `producto`. Opcionales: `supermercado`, `marca`, `precio_minimo`, `precio_maximo`.

Se validan los bytes OOXML, expansión, filas, columnas y textos; no se admiten fórmulas ni contenido activo. En modo web se procesan únicamente los datos de la solicitud asignada.

El Excel incluye productos encontrados y consultas sin resultado, con una columna `error`. Los resultados correctos la dejan vacía. La entrega web es `resultado_DDMMYYYY-HHMMSS.zip` y contiene solo `datos/reportes/resultados.xlsx`; no incluye el JSON de ejecución, inputs, logs ni bases de datos.

Los estados son PENDIENTE, EN_EJECUCION, EN_REPROCESO y los cierres FINALIZADO, FINALIZADO_CON_OBSERVACIONES, CANCELADO, INTERRUMPIDO o ERROR. Se envía el código del estado. El reporte y el cierre exitoso se entregan juntos.

## Comandos

```bat
python main.py
python main.py api-verificar
python main.py web-procesar
python main.py procesar
python main.py procesar --productos-json ruta/productos.json
python main.py cargar
python main.py estado
python main.py reporte
python main.py correos-estado
python main.py correos-reintentar --id-correo ID
```

`api-verificar` inicia la sesión JWT BOT y comprueba el acceso sin tomar solicitudes: un GET autenticado a `siguiente` debe responder 405, porque la toma admite únicamente POST. `procesar` explícito utiliza el flujo local; sin comando se respeta `BACKEND_ACTIVO`. `ejecutar.bat` admite los mismos argumentos y conserva el código de salida.

## Correo y recuperación

SMTP se configura con `SMTP_SERVER`, `SMTP_PORT`, las credenciales estandarizadas `WEYLAND_SMTP_MVTESTING_USER` / `WEYLAND_SMTP_MVTESTING_PWD` y las listas de distribución. Inicio y resultados van a ambas listas; fallas a `LISTA_DISTRIBUCION_ERROR`. Se eliminan destinatarios repetidos.

La web puede deshabilitar avisos mediante `configuracion.notificar_correo=false`. La entrega web es independiente del correo. Los envíos se conservan por destinatario y no se reenvían si ya están aceptados por SMTP. Una entrega incierta requiere revisión con el proveedor:

```bat
python main.py correos-resolver --id-correo ID --entregado
python main.py correos-resolver --id-correo ID --no-entregado
```

Conservar checkpoints y evidencia de `BACKEND_TRABAJOS_DIR`: una toma o entrega sin confirmación requiere conciliación manual desde la web en modo JWT BOT; un callback interrumpido no se repite automáticamente. El backend local no permite usar el JWT BOT en `/api/ejecuciones`, sus detalles, archivos ni catálogo: son endpoints del frontend. El bot bloquea esas consultas sin intentar un login web alternativo y no reenvía automáticamente operaciones inciertas. Los checkpoints existentes conservan el mismo formato y las mismas rutas. Un rechazo 401/403 en la toma se registra como TOMA_RECHAZADA; un timeout permanece TOMA_INCIERTA.

Para agregar un módulo: declarar su flag y settings en `config/configuracion.py` y `.env`, agregar sus consultas a `base_datos.py`, implementar el negocio en `integraciones/` y llamarlo desde `main.py` con `ejecutar_modulo()`. No colocar lógica de negocio en los servicios HTTP.

## Dirección del navegador

Configurar `RPA_CHROME_URL=http://127.0.0.1:9222` en `.env`. Esta dirección corresponde al servicio local de Chrome, no al backend. Se utiliza tanto para consultar Chrome como para iniciar su puerto de automatización. Solo admite HTTP en loopback, con puerto y sin credenciales, rutas ni parámetros. Si no se define, se conserva el puerto de `RPA_CHROME_PUERTO` (9222 por defecto); cuando se define, prevalece el puerto de la URL.

## Configuración operativa en .env

Las credenciales de Automate y SMTP continúan en las variables privadas estandarizadas. No se incorporan contraseñas ni tokens al código, al catálogo ni a la plantilla. Los tokens JWT siguen siendo temporales y permanecen en memoria.

Las URLs de tiendas se configuran mediante `SUPERMERCADO_LIDER_URL`, `SUPERMERCADO_TOTTUS_URL` y `SUPERMERCADO_UNIMARC_URL`. Lider conserva el marcador literal `{termino}` para construir cada búsqueda. Las esperas de cada tienda usan `SUPERMERCADO_<TIENDA>_ESPERA` y, cuando corresponde, `SUPERMERCADO_<TIENDA>_ESPERA_RESULTADOS`. El catálogo JSON contiene sus nombres de variable y los selectores DOM.

| Grupo | Variables |
|---|---|
| Consultas | `MAX_PRODUCTOS_POR_BUSQUEDA`, `RPA_TIMEOUT`, `RPA_ESPERA_MAX`, `RPA_DOM_REINTENTOS`, `RPA_RESULTADOS_INTERVALO`, `RPA_ESCRITURA_PAUSA` |
| Navegador | `RPA_CHROME_URL`, `RPA_HTTP_TIMEOUT`, `RPA_CIERRE_TIMEOUT`, `RPA_SONDEO_INTERVALO`, `RPA_JS_REINTENTOS`, `RPA_JS_PAUSA`, `RPA_CAPTURA_PAUSA` |
| Archivos y reintentos | `ARCHIVO_REINTENTOS`, `ARCHIVO_REINTENTO_PAUSA`, `REINTENTOS_PAUSA`, `REINTENTOS_PAUSA_MAX` |
| Correo y BD | `SMTP_TIMEOUT`, `DB_TIMEOUT`, `CORREO_DB_TIMEOUT` |
| Límites | `BACKEND_MAX_BYTES`, `BACKEND_MAX_PAGINAS`, `MAX_EXCEL_ENTRADAS_ZIP` y los límites Excel existentes |
| Dependencias | `DEPENDENCIAS_TIMEOUT`; `CHROME_PATH` opcional para reemplazar la detección automática |

Los tiempos se expresan en segundos; los tamaños, en bytes. Se rechazan números negativos, cero, NaN e infinito en los ajustes que requieren valores positivos. Las URLs de supermercados requieren HTTPS sin credenciales. El entorno del sistema prevalece sobre `.env`; reiniciar el bot después de cambiar valores.

El puente CDP es una constante dentro de `integraciones/supermercados.py` y Node.js lo ejecuta en memoria con `-e`; no necesita un archivo `.cjs` externo ni una ruta en `.env`. `config/configuracion.py` centraliza los valores y conserva valores predeterminados compatibles para los ajustes opcionales. Los códigos HTTP, nombres de endpoints y espacios de nombres XML son detalles del contrato y permanecen en el código.

## Activar o desactivar la conexión al backend

```dotenv
BACKEND_ACTIVO=true
CARPETA_INPUTS=input
```

Con `true`, `python main.py` autentica por JWT y toma una solicitud del backend: descarga su input si existe o utiliza sus parámetros JSON. Después publica los estados y entrega el reporte. Si la web no responde o no hay solicitudes, no utiliza automáticamente archivos locales.

Con `false`, el mismo comando lee todos los Excel compatibles de `CARPETA_INPUTS`, sin exigir un nombre. No inicia sesión, consulta, descarga ni sube resultados al backend. Limita las tareas y el reporte a esos archivos; conserva el historial en SQLite sin incorporar trabajo de archivos que ya no están en la carpeta. Si no hay Excel, falla la validación de entrada. Las validaciones y el correo siguen respetando su configuración independiente.

`ARRANQUE_MODO` fue sustituido por `BACKEND_ACTIVO`. Con el backend desactivado, `api-verificar` informa que no realizará peticiones y `web-procesar` se rechaza. El argumento `--productos-json` requiere tener el backend habilitado; el subproceso de una solicitud web lo utiliza cuando corresponde. `procesar` sigue siendo el comando local explícito utilizado también por el coordinador.

## Automatización unificada

`integraciones/supermercados.py` reúne el catálogo `CATALOGO_SUPERMERCADOS`, el puente `PUENTE_CHROME_JS`, la clase `ChromeRobot`, la lectura segura de Excel/JSON y las búsquedas. Cada función y método incluye su descripción mediante una docstring `"""..."""` como primera instrucción. Las URLs, esperas y límites se siguen leyendo de `.env`.

`main.py` llama a `supermercados.validar_catalogo()`, las funciones de carga y `procesar_cola_a_resultados()`. `integraciones/reportes.py` reutiliza las funciones de formato literal y clasificación de incidencias del mismo módulo. El módulo de configuración valida los servicios técnicos y no importa la automatización.

## Varios archivos de entrada en una solicitud

El bot anuncia `soporta_multiples_inputs: true` en `POST /api/bot/ejecuciones/siguiente`. Lee la lista `inputs`, valida cada `archivo_id`, nombre y ruta y descarga cada archivo mediante `GET /api/bot/ejecuciones/{id}/input` con el parámetro `archivo_id`. No envía el JWT a URLs firmadas externas.

Cada descarga tiene un nombre local independiente, aunque dos adjuntos tengan el mismo nombre original. Se conserva su tamaño y SHA-256 en el checkpoint; las descargas completas se verifican antes de volver a utilizarlas. Un lote incompleto o alterado no se entrega al negocio. Los checkpoints anteriores con un solo `input` siguen siendo admitidos.

Los archivos se procesan secuencialmente: se carga un Excel, se completan sus consultas y después se continúa con el siguiente. Los reintentos quedan limitados a las tareas del archivo actual. Al finalizar se genera un único `resultados.xlsx` con los productos y las incidencias de todos los archivos. Se sube una sola entrega con un solo estado final y un resumen consolidado; los correos de la solicitud tampoco se multiplican por archivo.

`BACKEND_MAX_INPUTS` limita la cantidad de adjuntos y `BACKEND_MAX_INPUTS_BYTES` su tamaño total descargado en bytes. Se mantienen los límites individuales `MAX_ARCHIVO`, `MAX_EXPANDIDO`, `MAX_FILAS` y `MAX_COLUMNAS`. Una comunicación o escritura incierta conserva la evidencia y mantiene las reglas de revisión del coordinador.

## Funciones y sesiones de ejecución

Las validaciones, lectores, búsquedas y reportes siguen siendo funciones. Los objetos con estado tienen un ciclo de vida propio: `autenticacion.py` administra los tokens mediante funciones y una sesión HTTP explícita, `SesionAutomatizacion` administra el navegador y las capturas, y `BaseDatosEjecucion` selecciona la base SQLite de la ejecución.

`main()` delimita la base de datos y el ID local mediante `ambito_base_datos()` y `ambito_identificacion()`. `ejecutar_local()` y `diagnosticar()` delimitan el navegador con `sesion_automatizacion()`. Los recursos se cierran y los contextos anteriores se restauran también cuando ocurre una excepción. La creación del controlador de Chrome es diferida: importar un módulo no crea una sesión de navegador.

Los `ContextVar` definidos en los módulos son claves estables de contexto; las instancias operativas y sus valores pertenecen a cada ejecución. No hay un navegador, lista de capturas, ruta SQLite o ID local mutable compartido como variable global. Para invocar directamente funciones de automatización desde otro programa, usar `with sesion_automatizacion():`; para operar con otra SQLite, usar `with ambito_base_datos(ruta):`.

## Error de integración unificado

La conexión usa una sola excepción, `ErrorWeyland`, con `tipo` (`HTTP`, `RED`, `CONTRATO`, `ASIGNACION_INCOMPATIBLE` o `SIN_PENDIENTES`), `status_code` y `ejecucion_id` cuando corresponden. El coordinador distingue las categorías para conservar checkpoints y evitar operaciones duplicadas. HTTP 204 sin trabajo sigue siendo una finalización normal.

La autenticación del frontend y la compatibilidad con API key fueron retiradas del cliente; se mantienen login, refresh y cierre JWT sobre `/api/bot/*`.
