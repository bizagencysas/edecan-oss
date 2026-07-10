# Contributing to Edecán

Thanks for your interest in contributing. This document summarizes the monorepo's conventions. The full, binding technical contract lives in [`ARCHITECTURE.md`](./ARCHITECTURE.md) §10 — any change that touches it must update that document in the same PR.

## Monorepo layout

- `apps/*` — thin applications: `api` (FastAPI), `worker` (job consumer), `web` (Next.js), `companion` (opt-in local desktop agent), `desktop` (Tauri shell, since v3), `local` (the desktop app's packaged backend, since v3), and `mobile` (native iOS/Android projects).
- `packages/*` — installable, reusable Python packages, prefixed `edecan_` (`edecan_schemas`, `edecan_db`, `edecan_llm`, `edecan_core`, `edecan_toolkit`, `edecan_connectors`, `edecan_voice`, `edecan_evals`, and more — 28 members today in the uv workspace, see `[tool.uv.workspace].members` in `pyproject.toml`).
- `premium/` — the commercial layer (`edecan_premium`), under a separate license — see [`NOTICE`](./NOTICE). Not part of this public core export.
- `infra/` — infrastructure as code (Terraform, Dockerfiles); written and reviewed, **never applied automatically**. Not part of this public core export either.
- `docs/` — extended documentation (self-hosting, connectors, compliance, runbooks). Mostly in Spanish, since the product itself targets Spanish-speaking users first.

## Code conventions

- Python **3.12**, managed with **uv** (workspace declared in the root `pyproject.toml`). Each package lives at `packages/<dir>/edecan_<name>/` with its own `pyproject.toml` and `tests/`.
  - **Never run a bare `uv sync`/`uv run <command>` (without `--all-packages`) at the root**: the root `pyproject.toml` has no `dependencies` of its own, so that silently prunes the workspace's editable packages (you'll see `ModuleNotFoundError` in pytest afterward). Use `make test`/`make lint`/`make fmt` (already guarded) or `uv sync --all-packages` / `uv run --all-packages <command>` if you're calling `uv` directly.
- Formatting and linting with **ruff**, max line length **100**. Type hints are required.
- Tests with **pytest** + **pytest-asyncio**; must be **offline and deterministic** — use `respx`/fakes for HTTP, never real network calls or calls to paid services.
- **A package's tests never import sibling packages**: they use the fakes/stubs that implement the contracts defined in `ARCHITECTURE.md` §10. Importing sibling packages in production code (not tests) is fine, by module name.
- Frontend in `apps/web`: **Next.js 14 (App Router) + TypeScript + Tailwind**.
- UI and docs default to **Spanish**.

## Hard rules (non-negotiable)

1. **Zero real secrets.** Only `YOUR_X_HERE`-style placeholders in `.env.example`/docs. Never real API keys, tokens, or anyone's real personal data.
2. **LinkedIn is banned** in any form: code, scopes, URLs, UI copy, or documentation. The `test_no_linkedin` test in `packages/connectors/` must always keep passing.
3. **Official APIs only.** Each tenant connects their own credentials via OAuth. Never scraping, never shared or hardcoded credentials.
4. **Never run**, from this repo's dev flow, CI, or automated agents: `terraform apply`, `aws` commands with real effects, `docker push`, or tests that hit real network calls to paid services. `infra/terraform` is written and reviewed like any other code; applying it is always a manual step outside this repository.
5. Changes to the contracts in `ARCHITECTURE.md` §10 (table names, signatures, routes, job types, tool names) require explicit coordination, since other packages are developed in parallel against those same contracts.

## Contributor License Agreement (CLA)

By opening a PR against this repository's core (any path outside `premium/`), you agree that your contribution is licensed under **Apache License 2.0** (the same terms covering the rest of the project — see [`LICENSE`](./LICENSE) §5, "Submission of Contributions"), with no additional conditions. There's no separate CLA document to sign for core contributions: submitting the PR is itself that agreement ("inbound = outbound").

If your contribution touches `premium/` (commercially licensed software, see [`NOTICE`](./NOTICE) and `premium/LICENSE-COMMERCIAL.md`), a separately signed contributor license agreement with the Edecán Project is required before the PR can be reviewed; reach out to the maintainers through the channel described in [`SECURITY.md`](./SECURITY.md) to arrange it.

## Workflow

1. Open an issue or discuss the proposed change before investing a lot of time in a large PR.
2. Create a descriptive branch and keep the PR small, focused on a single goal.
3. Make sure `make lint` and `make test` pass locally before opening the PR.
4. Describe what changes and why in the PR; if the change touches an `ARCHITECTURE.md` §10 contract, update that document in the same PR.
5. If your change adds a new agent tool, a new job type, a new HTTP route, or a new environment variable, reflect that in `ARCHITECTURE.md` and `.env.example` as appropriate.

## How to fork and open a pull request

1. Click "Fork" on the repo page to get your own copy under your GitHub account.
2. Clone your fork, create a branch, and make your change there.
3. Push the branch to your fork and open a pull request back against `isaccmanuel/edecan`.
4. A maintainer reviews it and merges when it's ready. Forking and opening a PR never grants write access to the original repo — only a maintainer can merge.

## Running the local environment

See the "Developer mode (self-host from source)" section in [`README.md`](./README.md).

## Reporting security issues

Don't use public issues for vulnerabilities — follow the process described in [`SECURITY.md`](./SECURITY.md).
