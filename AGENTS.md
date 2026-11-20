# AGENTS.md

## Propósito y estado

Supermercado consulta precios de los productos en los supermercados configurados. Estado **DESARROLLO**, con `ENTORNO=DESARROLLO` y `MOTOR_BD=sqlite`.

## Entrada y módulos

- `main.py` consulta una solicitud de Weyland Automate. Supermercado puede desactivar la conexión con `BACKEND_ACTIVO`.
- `python main.py procesar` trabaja con Excel con productos y filtros opcionales en `input/`.
- `python main.py api-verificar` abre la sesión con `BACKEND_API_KEY` y comprueba el acceso JWT sin tomar trabajo.
- El worker registra `id_ejecucion` en formato `DDMMYYYY_HHMMSS` antes de publicar el estado de inicio. El checkpoint conserva el valor y su confirmación.
- `integraciones/supermercados.py` contiene la automatización. `integraciones/reportes.py` genera los Excel desde SQLite.
- `integraciones/weyland.py` separa autenticación, cliente HTTP y procesamiento de solicitudes con checkpoints.
- `config/monitor.py` obtiene los parámetros funcionales de la solicitud de Weyland Automate.

## Configuración, archivos y recuperación

- Nombre de proceso en `NOMBRE_PROCESO` y `NOMBRE_PROCESO_TEXTO`. Los flags `MODULO_*`, los reintentos y las rutas se documentan en `.env.example`.
- Las credenciales temporales viven en el entorno privado. Mantener fuera de Git secretos, SQLite y archivos operativos.
- `RUTA_HISTORICO` guarda ZIP de ejecución con 18 meses de retención por defecto. Los logs mensuales y los diagnósticos locales viven en `logs/` y `fallos/`, con nombre `FALLO_<ID>_<HHMMSS_mmm>.md`.
- El estado exitoso del detalle es `LEIDO`. Los checkpoints web conservan el estado de un POST incierto. Al recuperar una solicitud, revisar los checkpoints antes de repetir un envío.
- Key Vault y Azure SQL quedan fuera del perfil actual. Los parámetros usan Weyland Automate durante esta etapa.

## Validación y control de cambios

- Ejecutar `python -m unittest discover -s pruebas -v`, `python -m compileall -q .`, `python main.py --help` y `python -m pip check` desde el entorno virtual.
- Usar simulaciones para browser, SMTP y Weyland en pruebas automatizadas. Validar manualmente los selectores en la VM de prueba si cambian.
- Registrar cambios de comportamiento en `CHANGELOG.md` bajo `## Sin publicar`. Al publicar, mover las entradas a la versión y conservar el tag anotado.

SQLite usa QUEUE_SUPERMERCADOS para archivos, DETAILS_SUPERMERCADOS para búsquedas y AUX_SUPERMERCADOS_RESULTADOS para precios. La migración de estados conserva las filas existentes. El cierre pendiente mantiene la entrada fuera del reproceso hasta confirmar la entrega.

## Estándar Weyland v1.2.0

El perfil validado localmente usa SQLite. Los parámetros de trabajo vienen de Weyland Automate durante esta etapa. La integración con Weyland Monitor mediante sus procedimientos en Azure SQL y la adopción de Key Vault quedan fuera del perfil acordado. La prueba visual con cuenta de prueba y la verificación del despliegue siguen pendientes.

Los archivos de fallo guardan un diagnóstico local con ubicaciones de código y metadatos del log sanitizados. El correo de error identifica la ejecución sin adjuntar el log mensual.

Los puntos de entrada, el cliente Weyland, el navegador y el correo usan funciones y datos explícitos. En producción solo se admite FiltroContexto como clase de logging. La validación local comprueba la regla de Weyland v1.2.1.

Los puntos de entrada comprueban ID_EJECUCION en modo de solo lectura antes de ejecutar módulos o tomar solicitudes. Un ID cerrado termina con código 0. El diagnóstico local admite únicamente campos estructurados de una lista permitida.

Weyland detecta la máquina por la IP pública de salida registrada y devuelve su ID al abrir la sesión. Los checkpoints conservan sus carpetas por ID numérico. Si la IP es desconocida o está registrada en varias máquinas, el bot se detiene antes de consultar la cola.

El arranque web y la verificación aceptan `--maquina-id` como opción explícita por corrida. La sesión debe confirmar esa misma máquina antes de acceder a la cola.

El arranque sin argumentos usa la máquina local previamente confirmada con `api-verificar --maquina-id NUMERO --ipv4-publica IPV4 --guardar-maquina` cuando existe. El vínculo se guarda en `.runtime/maquina_weyland.json`, queda fuera de Git y es específico del backend.

Comprueba la IPv4 pública antes del POST siguiente y valida maquina.direccion_ip antes de correr el negocio.
