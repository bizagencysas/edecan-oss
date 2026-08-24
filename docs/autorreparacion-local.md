# Autorreparación local segura

Edecán puede aprender una capacidad o reparar su propio código local, pero no
recibe acceso irrestricto al equipo. La autorreparación es un ciclo explícito,
reversible y con aprobaciones humanas.

## Estrategia escalonada

Cuando una persona dice «te pedí esto y dijiste que no podías; haz que se
pueda», `diagnosticar_autorreparacion_local` elige la vía menos invasiva:

1. Reutilizar o configurar una capacidad que ya existe.
2. Crear o actualizar una skill local aislada con
   `reparar_con_skill_local`.
3. Editar el núcleo únicamente cuando hay evidencia de un defecto del código,
   la instalación conserva un clon Git y el dueño habilitó el modo de
   autorreparación.

Una skill local se lee de la base local cada vez que se usa. Por eso se puede
probar inmediatamente, sin recompilar ni reiniciar. Cada actualización guarda
la versión anterior y exige al menos un caso de aceptación. Una colisión con
una skill instalada desde un tercero se rechaza, en vez de sobrescribirla.

## Ciclo de reparación del núcleo

`gestionar_autorreparacion_local` es una herramienta peligrosa a efectos del
orquestador. Cada una de estas transiciones genera una confirmación separada:

1. `iniciar`: exige un repositorio limpio, fija `HEAD` como checkpoint y crea
   una rama `edecan/repair-<id>` dentro de un Git worktree aislado.
2. `aplicar_cambios`: recibe rutas relativas, contenido y el SHA-256 previo.
   Si un archivo cambió desde el diagnóstico, no escribe ningún archivo.
3. `instalar_dependencias`: opcional; solo ejecuta un `argv` que coincida
   exactamente con la allowlist de instalación del dueño.
4. `ejecutar_pruebas`: solo ejecuta un `argv` exacto de la allowlist de
   pruebas, sin shell. Un commit es imposible hasta tener una prueba verde.
5. `crear_commit`: exige `rutas_esperadas` y las compara con todos los cambios
   reales del worktree antes de preparar exactamente esas rutas. Si una prueba
   o instalación alteró algo inesperado, se detiene. No usa `git add --all` y
   nunca hace push.
6. `integrar`: vuelve a comprobar que el clon principal sigue limpio y en el
   checkpoint. Solo permite un fast-forward al commit probado.
7. La intención original se reintenta. `registrar_reintento` conserva el
   worktree para otro ciclo si vuelve a fallar, o lo elimina junto con la rama
   temporal cuando el resultado fue verificado.
8. `revertir`: antes de integrar, descarta solamente el worktree aislado;
   después de uno o más ciclos integrados, comprueba que la historia desde el
   checkpoint original contiene exactamente los commits de esa reparación y
   crea un único commit local que restaura el árbol completo del checkpoint.
   Si aparece un commit ajeno o `HEAD` cambió, no modifica nada.

El manifiesto vive en `DATA_DIR/self-repair/<repair_id>/manifest.json`, fuera
del repositorio. Guarda estados y evidencia resumida, no la salida completa de
los procesos ni secretos.

## Configuración

Todo está apagado por defecto:

```dotenv
EDECAN_LOCAL_MODE=true
EDECAN_LOCAL_REPO_PATH=/ruta/al/clon/edecan
EDECAN_SELF_REPAIR_ENABLED=true
EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON=[["uv","run","--frozen","pytest","packages/toolkit/tests"]]
EDECAN_SELF_REPAIR_INSTALL_COMMANDS_JSON=[]
EDECAN_SELF_REPAIR_COMMAND_TIMEOUT_SECONDS=300
```

Las allowlists contienen comandos completos como arrays JSON. No son prefijos:
si se autorizó `['uv', 'run', '--frozen', 'pytest',
'packages/toolkit/tests']`, agregar `-c`, cambiar la ruta o pedir otro comando
produce un rechazo. Para autorizar una instalación, el dueño debe añadir por
adelantado el `argv` exacto a `EDECAN_SELF_REPAIR_INSTALL_COMMANDS_JSON` y
después aprobar también la tarjeta de confirmación de esa ejecución.

## Límites deliberados

- Solo funciona en modo local de un único dueño. Nunca en hosted multi-tenant.
- La reparación de código requiere un clon Git conservado por el usuario; una
  aplicación empaquetada sin fuentes solo puede mejorar mediante configuración
  o skills locales.
- No concede permisos del sistema, no desactiva el sandbox, no interpreta
  strings de shell, no empuja remotos y no instala comandos no allowlisted.
- El reintento de la intención usa las herramientas normales de Edecán y sus
  confirmaciones. Marcar `reintento_exitoso=true` debe ocurrir solo después de
  observar el resultado real.
- El trust tier `local_aprobada` es texto de procedencia, no un bypass de
  seguridad. La columna no tiene un vocabulario cerrado y las skills locales
  siguen pasando por el escáner de inyección y por las confirmaciones de toda
  herramienta peligrosa que invoquen.

## Relación con las piezas existentes

No hay un segundo agente ni un ejecutor paralelo. Este flujo reutiliza:

- el gate `Tool.dangerous` y las confirmaciones de un solo uso del chat;
- el registro de herramientas y el orquestador de misiones;
- la tienda `skills` para capacidades locales recargables;
- Git como checkpoint, aislamiento, commit y reversión;
- el acceso local configurado por `EDECAN_LOCAL_MODE` y
  `EDECAN_LOCAL_REPO_PATH`.

La herramienta antigua `acceder_codigo_local.git_commit` también exige ahora
una lista explícita de `rutas` y se niega a mezclar cambios que el usuario ya
tenía en el índice de Git.
