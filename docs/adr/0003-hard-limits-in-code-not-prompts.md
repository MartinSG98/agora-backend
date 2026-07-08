# ADR 0003: Hard limits are enforced in code, never in prompts

## Status

Accepted

## Context

Debates have resource rules: rebuttal round counts, response token caps,
a quota of evidence requests per debater per phase. The tempting
implementation is a prompt line ("please do not exceed three evidence
requests"). LLMs eventually ignore such instructions — under pressure from
a long context, an adversarial topic, or plain sampling variance.

## Decision

All limits live in `app/config.py` (`HardLimits`) and are enforced by the
orchestration layer:

- rebuttal rounds: format requests are clamped by
  `clamp_rebuttal_rounds()` — a format file is data, not an authority
- response tokens: passed to Bedrock as `inferenceConfig.maxTokens`
- evidence quota: counted in the agent tool loop; over-quota tool calls
  are rejected with an explanatory tool result instead of being executed
- tool-loop iterations: hard cap prevents infinite tool-call loops

Prompts may *mention* the rules so agents behave sensibly, but no rule
depends on the model choosing to comply.

## Consequences

- Cost and runtime per debate are bounded regardless of model behavior.
- Rule compliance is testable without any LLM.
- Slightly more orchestration code than a prompt-only approach.
