"""LLM providers: real Bedrock and the deterministic mock.

The canonical message format throughout the backend is the Bedrock
Converse shape, so no translation layer is needed on the real path:

    messages: [{"role": "user"|"assistant", "content": [<block>, ...]}]
    block:    {"text": str}
            | {"toolUse": {"toolUseId", "name", "input"}}
            | {"toolResult": {"toolUseId", "content": [{"text": str}],
                              "status": "success"|"error"}}

Both providers implement ``generate()`` with the same signature; which one
runs is a config decision (AGORA_MOCK_MODE), not a code path difference —
see ADR 0010.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    input_tokens: int
    output_tokens: int
    # Full assistant content blocks, appended verbatim to the conversation
    # when the tool loop continues.
    raw_content: list = field(default_factory=list)


class LLMProvider(Protocol):
    async def generate(
        self,
        *,
        model_id: str,
        system: str,
        messages: list[dict],
        tools: dict | None = None,
        max_tokens: int = 600,
        hint: str = "",
    ) -> LLMResponse: ...


class BedrockProvider:
    """Real models via the Bedrock Converse API.

    Converse gives one request/response and tool-use shape across vendors
    (Claude, Nova, ...), which is what makes per-role model configuration
    and cross-vendor comparison possible (ADR 0004). boto3 is synchronous,
    so calls run in a worker thread.
    """

    def __init__(self, region: str):
        import boto3

        self._client = boto3.client("bedrock-runtime", region_name=region)

    async def generate(
        self,
        *,
        model_id: str,
        system: str,
        messages: list[dict],
        tools: dict | None = None,
        max_tokens: int = 600,
        hint: str = "",
    ) -> LLMResponse:
        kwargs: dict = {
            "modelId": model_id,
            "messages": messages,
            "system": [{"text": system}],
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if tools:
            kwargs["toolConfig"] = tools

        response = await asyncio.to_thread(self._client.converse, **kwargs)

        content = response["output"]["message"]["content"]
        text = "\n".join(block["text"] for block in content if "text" in block)
        tool_calls = [
            ToolCall(
                id=block["toolUse"]["toolUseId"],
                name=block["toolUse"]["name"],
                arguments=block["toolUse"]["input"],
            )
            for block in content
            if "toolUse" in block
        ]
        usage = response["usage"]
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=response["stopReason"],
            input_tokens=usage["inputTokens"],
            output_tokens=usage["outputTokens"],
            raw_content=content,
        )


def _estimate_tokens(messages: list[dict], system: str) -> int:
    total = len(system)
    for message in messages:
        for block in message.get("content", []):
            total += len(json.dumps(block))
    return max(1, total // 4)


def _has_tool_result(messages: list[dict]) -> bool:
    return any(
        "toolResult" in block
        for message in messages
        for block in message.get("content", [])
    )


# Scripted statements, keyed (side, phase). Openings and rebuttals cite the
# offline evidence fixtures so the fact-checker path has real work to do.
_STATEMENTS = {
    ("pro", "opening"): (
        "I open in favour of the motion. Studies conducted between 2020 and "
        "2024 found that remote workers reported higher job satisfaction "
        "(source: 1001). Flexibility is not a perk; it is a productivity "
        "lever, and the evidence shows people do more of their best work "
        "when trusted with autonomy."
    ),
    ("con", "opening"): (
        "I open against the motion. The same body of research notes that "
        "some organisations observed a decline in spontaneous collaboration "
        "after moving fully remote (source: 1001). What is gained in "
        "individual comfort is paid for in collective creativity."
    ),
    ("pro", "rebuttal"): (
        "My opponent points to lost spontaneous collaboration, but "
        "collaboration is a practice, not a place. Trials of restructured "
        "work arrangements reported maintained or improved productivity "
        "alongside reduced burnout (source: 1002). Burnout, not distance, "
        "is the real collaboration killer."
    ),
    ("con", "rebuttal"): (
        "My opponent cites productivity trials, yet the same source admits "
        "results vary significantly by industry (source: 1002). A policy "
        "that only works for some industries cannot carry a universal "
        "motion. The variance is my case in point."
    ),
    ("pro", "closing"): (
        "To close: the evidence already before you shows higher "
        "satisfaction and sustained productivity. My opponent's strongest "
        "point — industry variance — argues for flexibility in "
        "implementation, not against the motion itself. I rest in favour."
    ),
    ("con", "closing"): (
        "To close: satisfaction surveys measure comfort, not output, and "
        "the collaboration losses are documented in the very sources my "
        "opponent cites. A motion this broad fails on its own evidence. "
        "I rest against."
    ),
}

_JUDGE_VERDICT = {
    "winner": "participant_x",
    "confidence": 0.72,
    "scores": {
        "participant_x": {
            "argument_quality": 8, "evidence_quality": 8,
            "rebuttal_effectiveness": 7, "logical_consistency": 8,
            "topic_relevance": 9, "rule_compliance": 9,
        },
        "participant_y": {
            "argument_quality": 7, "evidence_quality": 7,
            "rebuttal_effectiveness": 8, "logical_consistency": 7,
            "topic_relevance": 9, "rule_compliance": 9,
        },
    },
    "decisive_moment": (
        "Participant X reframed the collaboration objection as an "
        "implementation detail and anchored it to cited evidence."
    ),
    "reasoning_summary": (
        "Both participants argued within the rules and cited sources. "
        "Participant X paired every major claim with evidence and closed by "
        "absorbing the opponent's strongest point; Participant Y rebutted "
        "well but leaned on a single line of attack."
    ),
}

_FACT_CHECK_CLAIMS = {
    "claims": [
        {
            "claim": "Remote workers reported higher job satisfaction.",
            "side": "pro",
            "source_id": "1001",
            "quote": "remote workers reported higher job satisfaction",
        },
        {
            "claim": "Organisations observed a decline in spontaneous collaboration.",
            "side": "con",
            "source_id": "1001",
            "quote": "decline in spontaneous collaboration",
        },
        {
            "claim": "Productivity doubled for every remote team.",
            "side": "pro",
            "source_id": "1002",
            "quote": "productivity doubled for every remote team",
        },
    ]
}


class MockProvider:
    """Deterministic scripted provider — see ADR 0010.

    Fakes only the LLM call; the agent loop, MCP round-trips, quota
    enforcement, storage and events all run for real. Behaviour is selected
    by the ``hint`` argument ("debater:<side>:<phase>", "judge",
    "fact_checker") and by whether the conversation already contains a tool
    result:

    - A debater's opening turn first emits a scripted ``search_sources``
      tool call, so mock debates exercise the full MCP tool path.
    - Every other case returns the scripted statement or JSON verbatim.
    """

    async def generate(
        self,
        *,
        model_id: str,
        system: str,
        messages: list[dict],
        tools: dict | None = None,
        max_tokens: int = 600,
        hint: str = "",
    ) -> LLMResponse:
        input_tokens = _estimate_tokens(messages, system)

        if hint.startswith("debater"):
            _, side, phase = hint.split(":")
            if phase == "opening" and tools and not _has_tool_result(messages):
                tool_use = {
                    "toolUseId": f"mocktool-{side}-opening",
                    "name": "search_sources",
                    "input": {"query": "remote work productivity satisfaction"},
                }
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(tool_use["toolUseId"],
                                         tool_use["name"], tool_use["input"])],
                    stop_reason="tool_use",
                    input_tokens=input_tokens,
                    output_tokens=25,
                    raw_content=[{"toolUse": tool_use}],
                )
            text = _STATEMENTS[(side, phase)]
        elif hint == "judge":
            text = json.dumps(_JUDGE_VERDICT)
        elif hint == "fact_checker":
            text = json.dumps(_FACT_CHECK_CLAIMS)
        else:
            text = "Mock response."

        return LLMResponse(
            text=text,
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=input_tokens,
            output_tokens=max(1, len(text) // 4),
            raw_content=[{"text": text}],
        )
