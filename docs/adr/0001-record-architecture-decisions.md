# ADR 0001: Record architecture decisions

## Status

Accepted

## Context

Agora is a multi-agent system with several deliberate, non-obvious design
choices (deterministic moderator, code-enforced limits, blind judging).
Without a record, the reasoning behind them is lost and the codebase reads
as arbitrary.

## Decision

Keep Architecture Decision Records in `docs/adr/`, numbered sequentially,
one decision per file. Format: Status, Context, Decision, Consequences.
A superseded ADR is never deleted — its status changes and it links to the
successor.

## Consequences

Design reasoning is reviewable alongside the code it explains. Small
overhead per significant decision.
