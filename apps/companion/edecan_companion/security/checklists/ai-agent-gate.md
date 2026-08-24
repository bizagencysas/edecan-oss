# AI Agent Security Gate

Use after master `security.md` for any new model, MCP server, tool, RAG source or memory capability.

## Trust and authority

- [ ] Content/tool descriptions/results marked untrusted.
- [ ] External deterministic policy gate exists.
- [ ] User identity and tenant bound server-side.
- [ ] Model text cannot represent human approval.
- [ ] Read/write and staging/production capabilities separated.

## Tool/MCP

- [ ] Server/source/version/auth verified.
- [ ] Tool names, schemas, descriptions and side effects reviewed.
- [ ] Description/schema hash and change detection.
- [ ] Arguments strict and allowlisted.
- [ ] Per-tool scoped/short-lived credentials.
- [ ] Egress restricted.
- [ ] Cost/time/call limits.
- [ ] Audit and revocation path.

## Prompt/RAG/memory

- [ ] Direct and indirect injection tests.
- [ ] Tool-description/result injection tests.
- [ ] Canary exfiltration test.
- [ ] Tenant ACL before retrieval.
- [ ] Provenance and deletion propagation.
- [ ] Memory write gate, trust, TTL and rollback.
- [ ] No secrets in durable memory or prompt by default.

## Execution

- [ ] Browser domain controls and side-effect confirmation.
- [ ] Terminal/code sandbox, non-root and egress limits.
- [ ] Max steps/calls/retries/tokens/spend/delegation.
- [ ] Loop and scope-mismatch stop conditions.
- [ ] Kimi/GLM independent review for high-risk code.
- [ ] Deterministic tests prove no tool call/egress/state change on denied cases.
