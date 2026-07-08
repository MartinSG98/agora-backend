# ADR 0002: Deterministic state machine as the moderator, not an LLM

## Status

Accepted

## Context

The common design for a debate platform assigns four LLM roles: two
debaters, a judge, and a moderator that controls turns, phases and timing.
But turn order and phase flow are not judgment calls — they are guarantees
the platform must uphold. An LLM moderator can be persuaded, confused, or
simply sample its way out of the rules.

## Decision

The moderator is `app/orchestrator/state_machine.py`: an immutable
`DebateProgress` dataclass with an explicit `advance()` transition function
(CREATED → OPENING → REBUTTAL×N → CLOSING → VERIFICATION → JUDGING →
COMPLETE). LLM agents are used only where judgment is genuinely required:
arguing, fact-checking, scoring.

## Consequences

- Turn order and phase flow are provable — unit-tested by walking the
  machine and asserting the exact trail.
- One fewer LLM call per phase transition: cheaper and faster.
- We lose "moderator color commentary"; if ever wanted, it can be a
  cosmetic narrator agent with zero authority over flow.
