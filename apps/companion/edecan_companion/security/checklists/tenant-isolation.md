# Tenant Isolation Checklist

Use after master `security.md`.

## Context source

- [ ] Tenant derived from authenticated server context.
- [ ] Membership/status checked on organization switch.
- [ ] No client-controlled tenant/role/ownership authority.
- [ ] Jobs/WebSockets/tools carry server-bound context.

## Database

- [ ] Tenant key on owned tables.
- [ ] Composite foreign/unique constraints where needed.
- [ ] Every read/write/list/search/export scoped.
- [ ] App role is not owner/superuser.
- [ ] RLS policies and bypass roles tested if used.
- [ ] Migrations/backfills preserve tenant ownership.

## Other storage/processing

- [ ] Cache keys.
- [ ] R2/object prefixes and presigned URLs.
- [ ] KV/Durable Object IDs.
- [ ] Queues/workflows.
- [ ] Vector/RAG/search namespaces and ACL.
- [ ] Logs/exports/backups.
- [ ] Feature flags/rate limits/webhooks.

## Test identities

- [ ] Tenant A member/admin.
- [ ] Tenant B member/admin.
- [ ] Removed/suspended identity.
- [ ] Support/global role if present.
- [ ] Synthetic canary data only.

## Actions tested

- [ ] Create/read/list/search/update/delete.
- [ ] Export/share/upload/download.
- [ ] WebSocket subscribe/broadcast.
- [ ] Queue/job/retry/replay.
- [ ] RAG retrieval/memory.
- [ ] Admin/support impersonation.
- [ ] No side effects on denied requests.

Any confirmed cross-tenant access is release-blocking.
