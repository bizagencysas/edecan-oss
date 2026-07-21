# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-20

### Added

- An assistant-first product contract built around one text or voice request and
  three primary spaces: Edecan, Activity and Settings
- Deterministic capability routing for compound natural-language requests,
  including short conversational follow-ups and contextual MCP tools
- A local recovery ladder that diagnoses a failed intent, can create a
  reversible local skill and can prepare an isolated, tested source repair
- An Activity overview that combines ongoing work, reminders and automations
  without making internal modules the primary navigation

### Changed

- Web, iOS and Android navigation now present Edecan as the front door; the
  previous specialist surfaces remain available under advanced settings
- Voice and typed requests share the chat execution and confirmation path
- Dangerous actions pause the original compound turn and resume it after a
  one-time confirmation, preserving the remaining safe work without replay

### Fixed

- Database transactions used by the primary repositories now commit before a
  successful HTTP response reaches the client, preventing new-account races
- Activity ignores superseded concurrent refreshes instead of displaying a
  stale partial-failure warning after newer requests have succeeded

### Security

- Source self-repair is opt-in and local-only, rejects dirty repositories,
  applies hash-guarded edits in an isolated Git worktree, runs only exact
  allowlisted argument vectors, requires passing tests before integration and
  never pushes code
- Local code commits stage only explicit paths and reject unrelated staged work

## [0.2.0] - 2026-07-20

### Added

- Cross-platform continuous integration for Python, web, desktop, iOS shared code, and Android
- Automated dependency update configuration for the repository's package ecosystems
- Structured issue forms and a pull request template
- Public governance, maintainership, support, and community conduct policies
- A reproducible self-hosting Docker Compose profile with non-root images
- An isolated self-host smoke test covering image builds, migrations, readiness, CSP, and runtime users
- Continuous-listening desktop support with explicit microphone consent and local wake-word detection
- Server-side refresh-token rotation and revocation, authentication rate limits, membership revalidation, and login/logout audit events
- Strict web, static-export, and Tauri content security policies and defensive HTTP headers
- A dependency-aware `/readyz` endpoint for PostgreSQL and Redis
- A deterministic new-user journey covering readiness, registration, profile,
  setup, chat streaming, file upload, refresh rotation, logout, and revocation

### Changed

- The public workspace now installs without the absent private extension and all public packages declare Apache-2.0 metadata
- Browser tokens use tab-scoped session storage instead of persistent local storage
- File uploads are streamed from Starlette's spooled file, bounded by `MAX_UPLOAD_BYTES`, and stored under sanitized names
- Git operations in the local-code tool use argument-vector subprocesses so commit messages cannot execute shell syntax
- The supported Node 22 LTS container build consumes a compatible npm 10 lockfile, reuses the official non-root user, and ships every runtime config module
- Web API clients share one refresh operation, protected TOTP routes carry the
  access token, and file processing status updates without blocking the page
- Android and iOS coalesce refresh rotation, recover chat SSE once after a
  `401`, revoke sessions remotely, and invalidate local logout immediately
- The local runtime installs embedded PostgreSQL by default, while Tauri
  development builds a same-origin web UI and runs the backend from source

### Security

- Removed the custom commercial exception from the repository-wide Apache-2.0 license
- Upgraded the web runtime and pinned dependency overrides to a zero-advisory npm audit
- Added pinned Python advisory scanning; the current exported lock resolves with no known vulnerabilities
- CI now proves that every Python workspace member produces both an sdist and a wheel
- Docker build contexts exclude local secrets, caches, dependency trees, and generated artifacts
- Linux and macOS now exercise the same portable fake-ffmpeg byte fixture

## [0.1.0] - 2026-07-10

### Added

- Initial public release of the Apache-2.0-licensed Edecan core

[Unreleased]: https://github.com/bizagencysas/edecan-oss/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/bizagencysas/edecan-oss/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/bizagencysas/edecan-oss/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bizagencysas/edecan-oss/releases/tag/v0.1.0
