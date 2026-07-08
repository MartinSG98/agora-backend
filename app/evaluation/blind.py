"""Blind judging support (ADR 0008).

Before a transcript reaches the judge, sides are relabeled participant_x /
participant_y. The assignment is random but seeded by debate id, so a
debate always blinds the same way (reproducible) while different debates
vary (the judge can't learn "x is always pro").
"""

import random

LABELS = ("participant_x", "participant_y")
SIDES = ("pro", "con")


def assign_labels(debate_id: str) -> dict[str, str]:
    """Map side -> anonymous label, deterministically per debate."""
    labels = list(LABELS)
    random.Random(debate_id).shuffle(labels)
    return dict(zip(SIDES, labels))


def unblind(mapping: dict[str, str], label: str) -> str:
    """Map a label back to its side; 'draw' and unknowns pass through."""
    reverse = {label_: side for side, label_ in mapping.items()}
    return reverse.get(label, label)


def blind_transcript(turns: list[dict], mapping: dict[str, str]) -> str:
    """Render the public transcript with anonymous labels only.

    The judge sees phases and statements — never sides or model names.
    """
    lines = []
    for turn in turns:
        marker = turn["phase"]
        if turn.get("round"):
            marker += f" round {turn['round']}"
        lines.append(f"[{marker}] {mapping[turn['side']]}:\n{turn['content']}")
    return "\n\n".join(lines)
