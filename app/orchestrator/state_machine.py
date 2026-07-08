"""The debate state machine — Agora's "moderator".

Deliberately implemented as deterministic code rather than a moderator LLM:
turn order, round counts and phase transitions are guarantees, so they live
where guarantees are enforceable. LLM agents are used only where judgment
is required (arguing, fact-checking, scoring).

Phase flow:

    CREATED -> OPENING -> REBUTTAL (x N rounds) -> CLOSING
            -> VERIFICATION -> JUDGING -> COMPLETE

Any phase may transition to FAILED via ``fail()``. COMPLETE and FAILED are
terminal.
"""

from dataclasses import dataclass, replace
from enum import Enum

from app.config import HardLimits


class DebatePhase(str, Enum):
    CREATED = "created"
    OPENING = "opening"
    REBUTTAL = "rebuttal"
    CLOSING = "closing"
    VERIFICATION = "verification"
    JUDGING = "judging"
    COMPLETE = "complete"
    FAILED = "failed"


TERMINAL_PHASES = {DebatePhase.COMPLETE, DebatePhase.FAILED}

# Phases in which debaters speak, in debate order (pro speaks first).
SPEAKING_PHASES = (DebatePhase.OPENING, DebatePhase.REBUTTAL, DebatePhase.CLOSING)


class InvalidTransition(Exception):
    pass


def clamp_rebuttal_rounds(requested: int, limits: HardLimits) -> int:
    """A debate format may request any round count; the hard limit wins."""
    return max(0, min(requested, limits.max_rebuttal_rounds))


@dataclass(frozen=True)
class DebateProgress:
    """Immutable position within a debate. ``advance()`` returns the next one."""

    rebuttal_rounds_total: int
    phase: DebatePhase = DebatePhase.CREATED
    rebuttal_round: int = 0  # 1-based while phase == REBUTTAL, else 0

    def advance(self) -> "DebateProgress":
        if self.phase in TERMINAL_PHASES:
            raise InvalidTransition(f"cannot advance from terminal phase {self.phase}")

        if self.phase == DebatePhase.CREATED:
            return replace(self, phase=DebatePhase.OPENING)

        if self.phase == DebatePhase.OPENING:
            if self.rebuttal_rounds_total > 0:
                return replace(self, phase=DebatePhase.REBUTTAL, rebuttal_round=1)
            return replace(self, phase=DebatePhase.CLOSING)

        if self.phase == DebatePhase.REBUTTAL:
            if self.rebuttal_round < self.rebuttal_rounds_total:
                return replace(self, rebuttal_round=self.rebuttal_round + 1)
            return replace(self, phase=DebatePhase.CLOSING, rebuttal_round=0)

        if self.phase == DebatePhase.CLOSING:
            return replace(self, phase=DebatePhase.VERIFICATION)

        if self.phase == DebatePhase.VERIFICATION:
            return replace(self, phase=DebatePhase.JUDGING)

        if self.phase == DebatePhase.JUDGING:
            return replace(self, phase=DebatePhase.COMPLETE)

        raise InvalidTransition(f"no transition defined from {self.phase}")

    def fail(self) -> "DebateProgress":
        if self.phase in TERMINAL_PHASES:
            raise InvalidTransition(f"cannot fail from terminal phase {self.phase}")
        return replace(self, phase=DebatePhase.FAILED)

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    @property
    def is_speaking_phase(self) -> bool:
        return self.phase in SPEAKING_PHASES
