# Infrastructure, Docker, AWS and Supply-Chain Security Module

Load after master `security.md`.

## AWS

- Root protected, MFA and no daily use.
- Individual identities, roles and temporary credentials.
- Least-privilege IAM and trust policies; no unjustified wildcards.
- EC2 via SSM/Tailscale/private path; public SSH/RDP minimized.
- IMDSv2, encrypted EBS, scoped instance role and no secrets in user-data.
- Security Groups/databases private; egress considered.
- CloudTrail/config/change alerts and cost anomalies.
- Backups/restore and account/environment separation.

## Linux

- Supported OS and signed repositories.
- Key-based access, root login off, sudo minimal.
- Firewall default-deny inbound and listener inventory.
- Service users, systemd hardening and resource limits.
- Secrets/file permissions/log redaction.
- Time sync, auth/sudo/service auditing and central logs.
- Encrypted off-host backups with restore tests.

## Docker

- Minimal maintained base, pinned/reproducible builds and multi-stage.
- No secrets in layers/ARG/history.
- Non-root, read-only filesystem, dropped capabilities, no-new-privileges.
- No privileged mode, host network or Docker socket.
- Minimal volumes/networks, egress control and resource limits.
- SBOM, image/filesystem scan and digest-based promotion.
- Verify runtime user, ports, secrets and health semantics.

## GitHub/CI

- Protected branches/rulesets, CODEOWNERS and required checks.
- Workflow permissions minimal and OIDC for cloud.
- Pin third-party actions to full commit SHA.
- Never expose secrets to untrusted PR code; review `pull_request_target`.
- Isolate self-hosted runners.
- Protect environments and require approval for production.
- Scan secrets/dependencies; lockfiles and artifact provenance.

## Supply chain

- Verify package publisher, registry, lifecycle scripts and provenance.
- Prevent dependency confusion/typosquatting.
- Remove unused dependencies.
- Generate SBOM and link source commit/builder/artifact digest.
- Sign/verify artifacts when pipeline supports it.
- Promote the same artifact through environments.

## Remote change gate

DNS, WAF, firewall, IAM, Security Groups, production access/deploy, secret rotation and destructive restore/rollback require explicit approval with target, backup and rollback.
