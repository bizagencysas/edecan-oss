# Governance

Edecan uses a maintainer-led governance model designed to keep decisions accountable while the contributor community grows. This document describes how technical, community, and release decisions are made.

## Principles

- Favor user safety, privacy, security, reliability, and backward compatibility.
- Make significant decisions in durable public artifacts whenever disclosure is safe.
- Welcome evidence-based disagreement and separate technical critique from personal criticism.
- Keep processes proportional to the risk and reversibility of a change.
- Avoid vendor or contributor interests overriding the health of the project.

## Roles

### Contributors

Anyone who reports issues, improves documentation, submits code, reviews changes, or helps other users is a contributor. Contributors are expected to follow the Code of Conduct and contribution guidelines.

### Maintainers

Maintainers have repository decision and review authority for the scopes listed in [MAINTAINERS.md](MAINTAINERS.md). Their responsibilities include:

- Reviewing and merging changes
- Protecting release and supply-chain integrity
- Maintaining compatibility and quality standards
- Coordinating security response
- Documenting significant decisions and conflicts of interest
- Enforcing community policies consistently

The repository permission system is the source of truth for current access. `MAINTAINERS.md` is the public record of project governance roles and should be updated when access changes.

## Decision making

Routine, reversible changes use lazy consensus: discussion remains open long enough for relevant maintainers and contributors to respond, and a maintainer may merge once concerns are resolved and required checks pass.

Changes to public interfaces, persisted data, security boundaries, licensing, governance, or major architecture require an explicit design discussion in an issue or pull request. The proposal should explain motivation, alternatives, compatibility, migration, operational risk, and rollback.

Maintainers seek consensus. When consensus is not possible, the maintainer responsible for the affected scope makes the final decision and records the rationale. Decisions may be revisited when new evidence emerges.

## Pull requests and reviews

- Authors must not approve their own changes as the only review when another qualified maintainer is available.
- Review depth should match the change's risk.
- Security-sensitive changes should minimize disclosure until a coordinated fix is ready.
- Generated files, dependency updates, and migrations require the same review discipline as source changes.
- A maintainer may close changes that are unsafe, out of scope, unmaintainable, or incompatible with project direction, with a concise rationale.

## Releases

Maintainers select release scope, verify required checks, prepare release notes, and publish artifacts through protected project infrastructure. Releases should use immutable version tags and document breaking changes and migration requirements.

No person should publish a release from an unreviewed or unverified working tree. Credentials and signing authority must remain limited to maintainers who need them.

## Security

Potential vulnerabilities must follow [SECURITY.md](SECURITY.md), not public issue discussion. Maintainers coordinate triage, remediation, disclosure, and credit while limiting sensitive information to people who need it.

## Becoming a maintainer

Maintainers may nominate contributors who demonstrate sustained technical judgment, constructive review, dependable follow-through, respect for security and privacy, and alignment with the Code of Conduct. Existing maintainers evaluate nominations based on demonstrated work rather than a fixed contribution count.

The decision and scope are documented in a pull request updating `MAINTAINERS.md`. Access should begin with the narrowest permissions needed and expand as responsibility grows.

## Inactivity and removal

A maintainer may step down at any time. Scope or access may be reduced after prolonged inactivity, inability to fulfill responsibilities, a conflict of interest, or a serious policy violation. Whenever safety and privacy permit, the reason is documented and the affected maintainer can respond.

## Conflicts of interest

Maintainers must disclose financial, employment, or personal interests that could materially affect a decision. A maintainer with a conflict should recuse themselves when another qualified maintainer can decide.

## Amending governance

Governance changes require a pull request that explains the problem and expected impact. They follow the explicit decision process above and take effect when merged.
