# edecan_skills

Marketplace de "Agent Skills" — el estándar abierto que indexa skills.sh (`ARCHITECTURE.md`
§12, `DIRECCION_ACTUAL.md` "Confirmado: agregar Ollama + integrar el marketplace de
skills.sh"). Ver [`../../docs/skills.md`](../../docs/skills.md) para la documentación de
producto completa (qué es, cómo instalar, modelo de seguridad).

Un "Agent Skill" es un repo/carpeta con `SKILL.md` (frontmatter YAML `name`/`description`/
opcional `version`/`license`/metadata) cuyo cuerpo markdown son **instrucciones para el
agente** — no código que se ejecute. Este paquete replica el mecanismo de instalación de
`npx skills add <owner/repo>` (lectura directa de `raw.githubusercontent.com`) más una
búsqueda best-effort contra la API de skills.sh.

## Módulos

| Módulo | Qué hace |
|---|---|
| `client.py` | `SkillsIndexClient.search(q, k)` — búsqueda best-effort en el índice de skills.sh; `[]` ante cualquier fallo (el índice es solo descubrimiento, nunca bloquea instalar por `owner/repo` directo). |
| `installer.py` | `parse_source(source)` (valida y descompone `owner/repo[/subpath]`, anti path-traversal/SSRF), `fetch_skill(...)` (descarga el `SKILL.md` con 3 rutas candidatas + cap de tamaño), `parse_skill_md(texto)` (frontmatter YAML + cuerpo), `parse_capabilities(texto)` (campo `allowed-tools`, WP-V5-04), `install_from_source(...)` (pipeline completo, sin tocar la base de datos). |
| `security.py` | (WP-V5-04) Trust tiers (`TRUST_TIERS`, `clasificar_trust_tier`), capacidades (`CAPACIDADES_PELIGROSAS`, `capacidades_peligrosas`, `validar_capacidades`) y el escáner heurístico anti-inyección (`escanear_inyeccion`, `HallazgoInyeccion`) — ver `docs/skills.md` "Seguridad de skills de terceros". |
| `sources.py` | (WP-V5-04) `OpenClawSource`/`HermesSource` — búsqueda en esos dos índices vía tarball de `codeload.github.com` recorrido en memoria con `tarfile` (nunca `git`), ver `docs/skills.md` "Fuentes OpenClaw y Hermes". |
| `store.py` | CRUD de la tabla `skills` con `sqlalchemy.text` — nunca importa `edecan_db.models` (mismo criterio que `edecan_toolkit`/`edecan_premium`/`edecan_business`). `insert_skill` corre `security.escanear_inyeccion` y decide `enabled` (WP-V5-04). |
| `tools.py` | Las 5 herramientas del agente: `buscar_skills` (con `fuente`), `instalar_skill` (`dangerous=True`, con `fuente`/`trust_tier`/aviso de hallazgos), `listar_skills`, `usar_skill` (banner de capacidades peligrosas + recordatorio anti-inyección), `desinstalar_skill`. |

`get_all_tools()` (en `edecan_skills/__init__.py`) es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`, declarado en
`pyproject.toml` como `[project.entry-points."edecan.tools"]`.

## Decisiones de implementación

- **Anti path-traversal / anti-SSRF**: `parse_source()` nunca devuelve una URL lista para
  pedir, solo `(owner, repo, subpath)` ya validados contra una regex estricta (rechaza
  `..`, espacios, hosts que no sean github.com/skills.sh). `fetch_skill()` arma las URLs
  reales íntegramente a mano, siempre contra `raw.githubusercontent.com` — el host que el
  usuario haya pegado nunca se usa para la petición real.
- **`edecan_skills` NO ejecuta nada de una skill** — solo lee su `SKILL.md` como texto y lo
  entrega como instrucciones al modelo (`usar_skill`). Ningún script/binario que pueda venir
  en el repo se descarga ni se corre — límite deliberado v3, ver `docs/skills.md`.
- **`instalar_skill` es `dangerous=True`**: instala instrucciones de un tercero que el
  agente seguirá literalmente — exige el gate de confirmación humana existente
  (`ARCHITECTURE.md` §10.7). `usar_skill` envuelve el contenido con un encabezado que deja
  explícito que son instrucciones del usuario y NUNCA anulan las reglas de seguridad del
  sistema — mismo principio que `edecan_core.persona.build_system_prompt`.
- **`sqlalchemy` no es dependencia dura** (ver `pyproject.toml`): `store.py` hace el mismo
  import diferido/opcional que `edecan_core.memory._sql` — en el proceso real siempre está
  instalada (la declaran `apps/api`/`edecan_db`).
- **`user_id` en `store.py`**: `list_skills` filtra por `(tenant_id, user_id)` ("lo que
  instalé"); `get_by_slug`/`get_by_id`/`set_enabled`/`delete_skill` filtran solo por
  `tenant_id` (una vez instalada, cualquier miembro del tenant puede usarla/gestionarla) —
  coincide con el `UNIQUE(tenant_id, slug)` real de la tabla, que no incluye `user_id`. Ver
  el docstring de `store.py` para el razonamiento completo.

## Tests

`tests/` usa `respx` para toda llamada HTTP (índice de skills.sh, `raw.githubusercontent.com`,
tarballs de `codeload.github.com` para `sources.py`) y una `FakeSession` local (duck typing)
para `store.py` — offline y deterministas (`ARCHITECTURE.md` §10.15). Ningún test importa
`edecan_core`, `edecan_db` ni `sqlalchemy` para construir sus dobles. Los tarballs de
`test_sources.py` se generan en memoria con `tarfile`/`io.BytesIO`, nunca se descarga nada
real.
