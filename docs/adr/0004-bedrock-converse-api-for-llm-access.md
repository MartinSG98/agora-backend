# ADR 0004: AWS Bedrock Converse API (boto3) for LLM access

## Status

Accepted

## Context

Agora's evaluation angle depends on genuinely different models competing
(e.g. Claude vs Amazon Nova) and on per-role model configuration. Options
considered:

1. Anthropic API directly — simplest SDK, but all agents are Claude
   variants, which weakens model-vs-model comparison.
2. Anthropic-native Bedrock client — first-class Claude support, but
   serves only Claude models.
3. Bedrock **Converse API** via boto3 — one uniform request/response and
   tool-use shape across vendors (Claude, Nova, Llama, ...).

## Decision

Use the Bedrock Converse API through boto3. Model IDs live in a registry
in `app/config.py` mapping friendly names to cross-region inference
profiles; debates reference friendly names, so models are swappable per
role per debate. A deterministic `MockProvider` implements the same
provider interface for tests and zero-cost local runs.

## Consequences

- True cross-vendor leaderboards become possible.
- MCP tool schemas convert cleanly to Converse `toolSpec` once, for all
  vendors.
- boto3 is synchronous — calls are wrapped for asyncio.
- Anthropic-only features (prompt caching etc.) are not used; acceptable
  for short debate transcripts.
