<p align="center">
  <img src="apps/desktop/src-tauri/icons/icon.png" width="120" alt="Edecán logo" />
</p>

<h1 align="center">Edecan</h1>

<p align="center">
  <i>Your personal AI, on your own machine.</i>
</p>

<p align="center">
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" /></a>
  <a href="./pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-%3E%3D3.12-blue.svg" /></a>
  <img alt="Node" src="https://img.shields.io/badge/node-%3E%3D20-339933.svg" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20self--hosted-lightgrey.svg" />
  <a href="./CONTRIBUTING.md"><img alt="Contributions" src="https://img.shields.io/badge/contributions-welcome-brightgreen.svg" /></a>
</p>

<p align="center">
  <a href="#why-edecan">Why</a> ·
  <a href="#getting-started">Getting started</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="./ARCHITECTURE.md">Full technical spec</a> ·
  <a href="./docs/index.md">Docs</a> ·
  <a href="./CONTRIBUTING.md">Contributing</a> ·
  <a href="./SECURITY.md">Security</a>
</p>

---

A downloadable, installable desktop app (macOS/Windows) that gives you a personal AI butler running **entirely on your own machine** — chat, web voice, telephony, and real integrations (email, calendar, social networks via official APIs, WhatsApp, documents, personal finance, contacts/CRM, reminders, web research, content generation, an embedded IDE, and multi-agent missions) — with "god-mode" personalization: you define your assistant's name, tone, personality, standing instructions, and living memory/profile.

The full source is included and self-hostable if you want to customize or run your own instance — but that's a bonus. The product is the installed app, working out of the box.

## Why Edecan?

**Bring-your-own-everything.** Use what you already have: your Claude Code subscription, your Codex account, free local Ollama, your own Anthropic/OpenAI-compatible/Gemini API key, your own Twilio, your own Deepgram/ElevenLabs. Zero lock-in, zero markup on third-party usage — we never operate or bill third-party accounts on a customer's behalf.

**Open core.** Everything except `premium/` is Apache-2.0 and self-hostable via Docker Compose if you'd rather run it on your own server than install the app.

**Real integrations, official APIs only.** No scraping, ever. Every connector (Google, Microsoft, Meta, X, YouTube, Slack) is OAuth 2.0 with an app *you* register — never a shared platform credential.

## What it deliberately does NOT do

Set expectations up front:

- **No LinkedIn integration**, in any form — no code, no OAuth scopes, no URLs, no UI or documentation mentioning it. This is a permanent rule (see [`ARCHITECTURE.md`](./ARCHITECTURE.md) §0) enforced by a repo-wide test that fails if the word appears in `packages/connectors/`.
- **No scraping.** Every integration with Google, Microsoft, Meta, X, or YouTube uses their official OAuth 2.0 APIs. Each tenant authorizes their own account; credentials are never shared or hardcoded.
- **No unsolicited calls or texts.** The compliance engine in `premium/` requires recorded consent, respects the recipient's local 08:00–21:00 quiet hours, offers automatic opt-out ("STOP"), and always identifies itself as an automated assistant.
- **No infrastructure applies itself.** Code under `infra/terraform` (hosted-deployment infra, not part of this public core) is written and reviewed like any other code; `terraform apply` is always a manual, out-of-band step — never something a pipeline or agent in this repo runs automatically.
- **No real secrets in the repo.** Only `TU_X_AQUI`-style placeholders live in `.env.example` and the docs; each tenant's real credentials are encrypted at rest in the TokenVault (AES-256-GCM, wrapped with KMS or a local Fernet key).

## Getting started

Three paths, simplest to most hands-on:

