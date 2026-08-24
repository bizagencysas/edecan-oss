<p align="center">
  <img src="apps/web/src/app/icon.png" width="120" alt="Edecan robot with headphones" />
</p>

<h1 align="center">Edecan</h1>

<p align="center">
  <strong>Ask once. Edecan understands the job and gets it done.</strong><br />
  One text or voice conversation · local-first · human approval when it matters
</p>

<p align="center">
  <a href="https://github.com/your-org/edecan-oss/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/your-org/edecan-oss/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="./LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" /></a>
  <a href="./pyproject.toml"><img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB.svg" /></a>
  <a href="./apps/web/package.json"><img alt="Next.js 15" src="https://img.shields.io/badge/Next.js-15-000000.svg" /></a>
  <a href="./CONTRIBUTING.md"><img alt="Contributions welcome" src="https://img.shields.io/badge/contributions-welcome-brightgreen.svg" /></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="./docs/guia-completa.md">Complete guide</a> ·
  <a href="#what-works-today">Status</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="./docs/index.md">Docs</a> ·
  <a href="./CONTRIBUTING.md">Contributing</a> ·
  <a href="./SECURITY.md">Security</a>
</p>

> **Developer preview (v0.9).** The source, tests, web app, API, workers,
> desktop shell, and native companion clients are public. There are no signed
> installer assets yet; build from source and do not treat this release as
> production-ready without completing the deployment checklist.

## Complete guide

The canonical operator and developer manual is
**[Guía completa de Edecán](./docs/guia-completa.md)**. It covers the real
  repository paths, macOS/Windows/Linux packaging, installing iOS and Android
  with your own signing accounts, QR pairing, managed AI inference,
voice and call agents, the mobile IDE, local self-repair, remote control,
connectors, cloud deployment, private migration from another assistant, API
route families, secrets, testing and troubleshooting.

The guide explicitly separates implemented, built, installed, configured and
verified states. Every OSS installation owns its cloud accounts, signing
identities, data and credentials; cloning this repository never grants access
to a maintainer's infrastructure.

Existing private installations can use the
[local migration guide](./docs/migracion-asistente-privada.md) to import an
owner-confirmed profile, memory, conversations, call agents and editorial
preferences. The migration is opt-in, dry-run by default and keeps personal
data outside Git.

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
- **A mobile code studio.** Authorize a project on the desktop, then inspect
  and edit files, follow Codex or Claude work live, use a persistent terminal,
  and operate typed Git flows from either iOS or Android. Minimizing the phone
  does not cancel the process running on the computer.
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
- **Managed intelligence, portable architecture.** Chat, voice, calls and
  background jobs use Cloudflare Workers AI through a generic provider
  contract. Edecan chooses the model automatically; optional voice, image,
  search and OAuth integrations still use credentials owned by each
  installation.
- **Human control at the boundary.** Dangerous tools require confirmation;
  desktop access is sandboxed and remote input is disabled by default.
- **Spanish-first product design.** The UI and most operator documentation are
  written for Spanish-speaking users, while the public project entry points are
  kept accessible to the wider OSS community.

## What works today

| Surface | Current state | Evidence |
|---|---|---|
| Python core, API, workers, tools | Implemented | 5,300+ offline tests pass locally |
| Web application | Implemented | Next.js production build renders 37 routes |
| Local desktop runtime | Preview | Tauri shell + packaged Python backend for macOS, Windows and Linux x64 |
| Native desktop packaging | Preview | DMG, NSIS/MSI, Debian and RPM builds; public installers are not signed yet |
| Native iOS and Android companions | Preview | iOS 0.9.0 (build 68) in-place on the owner device: step cards only on tool turns; ordinary chat stays a bubble. Android debug APK compiles from source |
| Mobile code studio | Implemented | Authorized workspaces, durable agent/terminal sessions, editor and typed Git share one API contract on iOS and Android |
| Self-hosted server | Preview | Docker Compose and developer-mode paths; operator owns backups and TLS |
| BYO Twilio conversational calls | Implemented | Signed webhook and injected-provider tests; no real calls in CI |
| Local self-repair | Implemented, opt-in | Isolated Git worktree, exact-command allowlists, approval gates, tests, local commit, integration and rollback |

Capabilities include tool-using chat, memory and profile consolidation,
automations, reminders, document analysis, browser research, meetings,
messaging, voice, MCP servers, skills, business workflows, travel, vehicles,
Home Assistant, private artifact creation, inbound/outbound **call agents**
(Twilio voice URL must point at this installation, not a previous assistant;
the media WebSocket and hangup must close the call record), an embedded
IDE, and multi-agent missions. Availability depends
on configuration and feature flags; see the [documentation map](./docs/index.md)
instead of assuming every integration is enabled by default.

Recent companion work (owner installs, not a public release): inbound call
agents use the agent prompt and voice, not the lean chat prompt; native
Workers AI streams that omit OpenAI `choices` are parsed; iOS step cards
apply only to tool turns; ordinary chat stays a bubble.

Core intelligence requires the operator's Cloudflare account ID and Workers AI
token; end users never select models or connect an LLM account. See
[Workers AI inference](./docs/workers-ai.md) and the
[minimal configuration matrix](./docs/configuracion-minima.md) for optional
Internet, image, voice, travel, phone and OAuth credentials.

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

## Can Edecan edit its own code?

