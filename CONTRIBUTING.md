# Contributing to Edecan

Thank you for helping build a user-controlled AI operator. This guide covers
the public Apache-2.0 repository; the binding package and API contracts live in
[`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Before you start

- Search existing issues and pull requests.
- Use an issue for a substantial feature, new dependency, contract change, or
  security-sensitive design so the direction can be agreed before coding.
- Report vulnerabilities privately as described in [`SECURITY.md`](./SECURITY.md).
- Keep a pull request focused. Unrelated cleanup belongs in a separate PR.

Good first contributions include documentation fixes, deterministic test
coverage, accessibility improvements, provider fakes, and isolated bug fixes.

## Development setup

Required for the Python core: Python 3.12 and
[`uv`](https://docs.astral.sh/uv/). The web app additionally needs Node.js 22;
the full local stack needs Docker Compose v2.

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/edecan-oss.git
cd edecan-oss
git remote add upstream https://github.com/bizagencysas/edecan-oss.git
uv sync --all-packages --frozen
make check
```

Never run bare `uv sync` or `uv run` at the workspace root. Because the root
project is only a workspace container, uv can prune editable member packages.
Use the Make targets or pass `--all-packages`.

For the web and desktop checks:

```bash
make web-check
make desktop-test
```

Changes to Dockerfiles, Compose, migrations, runtime settings, or lockfiles
must also run `make selfhost-smoke`. It creates and removes an isolated Docker
project and never reuses a developer's existing stack.

For the runtime environment, copy `.env.example` to `.env`, generate your own
local secrets, then follow the developer stack in [`README.md`](./README.md).
Never commit `.env`.

## Repository structure

- `apps/api`, `apps/worker`, `apps/local`, `apps/companion`: thin Python apps.
- `packages/*`: installable `edecan_*` domain and integration packages.
- `apps/web`: Next.js + TypeScript + Tailwind.
- `apps/desktop`: Tauri shell and packaging.
- `apps/mobile`: native iOS and Android companion clients.
- `infra/docker`: public self-host container definitions.
- `docs`: operator, feature, security, and runbook documentation.

Everything versioned in this repository is Apache-2.0. Optional extensions
distributed elsewhere are not part of this contribution workflow.

## Engineering rules

### Python

- Python 3.12, full type hints, Ruff, maximum line length 100.
- Tests use pytest/pytest-asyncio and must be offline and deterministic.
- Use fakes or `respx` for HTTP; never call a paid service or real account.
- Package tests should test the package contract without depending on sibling
  implementations unless the test explicitly verifies an integration contract.

### Web and native clients

- Keep the in-product language Spanish-first.
- Preserve API behavior across web, iOS, and Android when a shared workflow
  changes.
- Run lint, typecheck, and production build for web changes.
- Keep lockfiles updated and do not suppress dependency advisories without a
  documented risk decision.

### Security and product boundaries

- Never commit secrets, tokens, personal data, private endpoints, or local
  filesystem paths. Examples use obvious placeholders.
- Integrations use official APIs and credentials supplied by the user/tenant.
- Real-money execution is not supported. Do not add a live commerce path.
- Dangerous tools must retain explicit approval and server-side authorization;
  hiding a UI control is not a security boundary.
- Remote input stays opt-in and local sandbox escapes are treated as security
  defects.

## Contracts and documentation

A change to a route, table, job type, tool name, feature flag, environment
variable, or package interface must update the relevant contract and docs in
the same PR. Prefer an ADR in `docs/adr/` for decisions that affect several
packages or long-term compatibility.

Do not leave references to private plans, local paths, agent work-package IDs,
or absent files in public documentation.

## Pull request workflow

1. Fork the repository and create a descriptive branch.
2. Add or update tests before changing behavior when practical.
3. Implement the smallest coherent solution and preserve compatibility.
4. Run `make check`; run the path-specific checks the PR template selects.
5. Update docs, configuration examples, and lockfiles together with code.
6. Open a PR explaining the user impact, design, validation, and security risk.
7. Resolve review comments and keep CI green. Maintainers merge; contributors
   never need write access to the upstream repository.

## Licensing of contributions

Under section 5 of Apache License 2.0, contributions intentionally submitted to
this project are provided under the same Apache-2.0 terms unless explicitly
stated otherwise. Opening a PR does not require a separate contributor license
agreement.

Community behavior is governed by [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md),
and maintainer responsibilities are described in [`GOVERNANCE.md`](./GOVERNANCE.md).
