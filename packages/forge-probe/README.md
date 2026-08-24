# edecan-forge-probe — fase 0 de Forge

Forge es el runtime de ingeniería de Edecán. **La fase 0 no escribe Forge: mide de qué es
capaz de verdad el modelo que lo va a mover, antes de escribirlo.**

Entre el 40 % y el 70 % del diseño de la fase 1 descansa sobre números que nadie ha medido:
cuánto contexto recuerda de verdad, si sabe meter un parche de 40 líneas dentro de un campo
JSON sin romperlo, cuántas herramientas aguanta antes de elegir mal, cuánto cuesta un turno.
Este paquete produce dos cosas y sólo dos:

1. una **`ModelCard` medida**, con intervalo de confianza y evidencia auditable;
2. un **banco público de 15 tareas reales** sobre `edecan`, con criterio
   ejecutable, verificado de que hoy FALLA sobre el repo intacto.

Regla dura del paquete: **lo que no se mide vale `None`.** No hay valores por defecto
«razonables», ni números copiados del catálogo del proveedor. Un `None` arrastra
`Veredicto.SIN_DATO`, y `SIN_DATO` bloquea igual que `FALLA`.

## El modelo objetivo

`@cf/moonshotai/kimi-k2.7-code` en Cloudflare Workers AI. Verificado contra la API real el
27-07-2026:

| dato | valor |
| --- | --- |
| ventana anunciada | 262 144 tokens |
| precio entrada | 0,95 USD / MTok |
| precio entrada **cacheada** | 0,19 USD / MTok (5x más barata) |
| precio salida | 4,00 USD / MTok |
| `function_calling` / `reasoning` / `vision` | sí / sí / sí |

Kimi K3 está documentado pero **esta cuenta no tiene acceso** (403, `code: 5018`). El día que
se habilite, se cambia con `FORGE_PROBE_MODEL`; no hay que tocar código.

Tres señales del proveedor que el contrato común de `edecan_llm` no modela y que aquí sí se
registran, porque son las que gobiernan la factura:

- `usage.prompt_tokens_details.cached_tokens` — el acierto de caché de prefijo;
- `usage.neurons` — la unidad de facturación real de Cloudflare;
- `message.reasoning_content` — **el razonamiento está SIEMPRE activo**, viaja en un campo
  separado de `message.content` y se factura a 4,00 USD/MTok. Medido: una respuesta de dos
  palabras gastó 65 tokens de salida, ~57 de ellos de razonamiento. Con `max_tokens: 32` la
  respuesta llegó con `content` VACÍO y se cobró igual. Es un modo de fallo de primera clase:
  toda sonda reserva presupuesto para el razonamiento y mide su sobrecarga como métrica propia.

## Cómo se ejecuta

Desde la raíz del monorepo. El paquete ya es miembro del workspace de uv, así que
`uv sync --all-packages` lo instala y el módulo se invoca directo.

```bash
# 0. ¿puedo medir hoy?  UNA llamada barata.
uv run python -m edecan_forge_probe humo

# 1. el banco mide algo?  Comprueba que cada criterio FALLA sobre el repo limpio.
uv run python -m edecan_forge_probe banco --listar
uv run python -m edecan_forge_probe banco --verificar

# 2. barrido. Empieza barato y con tope de gasto.
uv run python -m edecan_forge_probe sondear \
    --solo perf,tools --n 35 \
    --precio-entrada 0.95 --precio-salida 4.00 --precio-cacheada 0.19 \
    --presupuesto-usd 1.00 \
    --evidencia .forge-probe

# 3. informe sin gastar un token: recompone desde la evidencia en disco.
uv run python -m edecan_forge_probe informe --evidencia .forge-probe
```

`humo` es lo primero que se ejecuta y distingue los cuatro fallos que cuestan una tarde si se
confunden: falta el token, falta la cuenta, la credencial no vale, y el modelo existe pero la
cuenta no tiene acceso. El token nunca se imprime, ni truncado, ni en un mensaje de error.

Códigos de salida, pensados para encadenar en un script: `0` todo bien (y veredicto GO), `1`
error de entorno, `2` uso incorrecto, `3` **NO-GO**, `4` el banco tiene criterios que ya pasan
hoy (no mide nada), `5` falta credencial, `6` el modelo no existe o no hay acceso.

### Banderas que hay que entender antes de gastar

| bandera | por qué existe |
| --- | --- |
| `--presupuesto-usd` | Tope **global** de gasto. Exige `--precio-entrada` y `--precio-salida`: sin precios el coste no se puede calcular y el tope no se puede hacer cumplir, así que el runner se niega en vez de fingir. |
| `--precio-cacheada` | Sin ella la entrada cacheada se factura a precio frío y el coste sale como **cota superior** (hasta 5x de más). Se anota en el informe. |
| `--n` | Recorta las muestras para una pasada barata. Las series de tool-calling se **saltan** por debajo de 35 intentos en vez de publicar un número que no decide nada (ver abajo). |
| `--solo` | Ejecución parcial por nombre, grupo (`perf`, `tools`, `context`) o prefijo. |
| `--rehacer` | Ignora la evidencia en disco y vuelve a medir **y a pagar** todo. |
| `--deadline-s` | Reloj de pared total. Un corte deja en disco lo ya medido. |

