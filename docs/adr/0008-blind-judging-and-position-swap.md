# ADR 0008: Blind judging and position-swap evaluation

## Status

Accepted

## Context

The naive judge implementation — "here is the transcript, who won?" — is
subjective and manipulable. Known LLM-judge biases include identity bias
(learning that "Debater A is always model X"), position bias (the
proposition side may be inherently easier to argue), and order bias (the
transcript order influencing the verdict).

## Decision

Three countermeasures, all cheap because they are transcript
transformations and re-runs:

1. **Blind judging** — before the transcript reaches the judge, sides are
   relabeled Participant X / Participant Y with a random assignment
   (seeded per debate for reproducibility). The judge never sees model
   names or pro/con labels. The mapping is persisted for de-anonymization
   in the results view.
2. **Structured rubric** — the judge must return schema-validated JSON
   scoring each participant 0–10 per rubric category (read from the rules
   MCP server), plus winner, confidence, and reasoning. Invalid output is
   retried once with the validation error attached.
3. **Position swap** — an evaluation mode runs the same topic twice with
   models exchanging sides. If the "winner model" flips with the side,
   the topic has position bias; that is reported instead of a bogus
   model ranking.

## Consequences

- Verdicts come with an auditable score breakdown, not vibes.
- Judge-bias findings become a README selling point (numbers, not claims).
- Position swap doubles token cost for evaluation runs — it is a separate
  opt-in mode, not the default single-debate flow.
