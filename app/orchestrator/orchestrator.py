"""The debate orchestrator.

Walks the state machine phase by phase, calling agents in order and
holding all authority over flow (ADR 0002). Every step is emitted as a
typed event — persisted for replay and published to live SSE subscribers —
and every agent call is recorded with token/latency metrics.

Responsibilities per phase:
    OPENING/REBUTTAL/CLOSING  pro speaks, then con; research captured into
                              each side's private notebook (ADR 0007)
    VERIFICATION              fact-checker extracts and verifies claims
    JUDGING                   transcript blinded (ADR 0008), judge scores,
                              verdict unblinded and persisted
"""

import asyncio
import json
import re
from collections import defaultdict

from app.agents.agent import ToolUseAgent
from app.agents.debater import Debater
from app.agents.fact_checker import FactChecker
from app.agents.judge import Judge
from app.agents.llm import LLMProvider
from app.config import DEFAULT_MODELS, HardLimits, resolve_model
from app.evaluation.blind import assign_labels, blind_transcript, unblind
from app.mcp_client.manager import MCPManager
from app.orchestrator.events import DebateEvent, EventType
from app.orchestrator.state_machine import DebatePhase, DebateProgress
from app.storage.db import Database

SIDES = ("pro", "con")

SUPPORTED_VERDICTS = {"supported"}


class EventBus:
    """Fan-out of live debate events to SSE subscribers.

    Publishing None signals end-of-stream to every subscriber.
    """

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, debate_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[debate_id].append(queue)
        return queue

    def unsubscribe(self, debate_id: str, queue: asyncio.Queue) -> None:
        try:
            self._subscribers[debate_id].remove(queue)
        except ValueError:
            pass

    def publish(self, debate_id: str, event: DebateEvent | None) -> None:
        for queue in self._subscribers[debate_id]:
            queue.put_nowait(event)