1. **Desktop app (recommended)** — download the macOS/Windows installer and follow the 2–3 step welcome wizard (connect one LLM provider and you're done). It auto-detects `claude`/`codex`/Ollama if you already have them and offers to use them with one click, no API key required. See [`docs/primeros-pasos.md`](./docs/primeros-pasos.md) and [`docs/proveedores-llm.md`](./docs/proveedores-llm.md) *(Spanish — see note below)*.
2. **Self-host with Docker Compose** — the full stack (`api`, `worker`, `web` + Postgres/Redis) in containers on your own server (VPS, NAS, mini-PC). Full guide in [`docs/self-hosting.md`](./docs/self-hosting.md) §3.
3. **Developer mode** — every service running directly on your machine via `make`, for anyone modifying the code. Covered below.

> **A note on language:** this README is in English for discoverability, but the product itself targets Spanish-speaking users first — most of `docs/` and all in-app UI/copy are written in Spanish. Machine-translate as needed, or see [`CONTRIBUTING.md`](./CONTRIBUTING.md) if you'd like to help with an English docs pass.

## Developer mode (self-host from source)

Requirements: Docker and Docker Compose, Python 3.12 with [`uv`](https://docs.astral.sh/uv/), Node.js 20+.

1. **Set up your own credentials.**
   ```bash
   cp .env.example .env
   # Edit .env with YOUR OWN API keys (LLM, voice, each connector's OAuth app, etc.)
   # Never commit or share your real .env.
   ```
2. **Start local dependencies** (Postgres+pgvector, Redis, LocalStack for S3/SQS):
   ```bash
   make deps
   ```
3. **Run migrations:**
   ```bash
   make db-migrate
   ```
4. **Start each service** (in separate terminals):
   ```bash
   make api      # FastAPI on :8000
   make worker   # job queue consumer
   make web      # Next.js on :3000
   ```
5. Open `http://localhost:3000`, create your tenant, and configure your assistant's persona (name, tone, instructions, memory).

The `free_selfhost` plan (see [`ARCHITECTURE.md`](./ARCHITECTURE.md) §10.13) has no message or web-voice limits and includes social connectors with your own OAuth apps. Telephony (`premium/`) is a separate package and isn't required for the core to work.

Other useful commands: `make test` (offline pytest, no real network) and `make lint` (ruff).

> **Watch out for a bare `uv sync`/`uv run`.** The root `pyproject.toml` declares the workspace but has no `dependencies` of its own, so running `uv sync` or `uv run <cmd>` directly (bypassing `make`, without `--all-packages`) **silently uninstalls** every editable workspace package (`edecan_core`, `edecan_agents`, etc. — see `[tool.uv.workspace].members`). The symptom is `ModuleNotFoundError: No module named 'edecan_core'` when you next run pytest. All `make` targets already pass `--all-packages` for you. If it happens, `uv sync --all-packages` reinstalls everything and fixes the environment.

## Architecture

Reference flow for a single conversation turn (full detail in [`ARCHITECTURE.md`](./ARCHITECTURE.md) §7 and §9):

```
   Web (Next.js) · Desktop companion · Phone (Twilio, premium*)
                              │  HTTPS / SSE / WebSocket
                              ▼
                    apps/api (FastAPI, :8000)
                    Agent.run_turn — tool loop (max 8 iterations)
              ┌───────────┼────────────┬──────────────┐
              ▼           ▼            ▼              ▼
        Postgres 16    Redis        LLMRouter      TokenVault
        + pgvector     cache ·      Anthropic      per-tenant
        (per-tenant    rate-limit · (primary) ·    encrypted
        RLS)           pairing ·    OpenAI-compat · credentials
                       confirms     Bedrock (stub)  (AES-256-GCM,
              ▲                                     KMS or local Fernet)
              │                     SQS edecan-jobs (+ edecan-jobs-dlq)
              │                               │
              │                               ▼
              └───────────────────  apps/worker
                                     ingest_file · sync_connector ·
                                     send_reminder(_scan) · run_campaign_step
                                     (premium) · memory_consolidate ·
                                     generate_content — retries with backoff
                                     (2^attempt·30s, up to 5, then DLQ)
                                               │
                                               ▼
                                     S3 edecan-files (per-tenant prefix)
```

`*` Telephony only activates if the tenant connects their own Twilio account (`premium/` package, `voice.telephony` plan flag); the core works completely without it.

In the desktop app, this same backend runs packaged and local on the client's machine with an embedded Postgres — see [`docs/desktop-local.md`](./docs/desktop-local.md).

## Repository layout

```
edecan/
├── README.md
├── ARCHITECTURE.md              # binding technical contract
├── LICENSE                      # Apache-2.0 (core)
├── NOTICE                       # clarifies premium/ is proprietary
├── SECURITY.md
├── CONTRIBUTING.md
├── .env.example
├── pyproject.toml               # uv workspace root (Python 3.12)
├── docker-compose.yml           # dev deps: postgres+pgvector, redis, localstack
├── apps/
│   ├── api/                     # FastAPI (edecan_api) — auth, chat SSE, connectors, voice, billing
│   ├── worker/                  # SQS consumer + job handlers (edecan_worker)
│   ├── web/                     # Next.js 14 + TS + Tailwind
│   ├── desktop/                 # Tauri shell — packages apps/web as the installable desktop app
│   ├── local/                   # edecan_local — runs api+worker+db locally for the desktop app
│   ├── mobile/                  # native iOS/Android companion apps
│   └── companion/               # opt-in local desktop agent (edecan_companion)
├── packages/                    # edecan_core, edecan_llm, edecan_connectors, edecan_toolkit,
│                                 # edecan_voice, edecan_schemas, edecan_db, and ~20 more feature packages
├── prompts/                     # versioned system prompt templates
├── premium/                     # edecan_premium — Twilio telephony, campaigns, quotas (commercial license)
└── docs/                        # self-hosting, connectors, compliance, runbooks (mostly Spanish)
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) §11-§15 and [`docs/index.md`](./docs/index.md) for the complete package map.

## Free core vs. `premium/` (commercial license)

- **Core (`Apache-2.0`)**: everything in this repo except `premium/`. Chat, the tool-using agent, memory/graph, missions, automations, the embedded IDE, an analyst, a browser tool, Google/Microsoft/social connectors with your own OAuth apps, web voice with your own STT/TTS keys, and the desktop companion. Installs with the desktop app or `docker-compose`, always with your own API keys; community support.
- **`premium/` (commercial license, see [`NOTICE`](./NOTICE))**: per-tenant Twilio telephony, voice/SMS campaigns with a compliance engine, plan-based quotas, and premium agent tools. `apps/api` mounts it at runtime only if the `edecan_premium` package is installed, and every capability is additionally gated by the tenant's plan flags (see [`ARCHITECTURE.md`](./ARCHITECTURE.md) §10.13).

## Documentation

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — technical architecture and the binding contracts between packages.
- [`SECURITY.md`](./SECURITY.md) — security policy and vulnerability reporting.
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — code conventions and contribution flow.
- [`docs/index.md`](./docs/index.md) — full map of extended documentation (self-hosting, API, connectors, compliance, runbooks). Mostly in Spanish.

## License

The core of this repository is distributed under the **Apache License 2.0** (see [`LICENSE`](./LICENSE)). The `premium/` directory is proprietary software under a separate commercial license and is **not** covered by the Apache License (see [`NOTICE`](./NOTICE)).
