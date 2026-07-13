"""Typed debate events.

This is the contract between the orchestrator and every consumer: the SSE
stream, the events table used for replay, and (later) the frontend. Events
are append-only and ordered by ``seq`` within a debate, so a stored debate
can be replayed with the exact same event stream the live run produced.
"""

import time
from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    DEBATE_STARTED = "debate_started"
    AWAITING_ADVANCE = "awaiting_advance"
    PHASE_CHANGED = "phase_changed"
    TURN_STARTED = "turn_started"
    MESSAGE_DELTA = "message_delta"
    TURN_COMPLETED = "turn_completed"
    EVIDENCE_USED = "evidence_used"
    CLAIM_VERDICT = "claim_verdict"
    JUDGE_RESULT = "judge_result"
    DEBATE_COMPLETED = "debate_completed"
    DEBATE_FAILED = "debate_failed"


class DebateEvent(BaseModel):
    debate_id: str
    seq: int
    type: EventType
    payload: dict = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)

    def to_sse(self) -> str:
        return f"event: {self.type.value}\ndata: {self.model_dump_json()}\n\n"
