"""
src/a2a_services/quiz_service.py

The Quiz Generator exposed as a standalone A2A service.

This turns the quiz_generator agent logic into a network service
that any A2A-compatible agent can call, regardless of framework.

Architecture:
  - A2A server (Starlette/uvicorn) handles HTTP and protocol
  - QuizAgentExecutor contains the actual quiz logic
  - Agent Card describes capabilities to callers
  - InMemoryTaskStore tracks task state

Run standalone:
  python src/a2a_services/quiz_service.py

Then discover:
  curl http://localhost:9001/.well-known/agent-card.json

Submit a task:
  See src/a2a_services/a2a_client.py for the client.
"""

import asyncio
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

# Ensure src/ is on path when running as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()  # same .env as main.py: OPIK_PROJECT_NAME, TELEMETRY_MODE, OTEL_* (cwd-independent)

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from a2a.utils.message import new_agent_text_message

from agents.quiz_generator import generate_questions, grade_answer


# ─────────────────────────────────────────────────────────────────────────────
# Trace continuation across the A2A hop
#
# The caller (a2a_client.send_task) attaches trace headers to the JSON-RPC
# request. a2a-sdk copies the raw HTTP headers into call_context.state["headers"];
# traced_request() turns them into a span that nests under the caller's trace.
# TELEMETRY_MODE is read per request so the service never needs a restart to
# follow the client's track. With no headers the request is traced standalone.
# ─────────────────────────────────────────────────────────────────────────────

PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab")


def _incoming_headers(context: RequestContext) -> dict[str, str]:
    """Lower-cased HTTP headers of the JSON-RPC request (a2a-sdk: call_context.state['headers'])."""
    call_ctx = getattr(context, "call_context", None)
    state = getattr(call_ctx, "state", None) or {}
    headers = state.get("headers") or {}
    return {str(k).lower(): str(v) for k, v in dict(headers).items()}


def _request_summary(request_data: dict) -> dict:
    return {
        "topic": request_data.get("topic"),
        "answers_provided": len(request_data.get("answers") or []),
        "explanation_chars": len(request_data.get("explanation") or ""),
    }


def _result_summary(result: dict) -> dict:
    return {
        "status": result.get("status"),
        "topic": result.get("topic"),
        "score": result.get("score"),
        "weak_areas": result.get("weak_areas"),
        "questions": len(result.get("questions") or []),
    }


@contextmanager
def traced_request(headers: dict[str, str], request_data: dict):
    """
    Wrap one A2A request in a span that continues the caller's trace.

      TELEMETRY_MODE=otel   : OTel SERVER span, child of the incoming W3C `traceparent`
                              (observability.otel_setup.server_span). The LangChain calls
                              inside generate_questions/grade_answer become its children
                              through the LangChain configure hook, across asyncio.to_thread.
      TELEMETRY_MODE=native : opik.start_as_current_span(..., opik_distributed_trace_headers=…)
                              built from `opik_trace_id` / `opik_parent_span_id`, so the
                              @track spans inside generate_questions/grade_answer nest under
                              the caller's span. Without headers it opens its own trace.
      TELEMETRY_MODE=off    : nothing.

    Yields record(result): call it with the result dict to set the span output.

    Which track: the headers decide when they are present (`traceparent` → otel,
    `opik_trace_id` → native), so one running service serves native and OTel
    callers side by side. A bare request falls back to this process's
    TELEMETRY_MODE, read here at request time.
    """
    mode = os.getenv("TELEMETRY_MODE", "native").lower()
    if "traceparent" in headers:
        mode = "otel"
    elif "opik_trace_id" in headers:
        mode = "native"
    summary = _request_summary(request_data)

    if mode == "otel":
        try:
            from observability.otel_setup import server_span, set_span_json
        except Exception as e:  # OTel deps missing: serve the request untraced
            print(f"[Quiz A2A] OTel unavailable ({e}); request untraced")
            yield lambda result: None
            return
        with server_span("quiz_service.execute", headers, agent_name="quiz-generator-service") as span:
            set_span_json(span, "input", summary)
            yield lambda result: set_span_json(span, "output", _result_summary(result))
        return

    if mode == "native":
        try:
            import opik
        except Exception as e:
            print(f"[Quiz A2A] opik unavailable ({e}); request untraced")
            yield lambda result: None
            return
        kwargs = {}
        if headers.get("opik_trace_id"):
            kwargs["opik_distributed_trace_headers"] = {
                "opik_trace_id": headers["opik_trace_id"],
                "opik_parent_span_id": headers.get("opik_parent_span_id"),
            }
        with opik.start_as_current_span(
            "quiz_service.execute", type="general", input=summary, project_name=PROJECT, **kwargs
        ) as span_data:
            def record(result: dict) -> None:
                span_data.update(output=_result_summary(result))

            yield record
        return

    yield lambda result: None


# ─────────────────────────────────────────────────────────────────────────────
# Agent Card
#
# The Agent Card is the "business card" of this A2A service.
# It's served automatically at /.well-known/agent-card.json
# Any caller fetches this first to discover what the service can do.
# ─────────────────────────────────────────────────────────────────────────────

QUIZ_SKILL = AgentSkill(
    id="generate_and_grade_quiz",
    name="Generate and Grade Quiz",
    description=(
        "Given a topic and optional explanation text, generates quiz questions "
        "that test conceptual understanding. If answers are provided, grades "
        "each answer and returns scores with identified weak areas."
    ),
    tags=["quiz", "assessment", "education", "grading"],
    examples=[
        "Generate a quiz on Python closures",
        "Grade these answers for a decorators quiz: ...",
    ],
)

