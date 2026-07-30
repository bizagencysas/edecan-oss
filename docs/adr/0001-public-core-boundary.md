# ADR 0001: Public core boundary

- Status: Accepted
- Date: 2026-07-20

## Context

The first public export was created from a larger private workspace. It omitted
commercial and hosted-deployment directories, but the public workspace,
documentation, tests, and scripts still referenced them. A fresh clone could
not resolve the Python workspace and several documented paths did not exist.

An open-source repository must be understandable, buildable, testable, and
licensed without access to private sibling code.

## Decision

Everything versioned in this repository is Apache-2.0 and forms a standalone
public core. Optional private extensions:

- are not workspace members or test requirements;
- are detected through guarded runtime plugin boundaries;
- do not own public contracts required to boot the core;
- are not linked as if their source were present;
- have their own licensing and security process outside this repository.

Public self-host assets may live in `infra/docker`; private hosted topology and
credentials remain outside the repository. Tests that validate an optional
extension add those cases only when the extension is installed.

## Consequences

- A clean clone can run `uv sync --all-packages --frozen` and the full public
  suite without private files.
- The public lockfile represents only public packages.
- Contributors receive Apache-2.0 terms for every versioned path.
- Documentation must describe optional external extensions as unavailable here,
  never as a directory a contributor can modify.
- CI must fail on new missing workspace members or broken local links.

## Security impact

The boundary reduces accidental publication of proprietary code and secrets,
and prevents private-package imports from disabling public security tests.
Plugin loading remains fail-closed: absence of an extension must not expose its
routes, tools, flags, or worker behavior.

## Alternatives considered

- **Keep placeholder directories:** rejected because empty proprietary stubs
  create ambiguous licensing and can conceal runtime coupling.
- **Publish every private component:** rejected because open-sourcing scope is a
  deliberate maintainer decision, not a build workaround.
- **Separate the core into another repository:** deferred; the monorepo remains
  useful while its public boundary stays reproducible.
