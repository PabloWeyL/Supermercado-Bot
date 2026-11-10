# Correos pendientes, trazabilidad y controles de Excel

Estas funciones pertenecen al bot de supermercados. No modifican el backend ni las reglas de los otros bots.

## Cola de correo

`MAX_ADJUNTOS_BYTES=20971520` en `.env` establece el limite total de adjuntos por correo (20 MiB iniciales). Es obligatorio y debe ser un entero positivo en bytes. Reiniciar el bot despues de modificarlo; el limite independiente del mensaje MIME persistido sigue siendo 30 MiB.

Cada aviso se guarda antes del envio en una base SQLite independiente, correos.db. Incluye una copia inmutable del mensaje MIME con sus adjuntos, un hash de integridad, Message-ID estable y estado por destinatario. No guarda la contrasena SMTP ni el token de la API. Los mensajes contienen informacion del proceso y deben tratarse como datos privados.

La ubicacion se configura con CORREO_PENDIENTES_DIR. Si falta, se utiliza temporales/correos dentro de la base operativa configurada. En ejecuciones web se transmite la ubicacion del padre al proceso de negocio, para compartir la misma cola entre trabajos. La cola no se incluye en los exports web ni en Git. En una VM, restringir su acceso a la cuenta del bot y ubicarla fuera del repositorio cuando corresponda.

Se revisan pendientes al finalizar las comprobaciones de arranque y al iniciar el negocio; tambien existe un comando independiente que nunca repite el scraping. No hay un servicio de correo en segundo plano: si el bot no vuelve a ejecutarse, los pendientes esperan a la siguiente revision o al comando manual.

| Estado por destinatario | Significado |
|---|---|
| PENDIENTE | Fallo definitivo temporal o conexion no establecida; puede reintentarse cuando venza la espera. |
| ENVIANDO | Se registro el intento antes de contactar a SMTP. |
| ENVIADO | SMTP confirmo aceptacion para ese destinatario. No implica entrega final en la bandeja del usuario. |
| INCIERTO | Se perdio la confirmacion durante el envio, o hubo una caida con un intento activo. No se reenvia automaticamente. |
| AGOTADO | Respuesta permanente, datos alterados o limite de intentos alcanzado; requiere revisar el motivo. |

Los intentos temporales usan espera creciente; por defecto empiezan en 60 segundos y se permiten hasta cinco intentos. Se puede configurar CORREO_REINTENTO_SEGUNDOS y CORREO_MAX_INTENTOS (maximo 20). La espera calculada se limita a 24 horas. No se hacen varios envios seguidos para agotar el contador de una sola vez.

Si SMTP acepta unos destinatarios y rechaza otros, solo se reintentan los rechazados temporales. Los ya aceptados no se vuelven a incluir en el sobre SMTP. Si falla quit despues de una aceptacion confirmada, el correo sigue aceptado. Los adjuntos se reenvian desde la copia guardada, aunque se haya modificado el archivo original.

La falta de respuesta al final del envio no permite garantizar si el proveedor acepto el mensaje. Por eso INCIERTO requiere comprobar el Message-ID con el proveedor antes de resolverlo. No se promete entrega exactamente una vez mediante SMTP.

## Operacion sin repetir el proceso

Con el entorno virtual activo:

```powershell
python main.py correos-estado
python main.py correos-reintentar
```

El estado muestra ID del correo, destinatario, intentos y codigo. El segundo comando procesa solo pendientes cuyo plazo ya vencio; no ejecuta los tests ni el scraping.

Despues de corregir un fallo permanente o credenciales, reactivar un mensaje agotado:

```powershell
python main.py correos-reintentar --id-correo ID_DEL_CORREO
```

Para resolver un envio incierto, confirmar primero la entrega con el proveedor:

```powershell
python main.py correos-resolver --id-correo ID_DEL_CORREO --entregado
python main.py correos-resolver --id-correo ID_DEL_CORREO --no-entregado
```

Son alternativas, no se ejecutan ambas. --entregado marca como aceptados los destinatarios inciertos; --no-entregado los devuelve a pendientes. La resolucion no envia nada por si sola. Despues puede ejecutarse correos-reintentar.

