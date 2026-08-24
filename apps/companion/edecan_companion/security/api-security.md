# API and Multi-Tenant Security Module

Load after master `security.md` for REST, GraphQL, WebSockets, webhooks and multi-tenant services.

## Inventory

Build an endpoint/action inventory from code, schema, clients, gateway and logs. Include legacy/admin/internal routes, WebSocket messages, jobs and webhooks.

## Identity and tenant rules

- Derive actor and tenant from validated server-side context.
- Never trust client `tenant_id`, `user_id`, `role`, ownership or price.
- Reauthorize at the operation that performs the effect.
- Bind jobs, cache keys, storage keys, vectors and WebSocket rooms to tenant.
- Revoke long-lived connections and async work when identity changes.

## Test matrix

For each resource and action, test:

- anonymous;
- own tenant member;
- own tenant admin;
- other tenant member/admin;
- removed/suspended user;
- expired/revoked token;
- service account with narrower scope;
- support/global role if present.

Actions: create, read, list, search, update, delete, export, share, upload/download, subscribe, invoke, retry and replay.

## API abuse classes

- BOLA/IDOR;
- property-level authorization/mass assignment;
- function-level authorization;
- resource consumption and business-flow abuse;
- SSRF;
- inventory/version gaps;
- unsafe third-party API consumption;
- duplicate/replay/race conditions;
- schema/parser ambiguities.

## WebSockets

Authenticate handshake, validate origin as appropriate, authorize every channel/message, bind immutable tenant context, handle token expiry/revocation, limit message size/frequency/subscriptions/fan-out, enforce backpressure and test cross-tenant broadcast.

## Webhooks

Verify raw-body signature, timestamp and replay protection; enforce idempotency; map provider resources to tenant server-side; validate schema; rotate keys; do not trust source IP alone.

## Required isolation evidence

Use two synthetic tenants with canary objects. A denied request must cause no read, write, notification, cache mutation, queue event, billing effect or timing leak of material value.

## Release gate

Any confirmed cross-tenant access, client-controlled tenant authority, admin function bypass, replayed financial mutation or unscoped API key blocks release.
