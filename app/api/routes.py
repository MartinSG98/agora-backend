"""REST + SSE routes.

POST /debates                      create a debate, orchestration starts
GET  /debates                      list debates
GET  /debates/{id}                 debate detail: transcript, result,
                                   research notebooks (post-debate reveal)
GET  /debates/{id}/events          SSE stream — live while running; stored
                                   events replayed for finished debates
GET  /debates/{id}/metrics         per-agent token/latency/tool-call stats
GET  /models, GET /formats         configuration surface for the frontend
"""

import asyncio
import json
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import (
    DEFAULT_FORMAT,
    DEFAULT_MODELS,
    MODEL_REGISTRY,
    allowed_models,
)
from app.evaluation.position_swap import run_position_swap, swap_debaters
from app.mcp_client.manager import RULES
from app.orchestrator.events import DebateEvent, EventType
from app.orchestrator.state_machine import clamp_rebuttal_rounds

router = APIRouter()

TERMINAL_PHASES = ("complete", "failed")


class CreateDebateRequest(BaseModel):
    topic: str = Field(min_length=8, max_length=300)
    format: str = DEFAULT_FORMAT
    models: dict[str, str] = Field(default_factory=dict)
    rebuttal_rounds: int | None = None
    # pause before every unit of work; advance via POST /debates/{id}/advance
    step_mode: bool = False


async def _prepare_debate(body: CreateDebateRequest, state) -> tuple[dict, dict, int]:
    """Shared validation: returns (models, format_spec, rebuttal_rounds)."""
    unknown_roles = set(body.models) - set(DEFAULT_MODELS)
    if unknown_roles:
        raise HTTPException(422, f"unknown roles: {sorted(unknown_roles)};"
                                 f" valid roles: {sorted(DEFAULT_MODELS)}")
    models = {**DEFAULT_MODELS, **body.models}
    unknown_models = {name for name in models.values()
                      if name not in MODEL_REGISTRY}
    if unknown_models:
        raise HTTPException(422, f"unknown models: {sorted(unknown_models)};"
                                 f" available: {sorted(MODEL_REGISTRY)}")
    allowed = allowed_models()
    blocked = {name for name in models.values() if name not in allowed}
    if blocked:
        raise HTTPException(
            422,
            f"models not on the cost allowlist: {sorted(blocked)}; allowed:"
            f" {sorted(allowed)}. Expand with AGORA_ALLOWED_MODELS if"
            " intentional.",
        )

    try:
        format_spec = json.loads(await state.mcp.read_resource(
            RULES, f"debate://formats/{body.format}"
        ))
    except Exception:
        raise HTTPException(404, f"unknown debate format: {body.format}")

    requested_rounds = (body.rebuttal_rounds if body.rebuttal_rounds is not None
                        else format_spec["rebuttal_rounds"])
    rounds = clamp_rebuttal_rounds(requested_rounds, state.settings.limits)
    return models, format_spec, rounds


def _launch(state, coroutine, name: str) -> None:
    task = asyncio.create_task(coroutine, name=name)
    state.debate_tasks.add(task)
    task.add_done_callback(state.debate_tasks.discard)


@router.post("/debates")
async def create_debate(body: CreateDebateRequest, request: Request):
    state = request.app.state
    models, format_spec, rounds = await _prepare_debate(body, state)

    debate = state.db.create_debate(body.topic, body.format, models, rounds)
    debate_id = debate["id"]

    if body.step_mode:
        gate = asyncio.Semaphore(0)
        state.step_controllers[debate_id] = gate

        async def run_stepped():
            try:
                await state.orchestrator.run_debate(
                    debate, format_spec["rules"], step=gate
                )
            finally:
                state.step_controllers.pop(debate_id, None)

        _launch(state, run_stepped(), f"debate-{debate_id}")
    else:
        _launch(state, state.orchestrator.run_debate(debate, format_spec["rules"]),
                f"debate-{debate_id}")
    return {**debate, "step_mode": body.step_mode}


@router.post("/debates/{debate_id}/advance")
async def advance_debate(debate_id: str, request: Request):
    """Release the next unit of a step-mode debate."""
    state = request.app.state
    gate = state.step_controllers.get(debate_id)
    if gate is None:
        if state.db.get_debate(debate_id) is None:
            raise HTTPException(404, "debate not found")
        raise HTTPException(
            409, "debate is not awaiting an advance (not step mode, or finished)"
        )
    gate.release()
    return {"advanced": True}


@router.get("/debates")
async def list_debates(request: Request):
    return {"debates": request.app.state.db.list_debates()}


@router.get("/debates/{debate_id}")
async def get_debate(debate_id: str, request: Request):
    db = request.app.state.db
    debate = db.get_debate(debate_id)
    if debate is None:
        raise HTTPException(404, "debate not found")
    return {
        **debate,
        "turns": db.get_turns(debate_id),
        # the private notebooks, revealed once the debate is over
        "research_notes": {
            side: db.get_research_notes(debate_id, side) for side in ("pro", "con")
        },
    }


