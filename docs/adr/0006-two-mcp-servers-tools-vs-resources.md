# ADR 0006: Two MCP servers — evidence (tools) and rules (resources/prompts)

## Status

Accepted

## Context

MCP has two consumption models: *tools* (model-invoked actions) and
*resources/prompts* (application-read data and templates). A single
kitchen-sink server would blur which agent may do what, and would
demonstrate only half the spec.

## Decision

Two servers under `mcp-servers/`, each mapped to a consumer:

- **evidence** exposes tools (`search_sources`, `get_source_content`,
  `verify_quote`) — used by debaters and the fact-checker. Both debaters
  connect to the same server, so neither side has privileged evidence
  access.
- **rules** exposes resources (`debate://formats/*`,
  `debate://rubrics/default`, `debate://fallacies/catalogue`) and prompt
  templates — read by the judge and orchestrator.

Both live in this repo (simpler for a portfolio project) but are
standalone-runnable for MCP Inspector demos. The evidence server has an
offline fixture mode (`AGORA_EVIDENCE_OFFLINE=1`) so tests and mock runs
never touch the network.

## Consequences

- Capability boundaries are structural: a debater session simply has no
  rules-server tools to call.
- Demonstrates both halves of the MCP spec with a real reason for each.
- Two subprocesses to manage in the backend lifespan.
