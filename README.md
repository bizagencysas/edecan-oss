<p align="center">
  <img src="apps/desktop/src-tauri/icons/icon.png" width="120" alt="Edecan logo" />
</p>

<h1 align="center">Edecan</h1>

<p align="center">
  <strong>Ask once. Edecan understands the job and gets it done.</strong><br />
  One text or voice conversation · local-first · human approval when it matters
</p>

<p align="center">
  <a href="https://github.com/bizagencysas/edecan-oss/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/bizagencysas/edecan-oss/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="./LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" /></a>
  <a href="./pyproject.toml"><img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB.svg" /></a>
  <a href="./apps/web/package.json"><img alt="Next.js 15" src="https://img.shields.io/badge/Next.js-15-000000.svg" /></a>
  <a href="./CONTRIBUTING.md"><img alt="Contributions welcome" src="https://img.shields.io/badge/contributions-welcome-brightgreen.svg" /></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#what-works-today">Status</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="./docs/index.md">Docs</a> ·
  <a href="./CONTRIBUTING.md">Contributing</a> ·
  <a href="./SECURITY.md">Security</a>
</p>

> **Developer preview (v0.6).** The source, tests, web app, API, workers,
> desktop shell, and native companion clients are public. There are no signed
> installer assets yet; build from source and do not treat this release as
> production-ready without completing the deployment checklist.

## Why Edecan?

Edecan is an assistant, not a collection of dashboards. A person should be
able to say, in one message or voice request, “organize my tasks, answer this
email, review the document and remind me to pay tomorrow.” Edecan decides which
capabilities are needed, coordinates them and reports the result in the same
conversation.

The mobile product has three human-facing places: **Edecan**, **Activity** and
**You**. Creation, voice and remote control are contextual actions inside that
experience; Skills, MCP and business modules stay behind human language and
advanced settings. They are capabilities, not separate products the person
must learn.

Most assistants forget context, stop at text, or require credentials to pass
through a hosted intermediary. Edecan follows a different model:

- **Persistent, inspectable memory.** Conversations, profile facts, files,
  and graph relationships live in storage you control.
- **Actions, not just answers.** A typed tool registry connects reminders,
  documents, research, messaging, workflows, an IDE, and multi-agent missions.
- **One intent, several actions.** The assistant selects only the capability
  families relevant to each request and can combine them in one turn.
- **Creation that produces files.** One request can deliver private Word, PDF,
  PowerPoint, post, website and executable app-project files with a manifest,
  hashes and authenticated downloads instead of pretending plain text is a file.
- **Voice in and out.** Spoken requests use the same agent path as chat, while
  a tenant-owned Twilio number can place or receive consent-aware conversational
  calls whose status and transcript remain attached to the conversation.
- **A real rich conversation.** Private attachments, authenticated media,
  URL previews, image/video/audio, flight and hotel cards, deep links and safe
  draft actions survive reloads across web, iOS and Android.
- **A relationship style you control.** Professional, coach, friend and
  adult-consented romantic tones are editable preferences. Edecan remains
  explicit that it is AI and never uses dependency or exclusivity tactics.
- **Recover instead of giving up.** When a capability is missing, Edecan can
  diagnose the failure, reuse existing configuration, create a reversible local
  skill, or — in an explicitly enabled source checkout — prepare, test and
  roll back a local core repair before retrying the original intent.
- **Bring your own providers.** Use local Ollama, authenticated Claude/Codex
  CLIs, or your own API and OAuth credentials. Provider access is never shared
  between tenants.
- **Human control at the boundary.** Dangerous tools require confirmation;
  desktop access is sandboxed and remote input is disabled by default.
- **Spanish-first product design.** The UI and most operator documentation are
  written for Spanish-speaking users, while the public project entry points are
  kept accessible to the wider OSS community.

## What works today

| Surface | Current state | Evidence |
|---|---|---|
| Python core, API, workers, tools | Implemented | 4,300+ offline tests pass locally |
| Web application | Implemented | Next.js production build renders 37 routes |
| Local desktop runtime | Preview | Tauri shell + packaged Python backend; source build required |
| macOS and Windows desktop packaging | Preview | Build scripts exist; signed public installers do not yet |
| Native iOS and Android companions | Preview | iOS simulator build and Android debug APK compile from source |
| Self-hosted server | Preview | Docker Compose and developer-mode paths; operator owns backups and TLS |
| BYO Twilio conversational calls | Implemented | Signed webhook and injected-provider tests; no real calls in CI |

Capabilities include tool-using chat, memory and profile consolidation,
automations, reminders, document analysis, browser research, meetings,
messaging, voice, MCP servers, skills, business workflows, travel, vehicles,
Home Assistant, private artifact creation, inbound/outbound calls, an embedded
IDE, and multi-agent missions. Availability depends
on configuration and feature flags; see the [documentation map](./docs/index.md)
instead of assuming every integration is enabled by default.

