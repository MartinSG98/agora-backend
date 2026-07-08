import pytest

from app.storage.db import Database


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture()
def debate(db):
    return db.create_debate(
        topic="Remote work is better than office work",
        format_name="oxford",
        models={"debater_pro": "mock", "debater_con": "mock", "judge": "mock"},
        rebuttal_rounds=2,
    )


def test_debate_lifecycle(db, debate):
    assert debate["phase"] == "created"
    assert debate["models"]["judge"] == "mock"

    db.update_phase(debate["id"], "opening")
    assert db.get_debate(debate["id"])["phase"] == "opening"

    db.set_result(debate["id"], winner="pro", result={"confidence": 0.8})
    final = db.get_debate(debate["id"])
    assert final["winner"] == "pro"
    assert final["result"]["confidence"] == 0.8
    assert final["completed_at"] is not None

    assert db.get_debate("nonexistent") is None
    assert [d["id"] for d in db.list_debates()] == [debate["id"]]


def test_turns_preserve_order(db, debate):
    db.add_turn(debate["id"], "opening", 0, "pro", "Opening for the motion.")
    db.add_turn(debate["id"], "opening", 0, "con", "Opening against the motion.")
    db.add_turn(debate["id"], "rebuttal", 1, "pro", "Rebuttal.")

    turns = db.get_turns(debate["id"])
    assert [(t["phase"], t["side"]) for t in turns] == [
        ("opening", "pro"),
        ("opening", "con"),
        ("rebuttal", "pro"),
    ]


def test_research_notes_are_private_per_side(db, debate):
    db.add_research_note(
        debate["id"], "pro", kind="source_content",
        content="Remote workers report higher satisfaction.",
        source_id="1001", title="Remote work",
    )
    db.add_research_note(
        debate["id"], "con", kind="source_content",
        content="Collaboration declined after going remote.",
        source_id="1001", title="Remote work",
    )

    pro_notes = db.get_research_notes(debate["id"], "pro")
    assert len(pro_notes) == 1
    assert "satisfaction" in pro_notes[0]["content"]
    # con's research never leaks into pro's notebook
    assert all(n["side"] == "pro" for n in pro_notes)


def test_events_roundtrip_in_sequence_order(db, debate):
    db.append_event(debate["id"], 2, "phase_changed", {"phase": "opening"}, 2.0)
    db.append_event(debate["id"], 1, "debate_started", {"topic": "x"}, 1.0)

    events = db.get_events(debate["id"])
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["payload"] == {"topic": "x"}


def test_agent_run_metrics(db, debate):
    db.add_agent_run(
        debate["id"], agent="debater_pro", phase="opening", model_id="mock",
        input_tokens=100, output_tokens=50, latency_ms=12.5, tool_calls=2,
    )
    runs = db.get_agent_runs(debate["id"])
    assert runs[0]["output_tokens"] == 50
    assert runs[0]["tool_calls"] == 2


def test_evaluation_roundtrip(db, debate):
    evaluation = db.create_evaluation("position_swap", "topic", [debate["id"]])
    assert evaluation["result"] is None

    db.set_evaluation_result(evaluation["id"], {"position_bias_detected": False})
    stored = db.get_evaluation(evaluation["id"])
    assert stored["debate_ids"] == [debate["id"]]
    assert stored["result"]["position_bias_detected"] is False
