## Arranque completo sin parametros

El contrato configurado ahora es `BACKEND_CONTRATO=cola_actual`, compatible con el
backend vigente. Completar `BACKEND_MAQUINA_ID` con la VM registrada antes de ejecutar.
No se inventa ese ID ni se toman trabajos de la cola global cuando falta.
La [guia del contrato actual](docs/GUIA_BACKEND_ACTUAL.md) explica las funciones
reutilizables, archivos, configuracion, input opcional, cierre y recuperacion.

Correos con reintentos persistentes, IDs de ejecucion y controles de Excel:
[guia operativa](docs/CORREOS_TRAZABILIDAD_EXCEL.md).

Con el entorno virtual del proyecto activo, ejecutar desde esta carpeta:

```powershell
python main.py
```

Tambien se puede abrir `ejecutar.bat` sin argumentos. Este arranque verifica configuracion,
Node.js/Chrome y la integridad de SQLite; ejecuta todos los tests del proyecto en un
proceso aislado e inicializa/verifica la base operativa. Por defecto continua con
el modo web: se autentica y llama a `/siguiente` con la VM registrada. Un 200 ya toma
una ejecucion EN_COLA; un 204 termina sin trabajo. Valida el proceso recibido,
descarga el input si corresponde o utiliza los productos de parametros, realiza
el scraping y entrega el ZIP. El correo final se prepara despues de la confirmacion web.
Si una comprobacion falla, sale con codigo 1 y no
inicia el scraping. `MODULO_SCRAPING=false` mantiene el proceso desactivado.

`python main.py web-procesar` ejecuta directamente el modo web configurado, sin ID manual.
`python main.py api-verificar` comprueba solo el login. El modo anterior por ID se conserva
unicamente mediante `BACKEND_CONTRATO=por_id`; no existe fallback automatico por errores HTTP.

Para el modo web sin argumentos, completar el .env privado con
`ARRANQUE_MODO=web`, `BACKEND_AUTH_MODE=weyland`, `BACKEND_CONTRATO=cola_actual`,
`BACKEND_MAQUINA_ID` y `BACKEND_PROCESO_CODIGO` con sus registros reales, mas
la configuracion de conexion. La API key habilita las rutas de bot; login/perfil
por si solos no prueban permisos de toma o entrega.

Las pruebas usan bases y carpetas temporales, omiten el .env privado y no envian
correos reales. La revision de la base operativa no borra tareas ni resultados;
las migraciones existentes conservan sus respaldos. Los logs detallados de los
tests quedan en `logs/pruebas_DDMMYYYY-HHMMSS_identificador.log`, junto a su JSON resumen.

`PRUEBAS_TIMEOUT_SEGUNDOS` es opcional (600 por defecto, maximo 3600). Si faltan
tests, dependencias o permisos, o falla algun test, corregir el problema indicado
en el log antes de volver a iniciar. Si el correo esta habilitado, su configuracion
debe estar completa; no se prueba el login SMTP enviando mensajes.

Los comandos explicitos `procesar`, `cargar`, `estado`, `reporte`, `api-verificar`
y `web-procesar` conservan su uso operativo y no vuelven
a ejecutar la suite completa. Esto evita anidar tests dentro de la ejecucion web.
Si se desea el arranque local anterior, configurar `ARRANQUE_MODO=local`:
validara los Excel de input y la cola antes de procesarlos. El modo web utiliza
solo el input o parametros de su solicitud, sin cargar otros Excel locales.

### Probar el contrato actual sin backend ni correos reales

```powershell
python -B -m unittest discover -s tests -p test_cola_actual.py -v
python -B -m unittest discover -s tests -p test_supermercados_cola_actual.py -v
```

Estas pruebas usan el contrato vigente de cola, input opcional, reporte y recuperacion.
La segunda prueba usa SQLite y genera el Excel real, con extraccion y SMTP simulados.

### Simulador del contrato anterior por ID