La ejecución es **secuencial a propósito**. Dos sondas en paralelo comparten cola del
proveedor y se contaminan la latencia: aquí la concurrencia no es una optimización, es un
error de medición.

Cada `ProbeResult` se persiste sellado con modelo + revisión de sonda, y sólo se reutiliza si
ambos coinciden y `ok=True`. Un fallo no es una medición; una revisión distinta no es
comparable. Por eso un corte por Ctrl-C, deadline o presupuesto no tira lo ya pagado.

### Correr sin gastar: proveedor Ollama

```bash
uv run python -m edecan_forge_probe sondear --proveedor ollama --modelo <modelo-local> \
    --solo perf --n 3 --evidencia /tmp/forge-humo
```

Es el arnés de referencia: modelo local, sin caché de prefijo reportada y sin campo de
razonamiento separado, así que `cached_tokens`, `reasoning_tokens` y `neurons` salen siempre
en `None` — no medido, **no cero**. Sirve para demostrar que el andamiaje funciona de punta a
punta sin tocar la tarjeta de crédito. Una sonda que dé el mismo veredicto contra Ollama y
contra Workers AI está midiendo su propio código.

## Qué significa cada umbral

`UMBRALES_FASE_0` vive en `edecan_forge_probe/modelcard.py` y es el contrato: seis criterios de
sí/no. Todos se contrastan contra el **límite inferior del intervalo de Wilson al 95 %**, jamás
contra la media: el diseño tiene que sostenerse en el mal caso, no en el esperado.

| clave | criterio | qué se cae si falla |
| --- | --- | --- |
| `usable_context_tokens` | ≥ 48 000 | Contexto **útil**: la profundidad a la que todavía recupera y razona sobre lo que se le metió. No es la ventana anunciada. Si falla, el Context Engine (bloque 3) no puede confiar en meter el repo entero: hay que ir a recuperación por trozos con relevo. |
| `native_tools.code_blob.lower_95` | ≥ 0,90 | Fiabilidad llamando a una herramienta cuyo argumento es un bloque de código. Es exactamente lo que hace `apply_patch` en cada edición. Si falla, el Tool ABI (bloque 4) necesita transporte alternativo (XML, o parche fuera de banda) antes de escribirse. |
| `throughput_tps` | ≥ 25 tok/s | Tokens de salida por segundo en régimen sostenido. Si falla, el Agent Runtime (bloque 6) no aguanta turnos largos: hay que trocear. |
| `ttft_p95_s` | ≤ 2,5 s | Tiempo hasta el primer token, p95, en el escenario **corto e interactivo** (el umbral describe respuesta interactiva, no una carga de 52k). Si falla, no hay interacción síncrona: la UI tiene que ser asíncrona. |
| `bench_success_rate` | ≥ 0,55 | Tareas del banco real resueltas con criterio ejecutable, sin ayuda humana. Es el único número que mide el sistema completo. |
| `max_tools_effective` | ≥ 12 (**no bloqueante**) | Cuántas herramientas se pueden ofrecer antes de que la selección se derrumbe. Fija el techo de la superficie del ABI. |

Cuatro veredictos: `PASA`, `JUSTO` (dentro del umbral pero a menos del 10 % de margen: trátalo
como riesgo, no como aprobado), `FALLA` y `SIN_DATO`. **`SIN_DATO` se pinta tan rojo como
`FALLA` a propósito.** «No lo medimos» nunca se interpreta como «pasa».

### Un aviso aritmético sobre `code_blob`

Con N = 20 intentos, el límite inferior de Wilson **máximo alcanzable** es 0,839. Es decir: el
umbral de 0,90 sería inalcanzable con 20 muestras incluso con un modelo perfecto, y el `FALLA`
resultante sería aritmética, no modelo. Hacen falta **35 intentos como mínimo**; el defecto de
la sonda es 40. Por eso `--n` por debajo de 35 salta las series de tool-calling en vez de
publicarlas, y ese mínimo se **calcula** desde el umbral (`n_minimo_para`), no se escribe a
mano: si el umbral cambia, el mínimo lo sigue solo.

## Qué pasa si el veredicto es NO-GO

**NO-GO no significa que el modelo sea malo.** Significa que hay partes del diseño de la fase 1
que estaban apoyadas en una suposición que la medición no sostiene, y que hay que **rediseñarlas
antes de escribirlas** — que es exactamente para lo que existe esta fase. Descubrirlo aquí
cuesta unos dólares de API; descubrirlo en la fase 3 cuesta reescribir tres bloques.

`ModelCard.go()` devuelve `False` en cuanto un umbral **bloqueante** sale `FALLA` o `SIN_DATO`.
El informe pone el veredicto en la primera pantalla y justo debajo **qué rediseñar**, con una
entrada por umbral anclada a los bloques 3 (Context Engine), 4 (Tool ABI) y 6 (Agent Runtime)
de `docs/arquitectura-forge.md`.