**Yes, when the owner explicitly enables it on a local source checkout.**
This is a real tool path, not a system-prompt claim. Edecan can inspect its
configured repository, prepare a repair in an isolated Git worktree, write
files with optimistic SHA-256 checks, run only owner-allowlisted commands,
create a local commit, fast-forward the tested repair into the checkout, retry
the original request, and roll the complete repair back.

It is intentionally **off by default**. A packaged desktop app that does not
retain a Git checkout cannot rewrite its own signed application bundle. It can
still add or update a local skill immediately, but a core source repair needs
the public repository on the same computer:

```dotenv
EDECAN_LOCAL_MODE=true
EDECAN_LOCAL_REPO_PATH=/absolute/path/to/edecan-oss
EDECAN_SELF_REPAIR_ENABLED=true
EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON=[["uv","run","--all-packages","--frozen","pytest","packages/toolkit/tests"]]
EDECAN_SELF_REPAIR_INSTALL_COMMANDS_JSON=[]
```

The repository must be clean before a core repair starts. Every mutating step
appears as a separate confirmation in chat. Tests run without a shell and must
match an entire configured argument vector; prefix matching is not accepted.
The repair engine never pushes a remote.

The chat commands are provider-independent:

- `/fix <problem>` diagnoses first, then proposes the smallest reversible
  repair through the normal confirmation gates.
- `/oss <change>` works only inside the configured public OSS checkout and
  excludes credentials, personal data and private-only infrastructure.
- `/changes` reports status, diff and recent commits without editing, staging,
  committing, integrating or pushing anything.

The source of truth is
[`docs/autorreparacion-local.md`](./docs/autorreparacion-local.md). Its test
suite covers disabled mode, dirty repositories, path confinement, concurrent
file changes, non-allowlisted command rejection, tested commits, integration,
multi-cycle retry and rollback, and verifies that the repair engine does not
push.

## Quickstart

### Open Edecan like a normal app

- **macOS:** double-click **`Abrir Edecán.command`** at the repository root. It
  installs the native `Edecán.app` into your user Applications folder on first
  use and opens it thereafter.
- **Windows:** open the generated `Edecán-Setup.exe`; the installer creates the
  normal Start-menu shortcut and uninstaller.
- **Linux x64:** open the generated `.deb` in the software center on
  Debian/Ubuntu or use the `.rpm` on Fedora/openSUSE.

Mobile QR pairing and optional service connections live under
**Settings → Connections**. The operator configures Workers AI once in the host
environment; no provider or model selector is exposed in the normal-person
flow.

### Use the iPhone away from the computer's LAN

The iOS app can work with Edecán running on macOS, Windows or Linux. There are
two connection modes:

- **Same network:** the desktop app enables mobile access and the iPhone pairs
  with the computer's private LAN address. This is useful for local testing.
- **Any network:** the computer needs an outbound HTTPS tunnel or relay that
  points to its local Edecán API. This is the mode to use from a mall, mobile
  data, or another Wi-Fi network. Do not expose the API by opening a router
  port directly.

For the second mode, the operator must:

1. Create a hostname and HTTPS tunnel in their own Cloudflare account (or use
   another HTTPS reverse tunnel they control) with the tunnel origin pointing
   to `http://127.0.0.1:8765`.
2. Keep that tunnel and the Edecán desktop app running on the same computer.
3. Set `EDECAN_MOBILE_PUBLIC_URL` to the tunnel's HTTPS URL, or configure the
   tunnel URL in the local data directory as documented in
   [`desktop-local.md`](./docs/desktop-local.md).
4. Generate a new mobile pairing QR after configuring the URL and scan it from
   the iPhone. The QR carries the HTTPS server URL and a one-time pairing
   token; it does not contain a tunnel secret.

The OSS repository does not include anyone else's Cloudflare account, tunnel,
DNS zone or secret. Every operator creates and owns those resources. If the
computer is offline, the optional edge-continuity deployment can queue basic
chat; private files, local memory, IDE access, screen viewing and remote input
still require the computer and its tunnel to be online. See
[`continuidad-hibrida.md`](./docs/continuidad-hibrida.md) for that optional
deployment and its limits.

### Verify the public core

Requirements: Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/your-org/edecan-oss.git
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

Create an account in the local UI. The root `docker-compose.yml` intentionally
starts development dependencies only
(Postgres/pgvector, Redis, and LocalStack). For the containerized application
stack and its operational caveats, follow [Self-hosting](./docs/self-hosting.md).

To keep chat, phone agents, memory, reminders and notifications available
while the personal computer is off, deploy a personal HTTPS node with the
[Edecán online guide](./docs/online-node.md). It uses the same OSS core and
keeps each installation's domain, data and credentials under its owner's
control.

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
        Task router    + pgvector cache/pairing
              │          │
              └────┬─────┘
                   ▼
             durable job queue
                   │
                   ▼
          workers · schedules · files
```

The system is a Python `uv` monorepo with explicit package contracts, a
FastAPI boundary, deterministic provider fakes, host-managed Workers AI,
per-tenant connector encryption, and native clients. Read
[ARCHITECTURE.md](./ARCHITECTURE.md) for
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
- Desktop checks: locked Rust tests plus Linux Debian and RPM builds that boot
  the packaged backend, check `/healthz`, open a real
  window in a virtual display and verifies clean shutdown without orphaned
  sidecars.
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