If Codex CLI is already authenticated, the assistant needs **zero new API
keys** for its core intelligence. See the [minimal configuration matrix](./docs/configuracion-minima.md)
for optional Internet, image, voice, travel, phone and OAuth credentials.

The product behavior is defined in the
[assistant-first contract](./docs/producto-assistant-first.md). “Anything” means
anything that can be performed legitimately with the connected capabilities
and permissions; Edecan must explain and recover from a boundary, never pretend
that an unavailable action succeeded.

## Deliberate boundaries

- No scraping or shared third-party credentials. Connectors use official APIs
  and credentials supplied by the operator or tenant.
- No autonomous real-money execution. Commerce is pinned to paper mode.
- No silent device control. High-impact tools require explicit approval, and
  remote keyboard/mouse control requires a separate local opt-in.
- No silent phone calls. Outbound calls require recipient consent and human
  approval of the exact destination and objective; Twilio credentials and usage
  belong to the tenant.
- No real secrets in source, fixtures, logs, or example configuration.
- No claim of SOC 2, ISO 27001, external audit, or production certification.
- No silent self-modification. Local skills and source repairs require the
  normal human approval boundary; core repair is off by default, runs in an
  isolated Git worktree, uses allowlisted argument-vector commands, requires
  passing tests and never pushes code.

## Quickstart

### Verify the public core

Requirements: Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/bizagencysas/edecan-oss.git
cd edecan-oss
uv sync --all-packages --frozen
make check
```

`make check` runs Ruff and the deterministic Python suite. Tests do not call
paid providers or require real credentials.

### Run the full developer stack

Additional requirements: Docker with Compose v2 and Node.js 22.

```bash
cp .env.example .env
# Replace JWT_SECRET and LOCAL_MASTER_KEY using the commands documented in .env.example.

make deps
make db-migrate
```

Then start each process in a separate terminal:

```bash
make api       # FastAPI http://localhost:8000
make worker    # asynchronous jobs
make web       # Next.js http://localhost:3000
```

Create an account in the local UI and connect your own LLM provider. The root
`docker-compose.yml` intentionally starts development dependencies only
(Postgres/pgvector, Redis, and LocalStack). For the containerized application
stack and its operational caveats, follow [Self-hosting](./docs/self-hosting.md).

> Never run bare `uv sync` or `uv run` at the workspace root. The root project
> has no application dependencies of its own, so uv can prune editable workspace
> packages. Use the Make targets or include `--all-packages`.

## Architecture

```text
 Text or voice intent
          │
          ▼
 Edecan · Activity · Settings
                       │ HTTPS / SSE / WebSocket
                       ▼
                FastAPI application
        auth · tenant context · rate limits · approvals
              │          │          │
              ▼          ▼          ▼
        Agent + tools  Postgres   Redis
        LLM router     + pgvector cache/pairing
              │          │
              └────┬─────┘
                   ▼
             durable job queue
                   │
                   ▼
          workers · schedules · files
```

The system is a Python `uv` monorepo with explicit package contracts, a
FastAPI boundary, deterministic provider fakes, per-tenant credential
encryption, and native clients. Read [ARCHITECTURE.md](./ARCHITECTURE.md) for
the binding interfaces and [the threat model](./docs/seguridad-modelo-amenazas.md)
for trust boundaries and known risks.

## Repository map

```text
apps/
  api/          FastAPI HTTP and streaming boundary
  worker/       durable job handlers and scheduler
  local/        single-user packaged runtime
  companion/    opt-in sandboxed desktop actions
  desktop/      Tauri desktop shell and packaging
  web/          Next.js application
  mobile/       native iOS and Android clients
packages/       reusable Python domains and integrations
prompts/        versioned assistant prompts
docs/           guides, security model, and runbooks
scripts/        local install and repository verification helpers
```

## Quality and security

- Python formatting and linting: Ruff, line length 100.
- Python tests: pytest/pytest-asyncio, offline and deterministic.
- Python dependency advisories: pinned `pip-audit` scan of the exported lock.
- Web checks: dependency audit, ESLint, TypeScript, and production build.
- Desktop checks: locked Rust dependency graph and sidecar-free unit build.
- Self-host checks: clean image builds, real migrations, readiness, CSP, worker import, and non-root runtimes.
- CI uses least-privilege permissions and frozen lockfiles.
- Production startup rejects public placeholder secrets.
- Vulnerabilities are reported privately through GitHub Security Advisories;
  see [SECURITY.md](./SECURITY.md).

## Contributing

Start with [CONTRIBUTING.md](./CONTRIBUTING.md). Small fixes are welcome;
larger changes should begin with an issue so maintainers and contributors can
agree on contracts before implementation. The project also publishes its
[governance](./GOVERNANCE.md), [support policy](./SUPPORT.md), and
[code of conduct](./CODE_OF_CONDUCT.md).

## License

Everything in this repository is licensed under the
[Apache License 2.0](./LICENSE). Third-party attributions are recorded in
[NOTICE](./NOTICE).