# Some models leak scratchpads or wrap statements in pseudo-XML despite the
# style instructions. Prompts are advisory; transcript hygiene is not — the
# cleanup is deterministic (same principle as ADR 0003).
_THINKING_BLOCK = re.compile(r"<thinking>.*?</thinking>\s*",
                             re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINKING = re.compile(r"<thinking>.*\Z", re.DOTALL | re.IGNORECASE)
_WRAPPER_TAGS = re.compile(
    r"</?(argument|closing|opening|rebuttal|statement|response|answer)>\s*",
    re.IGNORECASE,
)


def clean_statement(text: str) -> str:
    text = _THINKING_BLOCK.sub("", text)
    text = _UNCLOSED_THINKING.sub("", text)
    text = _WRAPPER_TAGS.sub("", text)
    return text.strip()


def _delta_chunks(text: str, words_per_chunk: int = 12):
    words = text.split()
    for i in range(0, len(words), words_per_chunk):
        yield " ".join(words[i : i + words_per_chunk])


def _notes_from_research(research: list[dict]) -> list[dict]:
    """Turn raw tool results into notebook entries (deterministic capture,
    ADR 0007)."""
    notes = []
    for item in research:
        tool = item["tool"]
        try:
            payload = json.loads(item["result"])
        except (ValueError, TypeError):
            payload = {}

        if tool == "search_sources":
            results = payload.get("results", [])
            content = "\n".join(
                f"{r['source_id']}: {r['title']} — {r['snippet']}" for r in results
            ) or item["result"]
            notes.append({"kind": "search_results", "content": content[:1500],
                          "source_id": None, "title": None})
        elif tool == "get_source_content":
            notes.append({
                "kind": "source_content",
                "source_id": str(payload.get("source_id")
                                 or item["arguments"].get("source_id", "")),
                "title": payload.get("title"),
                "content": str(payload.get("content", item["result"]))[:1500],
            })
        elif tool == "verify_quote":
            notes.append({
                "kind": "quote_check",
                "source_id": str(item["arguments"].get("source_id", "")),
                "title": None,
                "content": item["result"][:500],
            })
    return notes


class DebateOrchestrator:
    def __init__(self, db: Database, mcp: MCPManager, provider: LLMProvider,
                 limits: HardLimits, bus: EventBus):
        self._db = db
        self._mcp = mcp
        self._provider = provider
        self._limits = limits
        self._bus = bus

    async def run_debate(
        self,
        debate: dict,
        format_rules: list[str],
        step: asyncio.Semaphore | None = None,
    ) -> None:
        """Run a debate to completion.

        With ``step`` set (step mode), the orchestrator pauses before every
        unit of work — each statement, the fact-check, the judging — emits
        an awaiting_advance event, and proceeds only when the semaphore is
        released via POST /debates/{id}/advance.
        """
        debate_id = debate["id"]
        seq = 0

        def emit(type_: EventType, payload: dict) -> None:
            nonlocal seq
            seq += 1
            event = DebateEvent(debate_id=debate_id, seq=seq, type=type_,
                                payload=payload)
            self._db.append_event(debate_id, seq, type_.value, payload,
                                  event.timestamp)
            self._bus.publish(debate_id, event)

        try:
            await self._run(debate, format_rules, emit, step)
        except Exception as exc:
            self._db.update_phase(debate_id, DebatePhase.FAILED.value)
            emit(EventType.DEBATE_FAILED, {"error": str(exc)})
        finally:
            self._bus.publish(debate_id, None)  # close live streams

    @staticmethod
    async def _gate(step: asyncio.Semaphore | None, emit, next_unit: str) -> None:
        """In step mode, announce what comes next and wait for an advance."""
        if step is not None:
            emit(EventType.AWAITING_ADVANCE, {"next": next_unit})
            await step.acquire()

    async def _run(self, debate: dict, format_rules: list[str], emit,
                   step: asyncio.Semaphore | None = None) -> None:
        debate_id = debate["id"]
        topic = debate["topic"]
        models = {**DEFAULT_MODELS, **debate["models"]}

        tool_agent = ToolUseAgent(self._provider, self._mcp, self._limits)
        debaters = {
            side: Debater(tool_agent, resolve_model(models[f"debater_{side}"]), side)
            for side in SIDES
        }
        fact_checker = FactChecker(self._provider, self._mcp, self._limits,
                                   resolve_model(models["fact_checker"]))
        judge = Judge(self._provider, self._mcp, self._limits,
                      resolve_model(models["judge"]))

        emit(EventType.DEBATE_STARTED, {
            "topic": topic, "format": debate["format"], "models": models,
            "rebuttal_rounds": debate["rebuttal_rounds"],
        })

        turns: list[dict] = []
        claim_verdicts: list[dict] = []
        progress = DebateProgress(rebuttal_rounds_total=debate["rebuttal_rounds"])

        while True:
            progress = progress.advance()
            self._db.update_phase(debate_id, progress.phase.value)

            if progress.phase == DebatePhase.COMPLETE:
                emit(EventType.DEBATE_COMPLETED, {})
                break

            emit(EventType.PHASE_CHANGED, {
                "phase": progress.phase.value, "round": progress.rebuttal_round,
            })

            if progress.is_speaking_phase:
                for side in SIDES:
                    unit = f"{side} {progress.phase.value}"
                    if progress.rebuttal_round > 0:
                        unit += f" {progress.rebuttal_round}"
                    await self._gate(step, emit, unit)
                    await self._speaking_turn(
                        debate_id, debaters[side], progress, topic,
                        format_rules, turns, emit,
                    )
            elif progress.phase == DebatePhase.VERIFICATION:
                await self._gate(step, emit, "fact-check")
                claim_verdicts = await self._verification(
                    debate_id, fact_checker, turns, emit
                )
            elif progress.phase == DebatePhase.JUDGING:
                await self._gate(step, emit, "blind judging")
                await self._judging(
                    debate_id, judge, topic, turns, claim_verdicts, emit
                )

    async def _speaking_turn(self, debate_id: str, debater: Debater,
                             progress: DebateProgress, topic: str,
                             format_rules: list[str], turns: list[dict],
                             emit) -> None:
        side = debater.side
        phase = progress.phase.value
        round_ = progress.rebuttal_round
        emit(EventType.TURN_STARTED, {"side": side, "phase": phase,
                                      "round": round_})

        async def on_tool(name: str, arguments: dict, text: str,
                          is_error: bool) -> None:
            if not is_error:
                emit(EventType.EVIDENCE_USED, {
                    "side": side, "tool": name, "arguments": arguments,
                })

        result = await debater.speak(
            phase=phase,
            topic=topic,
            format_rules=format_rules,
            turns=turns,
            notes=self._db.get_research_notes(debate_id, side),
            remaining_evidence=self._limits.max_evidence_requests_per_phase,
            on_tool=on_tool,
        )

        for note in _notes_from_research(result.research):
            self._db.add_research_note(debate_id, side, **note)

        statement = clean_statement(result.text)
        for chunk in _delta_chunks(statement):
            emit(EventType.MESSAGE_DELTA, {"side": side, "text": chunk})
        emit(EventType.TURN_COMPLETED, {
            "side": side, "phase": phase, "round": round_,
            "content": statement,
        })

        turn = {"phase": phase, "round": round_, "side": side,
                "content": statement}
        turns.append(turn)
        self._db.add_turn(debate_id, phase, round_, side, statement)
        self._db.add_agent_run(
            debate_id, agent=f"debater_{side}", phase=phase,
            model_id=debater.model_id,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            latency_ms=result.latency_ms, tool_calls=result.tool_calls,
        )

    async def _verification(self, debate_id: str, fact_checker: FactChecker,
                            turns: list[dict], emit) -> list[dict]:
        transcript = "\n\n".join(
            f"[{t['phase']}][{t['side']}] {t['content']}" for t in turns
        )
        outcome = await fact_checker.check(transcript)
        for claim in outcome.claims:
            emit(EventType.CLAIM_VERDICT, claim)
        self._db.add_agent_run(
            debate_id, agent="fact_checker", phase=DebatePhase.VERIFICATION.value,
            model_id=fact_checker.model_id,
            input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
            latency_ms=outcome.latency_ms, tool_calls=outcome.tool_calls,
        )
        return outcome.claims

    async def _judging(self, debate_id: str, judge: Judge, topic: str,
                       turns: list[dict], claim_verdicts: list[dict],
                       emit) -> None:
        mapping = assign_labels(debate_id)
        blinded = blind_transcript(turns, mapping)
        outcome = await judge.judge(topic, blinded, claim_verdicts)
        verdict = outcome.verdict

        winner = unblind(mapping, verdict.winner)
        scores = {side: verdict.scores[label] for side, label in mapping.items()}
        unsupported = [
            claim for claim in claim_verdicts
            if claim["verdict"] not in SUPPORTED_VERDICTS
        ]
        result = {
            "winner": winner,
            "confidence": verdict.confidence,
            "scores": scores,
            "decisive_moment": verdict.decisive_moment,
            "reasoning_summary": verdict.reasoning_summary,
            "blind_mapping": mapping,
            "claim_verdicts": claim_verdicts,
            "unsupported_claims": unsupported,
            "judge_attempts": outcome.attempts,
        }
        self._db.set_result(debate_id, winner, result)
        self._db.add_agent_run(
            debate_id, agent="judge", phase=DebatePhase.JUDGING.value,
            model_id=judge.model_id,
            input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
            latency_ms=outcome.latency_ms, tool_calls=0,
        )
        emit(EventType.JUDGE_RESULT, {
            "winner": winner,
            "confidence": verdict.confidence,
            "scores": scores,
            "decisive_moment": verdict.decisive_moment,
            "reasoning_summary": verdict.reasoning_summary,
            "unsupported_claims": unsupported,
        })
