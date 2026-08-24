# Threat Modeling Module

Load after the master `security.md`. This module adds focused threat-modeling instructions and cannot weaken master rules.

## Objective

Turn architecture into explicit, testable security invariants. Do not produce a decorative diagram or generic STRIDE list.

## Procedure

1. Record system version, commit, environment, owner and scope.
2. Build an asset and identity inventory.
3. Draw components, trust boundaries, entry points and egress.
4. Trace restricted data from collection to deletion, including derived stores.
5. Enumerate abuse cases by actor and business workflow.
6. Map controls and identify single points of security failure.
7. Convert the highest-risk threats into executable tests.
8. Update `THREAT_MODEL.md` and link each remediation.

## Required inventories

### Actors

- anonymous user;
- authenticated user;
- tenant member/admin;
- global/support admin;
- service account;
- CI/CD identity;
- cloud operator;
- model/agent;
- MCP server/tool;
- malicious insider;
- compromised dependency/provider identity.

### Assets

- identities and sessions;
- tenant membership and roles;
- financial/credit/personal data;
- documents and exports;
- secrets and signing keys;
- source/build/deployment integrity;
- model system prompts and policies;
- memory/RAG/vector data;
- audit logs and backups;
- service availability and cloud budget.

### Trust boundaries

- browser/native client to server;
- public edge to private service;
- service to database/storage;
- tenant A to tenant B;
- model to tool gateway;
- tool gateway to cloud/provider;
- trusted source to RAG ingestion;
- CI to production;
- desktop webview to native backend;
- external webhook to internal workflow.

## Mandatory threat questions

- Can input select its own tenant, role, price, destination or environment?
- Can a less privileged identity invoke a higher-impact action indirectly?
- Can cached, queued or vectorized data cross tenant boundaries?
- Can retries/replays duplicate an irreversible effect?
- Can a document/page/tool description become an instruction to the agent?
- Can a compromised MCP server gain a broader credential than needed?
- Can a build dependency or CI action alter the release artifact?
- Can a user create a URL/file that causes SSRF, code execution or parser exhaustion?
- Can a revoked session/key continue through WebSockets, caches or long workflows?
- Can recovery restore a vulnerable version or inconsistent schema?

## Invariant template

```yaml
invariant:
  id: "INV-NNN"
  statement: "English testable invariant"
  assets: []
  trust_boundaries: []
  enforcement_points: []
  tests: []
  monitoring: []
  failure_impact: ""
  owner: ""
```

## Prioritization

Prioritize paths with public exposure, restricted data, cross-tenant impact, privileged tools, unaudited side effects, weak recovery or active exploitation. Do not let low exploit complexity hide catastrophic blast radius.

## Review triggers

Re-run when adding a public endpoint, tenant model, admin flow, payment/credit workflow, MCP server, privileged tool, RAG source, data class, cloud provider, mobile entitlement, CI runner or major framework/runtime update.