Ejecutar `probar_flujo_web.bat`, o `python probar_flujo_web.py` con el entorno
virtual activo. Lee obligatoriamente `ID_PRUEBA` y `CODIGO_PRUEBA` del `.env`
(por ejemplo, 900001 y SUPERMERCADOS_PRUEBA) y usa un servidor HTTP local aislado,
ejecuta los tests y simula scraping y SMTP. Verifica login, consulta, inicio,
descarga, reporte, estado COMPLETADA y que el Excel del correo sea identico al
incluido en el ZIP web. No modifica .env ni usa solicitudes del backend real.
La evidencia queda en `temporales/prueba_web_<identificador>/resultado_prueba.json`.
Detalles en [prueba del flujo web](docs/PRUEBA_FLUJO_WEB.md).

Para el backend Django real, consultar primero la [guia de conexion local](docs/conexion_weyland_local.md): modo `weyland`, sesion/CSRF y API key. La documentacion de JWT corresponde al cliente legacy.

## Conexion web comun

Los cuatro bots comparten los modulos base de `integraciones/weyland` v2.0.0.
Supermercados agrega cuatro modulos portables para el contrato `cola_actual`,
tambien incluidos en la entrega del SDK y su plantilla. Usar la
[guia del backend actual](docs/GUIA_BACKEND_ACTUAL.md) para migrar otro bot.
La [conexion por ID anterior](docs/INTEGRACION_WEYLAND_COMUN.md) se conserva
como referencia legacy; los otros bots requieren adaptar su arranque y callbacks.


# ¿Qué hace este proceso?

El backend actual usa sesion Django/CSRF y API key, documentados en la
[guia vigente](docs/GUIA_BACKEND_ACTUAL.md). La guia
[Autenticacion de API](docs/api_autenticacion.md) corresponde al cliente JWT legacy.

**El robot de precios de supermercados** busca productos en Líder, Tottus y
Unimarc y reúne sus precios en un Excel. Sirve para comparar lo encontrado sin
hacer todas las búsquedas manualmente.

## Resumen sencillo

| Qué recibe | Qué hace | Qué entrega |
|---|---|---|
| Uno o varios Excel con productos y filtros opcionales | Busca en los supermercados elegidos, lee precios y guarda las coincidencias | Excel con producto buscado, nombre encontrado, precio, enlace, fecha y supermercado |

## Cómo funciona, paso a paso

1. **Lee los archivos de entrada.** Revisa los Excel de `input/` y valida sus columnas.
2. **Prepara la lista de trabajo.** Crea una tarea por producto y supermercado.
3. **Abre o reutiliza Chrome.** Usa una ventana reservada para el robot.
4. **Busca cada producto.** Puede agregar una marca y filtrar por precio mínimo o máximo.
5. **Lee los resultados.** Guarda hasta cinco coincidencias válidas por producto y supermercado.
6. **Entrega el reporte.** Genera el Excel y, si el correo está configurado, envía el resumen. El aviso de inicio se envía antes de cargar las tareas.

La **cola** es esa lista de tareas pendientes. SQLite es el archivo de base de
datos donde se conservan las tareas y los resultados para poder consultarlos después.
El controlador de Chrome permite que Python lea e interactúe con las páginas.

## Ejemplo de uso

Si el Excel contiene esta fila:

| supermercado | producto | marca | precio_minimo | precio_maximo |
|---|---|---|---|---|
| Unimarc | leche | Colun | 1000 | 2000 |

El robot busca leche de esa marca en Unimarc y conserva los resultados que
cumplen el rango de precios. Si `supermercado` está vacío, busca en todos los
supermercados habilitados. Marca y límites de precio son opcionales.

Los resultados corresponden a las tarjetas que el robot logra leer durante la
búsqueda. No garantiza recorrer todo el catálogo ni confirmar stock o precio
final de una compra. El proceso consulta información; no realiza compras.

## Qué debe preparar la persona

Colocar los Excel en la carpeta de entrada, indicar los productos y completar
los filtros que necesite. El equipo debe tener Chrome, Node.js y las
dependencias de Python instaladas, además de la configuración del proyecto.
No es necesario manejar el navegador durante una ejecución normal.