Si la confirmacion es distinta para cada destinatario, agregar `--destinatario persona@example.test`. Sin ese filtro, la resolucion se aplica a todos los destinatarios inciertos del mensaje: verificar a todos antes de utilizarla. Los reintentos estan deshabilitados si MODULO_CORREO=false.

Se conservan las listas acordadas: inicio y resultados van a ambas listas; fallas a LISTA_DISTRIBUCION_ERROR. Los limites son 20 MiB de adjuntos en total y 30 MiB del mensaje MIME; se rechazan adjuntos inexistentes o mensajes incompletos, dejando el error en el log. Fallos de disco o de registro deben corregirse; no se afirma que un mensaje quede encolado si no pudo persistirse.

## Trazabilidad

Los logs distinguen ID_LOCAL (cuando corresponde al proceso SQLite), SOLICITUD, EJECUCION_WEB y ACTIVACION. Solicitud y ejecucion web conservan el mismo ID del backend. La activacion es un UUID local de correlacion, no otro ID de solicitud. En modo local se muestra SIN_SOLICITUD en vez de inventar un ID web.

El contexto se propaga al subproceso web y se conserva en los errores de la CLI. En los reintentos SMTP se restaura el contexto del mensaje original, aunque el bot este atendiendo otra solicitud.

Los correos incluyen el ID web en asunto, cuerpo y encabezado X-Weyland-Solicitud, y la activacion en X-Weyland-Activacion. El Excel agrega la hoja ejecucion con IDs, activacion, hora UTC y cantidad de resultados. Tambien se crea trazabilidad_<activacion>.json; los archivos web contienen esta evidencia y el manifiesto del framework.

## Entradas Excel y JSON

Los limites se configuran en `.env` con enteros positivos: `MAX_ARCHIVO=20971520` y `MAX_EXPANDIDO=104857600` en bytes, `MAX_FILAS=50000` (sin encabezado) y `MAX_COLUMNAS=100`. Son obligatorios; si faltan o son invalidos, se informa un error. Reiniciar el bot despues de cambiarlos. Los valores siguientes describen esa configuracion inicial.

Se admiten XLSX/XLSM locales dentro de los formatos del proceso. El lector limita el archivo comprimido a 20 MiB, el contenido expandido a 100 MiB y los archivos internos a 2000. Limita la hoja activa a 50.000 filas de datos y 100 columnas. Estos controles reducen lecturas y asignaciones de memoria desproporcionadas; no equivalen a un analisis antivirus.

Las formulas en el input se rechazan, incluso si existe un valor calculado en cache. Guardar el archivo como valores antes de ejecutarlo. Se mantienen las validaciones de columnas duplicadas, precios finitos/no negativos y rangos coherentes. Se rechazan productos vacios, caracteres de control y textos excesivos: producto 500 caracteres, supermercado 100 y marca 200. Las filas completamente vacias se omiten; un archivo sin productos se rechaza.

Los productos JSON tienen los mismos limites de texto y un maximo de 50.000 registros y 20 MiB por archivo. El negocio sigue validando los supermercados habilitados.

## Validacion de bytes y estructura

Antes de abrir un input se verifica su firma ZIP OOXML, componentes obligatorios y CRC leyendo todas sus entradas en bloques con limite expandido. Se rechazan rutas ambiguas, duplicados, enlaces simbolicos internos, compresiones distintas de STORE/DEFLATE, macros, objetos OLE/ActiveX, conexiones y relaciones externas (incluidos hipervinculos). Los XML se analizan sin permitir DTD ni entidades; las formulas se rechazan en todas las hojas. Se procesa una copia en memoria de los mismos bytes validados, limitada por MAX_ARCHIVO. Los XLS antiguos y archivos cifrados no se admiten; guardar el input como XLSX solo con valores. Estos controles no sustituyen un antivirus ni garantizan detectar toda amenaza desconocida.

