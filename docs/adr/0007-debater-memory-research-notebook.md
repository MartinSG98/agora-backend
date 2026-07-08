# ADR 0007: Debater memory — private research notebooks, no vector store

## Status

Accepted

## Context

During a turn, a debater's evidence-tool results live in the tool loop's
message list (working memory). If agents are fully stateless between
turns, that research evaporates when the turn ends: the debater re-searches
the same topic in the next round, burns its evidence quota again, and can
lose track of the source_ids it already cited — the classic agent-amnesia
problem.

Common fixes are summarization memory, vector-store/RAG retrieval, or
agent-written scratchpads. A debate is ~6–8 turns and a handful of
sources; the entire research corpus fits in context, so retrieval
machinery would be infrastructure without benefit.

## Decision

Each debater gets a private **research notebook**, persisted in the
`research_notes` table keyed `(debate_id, side)`:

- **Capture** is deterministic code ("background memory"): after each
  turn the orchestrator extracts the turn's tool results — sources read,
  quotes verified — and writes them down. No extra LLM call, no latency.
- **Reinjection**: the next turn's prompt includes the notebook in a
  tagged section (`<your_research_notes>`), clearly separated from the
  public transcript.
- **Privacy**: side A never sees side B's notes — only B's public
  statements. Each side builds its own case file.
- The judge has **no memory at all** across debates, by design (bias).

Cross-debate long-term memory (opponent history, ELO, strategy learning)
is roadmap, not v1 — that is where tagging/retrieval would earn its keep.

## Consequences

- No repeated research; quota spends once per source.
- Fact-checker verifies quotes against the same content the debater read
  (the notebook doubles as an evidence cache).
- Deliberately not "impressive" memory infrastructure — right-sizing over
  cargo-culting a vector DB, and the README says so.
- Optional later: a hot-path strategy scratchpad (agent writes itself a
  memo per turn) — adds tokens, enables a nice UI reveal.