## Dónde quedan los resultados

- `reportes/`: Excel de resultados, o la ubicación definida en `EXCEL_SALIDA`.
- `logs/`: detalle de las búsquedas y de los problemas encontrados.
- `procesados/capturas/`: capturas de errores, salvo otra ruta configurada.
- SQLite: tareas, filtros y resultados guardados.

El Excel reúne el historial guardado. No representa exclusivamente la última
corrida. La ventana del robot permanece abierta al terminar para poder reutilizarla.

## Qué ocurre si algo falla

Un archivo inválido queda registrado y no impide intentar los otros archivos.
Una búsqueda que falla se reintenta de forma limitada; si no se resuelve, queda
con error. Si falla una etapa posterior, como generar el Excel, el coordinador
no repite por eso todas las búsquedas. El resumen indica si quedaron errores.

## Instalación y uso técnico

Las secciones siguientes contienen los comandos y los detalles necesarios
para instalar, configurar y ejecutar el proceso.

## Ejecución

Desde la carpeta del proyecto, con las dependencias de `requirements.txt`,
Node.js 22 o posterior disponible en `PATH` y la configuración `.env` existente:

```powershell
.\.venv\Scripts\python.exe main.py
```

También puedes usar `ejecutar.bat`, que utiliza el entorno virtual del proyecto
y se puede lanzar desde otra carpeta:

```powershell
.\ejecutar.bat            # Flujo completo
.\ejecutar.bat procesar   # Flujo completo
.\ejecutar.bat cargar     # Cargar los Excel
.\ejecutar.bat estado     # Consultar la cola
.\ejecutar.bat reporte    # Generar el reporte
.\ejecutar.bat --help     # Mostrar la ayuda
```

El archivo también puede abrirse con doble clic. La ventana se cierra al terminar;
puedes revisar el resultado en los logs y reportes. No incluye una pausa para
permitir su uso con el Programador de tareas de Windows. Devuelve el mismo código
de salida que Python y muestra un error si falta `.venv`.

Los comandos disponibles son:

```powershell
.\.venv\Scripts\python.exe main.py procesar  # Flujo completo; tambien es el predeterminado
.\.venv\Scripts\python.exe main.py cargar    # Cargar los Excel en SQLite
.\.venv\Scripts\python.exe main.py estado    # Mostrar el estado de la cola
.\.venv\Scripts\python.exe main.py reporte   # Generar el Excel del historial
```

La salida es `0` si el comando completa y `1` si fallan tareas, archivos de
entrada o una etapa. Un comando desconocido devuelve `2` y muestra la ayuda.
`MODULO_SCRAPING=false` impide cargar y procesar tareas; permite consultar el
estado y generar reportes del historial existente.

El flujo completo tiene un solo coordinador en `integraciones/robot.py`.
`main.py` interpreta argumentos y convierte el resultado del coordinador en un
codigo de salida. Los reintentos de productos pertenecen al scraper; un fallo
posterior al scraping, como no poder escribir Excel, no repite toda la busqueda.
Se envia un aviso de inicio y una sola notificacion final con el resumen de tareas
y los archivos de entrada rechazados. Una falla critica genera un aviso de error.
Un fallo de correo se registra sin repetir el scraping.

Importar `config.configuracion` lee valores, pero no crea ni migra SQLite.
La inicializacion ocurre al llamar explicitamente a `configuracion_inicial()`.
Las llamadas Python directas a funciones de persistencia deben inicializarla
primero, o configurar una base con `configurar_base_datos(ruta)`.
`config.excel` conserva alias compatibles del lector y la plantilla de
`integraciones.productos`, que es la unica implementacion de ese formato.

### Correos de inicio, resultados y fallas

| Aviso | LISTA_DISTRIBUCION_ERROR | LISTA_DISTRIBUCION_CORREO |
|---|---|---|
| Inicio del flujo | Sí | Sí |
| Resultados, incluidos resultados con errores por producto | Sí | Sí |
| Falla que interrumpe el flujo | Sí | No |

