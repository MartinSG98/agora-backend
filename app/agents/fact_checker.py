"""Fact-checker agent.

Two-step verification, split between judgment and mechanism:

1. An LLM extracts cited factual claims from the transcript (judgment —
   what counts as a claim, which citation backs it).
2. Each citation is verified mechanically via the evidence server's
   verify_quote tool — the same server, and thanks to the research-note
   cache the same content, the debater actually read.

Claims without a citation are marked "uncited"; extraction failures
degrade to an empty finding list rather than failing the debate.
"""

import json
import time
from dataclasses import dataclass, field

from app.agents.llm import LLMProvider
from app.config import HardLimits
from app.mcp_client.manager import EVIDENCE, MCPManager

_SYSTEM = (
    "You extract factual claims from debate transcripts. Return ONLY a JSON"
    " object shaped {\"claims\": [{\"claim\": str, \"side\": \"pro\"|\"con\","
    " \"source_id\": str|null, \"quote\": str|null}]}.\n"
    "A claim is a checkable statement of fact (not opinion or rhetoric)."
    " When the speaker cites a source, e.g. (source: 1001), set source_id"
    " and quote the exact words the claim rests on. When a factual claim"
    " has no citation, set source_id and quote to null."
)


@dataclass
class FactCheckOutcome:
    claims: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    tool_calls: int = 0


class FactChecker:
    def __init__(self, provider: LLMProvider, mcp: MCPManager, limits: HardLimits,
                 model_id: str):
        self._provider = provider
        self._mcp = mcp
        self._limits = limits
        self.model_id = model_id

    async def check(self, transcript: str) -> FactCheckOutcome:
        outcome = FactCheckOutcome()
        started = time.perf_counter()

        response = await self._provider.generate(
            model_id=self.model_id,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": [{"text": f"<transcript>\n{transcript}\n</transcript>"}],
            }],
            tools=None,
            max_tokens=self._limits.max_response_tokens * 2,
            hint="fact_checker",
        )
        outcome.input_tokens = response.input_tokens
        outcome.output_tokens = response.output_tokens

        try:
            start, end = response.text.find("{"), response.text.rfind("}")
            claims = json.loads(response.text[start:end + 1])["claims"]
        except (ValueError, KeyError, TypeError):
            outcome.latency_ms = (time.perf_counter() - started) * 1000
            return outcome  # extraction failed: no findings, not a crash

        for claim in claims:
            if not isinstance(claim, dict) or "claim" not in claim:
                continue
            if claim.get("source_id") and claim.get("quote"):
                text, is_error = await self._mcp.call_tool(
                    EVIDENCE,
                    "verify_quote",
                    {"source_id": str(claim["source_id"]), "quote": claim["quote"]},
                )
                outcome.tool_calls += 1
                if is_error:
                    verdict = "unverifiable"
                else:
                    verdict = json.loads(text).get("verdict", "unverifiable")
            else:
                verdict = "uncited"
            outcome.claims.append({
                "claim": claim["claim"],
                "side": claim.get("side"),
                "source_id": claim.get("source_id"),
                "quote": claim.get("quote"),
                "verdict": verdict,
            })

        outcome.latency_ms = (time.perf_counter() - started) * 1000
        return outcome
