# Release Security Gate

Use after master `security.md`.

## Identity and scope

- [ ] Commit/artifact/environment identified.
- [ ] Production action, if any, has explicit approval.
- [ ] In-scope and out-of-scope components recorded.
- [ ] Sensitive data and tenant impact classified.

## Research and dependencies

- [ ] Exact runtime/framework/package versions identified.
- [ ] Current official advisories checked.
- [ ] Lockfile reviewed and reproducible.
- [ ] New/changed packages, scripts and GitHub Actions verified.
- [ ] SCA/SBOM/provenance completed as applicable.

## Code and architecture

- [ ] Diff reviewed for new trust boundaries/authority.
- [ ] Input validation and output encoding tested.
- [ ] Auth, authorization and tenant isolation tested.
- [ ] Secrets scan ran on intended scope/history.
- [ ] SSRF/uploads/webhooks/queues/caches reviewed when touched.
- [ ] AI/MCP/tool/memory/RAG controls tested when touched.
- [ ] Mobile/desktop native boundaries reviewed when touched.

## Verification

- [ ] Original security regression fails safely.
- [ ] Legitimate behavior still works.
- [ ] Bypass variants tested.
- [ ] Actual generated outputs opened/inspected.
- [ ] Logs/alerts and no-side-effect behavior verified.
- [ ] Independent critic reviewed high-risk AI-written code.
- [ ] Staging artifact matches reviewed commit.

## Operations

- [ ] Backup/restore point.
- [ ] Migration compatibility.
- [ ] Rollback artifact/process.
- [ ] Monitoring and rollback thresholds.
- [ ] Approval-required actions listed.

## Verdict

- [ ] `APPROVED`
- [ ] `APPROVED_WITH_RISKS` with owners/dates
- [ ] `BLOCKED` with exact unblock path