Las listas se configuran en `.env`. Aceptan comas, punto y coma o espacios.
Una dirección presente en ambas recibe una sola copia de cada aviso de inicio
o resultados. Las fallas se envían exclusivamente a `LISTA_DISTRIBUCION_ERROR`;
si esa lista está vacía, no se sustituyen sus destinatarios por la otra lista.

`MODULO_CORREO=true` habilita los avisos. `MODULO_CORREO_INICIAL=true` habilita
el de inicio; en `false` se omite solamente ese aviso. Los dos valores son
`true` por defecto para mantener el envío del flujo configurado. Si
`MODULO_SCRAPING=false`, no se inicia el flujo ni se envían sus avisos.
Los comandos auxiliares `cargar`, `estado` y `reporte` no envían estos correos.

Se conserva el remitente y las listas privadas existentes. No se envían avisos
intermedios de avance. Los errores de SMTP se registran y no cambian el resultado
del procesamiento; las pruebas usan correo simulado.

### Ventana persistente del robot

Al iniciar, el robot comprueba la sesión de Chrome en `RPA_CHROME_PUERTO`
(predeterminado: `9222`, reservado para el robot). Si está disponible, reutiliza
su pestaña guardada, maximiza su ventana y la trae al frente. Si esa pestaña se
cerró, utiliza otra de la misma sesión; si no queda ninguna, crea una pestaña.
Si Chrome está cerrado, abre una ventana con un perfil propio en
`temporales/chrome_robot`. `CHROME_PATH` permite indicar el ejecutable.

Al terminar, se desconecta del navegador y deja la ventana abierta para la
siguiente ejecución. La pestaña se recuerda en `temporales/sesion_chrome.json`
(ambas rutas siguen `RUTA_BASE`). También se puede reutilizar la ventana del
robot anterior de TagUI si sigue disponible en el puerto configurado.
Una ventana normal de Chrome sin depuración remota no es una sesión del robot.
Con `RPA_HEADLESS=true` se omite la maximización. La navegación, escritura y
lectura del DOM se dirigen a la pestaña conectada mediante CDP; ya no se
reinicia TagUI ni se ejecuta su limpieza de procesos al iniciar el navegador.

Al ejecutar `main.py` se leen todos los archivos `.xlsx` y `.xlsm` directamente
en `input/` (dentro de `RUTA_BASE`), ordenados por nombre. No se recorren
subcarpetas y se ignoran los temporales de Excel cuyo nombre comienza con `~$`.
El único encabezado obligatorio es `producto`; el formato admite:

| supermercado | producto | marca | precio_minimo | precio_maximo |
|---|---|---|---|---|
| | leche | Colun | 1000 | 2000 |
| Unimarc | arroz | | | 2500 |
| Tottus | café | Nescafé | 3000 | |
| Líder | pan | | | |

`marca`, `precio_minimo` y `precio_maximo` son opcionales e independientes.
Un campo vacío no restringe la búsqueda. Los precios son pesos chilenos y los
límites se incluyen; se recomiendan celdas numéricas (por ejemplo, `1000`),
aunque también se admite texto como `$1.000`. Los valores negativos, no numéricos
o un mínimo mayor al máximo rechazan la carga de ese archivo e informan la fila.

El archivo `input/productos_filtros_ejemplo.xlsx` incluye ejemplos con filtros.
Todos los ejemplos que permanezcan en `input/` también se cargarán; deja allí
los archivos que quieras procesar.

Cada archivo se valida completo antes de insertar tareas. Si faltan encabezados,
hay encabezados reconocidos duplicados, datos inválidos o el archivo no puede
abrirse, queda registrado en `details` con estado `error` y su motivo en el log.
La carga continúa con los otros archivos. Cada Excel usa su hoja activa.
Un archivo con encabezados válidos y sin productos se registra como leído,
sin generar tareas. Una carpeta vacía o inexistente se informa en el log.

`EXCEL_ENTRADA` se conserva para crear plantillas y para el lector individual;
la ejecución habitual carga la carpeta completa. Para cargar explícitamente
un solo archivo desde Python se puede llamar a
`cargar_excel_a_tabla_cola('ruta/al/archivo.xlsx')`.

