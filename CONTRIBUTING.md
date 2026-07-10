# Contribuir a Edecán

Gracias por tu interés en contribuir. Este documento resume las convenciones del monorepo. El contrato técnico completo y vinculante está en [`ARCHITECTURE.md`](./ARCHITECTURE.md) §10 — cualquier cambio que lo toque debe actualizar ese documento en el mismo PR.

## Estructura del monorepo

- `apps/*` — aplicaciones delgadas: `api` (FastAPI), `worker` (consumidor de jobs), `web` (Next.js), `companion` (agente local de escritorio), `desktop` (shell Tauri, desde v3), `local` (backend empaquetado de la app de escritorio, desde v3) y `mobile` (proyectos nativos iOS/Android).
- `packages/*` — paquetes Python instalables y reutilizables, prefijo `edecan_` (`edecan_schemas`, `edecan_db`, `edecan_llm`, `edecan_core`, `edecan_toolkit`, `edecan_connectors`, `edecan_voice`, `edecan_evals`, entre otros — 28 miembros hoy en el workspace uv, ver `[tool.uv.workspace].members` en `pyproject.toml`).
- `premium/` — capa comercial (`edecan_premium`), licencia separada — ver [`NOTICE`](./NOTICE).
- `infra/` — infraestructura como código (Terraform, Dockerfiles); se escribe y se revisa, **nunca se aplica automáticamente**.
- `docs/` — documentación extendida (self-hosting, conectores, cumplimiento, runbooks).

## Convenciones de código

- Python **3.12**, gestionado con **uv** (workspace declarado en el `pyproject.toml` raíz). Cada paquete vive en `packages/<dir>/edecan_<nombre>/` con sus propios `pyproject.toml` y `tests/`.
  - **Nunca corras `uv sync`/`uv run <comando>` sueltos (sin `--all-packages`) en la raíz**: el `pyproject.toml` raíz no tiene `dependencies` propias, así que eso poda en silencio los paquetes editables del workspace (verás `ModuleNotFoundError` en pytest después). Usa `make test`/`make lint`/`make fmt` (ya protegidos) o `uv sync --all-packages` / `uv run --all-packages <comando>` si invocás `uv` directo.
- Formateo y lint con **ruff**, línea máxima de **100** caracteres. Type hints obligatorios.
- Tests con **pytest** + **pytest-asyncio**; deben ser **offline y deterministas** — usa `respx`/fakes para HTTP, nunca llamadas de red reales ni a servicios de pago.
- **Los tests de un paquete no importan paquetes hermanos**: usan los *fakes*/stubs que implementan los contratos definidos en `ARCHITECTURE.md` §10. Importar paquetes hermanos en código de producción (no de tests) sí está permitido, por nombre de módulo.
- Frontend en `apps/web`: **Next.js 14 (App Router) + TypeScript + Tailwind**.
- UI y documentación por defecto en **español**.

## Reglas duras (no negociables)

1. **Cero secretos reales.** Solo placeholders `TU_X_AQUI` en `.env.example`/docs. Nunca API keys, tokens ni datos personales reales de nadie.
2. **LinkedIn está prohibido** en cualquier forma: código, scopes, URLs, texto de UI o documentación. El test `test_no_linkedin` en `packages/connectors/` debe seguir pasando siempre.
3. **Solo APIs oficiales.** Cada tenant conecta sus propias credenciales vía OAuth. Nunca scraping ni credenciales compartidas o hardcodeadas.
4. **Nunca ejecutar** desde el flujo de desarrollo, CI o agentes automatizados de este repo: `terraform apply`, comandos `aws` con efectos reales, `docker push`, ni pruebas con red real hacia servicios de pago. `infra/terraform` se escribe y se revisa como código; su aplicación es un paso manual fuera de este repositorio.
5. Cambios a los contratos de `ARCHITECTURE.md` §10 (nombres de tablas, firmas, rutas, tipos de jobs, nombres de herramientas) requieren coordinación explícita, porque otros paquetes se desarrollan en paralelo contra esos mismos contratos.

## Acuerdo de licencia de contribuyente (CLA)

Al abrir un PR contra el núcleo de este repositorio (cualquier ruta fuera de `premium/`), aceptas que tu contribución se licencia bajo **Apache License 2.0** (los mismos términos que cubren el resto del proyecto — ver [`LICENSE`](./LICENSE) §5, "Submission of Contributions"), sin condiciones adicionales. No hace falta firmar un documento CLA aparte para contribuir al núcleo: el acto de enviar el PR ya constituye ese acuerdo ("inbound = outbound").

Si tu contribución toca `premium/` (software bajo licencia comercial, ver [`NOTICE`](./NOTICE) y `premium/LICENSE-COMMERCIAL.md`), se requiere un acuerdo de licencia de contribuyente firmado por separado con el Proyecto Edecán antes de que el PR pueda revisarse; contacta a los mantenedores por el canal indicado en [`SECURITY.md`](./SECURITY.md) para gestionarlo.

## Flujo de trabajo

1. Abre un issue o discute el cambio propuesto antes de invertir mucho tiempo en un PR grande.
2. Crea una rama descriptiva y mantén el PR pequeño y enfocado en un solo objetivo.
3. Asegúrate de que `make lint` y `make test` pasen localmente antes de abrir el PR.
4. Describe en el PR qué cambia y por qué; si el cambio toca un contrato de `ARCHITECTURE.md` §10, actualiza el documento en el mismo PR.
5. Si tu cambio agrega una nueva herramienta del agente, un nuevo tipo de job, una nueva ruta HTTP o una nueva variable de entorno, refléjalo también en `ARCHITECTURE.md` y en `.env.example` según corresponda.

## Cómo correr el entorno local

Ver la sección "Modo desarrollador (self-host desde el código fuente)" en [`README.md`](./README.md).

## Reportar problemas de seguridad

No uses issues públicos para vulnerabilidades — sigue el proceso descrito en [`SECURITY.md`](./SECURITY.md).
