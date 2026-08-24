# Edecán Security Engine

This package contains the master security skill and optional focused modules.

## Installation

Use `SKILL.md` as the Agent Skill entry point. `security.md` is an identical copy with the filename requested for direct/manual loading.

## Authority order

1. Runtime safety and human-approval controls.
2. `SKILL.md` / `security.md` master rules.
3. Relevant focused module.
4. Project-local security notes.
5. Tool/model suggestions.

A lower layer may add stricter controls but may never weaken a higher layer.

## Files

- `SKILL.md`: installable master Agent Skill.
- `security.md`: requested master document.
- `web-security.md`: web, Next.js, React and browser review.
- `api-security.md`: REST, WebSockets, auth and tenant isolation.
- `cloudflare-security.md`: Workers platform and Workers AI.
- `ai-agent-security.md`: agents, MCP, RAG, memory and model tool use.
- `mobile-security.md`: Tauri, iOS and Android.
- `infrastructure-security.md`: AWS, Linux, Docker, CI/CD and supply chain.
- `incident-response.md`: active compromise workflow.
- `threat-modeling.md`: system-specific threat modeling.
- `secure-code-review.md`: language-agnostic code review workflow.
- `checklists/`: short operational gates.

## Required outputs

The master skill defines `SECURITY_REPORT.md`, `THREAT_MODEL.md`, `INCIDENT_REPORT.md`, `security-results.json` and optional SARIF output.