La marca se agrega al término de búsqueda y se comprueba en el campo de marca
de la tarjeta o, si falta, en su nombre, sin distinguir mayúsculas ni tildes y
respetando palabras completas. Si el sitio no expone la marca en esos campos,
el producto se omite. El selector `marca` de cada extractor es configurable.
Los filtros se aplican a las tarjetas cargadas antes de alcanzar los cinco
resultados por supermercado, usando el precio público extraído. El robot
continúa desplazándose y esperando hasta el límite `RPA_ESPERA_MAX`; no garantiza
recorrer todo el catálogo. Sin coincidencias, la tarea queda con error.

Un supermercado vacío (también si contiene solo espacios) busca en todos los
habilitados. Si se indica uno, busca únicamente en ese supermercado. Se aceptan
mayúsculas, espacios exteriores y `Lider` o `Líder`. Un nombre desconocido o
deshabilitado rechaza ese archivo e informa la fila antes de insertar tareas.
Las filas sin producto se ignoran. Las columnas se reconocen por su encabezado,
por lo que pueden cambiar de orden. Los archivos antiguos con solo `producto`
siguen funcionando y buscan en todos los habilitados; las columnas adicionales
se ignoran.

### Probar la carpeta de entrada

`input/productos_prueba.xlsx` conserva cuatro ejemplos sin filtros. Con los tres
supermercados habilitados ese archivo aporta seis tareas en una base vacía:
tres para leche y una para cada producto restante. Los demás archivos válidos
de la carpeta aportan sus propias tareas.

Desde la carpeta del proyecto, en una nueva ventana de PowerShell, estos comandos
usan la carpeta `input/` del proyecto, una base de prueba y un reporte separado:

```powershell
$env:RUTA_BASE = "$PWD"
$env:DB_PATH = "$PWD\temporales\prueba_supermercados.db"
$env:EXCEL_SALIDA = "$PWD\reportes\resultados_prueba.xlsx"
.\.venv\Scripts\python.exe main.py
```

Estas variables se aplican a esa sesión de PowerShell; cierra esa ventana para
volver a los valores del `.env`. La base de prueba se conserva entre ejecuciones:
las tareas exitosas no se repiten. Para una prueba nueva, cambia `DB_PATH` por
otro nombre de archivo.

El flujo completo también ejecuta las notificaciones por correo configuradas.
Para revisar solamente un extractor, sin cargar la cola ni enviar correos:

```powershell
.\.venv\Scripts\python.exe -c "from integraciones.scraping import diagnosticar; diagnosticar('unimarc', 'leche')"
.\.venv\Scripts\python.exe -c "from integraciones.scraping import diagnosticar; diagnosticar('tottus', 'arroz')"
```

Para comprobar la marca sin cargar tareas ni enviar correos:

```powershell
$env:DB_PATH = "$PWD\temporales\diagnostico_marcas.db"
.\.venv\Scripts\python.exe -c "from integraciones.scraping import diagnosticar; diagnosticar('unimarc', 'leche', marca='Colun')"
```

El diagnóstico acepta también `precio_minimo` y `precio_maximo`, imprime los
productos aceptados, URL final, cantidad de tarjetas y cuántas tienen marca,
junto a una muestra de sus campos. Puedes cambiar `unimarc` por `lider` o `tottus`.
Los registros distinguen precios fuera de rango, marca diferente, marca ausente
en el nombre, tarjetas ausentes y errores de extracción. La base separada evita
inicializar o migrar la base habitual durante el diagnóstico.

## Configuración y resultados

