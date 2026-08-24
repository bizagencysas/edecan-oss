# Incident Response Module

Load after master `security.md` when compromise, attack, leakage or suspicious privileged activity is reported.

## Priority

Preserve evidence, reduce harm, establish scope, eradicate root cause and recover trustworthy operation. Do not “clean up” before evidence preservation.

## First actions

1. Declare incident ID, provisional severity and UTC timeline.
2. Use trusted/out-of-band communication if needed.
3. Preserve logs, configs, deployment history, IAM, sessions, tool/agent traces and suspicious files.
4. Hash exported evidence and record collector/method.
5. Identify active path and assets at risk.
6. Prepare least-destructive containment actions.
7. Request explicit approval for production blocking, revocation, rotation, network changes or rollback unless incident-specific autonomous authority exists.

## Containment options

- revoke exact session/token/key;
- disable compromised identity/integration/MCP server;
- isolate host/service;
- restrict route/SG/WAF;
- pause workflow/tool;
- switch to known-good deployment;
- preserve forensic snapshot before rebuild.

## Scope questions

- Initial access and earliest evidence?
- Identities/keys/sessions used?
- Privilege escalation/lateral movement/persistence?
- Assets/tenants/data accessed, changed or exported?
- CI/CD, dependency or artifact integrity affected?
- Backups/logs altered?
- Attacker access still active?
- Evidence of exfiltration, and what visibility gaps exist?

## Recovery

Rebuild from trusted source/artifact when integrity is uncertain; rotate/revoke compromised material; validate tenant isolation, auth and critical workflows; deploy gradually; increase monitoring; retain evidence.

## Required output

Generate `INCIDENT_REPORT.md` with confirmed facts, hypotheses, unknowns, timeline, evidence, affected assets/tenants/data, containment, eradication, recovery, root cause, notification review and corrective actions.

## Stop conditions

Stop automation if scope expands, target becomes third-party, evidence integrity is threatened, destructive action is required, output exposes secrets or action may notify attacker unexpectedly.
