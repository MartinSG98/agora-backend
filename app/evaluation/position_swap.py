"""Position-swap evaluation (ADR 0008).

Runs the same topic twice with the debater models exchanging sides, then
asks the only question a single debate can't answer: does the win follow
the MODEL (a genuine capability gap) or the SIDE (the proposition itself
is easier to argue, or the judge favours a position)?

With models M1/M2 and runs (pro=M1, con=M2) then (pro=M2, con=M1):

    winner side | run A | run B | reading
    ------------+-------+-------+---------------------------------
                |  pro  |  con  | M1 won both -> model advantage
                |  con  |  pro  | M2 won both -> model advantage
                |  pro  |  pro  | side won both -> position bias
                |  con  |  con  | side won both -> position bias
                |  any draw     | inconclusive
"""

from app.orchestrator.orchestrator import DebateOrchestrator
from app.storage.db import Database

SWAPPED_ROLES = {"debater_pro": "debater_con", "debater_con": "debater_pro"}


def swap_debaters(models: dict[str, str]) -> dict[str, str]:
    return {SWAPPED_ROLES.get(role, role): name for role, name in models.items()}


def _winning_model(debate: dict) -> str | None:
    if debate["winner"] not in ("pro", "con"):
        return None
    return debate["models"][f"debater_{debate['winner']}"]


def analyse(run_a: dict, run_b: dict) -> dict:
    """Compare two completed swapped debates; see the module table."""
    summary = {
        "runs": [
            {
                "debate_id": run["id"],
                "winner_side": run["winner"],
                "winner_model": _winning_model(run),
                "confidence": (run.get("result") or {}).get("confidence"),
            }
            for run in (run_a, run_b)
        ]
    }

    if any(run["phase"] != "complete" for run in (run_a, run_b)):
        return {**summary, "verdict": "failed",
                "explanation": "at least one debate did not complete"}

    model_a, model_b = _winning_model(run_a), _winning_model(run_b)
    if model_a is None or model_b is None:
        return {**summary, "verdict": "inconclusive",
                "explanation": "at least one debate was a draw"}

    if model_a == model_b:
        return {
            **summary,
            "verdict": "model_advantage",
            "advantaged_model": model_a,
            "explanation": f"{model_a} won from both sides — the win follows"
                           " the model, not the position",
        }

    biased_side = run_a["winner"]  # model differs => side must repeat
    return {
        **summary,
        "verdict": "position_bias",
        "biased_side": biased_side,
        "explanation": f"the {biased_side} side won regardless of which"
                       " model argued it — the topic (or the judge) favours"
                       " that position; a model ranking from this topic"
                       " would be meaningless",
    }


async def run_position_swap(
    db: Database,
    orchestrator: DebateOrchestrator,
    evaluation_id: str,
    debates: list[dict],
    format_rules: list[str],
) -> None:
    """Run both debates sequentially, then persist the comparison."""
    try:
        for debate in debates:
            await orchestrator.run_debate(debate, format_rules)
        completed = [db.get_debate(debate["id"]) for debate in debates]
        db.set_evaluation_result(evaluation_id, analyse(*completed))
    except Exception as exc:
        db.set_evaluation_result(evaluation_id,
                                 {"verdict": "failed", "error": str(exc)})
