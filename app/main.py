"""Agora backend — FastAPI entrypoint.

The lifespan wires the whole system together: SQLite, the MCP client
manager (which launches both servers as subprocesses), the LLM provider
(mock by default, Bedrock when AGORA_MOCK_MODE=0), and the orchestrator.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.llm import BedrockProvider, MockProvider
from app.api.routes import router
from app.config import get_settings
from app.mcp_client.manager import MCPManager
from app.orchestrator.orchestrator import DebateOrchestrator, EventBus
from app.storage.db import Database


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    # mock mode also pins the evidence server to offline fixtures, so a
    # default run needs neither AWS nor network
    app.state.mcp = MCPManager(settings.mcp_servers_dir,
                               offline_evidence=settings.mock_mode)
    await app.state.mcp.start()

    provider = (MockProvider() if settings.mock_mode
                else BedrockProvider(settings.aws_region))
    app.state.bus = EventBus()
    app.state.orchestrator = DebateOrchestrator(
        app.state.db, app.state.mcp, provider, settings.limits, app.state.bus
    )
    app.state.debate_tasks: set = set()
    app.state.step_controllers: dict = {}  # debate_id -> advance semaphore

    yield

    for task in list(app.state.debate_tasks):
        task.cancel()
    await app.state.mcp.stop()
    app.state.db.close()


app = FastAPI(
    title="Agora",
    description="Multi-agent debate & evaluation platform",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # frontend origin gets pinned when it exists
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
