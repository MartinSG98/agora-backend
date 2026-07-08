"""Judge agent (ADR 0008).

Blind transcript in, schema-validated verdict out. The judge reads its
rubric and the fallacies catalogue from the rules MCP server (resources),
scores both anonymised participants per category, and must return pure
JSON. Invalid output is retried once with the validation error attached —
after that the debate fails rather than accepting an unscored verdict.
"""

import json
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.agents.llm import LLMProvider
from app.config import HardLimits
from app.mcp_client.manager import RULES, MCPManager

PARTICIPANTS = ("participant_x", "participant_y")


class JudgeVerdict(BaseModel):
    winner: Literal["participant_x", "participant_y", "draw"]
    confidence: float = Field(ge=0.0, le=1.0)
    scores: dict[str, dict[str, int]]
    decisive_moment: str
    reasoning_summary: str


class JudgeError(Exception):
    pass


@dataclass
class JudgeOutcome:
    verdict: JudgeVerdict
    input_tokens: int
    output_tokens: int
    latency_ms: float
    attempts: int


def _extract_json(text: str) -> dict:
    """Parse the first JSON object in the text (tolerates markdown fences)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in judge output")
    return json.loads(text[start : end + 1])


def _validate_scores(verdict: JudgeVerdict, categories: set[str]) -> None:
    if set(verdict.scores) != set(PARTICIPANTS):
        raise ValueError(f"scores must cover exactly {PARTICIPANTS}")
    for participant, scores in verdict.scores.items():
        if set(scores) != categories:
            raise ValueError(
                f"{participant} must score exactly the rubric categories"
                f" {sorted(categories)}, got {sorted(scores)}"
            )
        for category, value in scores.items():
            if not 0 <= value <= 10:
                raise ValueError(f"{participant}.{category} out of range: {value}")


class Judge:
    def __init__(self, provider: LLMProvider, mcp: MCPManager, limits: HardLimits,
                 model_id: str):
        self._provider = provider
        self._mcp = mcp
        self._limits = limits
        self.model_id = model_id

    async def _system_prompt(self) -> tuple[str, set[str]]:
        rubric = json.loads(
            await self._mcp.read_resource(RULES, "debate://rubrics/default")
        )
        fallacies = json.loads(
            await self._mcp.read_resource(RULES, "debate://fallacies/catalogue")
        )
        categories = set(rubric["categories"])

        category_lines = "\n".join(
            f"- {name} (weight {spec['weight']}): {spec['description']}"
            for name, spec in rubric["categories"].items()
        )
        fallacy_names = ", ".join(f["name"] for f in fallacies["fallacies"])
        example = {
            "winner": "participant_x | participant_y | draw",
            "confidence": 0.0,
            "scores": {p: {name: 0 for name in rubric["categories"]}
                       for p in PARTICIPANTS},
            "decisive_moment": "...",
            "reasoning_summary": "...",
        }
        system = (
            "You are an impartial debate judge. The participants are"
            " anonymised as participant_x and participant_y; judge only what"
            " is in the transcript.\n"
            f"Score each participant 0-10 in every category:\n{category_lines}\n"
            f"Penalise logical fallacies ({fallacy_names}) under"
            " logical_consistency, and unsupported or contradicted claims"
            " under evidence_quality.\n"
            "Respond with ONLY a JSON object in exactly this shape, no"
            f" markdown, no commentary:\n{json.dumps(example)}"
        )
        return system, categories

    async def judge(
        self,
        topic: str,
        blinded_transcript: str,
        claim_verdicts: list[dict] | None = None,
    ) -> JudgeOutcome:
        system, categories = await self._system_prompt()

        fact_check_section = ""
        if claim_verdicts:
            findings = "\n".join(
                f"- \"{c['claim']}\" -> {c['verdict']}" for c in claim_verdicts
            )
            fact_check_section = (
                "\n\n<fact_check_findings>\n"
                f"{findings}\n"
                "</fact_check_findings>"
            )

        user_prompt = (
            f"Motion: \"{topic}\"\n\n"
            "<transcript>\n"
            f"{blinded_transcript}\n"
            "</transcript>"
            f"{fact_check_section}\n\n"
            "Deliver your verdict as JSON."
        )

        messages = [{"role": "user", "content": [{"text": user_prompt}]}]
        input_tokens = output_tokens = 0
        started = time.perf_counter()
        last_error: Exception | None = None

        attempts_allowed = 1 + self._limits.judge_retries
        for attempt in range(1, attempts_allowed + 1):
            response = await self._provider.generate(
                model_id=self.model_id,
                system=system,
                messages=messages,
                tools=None,
                # verdict JSON is denser than a debate statement
                max_tokens=self._limits.max_response_tokens * 2,
                hint="judge",
            )
            input_tokens += response.input_tokens
            output_tokens += response.output_tokens

            try:
                verdict = JudgeVerdict.model_validate(_extract_json(response.text))
                _validate_scores(verdict, categories)
                return JudgeOutcome(
                    verdict=verdict,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attempts=attempt,
                )
            except (ValueError, ValidationError) as exc:
                last_error = exc
                # retry with the validation error in context
                messages.append(
                    {"role": "assistant", "content": [{"text": response.text}]}
                )
                messages.append({
                    "role": "user",
                    "content": [{
                        "text": f"Your output was invalid: {exc}. Respond"
                                " again with ONLY the corrected JSON object."
                    }],
                })

        raise JudgeError(f"judge produced invalid output after"
                         f" {attempts_allowed} attempts: {last_error}")
