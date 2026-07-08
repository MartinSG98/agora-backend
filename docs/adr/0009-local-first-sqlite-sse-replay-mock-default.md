# ADR 0009: Local-first — SQLite, SSE with event replay, mock mode default

## Status

Accepted

## Context

The "impressive" deployment (Step Functions, AgentCore, DynamoDB, API
Gateway WebSockets) costs real money, takes setup time before a single
debate runs, and makes the repo impossible to clone-and-run. A public
live demo has a second cost problem: strangers spending the owner's LLM
budget.

## Decision

- **SQLite** for persistence; the schema deliberately mirrors a DynamoDB
  layout so a cloud migration is a storage-adapter change.
- **SSE** (not WebSockets) for streaming — debate events flow one way,
  server to client, so SSE is the simpler correct tool.
- **Event replay**: every debate stores its full ordered event log; a
  stored debate replays through the same SSE endpoint with the exact
  event stream the live run produced. The public demo replays recorded
  debates — the live-looking experience at zero token cost.
- **Mock mode is the default** (`AGORA_MOCK_MODE=1`): a deterministic
  provider plays out a full debate, so a fresh clone runs the entire
  system — MCP servers included — with no AWS account.

The AWS deployment (and a Terraform module describing it) is roadmap: an
infrastructure-as-code showcase, potentially `terraform plan`-only.

## Consequences

- Anyone can run the whole stack in one command; CI needs no secrets.
- Real-model runs are an explicit opt-in (`AGORA_MOCK_MODE=0`), keeping
  spend intentional.
- No horizontal scaling story in v1 — acceptable and documented.
