from app.evaluation.blind import LABELS, assign_labels, blind_transcript, unblind


def test_assignment_is_deterministic_per_debate():
    assert assign_labels("debate-1") == assign_labels("debate-1")


def test_assignment_varies_across_debates():
    assignments = {tuple(assign_labels(f"debate-{i}").items()) for i in range(50)}
    assert len(assignments) == 2, "both orderings should occur across debates"


def test_labels_are_a_permutation():
    mapping = assign_labels("any")
    assert sorted(mapping.values()) == sorted(LABELS)


def test_unblind_roundtrip():
    mapping = assign_labels("debate-42")
    for side, label in mapping.items():
        assert unblind(mapping, label) == side
    assert unblind(mapping, "draw") == "draw"


def test_blind_transcript_never_leaks_sides():
    turns = [
        {"phase": "opening", "round": 0, "side": "pro", "content": "For the motion."},
        {"phase": "rebuttal", "round": 1, "side": "con", "content": "Against it."},
    ]
    mapping = assign_labels("debate-7")
    text = blind_transcript(turns, mapping)

    assert "participant_x" in text and "participant_y" in text
    assert "rebuttal round 1" in text
    for line in text.splitlines():
        if line.startswith("["):  # speaker lines carry labels, never sides
            assert " pro" not in line and " con" not in line
