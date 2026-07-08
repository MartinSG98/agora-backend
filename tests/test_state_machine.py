import pytest

from app.config import HardLimits
from app.orchestrator.state_machine import (
    DebatePhase,
    DebateProgress,
    InvalidTransition,
    clamp_rebuttal_rounds,
)


def walk_to_completion(progress: DebateProgress) -> list[tuple[DebatePhase, int]]:
    trail = [(progress.phase, progress.rebuttal_round)]
    while not progress.is_terminal:
        progress = progress.advance()
        trail.append((progress.phase, progress.rebuttal_round))
    return trail


def test_full_debate_with_two_rebuttal_rounds():
    trail = walk_to_completion(DebateProgress(rebuttal_rounds_total=2))
    assert trail == [
        (DebatePhase.CREATED, 0),
        (DebatePhase.OPENING, 0),
        (DebatePhase.REBUTTAL, 1),
        (DebatePhase.REBUTTAL, 2),
        (DebatePhase.CLOSING, 0),
        (DebatePhase.VERIFICATION, 0),
        (DebatePhase.JUDGING, 0),
        (DebatePhase.COMPLETE, 0),
    ]


def test_zero_rebuttal_rounds_skips_rebuttal_phase():
    trail = walk_to_completion(DebateProgress(rebuttal_rounds_total=0))
    phases = [phase for phase, _ in trail]
    assert DebatePhase.REBUTTAL not in phases
    assert phases[1:3] == [DebatePhase.OPENING, DebatePhase.CLOSING]


def test_advance_from_complete_raises():
    progress = DebateProgress(rebuttal_rounds_total=0, phase=DebatePhase.COMPLETE)
    with pytest.raises(InvalidTransition):
        progress.advance()


def test_fail_from_any_active_phase():
    for phase in (DebatePhase.OPENING, DebatePhase.REBUTTAL, DebatePhase.JUDGING):
        progress = DebateProgress(rebuttal_rounds_total=1, phase=phase)
        assert progress.fail().phase == DebatePhase.FAILED


def test_fail_from_terminal_raises():
    progress = DebateProgress(rebuttal_rounds_total=1, phase=DebatePhase.FAILED)
    with pytest.raises(InvalidTransition):
        progress.fail()


def test_progress_is_immutable():
    progress = DebateProgress(rebuttal_rounds_total=1)
    advanced = progress.advance()
    assert progress.phase == DebatePhase.CREATED
    assert advanced.phase == DebatePhase.OPENING


def test_clamp_rebuttal_rounds_enforces_hard_limit():
    limits = HardLimits(max_rebuttal_rounds=2)
    assert clamp_rebuttal_rounds(5, limits) == 2   # format asks for more than allowed
    assert clamp_rebuttal_rounds(1, limits) == 1
    assert clamp_rebuttal_rounds(-3, limits) == 0
