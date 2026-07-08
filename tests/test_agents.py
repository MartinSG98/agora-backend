"""Agent layer tests: quota enforcement in the tool loop, debater tool
gating, judge validation + retry, fact-checker verification — all against
the real MCP servers (evidence in offline fixture mode)."""

import json

import pytest

from app.agents.agent import ToolUseAgent
from app.agents.debater import Debater
from app.agents.fact_checker import FactChecker
from app.agents.judge import Judge, JudgeError
from app.agents.llm import LLMResponse, MockProvider, ToolCall
from app.config import REPO_ROOT, HardLimits
from app.mcp_client.manager import MCPManager


@pytest.fixture()
async def mcp():
    manager = MCPManager(REPO_ROOT / "mcp-servers", offline_evidence=True)
    await manager.start()
    yield manager
    await manager.stop()


class ScriptedProvider:
    """Returns queued LLMResponses; records every generate() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def tool_use_response(count: int, name: str = "search_sources") -> LLMResponse:
    calls = [ToolCall(f"call-{i}", name, {"query": f"q{i}"}) for i in range(count)]
    return LLMResponse(
        text="", tool_calls=calls, stop_reason="tool_use",
        input_tokens=10, output_tokens=5,
        raw_content=[{"toolUse": {"toolUseId": c.id, "name": c.name,
                                  "input": c.arguments}} for c in calls],
    )


def text_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], stop_reason="end_turn",
                       input_tokens=10, output_tokens=5,
                       raw_content=[{"text": text}])


# -- tool loop -----------------------------------------------------------------


async def test_evidence_quota_is_enforced_in_code(mcp):
    limits = HardLimits(max_evidence_requests_per_phase=3)
    provider = ScriptedProvider([
        tool_use_response(5),          # asks for 5 searches at once
        text_response("done"),
    ])
    agent = ToolUseAgent(provider, mcp, limits)

    result = await agent.run(model_id="x", system="s", user_prompt="go")

    assert result.tool_calls == 5
    assert result.quota_rejections == 2          # calls 4 and 5 rejected
    assert len(result.research) == 3             # only executed calls captured

    # the rejected calls got error tool-results, not executions
    tool_result_msg = provider.calls[1]["messages"][-1]
    statuses = [b["toolResult"]["status"] for b in tool_result_msg["content"]]
    assert statuses == ["success"] * 3 + ["error"] * 2
    error_payload = json.loads(
        tool_result_msg["content"][3]["toolResult"]["content"][0]["text"]
    )
    assert "quota" in error_payload["error"]


async def test_iteration_cap_terminates_a_tool_hungry_model(mcp):
    limits = HardLimits(max_tool_loop_iterations=3,
                        max_evidence_requests_per_phase=100)
    provider = ScriptedProvider([tool_use_response(1) for _ in range(3)])
    agent = ToolUseAgent(provider, mcp, limits)

    result = await agent.run(model_id="x", system="s", user_prompt="go")

    assert len(provider.calls) == 3              # hard stop, no fourth call
    assert result.tool_calls == 3


async def test_tool_callback_fires(mcp):
    provider = ScriptedProvider([tool_use_response(1), text_response("ok")])
    agent = ToolUseAgent(provider, mcp, HardLimits())
    seen = []

    async def on_tool(name, arguments, text, is_error):
        seen.append((name, is_error))

    await agent.run(model_id="x", system="s", user_prompt="go", on_tool=on_tool)
    assert seen == [("search_sources", False)]


# -- debater --------------------------------------------------------------------


async def test_debater_closing_has_no_tools(mcp):
    provider = ScriptedProvider([text_response("closing"), text_response("opening")])
    debater = Debater(ToolUseAgent(provider, mcp, HardLimits()), "x", "pro")
    common = dict(topic="t", format_rules=["r"], turns=[], notes=[],
                  remaining_evidence=3)

    await debater.speak(phase="closing", **common)
    assert provider.calls[0]["tools"] is None    # no new evidence in closing

    await debater.speak(phase="opening", **common)
    assert provider.calls[1]["tools"] is not None


async def test_debater_prompt_separates_notes_from_transcript(mcp):
    provider = ScriptedProvider([text_response("x")])
    debater = Debater(ToolUseAgent(provider, mcp, HardLimits()), "x", "con")

    await debater.speak(
        phase="rebuttal", topic="the motion", format_rules=["be nice"],
        turns=[{"phase": "opening", "round": 0, "side": "pro",
                "content": "pro said this"}],
        notes=[{"kind": "source_content", "source_id": "1001",
                "title": "Remote work", "content": "private research"}],
        remaining_evidence=2,
    )

    prompt = provider.calls[0]["messages"][0]["content"][0]["text"]
    assert "<your_research_notes>" in prompt and "private research" in prompt
    assert "<debate_transcript>" in prompt and "your opponent" in prompt
    assert "Evidence requests remaining this phase: 2" in prompt
    system = provider.calls[0]["system"]
    assert "AGAINST" in system and "be nice" in system


# -- transcript cleanup -----------------------------------------------------------


def test_clean_statement_strips_leaked_scratchpads():
    from app.orchestrator.orchestrator import clean_statement

    leaked = (
        "<thinking>Let me plan my argument here.</thinking>\n\n"
        "<argument>\nVideo games help (source: 59602196).\n</argument>"
    )
    assert clean_statement(leaked) == "Video games help (source: 59602196)."

    unclosed = "Real statement first. <thinking>then it trails off"
    assert clean_statement(unclosed) == "Real statement first."

    clean = "A perfectly normal statement."
    assert clean_statement(clean) == clean


# -- judge ----------------------------------------------------------------------


async def test_judge_accepts_valid_verdict(mcp):
    outcome_provider = MockProvider()  # mock judge emits schema-shaped JSON
    judge = Judge(outcome_provider, mcp, HardLimits(), "mock")

    outcome = await judge.judge("topic", "transcript", claim_verdicts=[
        {"claim": "c", "verdict": "supported"},
    ])
    assert outcome.verdict.winner == "participant_x"
    assert outcome.attempts == 1


async def test_judge_retries_once_then_succeeds(mcp):
    valid = json.loads(
        (await MockProvider().generate(model_id="m", system="s", messages=[],
                                       hint="judge")).text
    )
    provider = ScriptedProvider([
        text_response("not json at all"),
        text_response(json.dumps(valid)),
    ])
    judge = Judge(provider, mcp, HardLimits(judge_retries=1), "x")

    outcome = await judge.judge("topic", "transcript")
    assert outcome.attempts == 2
    # the retry message carried the validation error
    retry_prompt = provider.calls[1]["messages"][-1]["content"][0]["text"]
    assert "invalid" in retry_prompt


async def test_judge_fails_after_exhausting_retries(mcp):
    missing_category = {
        "winner": "draw", "confidence": 0.5,
        "scores": {"participant_x": {"argument_quality": 5},
                   "participant_y": {"argument_quality": 5}},
        "decisive_moment": "-", "reasoning_summary": "-",
    }
    provider = ScriptedProvider([
        text_response(json.dumps(missing_category)),
        text_response(json.dumps(missing_category)),
    ])
    judge = Judge(provider, mcp, HardLimits(judge_retries=1), "x")

    with pytest.raises(JudgeError):
        await judge.judge("topic", "transcript")


# -- fact checker -----------------------------------------------------------------


async def test_fact_checker_verifies_against_evidence_server(mcp):
    checker = FactChecker(MockProvider(), mcp, HardLimits(), "mock")

    outcome = await checker.check("transcript text")

    verdicts = {c["claim"]: c["verdict"] for c in outcome.claims}
    assert verdicts["Remote workers reported higher job satisfaction."] == "supported"
    assert (
        verdicts["Productivity doubled for every remote team."]
        in ("not_found", "partially_supported")
    )
    assert outcome.tool_calls == len(outcome.claims)


async def test_fact_checker_degrades_gracefully_on_garbage(mcp):
    provider = ScriptedProvider([text_response("no json here")])
    checker = FactChecker(provider, mcp, HardLimits(), "x")

    outcome = await checker.check("transcript")
    assert outcome.claims == []
