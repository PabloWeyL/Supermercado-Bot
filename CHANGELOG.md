# Changelog

## Sin publicar

- Permite guardar una máquina y una IPv4 pública confirmadas para usar el lanzador con doble clic, sin cambiar el .env.

- Permite indicar el ID de máquina para una sola corrida web cuando Weyland usa el contrato de sesión con ID explícito.

- Resuelve la máquina por IP pública al abrir sesión y conserva los checkpoints por ID de máquina.

- Abre la sesión de Weyland Automate con API key y registra el ID de la corrida al iniciar, conservando el checkpoint.

- Los mensajes operativos usan el logger Weyland y los fallos incluyen la traza y el contexto del log sanitizados. La plantilla deja ID_EJECUCION vacío para el worker web.

- Se incorporó el módulo de Key Vault a la estructura del proyecto. El arranque de desarrollo conserva la configuración privada y SQLite.

- Se sustituyeron las clases técnicas por funciones y errores estándar con categoría, manteniendo el contrato de Queue y checkpoints.

- Se retiraron el generador de una plantilla de ejemplo y el diagnóstico de navegador sin punto de entrada. Los fallos nuevos usan el nombre de Weyland v1.2.1.

- La validación de arranque usa la misma versión del esquema SQLite que el inicializador de la base.

- La reanudación conserva la Queue, omite el aviso inicial ya asociado a la ejecución y reutiliza el Excel y la confirmación de correo del cierre persistido. La migración conserva una fila por ID y ruta aunque un Excel se use en otra ejecución.

- Un ID cerrado detiene el arranque antes de correo, módulos o toma web. El diagnóstico añade contexto estructurado permitido y una guía de reproducción controlada.

- Los IDs de ejecuciones cerradas no reabren el negocio. Se simplificaron los registros de rutas, diagnóstico y resultados, y se retiraron las anotaciones de producción.

- Adopción de Weyland v1.2.0 para el diagnóstico local, la privacidad del correo de error y las etiquetas de log.

- Favicon de Weyland incorporado como logo del cliente y de Weyland en los correos.

### Cambiado

- La sesión JWT, el cliente HTTP y los checkpoints de Weyland Automate viven en \`integraciones/weyland.py\`.
- El bot usa el formato mensual de logging, el registro de fallos y la plantilla HTML Weyland.
- Se genera un ZIP histórico por ejecución y se documenta el perfil de desarrollo con SQLite.

- Migración transaccional de los nombres de tablas SQLite a QUEUE, DETAILS y AUX para Supermercado, con lectura de checkpoints anteriores.

- Migra estados de archivos y búsquedas. Persiste el cierre SQLite tras la confirmación de entrega y añade las hojas Queue, Details y Aux.