@router.get("/debates/{debate_id}/metrics")
async def get_metrics(debate_id: str, request: Request):
    db = request.app.state.db
    if db.get_debate(debate_id) is None:
        raise HTTPException(404, "debate not found")
    runs = db.get_agent_runs(debate_id)
    totals = {
        "input_tokens": sum(r["input_tokens"] for r in runs),
        "output_tokens": sum(r["output_tokens"] for r in runs),
        "latency_ms": round(sum(r["latency_ms"] for r in runs), 1),
        "tool_calls": sum(r["tool_calls"] for r in runs),
    }
    return {"runs": runs, "totals": totals}


def _stored_event_to_sse(stored: dict, debate_id: str) -> str:
    return DebateEvent(
        debate_id=debate_id, seq=stored["seq"], type=EventType(stored["type"]),
        payload=stored["payload"], timestamp=stored["timestamp"],
    ).to_sse()


@router.get("/debates/{debate_id}/events")
async def stream_events(debate_id: str, request: Request,
                        replay: bool = False, delay: float = 0.05):
    state = request.app.state
    debate = state.db.get_debate(debate_id)
    if debate is None:
        raise HTTPException(404, "debate not found")

    finished = debate["phase"] in TERMINAL_PHASES

    async def replay_stream():
        """Stored events re-emitted verbatim — the zero-cost demo path."""
        for stored in state.db.get_events(debate_id):
            yield _stored_event_to_sse(stored, debate_id)
            if delay > 0:
                await asyncio.sleep(delay)

    async def live_stream():
        # subscribe BEFORE reading history so no event can fall in the gap;
        # dedupe by seq
        queue = state.bus.subscribe(debate_id)
        try:
            last_seq = 0
            for stored in state.db.get_events(debate_id):
                last_seq = stored["seq"]
                yield _stored_event_to_sse(stored, debate_id)
            while True:
                event = await queue.get()
                if event is None:  # orchestrator closed the stream
                    break
                if event.seq <= last_seq:
                    continue
                yield event.to_sse()
        finally:
            state.bus.unsubscribe(debate_id, queue)

    generator = replay_stream() if (replay or finished) else live_stream()
    return StreamingResponse(generator, media_type="text/event-stream")


@router.post("/evaluations/position-swap")
async def create_position_swap(body: CreateDebateRequest, request: Request):
    """Run the same topic twice with debater models exchanging sides —
    does the win follow the model or the position? (ADR 0008)"""
    state = request.app.state
    models, format_spec, rounds = await _prepare_debate(body, state)

    debate_a = state.db.create_debate(body.topic, body.format, models, rounds)
    debate_b = state.db.create_debate(
        body.topic, body.format, swap_debaters(models), rounds
    )
    evaluation = state.db.create_evaluation(
        "position_swap", body.topic, [debate_a["id"], debate_b["id"]]
    )
    _launch(
        state,
        run_position_swap(state.db, state.orchestrator, evaluation["id"],
                          [debate_a, debate_b], format_spec["rules"]),
        f"evaluation-{evaluation['id']}",
    )
    return evaluation


@router.get("/evaluations")
async def list_evaluations(request: Request):
    return {"evaluations": request.app.state.db.list_evaluations()}


@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(evaluation_id: str, request: Request):
    db = request.app.state.db
    evaluation = db.get_evaluation(evaluation_id)
    if evaluation is None:
        raise HTTPException(404, "evaluation not found")
    return {
        **evaluation,
        "debates": [db.get_debate(d) for d in evaluation["debate_ids"]],
    }


@router.get("/models")
async def list_models():
    return {
        "registry": MODEL_REGISTRY,
        "defaults": DEFAULT_MODELS,
        "allowed": sorted(allowed_models()),
    }


@router.get("/config")
async def get_config(request: Request):
    """Runtime configuration the UI should not hardcode: whether this
    instance spends money, and the hard limits enforced in code."""
    settings = request.app.state.settings
    return {"mock_mode": settings.mock_mode, "limits": asdict(settings.limits)}


@router.get("/rubric")
async def get_rubric(request: Request):
    """The scoring rubric, read from the rules MCP server — the same
    resource the judge reads, so the UI can never drift from it."""
    return json.loads(await request.app.state.mcp.read_resource(
        RULES, "debate://rubrics/default"
    ))


@router.get("/formats")
async def list_formats(request: Request):
    mcp = request.app.state.mcp
    index = json.loads(await mcp.read_resource(RULES, "debate://formats"))
    formats = []
    for name in index["formats"]:
        formats.append(json.loads(
            await mcp.read_resource(RULES, f"debate://formats/{name}")
        ))
    return {"formats": formats}
