"""SQLite persistence layer.

Local-first by design: SQLite keeps the project clone-and-run. The schema
mirrors what a DynamoDB layout would look like on AWS (see README roadmap).

Tables:
    debates        one row per debate (config, phase, final result)
    turns          public transcript: every statement by every agent
    agent_runs     per-call metrics: model, tokens, latency, tool calls
    research_notes private per-debater memory — evidence gathered via MCP
                   tools, re-injected into that debater's later turns
    events         append-only event log, used for SSE replay
    evaluations    multi-debate experiments (e.g. position swap)
"""

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS debates (
    id              TEXT PRIMARY KEY,
    topic           TEXT NOT NULL,
    format          TEXT NOT NULL,
    phase           TEXT NOT NULL,
    models_json     TEXT NOT NULL,
    rebuttal_rounds INTEGER NOT NULL,
    winner          TEXT,
    result_json     TEXT,
    created_at      REAL NOT NULL,
    completed_at    REAL
);

CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id  TEXT NOT NULL REFERENCES debates(id),
    phase      TEXT NOT NULL,
    round      INTEGER NOT NULL DEFAULT 0,
    side       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id     TEXT NOT NULL REFERENCES debates(id),
    agent         TEXT NOT NULL,
    phase         TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms    REAL NOT NULL DEFAULT 0,
    tool_calls    INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS research_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id  TEXT NOT NULL REFERENCES debates(id),
    side       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    source_id  TEXT,
    title      TEXT,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    debate_id    TEXT NOT NULL REFERENCES debates(id),
    seq          INTEGER NOT NULL,
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    timestamp    REAL NOT NULL,
    PRIMARY KEY (debate_id, seq)
);

CREATE TABLE IF NOT EXISTS evaluations (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    topic           TEXT NOT NULL,
    debate_ids_json TEXT NOT NULL,
    result_json     TEXT,
    created_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_debate ON turns(debate_id);
CREATE INDEX IF NOT EXISTS idx_runs_debate ON agent_runs(debate_id);
CREATE INDEX IF NOT EXISTS idx_notes_debate_side ON research_notes(debate_id, side);
"""


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


class Database:
    """Thread-safe wrapper around a single SQLite connection.

    Writes happen from the orchestrator's asyncio task and reads from API
    handlers, so every statement runs under one lock. Operations are tiny;
    contention is not a concern at this scale.
    """

    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- debates -----------------------------------------------------------

    def create_debate(
        self, topic: str, format_name: str, models: dict, rebuttal_rounds: int
    ) -> dict:
        debate_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO debates (id, topic, format, phase, models_json,"
                " rebuttal_rounds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (debate_id, topic, format_name, "created", json.dumps(models),
                 rebuttal_rounds, now),
            )
        return self.get_debate(debate_id)

    def update_phase(self, debate_id: str, phase: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE debates SET phase = ? WHERE id = ?", (phase, debate_id)
            )

    def set_result(self, debate_id: str, winner: str | None, result: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE debates SET winner = ?, result_json = ?, completed_at = ?"
                " WHERE id = ?",
                (winner, json.dumps(result), time.time(), debate_id),
            )

    def get_debate(self, debate_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM debates WHERE id = ?", (debate_id,)
            ).fetchone()
        if row is None:
            return None
        debate = _row_to_dict(row)
        debate["models"] = json.loads(debate.pop("models_json"))
        result_json = debate.pop("result_json")
        debate["result"] = json.loads(result_json) if result_json else None
        return debate

    def list_debates(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, topic, format, phase, winner, created_at, completed_at"
                " FROM debates ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- turns (public transcript) ------------------------------------------

    def add_turn(
        self, debate_id: str, phase: str, round_: int, side: str, content: str
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO turns (debate_id, phase, round, side, content,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (debate_id, phase, round_, side, content, time.time()),
            )

    def get_turns(self, debate_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM turns WHERE debate_id = ? ORDER BY id", (debate_id,)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- agent runs (metrics) ------------------------------------------------

    def add_agent_run(
        self,
        debate_id: str,
        agent: str,
        phase: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        tool_calls: int,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO agent_runs (debate_id, agent, phase, model_id,"
                " input_tokens, output_tokens, latency_ms, tool_calls, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (debate_id, agent, phase, model_id, input_tokens, output_tokens,
                 latency_ms, tool_calls, time.time()),
            )

    def get_agent_runs(self, debate_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_runs WHERE debate_id = ? ORDER BY id",
                (debate_id,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- research notes (private per-debater memory) --------------------------

    def add_research_note(
        self,
        debate_id: str,
        side: str,
        kind: str,
        content: str,
        source_id: str | None = None,
        title: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO research_notes (debate_id, side, kind, source_id,"
                " title, content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (debate_id, side, kind, source_id, title, content, time.time()),
            )

    def get_research_notes(self, debate_id: str, side: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM research_notes WHERE debate_id = ? AND side = ?"
                " ORDER BY id",
                (debate_id, side),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- events (replay log) ---------------------------------------------------

    def append_event(
        self, debate_id: str, seq: int, type_: str, payload: dict, timestamp: float
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (debate_id, seq, type, payload_json, timestamp)"
                " VALUES (?, ?, ?, ?, ?)",
                (debate_id, seq, type_, json.dumps(payload), timestamp),
            )

    def get_events(self, debate_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE debate_id = ? ORDER BY seq", (debate_id,)
            ).fetchall()
        events = []
        for row in rows:
            event = _row_to_dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return events

    # -- evaluations (multi-debate experiments) ---------------------------------

    def create_evaluation(self, kind: str, topic: str, debate_ids: list[str]) -> dict:
        evaluation_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO evaluations (id, kind, topic, debate_ids_json,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (evaluation_id, kind, topic, json.dumps(debate_ids), time.time()),
            )
        return self.get_evaluation(evaluation_id)

    def set_evaluation_result(self, evaluation_id: str, result: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE evaluations SET result_json = ? WHERE id = ?",
                (json.dumps(result), evaluation_id),
            )

    def get_evaluation(self, evaluation_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)
            ).fetchone()
        if row is None:
            return None
        evaluation = _row_to_dict(row)
        evaluation["debate_ids"] = json.loads(evaluation.pop("debate_ids_json"))
        result_json = evaluation.pop("result_json")
        evaluation["result"] = json.loads(result_json) if result_json else None
        return evaluation