`config/supermercados.json` define cada supermercado: `nombre`, `habilitado`,
`extractor` y `espera` inicial en segundos. Cada `extractor` es un objeto con
`tipo` (`lider`, `tottus` o `unimarc`) y sus selectores CSS: `tarjeta`, `nombre`
o `boton_nombre`, `enlace` y los campos de precio propios de cada sitio.
Estos selectores se leen del JSON; no es necesario editar Python para cambiarlos.
Líder conserva sus selectores y usa `url_busqueda` con `{termino}`.
Unimarc usa `url_inicio` (`https://www.unimarc.cl`) y el XPath
`//input[@id='search-header__input']` para escribir el producto y enviar Enter.
Tottus también usa `url_inicio` y `selector_busqueda`: abre
`https://www.tottus.cl/tottus-cl`, espera el campo
`//input[@id='testId-SearchBar-Input']`, limpia el texto anterior, escribe el
producto, hace una pausa de medio segundo, enfoca el campo y envía Enter como
eventos de teclado `keyDown` y `keyUp` de Chrome. Esto evita depender del evento
de carácter de otras automatizaciones. Después espera `espera_resultados` (3 segundos) e
inicia la lectura de las tarjetas `a[id^="testId-pod-"]`. Cada producto se asocia
a su nombre `b[id^="testId-pod-displaySubTitle-"]`, su bloque
`testId-pod-prices-…` y sus precios `span[class*="line-height-22"]`,
conservando el nombre y enlace de esa misma tarjeta. Lee hasta cinco productos
con precios públicos válidos; si aún están cargando, continúa esperando hasta
`RPA_ESPERA_MAX`. No construye una URL de búsqueda para Tottus.
Este comportamiento se aplica al procesamiento de la cola y al diagnóstico.
Los números de los IDs se obtienen de cada tarjeta, sin fijar un producto concreto.
No se usan rutas absolutas ni clases `jsx-…` que dependan de una versión del sitio.
Las esperas máximas y los reintentos siguen usando `RPA_ESPERA_MAX` y
`REINTENTOS_MAX` de la configuración existente.

El robot usa el catálogo público o la ubicación que el sitio tenga disponible;
no selecciona una comuna ni configura despacho. Tottus y Unimarc buscan el precio
público vigente por unidad: toman ofertas generales, pero excluyen descuentos
que exijan tarjeta, Club, membresía o compra de varias unidades. Cuando no pueden
identificar un precio público, omiten ese resultado. El extractor de Líder
conserva su lectura del precio principal.

El Excel de salida (`EXCEL_SALIDA`, por defecto `reportes/resultados.xlsx`)
contiene `producto`, `nombre`, `precio`, `url`, `fecha` y, al final,
`supermercado`. El precio se guarda como número en pesos chilenos.

La cola evita repetir la combinación archivo/producto/supermercado/marca/rango de precios. Al cargar de
nuevo el mismo Excel se agregan las tareas faltantes; las tareas exitosas de ese
archivo no vuelven a buscarse. Otro archivo tiene sus propias tareas aunque
contenga el mismo producto y supermercado. Las tareas con error se reencolan al
iniciar el procesamiento, siguiendo el comportamiento previo.
Cambiar las filas del Excel no elimina tareas pendientes de cargas anteriores;
el robot procesa todos los pendientes de la base seleccionada.
Cambiar los filtros crea una tarea distinta y conserva los resultados anteriores.

## Base de datos

| Tabla | Contenido | Estados |
|---|---|---|
| `details` | Un registro por archivo, con nombre, ruta, fechas y error de lectura | `pendiente`, `leido`, `error` |
| `cola` | Las búsquedas de productos por supermercado, relacionadas con `details` mediante `detail_id` | `pendiente`, `leido`, `error` |
| `resultados` | Todos los productos encontrados, relacionados con su tarea mediante `cola_id` | No aplica |

En `details`, `leido` significa que el Excel se leyó y validó correctamente y sus
tareas se cargaron. Si falta el archivo, es inválido o tiene un supermercado
incorrecto, queda en `error` sin agregar una carga parcial. El estado de lectura
del archivo es independiente de sus búsquedas: un archivo leído puede tener
tareas pendientes o con error en `cola`.

En `cola`, `leido` significa que la búsqueda guardó sus resultados correctamente.
El lote de resultados y ese cambio de estado se confirman en una sola transacción.
Los resultados anteriores se conservan; un reintento no los elimina.

