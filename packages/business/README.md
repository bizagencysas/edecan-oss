# edecan_business

Negocios: facturación ligera (borrador → PDF real → S3) y KPIs mensuales del negocio
(`ROADMAP_V2.md` §5 P1 WP-V2-12, §7.4, §7.7).

- `invoices.py` — numeración (`next_numero`), totales con `Decimal` y redondeo bancario
  (`compute_totals`), PDF (`render_pdf`, fpdf2), y el orquestador `crear_factura` (usado
  tanto por la tool `crear_factura` como por `apps/api/edecan_api/routers/negocios.py`).
  También `listar_facturas`, `obtener_factura`, `cambiar_estado`.
- `kpis.py` — `kpis_mes`: ingresos/gastos/beneficio/nuevos clientes/facturado/cobrado/dona
  por canal/actividad reciente, reutilizando `transactions` y `contacts` de v1 (no duplica
  ningún cálculo financiero: ver el docstring del módulo para el criterio anti-doble-conteo).
- `tools.py` — `crear_factura`, `estado_negocio` (nombres exactos, `ROADMAP_V2.md` §7.7).
  Ninguna es `dangerous`: crear un borrador no mueve dinero.
- `_files.py` — sube el PDF a S3 + fila `files`. Copia adaptada (no import) de
  `edecan_creative._files.subir_archivo`: recibe `session`/`tenant_id`/`user_id`/`settings`
  explícitos en vez de un `ToolContext`, para que la use también el router (ver su docstring).

Todas las funciones de `invoices.py`/`kpis.py` reciben `session` explícito (no
`ToolContext`) — mismo criterio que `edecan_commerce.budgets`/`edecan_commerce.paper`: las
usa tanto `tools.py` (con `ctx.session`) como `apps/api/edecan_api/routers/negocios.py` (con
su propia sesión de tenant, `edecan_api.deps.get_tenant_session`) sin duplicar la lógica de
negocio ni depender de `edecan_api.repo.Repo` (paquete de trabajo prohibido de tocar).

Ver `docs/negocios.md` para el contrato completo: alcance/disclaimer, flujo de estados,
supuestos de los KPIs y roadmap P2.

## Tests

```
cd packages/business && PYTHONPATH=. pytest
```

Offline y determinista: `FakeSession` programable (mismo doble que
`packages/commerce/tests/conftest.py`) y `aioboto3.Session` sustituido con `monkeypatch` —
ninguna llamada de red ni de Postgres real.
