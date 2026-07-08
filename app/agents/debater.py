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


# Small models under-trigger tools when merely invited to use them, so the
# opening/rebuttal instructions are prescriptive about when to call which
# tool. The quota still caps them in code regardless.
PHASE_INSTRUCTIONS = {
    "opening": (
        "Before writing anything, call search_sources with a query about the"
        " motion, then call get_source_content on the most relevant result."
        " Only then write your opening statement, presenting your strongest"
        " two or three arguments, each backed by the sources you just read."
    ),
    "rebuttal": (
        "Write a rebuttal. Address at least one specific point your opponent"
        " made — quote or paraphrase it — and refute it. If your research"
        " notes lack the fact you need, call search_sources or"
        " get_source_content first; if you doubt a quote your opponent"
        " attributed to a source, call verify_quote on it."
    ),
    "closing": (
        "Write your closing statement. Summarise why your case prevails,"
        " using only evidence already introduced in the debate."
    ),
}


class Debater:
    def __init__(self, agent: ToolUseAgent, model_id: str, side: str):
        self._agent = agent
        self.model_id = model_id
        self.side = side

    def _system_prompt(self, topic: str, format_rules: list[str]) -> str:
        rules = "\n".join(f"- {rule}" for rule in format_rules)
        return (
            f"You are a skilled debater arguing {SIDE_STANCE[self.side]} the"
            f" motion: \"{topic}\".\n"
            f"Debate rules:\n{rules}\n"
            "Cite factual claims in the form (source: SOURCE_ID), using ONLY"
            " source ids that appear in your research notes or in evidence"
            " tool results. Never invent a source id — an uncited claim is"
            " better than a fabricated citation, and citations are verified."
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
            model_id=self.model_id,
            system=self._system_prompt(topic, format_rules),
            user_prompt=self._user_prompt(phase, turns, notes, remaining_evidence),
            use_tools=use_tools,
            hint=f"debater:{self.side}:{phase}",
            on_tool=on_tool,
        )
