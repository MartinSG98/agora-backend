"""Position-swap analysis (pure function) + the evaluation endpoint."""

import asyncio

import httpx
import pytest

from app.evaluation.position_swap import analyse, swap_debaters

M1, M2 = "model-one", "model-two"


def debate_row(debate_id: str, winner: str | None, pro: str, con: str,
               phase: str = "complete") -> dict:
    return {
        "id": debate_id,
        "phase": phase,
        "winner": winner,
        "models": {"debater_pro": pro, "debater_con": con},
        "result": {"confidence": 0.8},
    }


def test_swap_debaters_swaps_only_debaters():
    models = {"debater_pro": M1, "debater_con": M2,
              "judge": "j", "fact_checker": "f"}
    swapped = swap_debaters(models)
    assert swapped["debater_pro"] == M2
    assert swapped["debater_con"] == M1
    assert swapped["judge"] == "j" and swapped["fact_checker"] == "f"


def test_same_model_wins_both_sides_is_model_advantage():
    result = analyse(
        debate_row("a", "pro", pro=M1, con=M2),   # M1 wins as pro
        debate_row("b", "con", pro=M2, con=M1),   # M1 wins as con
    )
    assert result["verdict"] == "model_advantage"
    assert result["advantaged_model"] == M1


def test_same_side_wins_both_runs_is_position_bias():
    result = analyse(
        debate_row("a", "pro", pro=M1, con=M2),   # pro wins with M1
        debate_row("b", "pro", pro=M2, con=M1),   # pro wins with M2
    )
    assert result["verdict"] == "position_bias"
    assert result["biased_side"] == "pro"


def test_draw_is_inconclusive():
    result = analyse(
        debate_row("a", "draw", pro=M1, con=M2),
        debate_row("b", "con", pro=M2, con=M1),
    )
    assert result["verdict"] == "inconclusive"


def test_incomplete_debate_fails_the_evaluation():
    result = analyse(
        debate_row("a", "pro", pro=M1, con=M2),
        debate_row("b", None, pro=M2, con=M1, phase="failed"),
    )
    assert result["verdict"] == "failed"


def test_run_summaries_are_reported():
    result = analyse(
        debate_row("a", "pro", pro=M1, con=M2),
        debate_row("b", "con", pro=M2, con=M1),
    )
    assert [r["debate_id"] for r in result["runs"]] == ["a", "b"]
    assert result["runs"][0]["winner_model"] == M1
    assert result["runs"][0]["confidence"] == 0.8


# -- endpoint (mock, full stack) ------------------------------------------------


@pytest.fixture()
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGORA_MOCK_MODE", "1")
    monkeypatch.setenv("AGORA_DB_PATH", str(tmp_path / "swap.db"))

    from app.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as http:
            yield http


async def test_position_swap_endpoint_runs_both_debates(client):
    created = await client.post("/evaluations/position-swap", json={
        "topic": "Remote work is better than office work",
    })
    assert created.status_code == 200
    evaluation_id = created.json()["id"]
    assert len(created.json()["debate_ids"]) == 2

    deadline = asyncio.get_event_loop().time() + 120
    while asyncio.get_event_loop().time() < deadline:
        detail = (await client.get(f"/evaluations/{evaluation_id}")).json()
        if detail["result"] is not None:
            break
        await asyncio.sleep(0.3)
    else:
        raise TimeoutError("evaluation did not finish")

    result = detail["result"]
    assert result["verdict"] in ("model_advantage", "position_bias",
                                 "inconclusive")
    assert len(result["runs"]) == 2

    # debater models really were swapped between the two runs
    first, second = detail["debates"]
    assert first["models"]["debater_pro"] == second["models"]["debater_con"]
    assert first["models"]["debater_con"] == second["models"]["debater_pro"]
    assert all(d["phase"] == "complete" for d in detail["debates"])

    listed = (await client.get("/evaluations")).json()["evaluations"]
    assert listed[0]["id"] == evaluation_id
    assert listed[0]["done"] is True