La politica de rechazo de DTD y entidades responde a los riesgos XML descritos en la [documentacion de Python](https://docs.python.org/3/library/xml.html#xml-security).

## Salidas Excel

Los textos exportados se escriben como celdas literales XLSX, incluyendo valores que comiencen con =, +, - o @. Se conserva su texto y no se crean elementos de formula. Los precios siguen siendo numeros, no se convierten a texto. La proteccion aplica tambien a la hoja de trazabilidad. No hay un exportador CSV en este flujo; si se agrega uno, necesitara su propia proteccion.

## Verificacion

Las pruebas cubren persistencia tras fallos, deduplicacion, rechazos parciales, confirmaciones perdidas, recuperacion de intentos interrumpidos, limites de reintentos, adjuntos inmutables y corrupcion del mensaje guardado. Tambien verifican contexto de logs/errores y workbook XML sin formulas, conservando IDs y precios numericos.

`probar_flujo_web.bat` utiliza el ID/codigo del simulador configurados en .env, SMTP en memoria, un backend HTTP local y scraping controlado. Verifica que el Excel subido a la web sea el mismo del correo, que sus IDs/activacion coincidan y que los destinatarios queden aceptados en la cola. No demuestra entrega SMTP real ni disponibilidad del backend real.

## Pruebas de recuperacion ante cortes

Ejecutar desde la carpeta del bot:

```bat
python -B -m unittest discover -s tests -p test_recuperacion.py -v
```

Estas pruebas utilizan carpetas temporales, SQLite real y subprocesos que terminan con `os._exit(77)`, sin ejecutar bloques finally. No apagan la VM, no prueban un fallo fisico del disco y no envian solicitudes al backend real ni correos. El archivo `tests/soporte_cortes.py` es exclusivamente un soporte descartable de pruebas, no un comando operativo.

| Corte simulado | Comportamiento comprobado |
|---|---|
| Durante la descarga | El parcial no se procesa. Se conserva DESCARGANDO; al reiniciar se descarga completo y se ejecuta el negocio una sola vez. |
| Descarga completa, antes del checkpoint | Se reutiliza el input solo si coinciden su tamano y SHA-256; no se repite la descarga. |
| Conexion interrumpida leyendo bytes | El cliente limpia su parcial y conserva la posibilidad de retomar; no informa un error definitivo a la web. |
| HTTP 408, 429 o 5xx al descargar | Se conserva DESCARGANDO, sin bucle de reintentos. Se vuelve a intentar en la siguiente invocacion. Un 404 sigue siendo definitivo. |
| Durante una transaccion SQLite | Los resultados y el estado de la tarea se revierten juntos. La base conserva integrity_check=ok. |
| Despues del commit de una tarea | La tarea leida conserva sus resultados. Al retomar la misma base solo se consulta la tarea pendiente, sin duplicar resultados. |
| Durante el negocio web | Se conservan input, base y archivos que ya se hayan persistido. El journal EJECUTANDO bloquea otra invocacion del negocio hasta revision del operador. |
| Reporte aceptado, confirmacion perdida | Se conserva REPORTE_PENDIENTE. Se reenvian exactamente el mismo ZIP y la misma clave de idempotencia; el negocio no se repite. |
| Solicitud COMPLETADA | Una nueva invocacion con el mismo journal no descarga, procesa ni entrega otra vez. |

Se corrigio el caso en que un corte de red de descarga se clasificaba como INPUT_INVALIDO definitivo. Los datos descargados se sincronizan a disco antes del reemplazo atomico. Los parciales que queden tras una terminacion abrupta no se consideran inputs validos ni se exportan.

Para retomar DESCARGANDO o REPORTE_PENDIENTE, volver a ejecutar con el mismo ID, codigo, cuenta, backend y directorio de trabajos. El checkpoint decide que paso falta. No borrar los journals ni los directorios operativos para forzar una nueva ejecucion. EJECUTANDO requiere revisar las tareas y sus efectos; el coordinador no promete reanudacion automatica de un negocio incierto. La prueba de continuidad por tarea SQLite es local y no desbloquea automaticamente el journal web.

La ausencia de reportes duplicados en la web depende de que el backend implemente idempotencia persistente: la misma Idempotency-Key y hash deben devolver la confirmacion original sin crear otro reporte; un contenido distinto con la misma clave debe rechazarse. La prueba utiliza un backend ficticio que cumple ese contrato; queda pendiente verificarlo contra el backend real cuando esten disponibles los endpoints. Las pruebas de correo cubren aparte que los envios SMTP inciertos no se reenvien automaticamente.

Las dos correcciones de transporte/coordinacion se mantienen iguales en los cuatro bots, el SDK reutilizable y su plantilla. Las pruebas de continuidad del negocio corresponden al bot de supermercados.
