"""Debater agent: side persona, research notebook injection, phase prompts.

Memory design (ADR 0007): the debater is stateless between turns except
for its private research notebook, injected each turn in a tagged section
clearly separated from the public transcript. One format rule is enforced
in code rather than prompt: evidence tools are disabled entirely during
closing statements — "no new evidence in closing" is a guarantee, not a
request.
"""

from app.agents.agent import AgentResult, ToolCallback, ToolUseAgent

SIDE_STANCE = {"pro": "FOR", "con": "AGAINST"}


def format_notes(notes: list[dict]) -> str:
    if not notes:
        return "(no research gathered yet)"
    lines = []
    for note in notes:
        header = f"[{note['kind']}"
        if note.get("source_id"):
            header += f" | source {note['source_id']}"
        if note.get("title"):
            header += f" | {note['title']}"
        header += "]"
        lines.append(f"{header}\n{note['content']}")
    return "\n\n".join(lines)


def format_transcript(turns: list[dict], own_side: str) -> str:
    if not turns:
        return "(the debate has not started yet)"
    lines = []
    for turn in turns:
        speaker = "you" if turn["side"] == own_side else "your opponent"
        marker = turn["phase"]
        if turn.get("round"):
            marker += f" round {turn['round']}"
        lines.append(f"[{marker}] {speaker}:\n{turn['content']}")
    return "\n\n".join(lines)


PHASE_INSTRUCTIONS = {
    "opening": (
        "Write your opening statement. Research the motion with the evidence"
        " tools first, then present your strongest two or three arguments."
    ),
    "rebuttal": (
        "Write a rebuttal. Address at least one specific point your opponent"
        " made — quote or paraphrase it — and refute it."
    ),
    "closing": (
        "Write your closing statement. Summarise why your case prevails,"
        " using only evidence already introduced in the debate."
    ),
}


class Debater:
    def __init__(self, agent: ToolUseAgent, model_id: str, side: str):
        self._agent = agent
        self._model_id = model_id
        self.side = side

    def _system_prompt(self, topic: str, format_rules: list[str]) -> str:
        rules = "\n".join(f"- {rule}" for rule in format_rules)
        return (
            f"You are a skilled debater arguing {SIDE_STANCE[self.side]} the"
            f" motion: \"{topic}\".\n"
            f"Debate rules:\n{rules}\n"
            "Cite every factual claim with its source id, e.g. (source: 1001)."
            " Be persuasive but rigorous. Keep statements under 400 words."
        )

    def _user_prompt(
        self,
        phase: str,
        turns: list[dict],
        notes: list[dict],
        remaining_evidence: int,
    ) -> str:
        return (
            "<debate_transcript>\n"
            f"{format_transcript(turns, self.side)}\n"
            "</debate_transcript>\n\n"
            "<your_research_notes>\n"
            f"{format_notes(notes)}\n"
            "</your_research_notes>\n\n"
            "Your research notes are private — your opponent cannot see"
            " them. The transcript is public.\n"
            f"Evidence requests remaining this phase: {remaining_evidence}.\n\n"
            f"{PHASE_INSTRUCTIONS[phase]}"
        )

    async def speak(
        self,
        *,
        phase: str,
        topic: str,
        format_rules: list[str],
        turns: list[dict],
        notes: list[dict],
        remaining_evidence: int,
        on_tool: ToolCallback | None = None,
    ) -> AgentResult:
        # Code-enforced format rule: no new evidence in closing statements.
        use_tools = phase != "closing"
        return await self._agent.run(
            model_id=self._model_id,
            system=self._system_prompt(topic, format_rules),
            user_prompt=self._user_prompt(phase, turns, notes, remaining_evidence),
            use_tools=use_tools,
            hint=f"debater:{self.side}:{phase}",
            on_tool=on_tool,
        )
