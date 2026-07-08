"""Integration tests for the two MCP servers, over real stdio transport.

Each test launches the server as a subprocess and speaks the MCP protocol
through the official client — the same path the backend uses in production.
The evidence server runs with AGORA_EVIDENCE_OFFLINE=1 so no test ever
touches the network.
"""

import json
import os
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import REPO_ROOT

EVIDENCE_SERVER = REPO_ROOT / "mcp-servers" / "evidence" / "server.py"
RULES_SERVER = REPO_ROOT / "mcp-servers" / "rules" / "server.py"


@asynccontextmanager
async def mcp_session(server_path, extra_env=None):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        env={**os.environ, **(extra_env or {})},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def tool_json(result) -> dict:
    """Parse the JSON payload of a tool result."""
    return json.loads(result.content[0].text)


# -- evidence server (tools) --------------------------------------------------


async def test_evidence_exposes_research_tools():
    async with mcp_session(EVIDENCE_SERVER, {"AGORA_EVIDENCE_OFFLINE": "1"}) as session:
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"search_sources", "get_source_content", "verify_quote"} <= names


async def test_evidence_search_and_fetch_offline():
    async with mcp_session(EVIDENCE_SERVER, {"AGORA_EVIDENCE_OFFLINE": "1"}) as session:
        search = tool_json(
            await session.call_tool("search_sources", {"query": "remote work"})
        )
        assert search["results"], "offline search must return fixture sources"
        source_id = search["results"][0]["source_id"]

        content = tool_json(
            await session.call_tool("get_source_content", {"source_id": source_id})
        )
        assert content["source_id"] == source_id
        assert len(content["content"]) > 50


async def test_evidence_verify_quote_verdicts():
    async with mcp_session(EVIDENCE_SERVER, {"AGORA_EVIDENCE_OFFLINE": "1"}) as session:
        supported = tool_json(
            await session.call_tool(
                "verify_quote",
                {
                    "source_id": "1001",
                    "quote": "remote workers reported higher job satisfaction",
                },
            )
        )
        assert supported["verdict"] == "supported"

        fabricated = tool_json(
            await session.call_tool(
                "verify_quote",
                {"source_id": "1001", "quote": "productivity dropped by 90 percent"},
            )
        )
        assert fabricated["verdict"] in ("not_found", "partially_supported")

        missing = tool_json(
            await session.call_tool(
                "verify_quote", {"source_id": "9999", "quote": "anything"}
            )
        )
        assert missing["verdict"] == "source_not_found"


# -- rules server (resources + prompts) ---------------------------------------


async def test_rules_resources():
    async with mcp_session(RULES_SERVER) as session:
        index = json.loads(
            (await session.read_resource("debate://formats")).contents[0].text
        )
        assert {"oxford", "casual"} <= set(index["formats"])

        oxford = json.loads(
            (await session.read_resource("debate://formats/oxford")).contents[0].text
        )
        assert oxford["rebuttal_rounds"] == 2
        assert any("closing" in rule.lower() for rule in oxford["rules"])

        rubric = json.loads(
            (await session.read_resource("debate://rubrics/default")).contents[0].text
        )
        assert "argument_quality" in rubric["categories"]
        weights = sum(c["weight"] for c in rubric["categories"].values())
        assert abs(weights - 1.0) < 1e-9

        fallacies = json.loads(
            (await session.read_resource("debate://fallacies/catalogue"))
            .contents[0].text
        )
        assert len(fallacies["fallacies"]) >= 5


async def test_rules_prompts():
    async with mcp_session(RULES_SERVER) as session:
        prompts = await session.list_prompts()
        names = {prompt.name for prompt in prompts.prompts}
        assert {
            "prepare_opening_statement",
            "prepare_rebuttal",
            "prepare_closing_statement",
            "judge_debate",
        } <= names

        opening = await session.get_prompt(
            "prepare_opening_statement",
            {"topic": "AI will create more jobs than it destroys", "side": "for"},
        )
        text = opening.messages[0].content.text
        assert "AI will create more jobs" in text
        assert "source_id" in text
