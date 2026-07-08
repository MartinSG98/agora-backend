"""Agora Rules MCP server.

The counterpart to the evidence server: where evidence exposes *tools*,
this server exposes the other half of the MCP spec — *resources* and
*prompts*. The judge reads the scoring rubric and fallacies catalogue from
here; the orchestrator reads debate format definitions.

Resources:
    debate://formats               index of available format names
    debate://formats/{name}        format definition (rounds, rules)
    debate://rubrics/default       weighted scoring rubric
    debate://fallacies/catalogue   logical fallacies the judge may penalise

Note: format files declare a rebuttal_rounds count, but the backend clamps
it against the hard limit in code — a resource is data, not an authority.
"""

import json
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agora-rules")

DATA = Path(__file__).parent / "data"

_FORMAT_NAME = re.compile(r"^[a-z0-9_-]+$")


@mcp.resource("debate://formats")
def list_formats() -> str:
    """Index of available debate format names."""
    names = sorted(path.stem for path in (DATA / "formats").glob("*.json"))
    return json.dumps({"formats": names})


@mcp.resource("debate://formats/{name}")
def get_format(name: str) -> str:
    """A debate format definition: rebuttal rounds and rules."""
    if not _FORMAT_NAME.match(name):
        raise ValueError(f"invalid format name: {name!r}")
    path = DATA / "formats" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"unknown debate format: {name}")
    return path.read_text(encoding="utf-8")


@mcp.resource("debate://rubrics/default")
def get_rubric() -> str:
    """The weighted scoring rubric the judge scores against."""
    return (DATA / "rubric_default.json").read_text(encoding="utf-8")


@mcp.resource("debate://fallacies/catalogue")
def get_fallacies() -> str:
    """Catalogue of logical fallacies the judge may penalise."""
    return (DATA / "fallacies.json").read_text(encoding="utf-8")


@mcp.prompt()
def prepare_opening_statement(topic: str, side: str) -> str:
    """Template guiding a debater's opening statement."""
    return (
        f"You are debating the motion: \"{topic}\". You argue {side}.\n"
        "Write your opening statement. Research the topic first with the"
        " evidence tools, present your two or three strongest arguments, and"
        " cite every factual claim with its source_id, e.g. (source: 1001)."
    )


@mcp.prompt()
def prepare_rebuttal(topic: str, side: str) -> str:
    """Template guiding a debater's rebuttal."""
    return (
        f"You are debating the motion: \"{topic}\". You argue {side}.\n"
        "Write a rebuttal. Quote or paraphrase at least one specific point"
        " your opponent made and refute it with evidence or reasoning."
        " Cite sources by source_id where you rely on facts."
    )


@mcp.prompt()
def prepare_closing_statement(topic: str, side: str) -> str:
    """Template guiding a debater's closing statement."""
    return (
        f"You are debating the motion: \"{topic}\". You argue {side}.\n"
        "Write your closing statement. Summarise why your case prevails,"
        " referencing evidence already introduced. Do not introduce new"
        " evidence."
    )


@mcp.prompt()
def judge_debate(topic: str) -> str:
    """Template framing the judge's evaluation task."""
    return (
        f"You are judging a debate on the motion: \"{topic}\".\n"
        "Score each participant against every rubric category from"
        " debate://rubrics/default on a 0-10 scale, check statements against"
        " the fallacies catalogue, and decide a winner. Judge only what is"
        " in the transcript."
    )


if __name__ == "__main__":
    mcp.run()
