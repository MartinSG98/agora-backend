"""MCPManager against both real servers over stdio."""

import json

import pytest

from app.config import REPO_ROOT
from app.mcp_client.manager import EVIDENCE, RULES, MCPManager


@pytest.fixture()
async def manager():
    mgr = MCPManager(REPO_ROOT / "mcp-servers", offline_evidence=True)
    await mgr.start()
    yield mgr
    await mgr.stop()


async def test_manager_holds_sessions_to_both_servers(manager):
    evidence_tools = {tool.name for tool in manager.get_tools(EVIDENCE)}
    assert {"search_sources", "get_source_content", "verify_quote"} <= evidence_tools
    # rules server exposes no tools — its surface is resources + prompts
    assert manager.get_tools(RULES) == []


async def test_call_tool_and_read_resource(manager):
    text, is_error = await manager.call_tool(
        EVIDENCE, "search_sources", {"query": "remote work"}
    )
    assert not is_error
    assert json.loads(text)["results"]

    rubric = json.loads(await manager.read_resource(RULES, "debate://rubrics/default"))
    assert "argument_quality" in rubric["categories"]


async def test_mcp_schema_converts_to_bedrock_toolspec(manager):
    config = MCPManager.to_bedrock_tool_config(manager.get_tools(EVIDENCE))
    specs = {entry["toolSpec"]["name"]: entry["toolSpec"] for entry in config["tools"]}

    search = specs["search_sources"]
    assert search["description"]
    schema = search["inputSchema"]["json"]
    assert schema["type"] == "object"
    assert "query" in schema["properties"]
