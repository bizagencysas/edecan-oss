# Cloudflare Security Module

Load after master `security.md`. Verify current Cloudflare docs, account capabilities, CLI version and compatibility dates before changes.

## Workers

- Inventory custom domains, routes, `workers.dev`, preview URLs and environments.
- Secrets via current encrypted secret mechanisms, not plaintext vars/config/source.
- API tokens scoped to exact account/resource/action.
- Separate bindings/resources for dev, staging and production.
- Validate auth in final Worker/RPC/DO operation.
- Restrict egress and protect against SSRF/redirect escape.
- Bound request size, CPU, subrequests, model tokens and cost.
- Review cache keys/private responses and source maps.
- Gradual deployment and tested rollback.

## Workers AI

- Model is not an authorization layer.
- Redact/minimize PII and secrets before prompts.
- Limit tokens, calls, retries, spend and tenant usage.
- Structured output with deterministic schema/policy checks.
- Tool calls through least-privilege gateway and approval rules.
- Test direct/indirect prompt injection, output injection, excessive agency and fallback drift.
- Guardrail/WAF products are additive controls, not a replacement for app policy.

## Durable Objects

- Object ID is not authorization.
- Authorize each public/RPC method/message.
- Minimize public methods and validate schemas.
- Tenant-safe IDs/namespaces/storage.
- Idempotency and transaction invariants.
- Retry only safe/idempotent operations; handle overload without retry storms.
- Forward/backward compatibility during global rollout.
- WebSocket identity restoration and revocation.

## D1

- Parameterized queries.
- Explicit tenant filter and constraints.
- Versioned migrations, backups/restore strategy and environment separation.
- Validate current transaction/limit/restore semantics.

## R2

- Private bucket by default.
- Presigned URLs are bearer credentials: narrow object/action/expiry and never log signatures.
- Validate upload ownership, size, magic bytes and content after upload.
- Safe serving headers, quarantine/scanning and lifecycle.
- CORS is not authorization.

## KV

- Avoid for immediate revocation/strong uniqueness without compensating design.
- Tenant/environment-safe keys, TTL, versioned values and cache-poisoning review.

## Queues and Workflows

- Schema/version/tenant/event IDs.
- No secrets in messages/state.
- Idempotent consumers/steps, bounded retries, DLQ and compensation.
- Human approval for production-capable or destructive steps.
- Replay/resume tests and audit trail.

## WAF, Rate Limiting and Access

- Origin cannot bypass edge controls.
- WAF is compensating defense, not root-cause fix.
- Rate limit by identity/tenant/key/cost, not only IP.
- Test rules, exceptions and false positives before blocking.
- Access policies deny-by-default with protected admin paths and service-token lifecycle.
- Remote changes require explicit approval and rollback.

## MCP on Cloudflare

- Server/tool allowlist, auth, schema and description change detection.
- Tool descriptions/results untrusted.
- Per-tool egress and credentials.
- Human approval for high-impact side effects.
- Tenant context server-bound.