El orden de reacción, en la práctica:

1. **¿Es `SIN_DATO`?** Entonces no hay veredicto todavía: hay una sonda que no corrió. Mira
   `ProbeResult.error` y `notas` de la tarjeta (corte por presupuesto, deadline, cancelación) y
   vuelve a lanzar sólo esa sonda con `--solo`. Lo ya medido no se vuelve a pagar.
2. **¿Es `FALLA` en un umbral bloqueante?** Rediseña el bloque que nombra el informe. No se
   sube el umbral: los umbrales se fijaron antes de medir, y bajarlos después de ver el
   resultado es convertir la fase 0 en una ceremonia.
3. **¿Es `JUSTO`?** Pasa, pero el margen es menor del 10 %. Escríbelo como riesgo conocido y
   pon el plan de contingencia por delante, no detrás.
4. **¿`max_tools_effective` falla?** No bloquea, pero fija el techo del ABI: recorta el
   catálogo de herramientas al número medido y monta un router de herramientas.

## Mapa del paquete

```
edecan_forge_probe/
  modelcard.py   El CONTRATO. ModelCard, ProbeResult, Reliability, Umbral, BenchTask.
                 Único sitio donde se define qué significa medir. No se toca sin decirlo.
  providers.py   WorkersAIProvider (REST + SSE + traducción de herramientas, errores
                 tipados con backoff y deadline) y OllamaProbeAdapter.
  runner.py      Orquestación secuencial, contabilidad, presupuesto, deadline, reanudación,
                 prueba de humo clasificada y verificación del banco.
  report.py      modelcard.json + informe.md, con el GO/NO-GO en la primera pantalla.
  __main__.py    CLI: humo · sondear · informe · banco.
  probes/
    registro.py  SONDAS: el adaptador entre cada sonda y el runner, y la contabilidad única.
    context.py   Contexto útil: aguja, multi-salto y recuerdo de restricción, 7 profundidades.
    tools.py     Tool-calling por ArgProfile, max_tools, max_schema, modos de fallo.
    perf.py      TTFT/TTFC, throughput, caché de prefijo, salida estructurada, razonamiento, visión.
bench/
  edecan.py      15 tareas reales sobre este repo.
  checks/        Criterios ejecutables propios, sin red.
  oraculos/      Oráculos fuera del repo bajo prueba: el agente no puede editar su examen.
```

## Las pruebas nunca salen a la red

Cada llamada a Workers AI cuesta dinero real. Una suite que lo gaste sin querer es un defecto
grave, así que:

- toda la suite corre dentro de un router de `respx`: una petición que nadie declaró
  explícitamente no sale a internet, revienta el test;
- el `.env` real se aísla con un fixture: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` y
  `FORGE_PROBE_MODEL` se borran del entorno de la suite;
- un test que necesite red se marca `@pytest.mark.integration` y **se salta salvo que esté
  puesta `FORGE_PROBE_INTEGRACION=1`**. Tener el token en el entorno NO basta como condición:
  eso convertiría cualquier `pytest` distraído en una factura.

```bash
uv run pytest packages/forge-probe -q
uv run ruff check packages/forge-probe && uv run ruff format --check packages/forge-probe
```

## Coste real de una campaña

Estimaciones aritméticas sobre las tarifas verificadas, no mediciones:

| sonda | llamadas por defecto | coste aproximado |
| --- | --- | --- |
| `perf.*` | ~110 | ~2 USD (dominado por los prompts de 52k del TTFT largo) |
| `native_tools.*` | ~560 | 3–5 USD |
| `context` | 504 (7 profundidades × 9 configs × 8 intentos) | **~50 USD**, dominado por 224k y 256k |

Decide el presupuesto **antes** de la primera ejecución en vivo. El barrido de contexto va de
menos a más profundidad a propósito: un tope bajo devuelve una **cota inferior honesta** en vez
de arruinar el crédito, y queda marcado como tal en `detalle["es_cota_inferior"]`.

## Estado (27-07-2026)

Lo que está **comprobado ejecutándolo**, no supuesto:

- La suite del paquete está verde y no toca la red.
- `banco --listar` carga las 30 tareas de los dos repos sin colisión de `id`.
- `banco --verificar` sobre `edecan`: **18 criterios, los 18 fallan hoy** sobre el repo
  intacto, ninguno inejecutable. El banco de `edecan` mide algo.
- `sondear` recorre el camino completo (CLI → runner → registro → proveedor → sondas →
  contabilidad → `modelcard.json` + `informe.md`) sobre HTTP real, con reanudación y
  presupuesto.

Lo que **NO** se ha hecho:

- **Ni una llamada a Workers AI.** No hay ningún número medido del modelo objetivo: la
  `ModelCard` de `@cf/moonshotai/kimi-k2.7-code` está por producir, y ése es el entregable de
  la fase 0. Todo lo verde de aquí describe el arnés.
- **Ollama no tiene ningún modelo descargado** en esta máquina (`ollama list` sale vacío, el
  servidor arranca bien). El proveedor `ollama` está cableado y probado contra un servidor
  local que habla `/api/chat`, pero no contra pesos reales.
