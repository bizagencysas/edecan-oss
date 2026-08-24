# Incident Triage Checklist

Use only after master `security.md` enters `INCIDENT` mode.

## Declare

- [ ] Incident ID and provisional severity.
- [ ] Trusted communication channel.
- [ ] UTC timeline started.
- [ ] Known facts, hypotheses and unknowns separated.

## Preserve

- [ ] Cloud/app/auth/WAF/access logs.
- [ ] Git/GitHub/CI/deployment history.
- [ ] IAM/users/roles/keys/sessions.
- [ ] Host/container/process/network state.
- [ ] Database/object storage audit/snapshots.
- [ ] Agent/MCP/tool/model traces with redaction.
- [ ] Suspicious files hashed.
- [ ] Chain of custody recorded.

## Contain

- [ ] Active path identified.
- [ ] Least-destructive containment options prepared.
- [ ] Required production approvals obtained.
- [ ] Specific sessions/keys/accounts isolated or revoked.
- [ ] Third-party targets excluded.
- [ ] Stop conditions monitored.

## Scope

- [ ] Initial access.
- [ ] Privilege escalation/lateral movement/persistence.
- [ ] Assets and tenants affected.
- [ ] Data viewed/modified/exfiltrated.
- [ ] CI/CD/supply-chain integrity.
- [ ] Backup/log integrity.
- [ ] Remaining attacker access.

## Recover

- [ ] Root cause/persistence removed.
- [ ] Credentials/sessions handled.
- [ ] Known-good source/artifact validated.
- [ ] Gradual recovery and monitoring.
- [ ] Critical business/auth/tenant flows tested.
- [ ] Evidence retained.
- [ ] `INCIDENT_REPORT.md` generated.
