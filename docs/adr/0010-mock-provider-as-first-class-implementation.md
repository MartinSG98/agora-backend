# ADR 0010: Mock provider as a first-class LLM implementation

## Status

Accepted

## Context

Developing and testing a multi-agent system against real models is
expensive (every debug run burns tokens), non-deterministic (no stable
assertions), and gate-keeps the repo behind an AWS account. Monkey-patching
the Bedrock client in tests would solve none of this for local runs or
demos.

## Decision

`MockProvider` implements the same `LLMProvider` interface as
`BedrockProvider` and is selected by configuration (`AGORA_MOCK_MODE=1`,
the default) — ports-and-adapters, not test-time patching.

Only the LLM call is faked. A mock debate runs the real state machine,
real MCP stdio round-trips (evidence server in offline fixture mode), real
quota enforcement, real events, and real persistence. To keep the MCP path
honest, the mock debater's opening turn is scripted to emit a
`search_sources` tool call before speaking. The mock judge returns JSON
that must pass the same schema validation as a real judge's output, and
the scripted statements include one deliberately unsupported claim so the
fact-checking path has real work to do.

## Consequences

- Full end-to-end debates cost $0, run in seconds, and are deterministic —
  the e2e test can assert exact outcomes.
- Fresh clones run the entire system with no AWS credentials; CI needs no
  secrets.
- The only unexercised surface is the Bedrock request format itself,
  covered by an occasional cheap live run.
- Scripted content must be kept consistent with the evidence fixtures and
  the judge schema when either changes.
