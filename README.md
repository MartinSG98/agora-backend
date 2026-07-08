# Agora — backend

Orchestration backend for **Agora**, a multi-agent debate and evaluation platform.

Two LLM debater agents argue a topic through a staged debate (opening → rebuttals → closing), a fact-checker verifies cited evidence, and a judge scores the debate against a rubric — blind, so it never knows which model argued which side.

> 🚧 Work in progress — this commit is the project skeleton.

## Architecture (planned)

- **FastAPI** orchestration service with an explicit debate state machine (hard limits — rounds, token caps, evidence quotas — enforced in code, never in prompts).
- **Hand-rolled agent loop** on the **AWS Bedrock Converse API** (model-per-role is configurable: Claude, Nova, ...), with a `MockProvider` fallback so the repo runs locally at zero cost.
- **MCP** integration via the official Python SDK:
  - *Evidence server* (tools) — debaters search and quote sources.
  - *Rules server* (resources + prompts) — the judge reads debate formats and scoring rubrics.
- **Evaluation layer**: blind judging, position-swap runs, per-agent token/latency metrics.
- **SQLite** persistence + **SSE** streaming of debate events (with replay).

## Layout

```
app/
├── main.py          # FastAPI app
├── config.py        # hard limits + model registry (coming)
├── api/             # REST + SSE routes
├── orchestrator/    # debate state machine + event types
├── agents/          # LLM providers, tool-use loop, debater/judge/fact-checker
├── mcp_client/      # MCP stdio client sessions
├── evaluation/      # blind judging, position swap, metrics
└── storage/         # SQLite layer
tests/
```

## Run

```
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
# GET http://127.0.0.1:8000/health
```
