# Architecture Decision Records

Significant design decisions for Agora, one file each. See
[ADR 0001](0001-record-architecture-decisions.md) for the convention.

| # | Decision |
|---|----------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions |
| [0002](0002-deterministic-state-machine-as-moderator.md) | Deterministic state machine as the moderator, not an LLM |
| [0003](0003-hard-limits-in-code-not-prompts.md) | Hard limits enforced in code, never in prompts |
| [0004](0004-bedrock-converse-api-for-llm-access.md) | AWS Bedrock Converse API (boto3) for LLM access |
| [0005](0005-hand-rolled-agent-loop-with-mcp-sdk.md) | Hand-rolled agent loop with the official MCP SDK |
| [0006](0006-two-mcp-servers-tools-vs-resources.md) | Two MCP servers — evidence (tools) vs rules (resources/prompts) |
| [0007](0007-debater-memory-research-notebook.md) | Debater memory — private research notebooks, no vector store |
| [0008](0008-blind-judging-and-position-swap.md) | Blind judging and position-swap evaluation |
| [0009](0009-local-first-sqlite-sse-replay-mock-default.md) | Local-first: SQLite, SSE with replay, mock mode default |
| [0010](0010-mock-provider-as-first-class-implementation.md) | Mock provider as a first-class LLM implementation |
