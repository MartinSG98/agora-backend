"""End-to-end: a full mock debate through the real API surface.

Boots the actual FastAPI app (lifespan included: MCP servers as
subprocesses, mock provider, SQLite in a temp dir), creates a debate over
HTTP, waits for the orchestrator to finish, then checks the transcript,
verdict, notebooks, metrics and the SSE replay stream.
"""

import asyncio
import json

import httpx
import pytest


@pytest.fixture()
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGORA_MOCK_MODE", "1")
    monkeypatch.setenv("AGORA_DB_PATH", str(tmp_path / "e2e.db"))

    from app.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as http:
            yield http


async def wait_for_completion(client, debate_id: str, timeout: float = 60.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        detail = (await client.get(f"/debates/{debate_id}")).json()
        if detail["phase"] in ("complete", "failed"):
            return detail
        await asyncio.sleep(0.2)
    raise TimeoutError("debate did not finish in time")


async def test_full_mock_debate(client):
    created = await client.post("/debates", json={
        "topic": "Remote work is better than office work",
    })
    assert created.status_code == 200
    debate_id = created.json()["id"]

    detail = await wait_for_completion(client, debate_id)
    assert detail["phase"] == "complete", detail.get("result")

    # transcript: oxford format = opening x2, rebuttal x2 rounds x2, closing x2
    turns = detail["turns"]
    assert [(t["phase"], t["side"]) for t in turns] == [
        ("opening", "pro"), ("opening", "con"),
        ("rebuttal", "pro"), ("rebuttal", "con"),
        ("rebuttal", "pro"), ("rebuttal", "con"),
        ("closing", "pro"), ("closing", "con"),
    ]
    assert all(len(t["content"]) > 100 for t in turns)

    # verdict: unblinded winner, per-side rubric scores, fact-check findings
    result = detail["result"]
    assert detail["winner"] in ("pro", "con", "draw")
    assert set(result["scores"]) == {"pro", "con"}
    assert len(result["scores"]["pro"]) == 6
    assert sorted(result["blind_mapping"].values()) == [
        "participant_x", "participant_y",
    ]
    # the mock fact-check set includes one fabricated claim
    assert len(result["unsupported_claims"]) >= 1
    assert len(result["claim_verdicts"]) >= 2

    # research notebooks were captured for both sides (openings call MCP)
    notes = detail["research_notes"]
    assert notes["pro"] and notes["con"]
    assert notes["pro"][0]["kind"] == "search_results"


async def test_metrics_capture_every_agent(client):
    created = await client.post("/debates", json={
        "topic": "Four day work weeks should be standard",
    })
    debate_id = created.json()["id"]
    await wait_for_completion(client, debate_id)

    metrics = (await client.get(f"/debates/{debate_id}/metrics")).json()
    agents = {run["agent"] for run in metrics["runs"]}
    assert agents == {"debater_pro", "debater_con", "fact_checker", "judge"}
    assert len(metrics["runs"]) == 10  # 8 turns + fact checker + judge
    assert metrics["totals"]["output_tokens"] > 0
    assert metrics["totals"]["tool_calls"] >= 2  # both scripted openings search


async def test_sse_replay_reproduces_the_debate(client):
    created = await client.post("/debates", json={
        "topic": "Cities should ban private cars downtown",
    })
    debate_id = created.json()["id"]
    await wait_for_completion(client, debate_id)

    event_types = []
    async with client.stream(
        "GET", f"/debates/{debate_id}/events", params={"replay": 1, "delay": 0}
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event_types.append(line.removeprefix("event: "))

    assert event_types[0] == "debate_started"
    assert event_types[-1] == "debate_completed"
    assert event_types.count("turn_completed") == 8
    assert event_types.count("evidence_used") >= 2
    assert event_types.count("claim_verdict") >= 2
    assert "judge_result" in event_types
    assert event_types.count("message_delta") > 8  # synthetic streaming chunks

    # replaying twice yields the identical stream
    second = []
    async with client.stream(
        "GET", f"/debates/{debate_id}/events", params={"replay": 1, "delay": 0}
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                second.append(line.removeprefix("event: "))
    assert second == event_types


async def test_validation_rejects_bad_input(client):
    too_short = await client.post("/debates", json={"topic": "nope"})
    assert too_short.status_code == 422

    bad_model = await client.post("/debates", json={
        "topic": "A perfectly fine topic",
        "models": {"judge": "gpt-99"},
    })
    assert bad_model.status_code == 422

    # in the registry, but outside the cost allowlist
    expensive_model = await client.post("/debates", json={
        "topic": "A perfectly fine topic",
        "models": {"judge": "mistral-large"},
    })
    assert expensive_model.status_code == 422
    assert "allowlist" in expensive_model.json()["detail"]

    bad_format = await client.post("/debates", json={
        "topic": "A perfectly fine topic", "format": "rap-battle",
    })
    assert bad_format.status_code == 404

    missing = await client.get("/debates/nonexistent")
    assert missing.status_code == 404


async def test_config_surface(client):
    models = (await client.get("/models")).json()
    assert "mock" in models["registry"]

    formats = (await client.get("/formats")).json()["formats"]
    assert {f["name"] for f in formats} == {"casual", "oxford"}
