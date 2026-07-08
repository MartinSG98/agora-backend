"""MockProvider behaviour: the scripted debate must exercise the same code
paths a real model would — tool use first, then statements, valid JSON from
judge and fact-checker."""

import json

from app.agents.llm import MockProvider

SEARCH_TOOLCONFIG = {
    "tools": [{"toolSpec": {"name": "search_sources", "description": "x",
                            "inputSchema": {"json": {"type": "object"}}}}]
}


def user_message(text: str) -> dict:
    return {"role": "user", "content": [{"text": text}]}


async def test_debater_opening_requests_evidence_before_speaking():
    provider = MockProvider()
    first = await provider.generate(
        model_id="mock", system="s", messages=[user_message("open")],
        tools=SEARCH_TOOLCONFIG, hint="debater:pro:opening",
    )
    assert first.stop_reason == "tool_use"
    assert first.tool_calls[0].name == "search_sources"
    assert first.raw_content[0]["toolUse"]["name"] == "search_sources"

    # after the tool result comes back, the debater speaks
    followup = [
        user_message("open"),
        {"role": "assistant", "content": first.raw_content},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": first.tool_calls[0].id,
            "content": [{"text": json.dumps({"results": []})}],
            "status": "success",
        }}]},
    ]
    second = await provider.generate(
        model_id="mock", system="s", messages=followup,
        tools=SEARCH_TOOLCONFIG, hint="debater:pro:opening",
    )
    assert second.stop_reason == "end_turn"
    assert not second.tool_calls
    assert "(source: 1001)" in second.text


async def test_all_statement_slots_are_scripted():
    provider = MockProvider()
    for side in ("pro", "con"):
        for phase in ("opening", "rebuttal", "closing"):
            response = await provider.generate(
                model_id="mock", system="s",
                messages=[user_message("go"),
                          {"role": "user", "content": [{"toolResult": {
                              "toolUseId": "x", "content": [{"text": "{}"}],
                              "status": "success"}}]}],
                hint=f"debater:{side}:{phase}",
            )
            assert len(response.text) > 100, f"missing script for {side}/{phase}"


async def test_judge_returns_rubric_shaped_json():
    provider = MockProvider()
    response = await provider.generate(
        model_id="mock", system="s", messages=[user_message("judge")], hint="judge",
    )
    verdict = json.loads(response.text)
    assert verdict["winner"] in ("participant_x", "participant_y", "draw")
    assert 0 <= verdict["confidence"] <= 1
    for participant in ("participant_x", "participant_y"):
        scores = verdict["scores"][participant]
        assert len(scores) == 6
        assert all(0 <= value <= 10 for value in scores.values())


async def test_fact_checker_returns_claims_with_citations():
    provider = MockProvider()
    response = await provider.generate(
        model_id="mock", system="s", messages=[user_message("check")],
        hint="fact_checker",
    )
    claims = json.loads(response.text)["claims"]
    assert len(claims) >= 2
    assert all({"claim", "side", "source_id", "quote"} <= set(c) for c in claims)


async def test_mock_is_deterministic():
    provider = MockProvider()
    kwargs = dict(model_id="mock", system="s",
                  messages=[user_message("judge")], hint="judge")
    first = await provider.generate(**kwargs)
    second = await provider.generate(**kwargs)
    assert first.text == second.text
