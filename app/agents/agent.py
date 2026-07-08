"""The hand-rolled tool-use loop (ADR 0005).

This is the enforcement point for two hard limits (ADR 0003):

- evidence quota: over-quota tool calls are NOT executed — the model gets
  an error tool-result explaining why, and must argue with what it has
- iteration cap: the loop terminates after max_tool_loop_iterations no
  matter what the model keeps asking for

It is also where research is captured for the debater's private notebook
(ADR 0007): every successful evidence tool result is collected and handed
back to the orchestrator to persist.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.agents.llm import LLMProvider
from app.config import HardLimits
from app.mcp_client.manager import EVIDENCE, MCPManager

EVIDENCE_TOOLS = {"search_sources", "get_source_content", "verify_quote"}

# on_tool(tool_name, arguments, result_text, is_error)
ToolCallback = Callable[[str, dict, str, bool], Awaitable[None]]


@dataclass
class AgentResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    tool_calls: int = 0
    quota_rejections: int = 0
    # successful evidence results, for the research notebook
    research: list[dict] = field(default_factory=list)


class ToolUseAgent:
    """Runs one agent turn: provider call -> execute tool calls via MCP ->
    feed results back -> repeat until the model answers in text (or a hard
    limit ends the loop)."""

    def __init__(self, provider: LLMProvider, mcp: MCPManager, limits: HardLimits):
        self._provider = provider
        self._mcp = mcp
        self._limits = limits

    async def run(
        self,
        *,
        model_id: str,
        system: str,
        user_prompt: str,
        use_tools: bool = True,
        server: str = EVIDENCE,
        hint: str = "",
        on_tool: ToolCallback | None = None,
    ) -> AgentResult:
        messages: list[dict] = [
            {"role": "user", "content": [{"text": user_prompt}]}
        ]
        tools = None
        if use_tools:
            tools = MCPManager.to_bedrock_tool_config(self._mcp.get_tools(server))

        result = AgentResult(text="")
        evidence_used = 0
        started = time.perf_counter()
        response = None

        for _ in range(self._limits.max_tool_loop_iterations):
            response = await self._provider.generate(
                model_id=model_id,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=self._limits.max_response_tokens,
                hint=hint,
            )
            result.input_tokens += response.input_tokens
            result.output_tokens += response.output_tokens

            if not response.tool_calls:
                result.text = response.text
                break

            messages.append({"role": "assistant", "content": response.raw_content})
            result_blocks = []
            for call in response.tool_calls:
                result.tool_calls += 1
                is_evidence = call.name in EVIDENCE_TOOLS

                if is_evidence and evidence_used >= self._limits.max_evidence_requests_per_phase:
                    # Hard limit: reject instead of executing (ADR 0003).
                    result.quota_rejections += 1
                    text = json.dumps({
                        "error": "evidence request quota for this phase is"
                                 " exhausted; argue with the evidence you"
                                 " already have"
                    })
                    is_error = True
                else:
                    if is_evidence:
                        evidence_used += 1
                    text, is_error = await self._mcp.call_tool(
                        server, call.name, call.arguments
                    )
                    if not is_error:
                        result.research.append({
                            "tool": call.name,
                            "arguments": call.arguments,
                            "result": text,
                        })

                if on_tool is not None:
                    await on_tool(call.name, call.arguments, text, is_error)

                result_blocks.append({
                    "toolResult": {
                        "toolUseId": call.id,
                        "content": [{"text": text}],
                        "status": "error" if is_error else "success",
                    }
                })
            messages.append({"role": "user", "content": result_blocks})
        else:
            # Iteration cap reached while the model still wanted tools.
            result.text = response.text if response else ""

        result.latency_ms = (time.perf_counter() - started) * 1000
        return result
