# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.4] - 2026-07-23

### Added

- Resumable mobile chat attempts with authenticated status lookup, exact SSE
  replay and completion notifications after a phone suspends the app
- Signed stable and preview update channels for the installed macOS, Windows
  and Linux applications
- An OSS Android update channel that verifies the downloaded APK size, hash,
  package identity, version and installed signing certificate
- A fail-closed iOS update channel that opens only an explicitly configured
  App Store, TestFlight, AltStore, SideStore or signed HTTPS destination

### Changed

- iOS and Android now restore in-flight chat work when returning to the
  foreground instead of presenting a false send failure
- Runtime version reporting now derives from installed package metadata
- Desktop, Android and iOS publishers share one serialized update-channel
  writer so platform manifests cannot overwrite one another
- Every public package and native client now reports version 0.7.4

### Fixed

- Backgrounded mobile requests no longer cancel the producer when the client
  socket disappears
- Python and web dependency audits are clean in the release pipeline

### Security

- Update signing keys and Android keystores remain outside Git and are supplied
  to release jobs only through repository secrets
- Update packages never include conversations, memories, credentials, user
  files or local configuration

## [0.7.3] - 2026-07-23

### Added

- An official bring-your-own-app LinkedIn connector using OpenID Connect,
  profile identity, image upload and the Posts API without bundling shared
  credentials
- A visible content studio that creates editable LinkedIn or X copy, original
  images and downloadable artifacts with Codex, Claude, Ollama or another
  configured model
- A configurable LinkedIn plan that prepares two or three distinct visual
  drafts every day, keeps publication under human review and reports its work
  through the existing activity and notification systems
- A persistent recent-drafts view for recovering, editing and publishing
  LinkedIn packages created manually or by the daily plan
- Confirmed LinkedIn publication from both the content studio and the assistant
  tool layer, with tenant-scoped encrypted tokens, audit events and artifact
  ownership checks

### Changed

- Connector setup now links every supported provider to its official
  application console and explains when the OAuth client secret is mandatory
- LinkedIn publication requests route through the official connector instead
  of computer control, so the selected language model changes intelligence but
  not execution capabilities
- The daily content plan now rotates editorial territories and formats, checks
  recent work for repetition and separates research, writing and factual review
- Every public package and native client now reports version 0.7.3

### Fixed

- Official provider links inside the desktop WebView now open in the operating
  system browser through a native, allowlisted HTTPS command instead of becoming
  dead clicks
- LinkedIn account callbacks now persist the authorized profile identity rather
  than a generic placeholder

### Security

- External portal opening rejects non-HTTPS URLs, embedded credentials and
  domains outside the documented provider allowlist
- LinkedIn image publication loads only tenant-owned private artifacts, caps
  payload size, validates provider upload hosts and requires explicit
  confirmation immediately before publishing

## [0.6.0] - 2026-07-21

### Added

- A versioned rich-chat contract for private image, video and audio, safe URL
  previews, flight and hotel cards, deep links and draft-only actions across
  web, iOS and Android
- Multi-file chat attachments with private upload/download, cancellation,
  retry, attachments-only messages and restored history
- Native zero-key mobile voice fallback using the iOS and Android speech and
  text-to-speech frameworks when cloud voice credentials are absent
- Persistent mobile conversation selection, new chat, delivery failure and
  retry, creation presets and one shared conversation for text and voice
- Stable per-turn idempotency across web, iOS and Android, with live SSE,
  exact replay after disconnects and recoverable pending confirmations

### Changed

- Native navigation now exposes only Edecan, Activity and You as primary
  spaces; creation and remote control are contextual actions instead of
  separate products
- Tool presentation is an explicit trusted channel: arbitrary provider or MCP
  data cannot mint UI actions, media references or links
- Search and travel cards identify live, demonstration and unknown provider
  state instead of making sample data look current
- Every public package and client now reports version 0.6.0

### Fixed

- Streaming persists the assistant message before the terminal SSE event and
  correctly parses CRLF, multi-line data and a final frame without a separator
- Tool calls with the same name correlate by stable call ID even when they
  finish out of order, and rich blocks restore after a reload
- Android destroys all account-scoped ViewModels when identity changes so a
  logout/login cannot expose the previous account's chat, drafts or attachments
- iOS clears account-scoped chat state on logout or expiry and ignores stale
  push-to-talk permissions after release, navigation or a newer recording
- Mobile attachment cancellation prevents late uploads from reappearing;
  Android streams uploads from private cache and refreshes media credentials
  for authenticated byte-range playback
- The setup screen no longer prefixes an already descriptive Codex CLI version
  with a misleading extra `v`

### Security

- Authenticated media streaming enforces tenant ownership, safe inline MIME
  types, byte ranges, no-store, no-sniff and a restrictive content policy
- Rich actions accept only public HTTP(S) destinations and allowlisted native
  screens; private-network, credential-bearing and active-content URLs are
  rejected
