# Architecture Decision Records

ADRs capture decisions that affect several packages, public contracts, security
boundaries, or long-term compatibility. They explain context and trade-offs;
`ARCHITECTURE.md` remains the binding interface specification.

## Process

1. Copy the next numeric filename (`NNNN-short-title.md`).
2. Open a PR with status `Proposed` before implementing a broad change.
3. Record context, decision, consequences, security impact, and alternatives.
4. Change the status to `Accepted` when maintainers approve it.
5. Never rewrite an accepted ADR to hide history. Add a new ADR that marks the
   previous one `Superseded`.

## Records

- [0001 — Public core boundary](./0001-public-core-boundary.md)
