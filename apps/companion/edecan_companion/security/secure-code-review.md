# Secure Code Review Module

Load after master `security.md`. Review complete flows and invariants, not isolated suspicious lines.

## Review order

1. Entry points and untrusted inputs.
2. Identity and tenant derivation.
3. Authorization decision.
4. Transformation/parsing/canonicalization.
5. Sensitive operation or data access.
6. Error/retry/concurrency behavior.
7. Logging and response.
8. Tests and deployment configuration.

## Diff review

For every changed file ask:

- What new input or authority exists?
- What trusted assumption changed?
- Does a client-controlled field affect identity, tenant, role, price, path, URL, query, command or tool?
- Does the change create a new public route, RPC, WebSocket message, job, webhook or MCP tool?
- Does caching/retry/background execution change authorization timing?
- Are secrets/config/dependencies/build scripts affected?
- Is there a regression test for malicious and legitimate cases?

## Sink checklist

Trace untrusted data into:

- SQL/ORM/raw query;
- shell/process execution;
- filesystem path;
- URL fetch/redirect;
- HTML/template/markdown/SVG;
- deserializer/parser;
- dynamic import/eval/plugin;
- log/header/cookie;
- auth/role/tenant policy;
- cache/object/vector key;
- queue/workflow;
- model prompt/memory/tool argument;
- cloud API call;
- financial mutation.

## Review quality rules

- Never approve based only on type checking.
- Never replace a missing invariant with a comment.
- Do not accept broad `try/catch` or ignore directives as remediation.
- Prefer central policy plus endpoint-level enforcement and tests.
- Verify generated code against current official APIs.
- Verify actual lockfile and deployed artifact.
- For high-risk AI-written diffs, use a different model as critic and deterministic tests as arbiter.

## Required output

For each meaningful issue provide file/line, flow, exploit preconditions, safe evidence, root cause, minimal correction, bypass tests and residual risk.
