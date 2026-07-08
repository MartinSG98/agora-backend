"""MCP client manager.

Owns long-lived stdio sessions to both MCP servers for the lifetime of the
backend process (started/stopped from the FastAPI lifespan). Agents never
talk to servers directly — they go through this manager, which is also
where MCP tool schemas are converted to Bedrock Converse ``toolSpec``
format.

Server names: ``evidence`` (tools for debaters/fact-checker) and ``rules``
(resources + prompts for the judge/orchestrator).
"""

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EVIDENCE = "evidence"
RULES = "rules"


class MCPManager:
    def __init__(self, servers_dir: Path, offline_evidence: bool = False):
        env = dict(os.environ)
        if offline_evidence:
            env["AGORA_EVIDENCE_OFFLINE"] = "1"
        self._params: dict[str, StdioServerParameters] = {
            EVIDENCE: StdioServerParameters(
                command=sys.executable,
                args=[str(servers_dir / "evidence" / "server.py")],
                env=env,
            ),
            RULES: StdioServerParameters(
                command=sys.executable,
                args=[str(servers_dir / "rules" / "server.py")],
                env=dict(os.environ),
            ),
        }
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, list] = {}
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()
        self._startup_error: BaseException | None = None

    async def start(self) -> None:
        """Launch the servers and open sessions.

        anyio cancel scopes (used inside the MCP stdio transport) must be
        entered and exited in the same asyncio task, so all context managers
        live inside one dedicated background task for the manager's whole
        lifetime; start()/stop() only signal it.
        """
        self._task = asyncio.create_task(self._run(), name="mcp-manager")
        await self._ready.wait()
        if self._startup_error is not None:
            await self._task
            raise self._startup_error

    async def _run(self) -> None:
        try:
            async with AsyncExitStack() as stack:
                for name, params in self._params.items():
                    read, write = await stack.enter_async_context(
                        stdio_client(params)
                    )
                    session = await stack.enter_async_context(
                        ClientSession(read, write)
                    )
                    await session.initialize()
                    self._sessions[name] = session
                    listed = await session.list_tools()
                    self._tools[name] = listed.tools
                self._ready.set()
                await self._closing.wait()
        except BaseException as exc:  # surface startup failures to start()
            self._startup_error = exc
        finally:
            self._sessions.clear()
            self._tools.clear()
            self._ready.set()

    async def stop(self) -> None:
        if self._task is not None:
            self._closing.set()
            await self._task
            self._task = None

    def get_tools(self, server: str) -> list:
        """Cached MCP tool definitions for a server (mcp.types.Tool)."""
        return self._tools[server]

    async def call_tool(
        self, server: str, name: str, arguments: dict
    ) -> tuple[str, bool]:
        """Execute a tool; returns (concatenated text content, is_error)."""
        result = await self._sessions[server].call_tool(name, arguments)
        text = "\n".join(
            block.text for block in result.content if getattr(block, "text", None)
        )
        return text, bool(result.isError)

    async def read_resource(self, server: str, uri) -> str:
        result = await self._sessions[server].read_resource(uri)
        return result.contents[0].text

    @staticmethod
    def to_bedrock_tool_config(tools: list) -> dict:
        """Convert MCP tool definitions to a Bedrock Converse toolConfig.

        MCP publishes JSON Schema for tool inputs; Converse expects the same
        schema under toolSpec.inputSchema.json — the conversion is direct.
        """
        return {
            "tools": [
                {
                    "toolSpec": {
                        "name": tool.name,
                        "description": tool.description or tool.name,
                        "inputSchema": {"json": tool.inputSchema},
                    }
                }
                for tool in tools
            ]
        }