QUIZ_AGENT_CARD = AgentCard(
    name="Quiz Generator Service",
    description=(
        "A specialised quiz generation and grading service built with LangGraph. "
        "Generates questions that test genuine understanding, grades answers "
        "using LLM-as-judge, and identifies weak areas for further study. "
        "Framework-agnostic: works with any A2A-compatible agent."
    ),
    url="http://localhost:9001/",
    version="1.0.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=False),
    skills=[QUIZ_SKILL],
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Executor
#
# The AgentExecutor is where the actual work happens.
# The A2A framework calls execute() for every incoming task.
# We parse the request, run quiz logic, and emit the result.
# ─────────────────────────────────────────────────────────────────────────────

class QuizAgentExecutor(AgentExecutor):
    """
    Handles incoming A2A quiz tasks.

    Request format (JSON in the text part):
    {
        "topic":       "Python Closures",
        "explanation": "A closure is...",   (optional)
        "answers":     ["answer 1", ...]    (optional, omit to just get questions)
    }

    Response format (JSON in the text part):
    {
        "status":   "questions_ready" | "graded",
        "topic":    "Python Closures",
        "questions": [...],            (always present)
        "score":    0.75,              (only when answers provided)
        "graded_questions": [...],     (only when answers provided)
        "weak_areas": [...]            (only when answers provided)
    }
    """

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Process an incoming quiz task.

        Trace continuation: the caller may send W3C `traceparent` (OTel track)
        or `opik_trace_id` / `opik_parent_span_id` (native track). traced_request()
        reads whichever is present and nests this request under the caller's
        trace; with no headers the request is traced on its own.
        """

        # ── Parse request ─────────────────────────────────────────────
        # a2a-sdk 0.3: the user message is context.message; get_user_input()
        # joins its text parts (each Part wraps the TextPart in .root).
        request_text = context.get_user_input()

        try:
            request_data = json.loads(request_text)
        except json.JSONDecodeError:
            # If it's not JSON, treat the whole thing as a topic
            request_data = {"topic": request_text, "explanation": ""}

        headers = _incoming_headers(context)
        with traced_request(headers, request_data) as record:
            result = await self._run(request_data, event_queue)
            record(result)

    async def _run(self, request_data: dict, event_queue: EventQueue) -> dict:
        """The quiz work itself; returns the result dict it emitted."""
        topic = request_data.get("topic", "General Knowledge")
        explanation = request_data.get("explanation", "")
        provided_answers = request_data.get("answers", [])

        print(f"[Quiz A2A] Task received: topic='{topic}', "
              f"answers_provided={len(provided_answers)}")

        # ── Generate questions (or reuse the caller's) ─────────────────
        # When the caller already asked the questions (second-opinion grading),
        # it sends them along so the grades refer to the same questions.
        questions_data = request_data.get("questions") or []
        if not questions_data:
            # Run in thread pool since it's a synchronous blocking call
            questions_data = await asyncio.to_thread(
                generate_questions, topic, explanation, 3
            )

        if not provided_answers:
            # No answers provided, return questions only
            result = {
                "status": "questions_ready",
                "topic": topic,
                "questions": questions_data,
                "message": (
                    "Questions generated. Submit again with 'answers' key to grade."
                ),
            }
        else:
            # Grade the provided answers
            graded = []
            total_score = 0.0
            weak_areas = []

            for q_data, answer in zip(questions_data, provided_answers):
                grade = await asyncio.to_thread(
                    grade_answer,
                    q_data["question"],
                    q_data["expected_answer"],
                    answer,
                )
                score = float(grade.get("score", 0.0))
                total_score += score
                missing = grade.get("missing_concept", "")
                if missing:
                    weak_areas.append(missing)

                graded.append({
                    "question":  q_data["question"],
                    "answer":    answer,
                    "score":     score,
                    "correct":   bool(grade.get("correct", False)),
                    "feedback":  grade.get("feedback", ""),
                })

            avg_score = total_score / len(questions_data) if questions_data else 0.0

            result = {
                "status":           "graded",
                "topic":            topic,
                "score":            avg_score,
                "questions":        questions_data,
                "graded_questions": graded,
                "weak_areas":       list(set(weak_areas)),
            }

        print(f"[Quiz A2A] Task complete: status={result['status']}")

        # ── Emit result ───────────────────────────────────────────────
        # a2a-sdk 0.3: a Message needs message_id and Part-wrapped parts;
        # new_agent_text_message() builds exactly that.
        await event_queue.enqueue_event(
            new_agent_text_message(json.dumps(result, indent=2))
        )
        return result

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Handle task cancellation, not needed for synchronous quiz tasks."""
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Server setup
# ─────────────────────────────────────────────────────────────────────────────

def create_quiz_server():
    """Build the A2A Starlette application."""
    request_handler = DefaultRequestHandler(
        agent_executor=QuizAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(
        agent_card=QUIZ_AGENT_CARD,
        http_handler=request_handler,
    )
    return app.build()


if __name__ == "__main__":
    print("[Quiz A2A Service] Starting on http://localhost:9001")
    print("[Quiz A2A Service] Agent Card: "
          "http://localhost:9001/.well-known/agent-card.json")
    print("[Quiz A2A Service] Press Ctrl+C to stop\n")
    uvicorn.run(create_quiz_server(), host="0.0.0.0", port=9001, log_level="warning")