- Chat media blocks can reference only artifacts returned by the same trusted
  tool execution

## [0.5.0] - 2026-07-20

### Added

- Native five-tab iOS and Android experiences centered on Edecan, Create,
  Remote, Activity and Settings; Create turns a short brief into a normal chat
  request instead of exposing another technical product
- A social content studio for LinkedIn, X, Instagram, Facebook, Threads and
  TikTok that creates copy, accessible image direction, manifests and optional
  original visual cards while always leaving publication to the person
- First-class Skills and MCP discovery in the advanced mobile settings, with
  human-readable status and no internal implementation details in the primary
  assistant flow
- Cross-platform companion backends for screen capture and keyboard/mouse
  control on Windows and Linux, complementing the native Quartz implementation
  on macOS
- Recoverable `trash_path` computer action, confined to the configured sandbox
  and protected by an unavoidable local approval

### Changed

- Remote viewing now requests compressed, width-bounded JPEG frames roughly
  three times per second without overlapping requests; PNG remains supported
- Web, iOS and Android remote clients understand MIME type and multi-monitor
  origins and expose drag, right click, scroll, extended keys and shortcuts
- The companion's optional `remote-control` dependency installs only the
  platform backend it needs and keeps non-remote installations lightweight
- Every public Python package, web, desktop and native client now reports
  version 0.5.0

### Fixed

- Remote frame and input routes use their dedicated high-frequency limits
  instead of inheriting the administrative 60-requests-per-minute limiter
- Remote clients prevent concurrent frame polls, avoiding stale frame races and
  runaway requests on slow networks
- Social creation remains compatible with the registry's hard prohibition on
  automatic social-network publishing

### Security

- Remote input remains opt-in, session-bound, locally approved and visibly
  terminable; operating-system accessibility and capture permissions are never
  bypassed
- Deletion is recoverable, cannot target the sandbox root or escape it through
  path traversal/symlinks, and cannot inherit auto-approval or remembered
  approval
- Generated social artifacts carry a `requires_human_confirmation` manifest
  and no publishing connector or platform credential is invoked

## [0.4.0] - 2026-07-20

### Added

- A universal creator that turns one text or voice request into private,
  downloadable Markdown, Word, PDF, PowerPoint, static-site and executable
  full-stack project artifacts with manifests, structural validation and
  SHA-256 evidence
- Authenticated artifact downloads in chat, with tenant isolation and native
  save/share support for the iOS and Android companions
- First-class OSS telephony for inbound and outbound conversational calls via
  the tenant's own Twilio account, including consent records, exact
  destination/goal approval, signed webhooks, transcripts and Activity state
- Professional, coach, friend and romantic conversation styles; romantic tone
  requires adult confirmation and explicit consent, stays transparent about
  being AI, prohibits dependency tactics and can be exited immediately
- Provider-catalog model discovery so new Anthropic, Google AI and
  OpenAI-compatible connections select exact available quality/fast model IDs
  instead of depending only on aging hard-coded names

### Changed

- Compound creation requests route through one manifest-producing tool rather
  than loosely combining unrelated format generators
- The chat now persists safe file references from tool results and restores
  their download controls after a reload
- Phone calls continue in the same conversation and appear alongside missions,
  reminders and automations instead of introducing another primary module

### Fixed

- Streaming chat turns keep one request-scoped database transaction until the
  final SSE event, so assistant replies and artifact controls remain present
  after reload instead of silently losing the post-response writes
- Codex CLI 0.144+ nested `item.completed` messages are parsed correctly, and
  every invocation now runs ephemerally in an empty read-only workspace with
  internal execution/apps disabled so it cannot bypass Edecan's tool sandbox
- Credential setup shares one transaction between connector-account and vault
  writes, eliminating the PostgreSQL foreign-key race seen in a real browser
- Artifact UUIDs are converted to JSON-safe values before message history is
  stored, preserving generated-file buttons without serialization failures
- Model-catalog validation rejects successful HTTP responses that are not a
  usable JSON catalog, and generic compatible providers choose a smaller model
  for fast work when model sizes are advertised
- Artifact responses no longer expose arbitrary tool data through SSE; only
  validated UUID, filename and MIME references cross the public event contract
- Phone initiation is ordered so the durable call state is visible before a
  provider can deliver its first webhook

### Security

- Artifact downloads require authentication, re-check tenant ownership, stream
  through the API and use private/no-store, attachment and no-sniff headers
- Outbound calls require both prior recipient consent and a one-time human
  confirmation of the exact international number and purpose; tests use an
  injected provider and never place real calls
- Romantic style stores no birth date, cannot be inferred from memory or
  conversation text and clears both consent indicators on exit

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

[Unreleased]: https://github.com/bizagencysas/edecan-oss/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/bizagencysas/edecan-oss/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/bizagencysas/edecan-oss/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/bizagencysas/edecan-oss/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bizagencysas/edecan-oss/releases/tag/v0.1.0