Se recorren automáticamente los Excel de `input/`, registrando cada archivo en
`details`. Volver a leer una ruta actualiza su registro y agrega únicamente
las tareas faltantes; los archivos inválidos no generan cargas parciales.

Las bases antiguas se migran automáticamente al inicializar la configuración.
La actualización de filtros conserva tareas y resultados existentes, dejando
los filtros anteriores vacíos. Las bases de versión 2 se respaldan junto al
archivo original con el sufijo `.pre_v3_<fecha>_<id>.bak` antes de migrarse.

Para bases anteriores a la versión 2,
los archivos de la antigua `cola` pasan a `details`, las tareas de la antigua
`details` pasan a `cola` y sus estados `ok` se convierten a `leido`. Se conservan
los IDs, errores, fechas y resultados. Antes se crea una copia SQLite junto a la
base con nombre `scraper.db.pre_v2_<fecha>_<id>.bak`. Si la migración falla,
se revierte toda la operación. Las bases ya migradas no vuelven a migrarse.

Los errores de acceso o ubicación obligatoria se registran explícitamente. Cada
tarea agotará los reintentos configurados y guardará una captura si es posible,
sin impedir que se procesen los otros supermercados. Una búsqueda sin productos
válidos también termina con error; no genera precios ficticios.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Las pruebas usan SQLite y Excel temporales, no envían correos y no modifican la
cola real. Los casos DOM requieren Node.js 22 o posterior y Chrome instalado en
`C:\Program Files\Google\Chrome\Application\chrome.exe`; se puede indicar otra
ruta con `CHROME_PATH`. Chrome se ejecuta sin interfaz con un perfil temporal.
Las tarjetas de prueba no requieren conexión a los supermercados.
Si ya tienes Chrome con depuración remota, puedes ejecutar las pruebas usando
`$env:CDP_ENDPOINT = 'http://127.0.0.1:9222'`. Se crea una pestaña de prueba
separada y se cierra al terminar; las otras pestañas se conservan. Sin esa
variable se inicia Chrome con un perfil temporal como antes.

El 29 de octubre de 2026 se verificaron los extractores en Chrome contra
búsquedas reales de `leche` y `leche colun` en los tres supermercados. Se
comprobó que el filtro acepta Colun y descarta otras marcas, incluso cuando
el nombre del producto no incluye la marca. Los selectores corregidos leen
`Shelf_brandText` en Unimarc, `.pod-title` en Tottus y el bloque de marca con
clases de estilo utilitarias en Líder. La evidencia está en
`temporales/validacion_marcas_reales.json`; los conteos corresponden a tarjetas
extraídas antes del límite de cinco resultados. La comprobación usó los
extractores y filtros reales con navegación CDP; no ejecutó la cola habitual
ni las notificaciones de TagUI.

Los selectores web pueden cambiar. En ejecuciones anteriores Tottus y Líder
mostraron verificaciones de seguridad; el robot las informa cuando las detecta
y no intenta eludirlas.


## Backend Weyland local

Flujo por la solicitud seleccionada por el operador: [solicitudes web](docs/flujo_solicitudes_supermercados.md). Ejecutar `ejecutar.bat web-procesar --id-solicitud 123`, usando el ID real. `solicitud_id` y `ejecucion_id` coinciden; `BACKEND_REUTILIZAR_ULTIMA=false`. Conserva input opcional y resultados asociados al mismo ID.

Login, sesion, ejecuciones web y recuperacion: [guia de conexion](docs/conexion_weyland_local.md). Usar `ejecutar.bat api-verificar` y `ejecutar.bat web-procesar --id-solicitud ID_REAL`.

Resumen completo de módulos, autenticación, endpoints y recuperación: [conexión al backend](docs/RESUMEN_CONEXION_BACKEND.md).


Cliente portable para otras maquinas y proyectos: [modulos y ejemplos de reutilizacion](docs/cliente_backend_reutilizable.md). El registro de maquina/proceso se configura posteriormente; no es necesario para construir el cliente ni verificar login.
