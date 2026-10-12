"""
src/a2a_services/a2a_client.py

Client utilities for calling A2A services.

The Progress Coach uses this to delegate quiz tasks to the
Quiz Generator A2A service instead of calling it directly.

Why a separate client module?
  Keeps the HTTP/protocol details out of agent code.
  The Progress Coach just calls delegate_quiz_task() and gets
  a result dict back, it doesn't need to know anything about
  JSON-RPC, Agent Cards, or HTTP.

Trace propagation (silent-drift-lab):
  send_task() adds trace headers to the request so the service can continue
  the caller's trace (see trace_headers()). Which headers depends on
  TELEMETRY_MODE, read at call time:
    otel   : W3C `traceparent` (+ tracestate/baggage) from the OTel propagator
    native : Opik's `opik_trace_id` / `opik_parent_span_id` from the active @track span
  Nothing is added when no trace is active; the service copes with bare requests.
"""

import json
import os
import uuid
import httpx

# Read from env so non-localhost deployments work correctly.
# These are the defaults used when callers don't pass an explicit URL.
QUIZ_SERVICE_URL = os.getenv("QUIZ_SERVICE_URL", "http://localhost:9001")

# How long to wait for the quiz service to respond.
# Quiz generation + grading takes 15-60s depending on model size.
DEFAULT_TIMEOUT = 120.0


def trace_headers() -> dict[str, str]:
    """
    HTTP headers that let the remote service join the current trace.

    Returns {} when nothing is being traced. Both lookups are best-effort:
    an import error or "no span in context" must never break the A2A call.
    """
    headers: dict[str, str] = {}
    mode = os.getenv("TELEMETRY_MODE", "native").lower()
    if mode == "off":
        return headers

    # W3C traceparent: only when an OTel span is live in this process.
    try:
        from observability.otel_setup import inject_trace_headers

        headers = inject_trace_headers(headers)
    except Exception:
        pass

    # Opik native ids: only when a @track span is on the context stack.
    if mode == "native":
        try:
            from opik import opik_context

            headers.update(opik_context.get_distributed_trace_headers())
        except Exception:
            pass

    return headers


def discover_agent(base_url: str) -> dict:
    """
    Fetch an agent's Agent Card to discover its capabilities.

    Args:
        base_url: The agent's base URL (e.g. 'http://localhost:9001')

    Returns:
        The parsed Agent Card dict, or {} if unreachable.
    """
    card_url = f"{base_url.rstrip('/')}/.well-known/agent-card.json"
    try:
        response = httpx.get(card_url, timeout=5.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[A2A Client] Cannot reach {card_url}: {e}")
        return {}


def _text_parts(result: dict) -> list[str]:
    """Collect text parts from a Message or Task result (accepts 'kind' and legacy 'type')."""
    texts: list[str] = []

    def take(parts):
        for part in parts or []:
            if part.get("kind", part.get("type")) == "text" and part.get("text") is not None:
                texts.append(part["text"])

    take(result.get("parts"))                          # result is a Message
    for artifact in result.get("artifacts") or []:     # result is a Task with artifacts
        take(artifact.get("parts"))
    take(((result.get("status") or {}).get("message") or {}).get("parts"))  # Task status message
    return texts


def send_task(
    base_url: str,
    message_text: str,
    task_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """
    Submit a task to an A2A agent and return the result.

    Constructs a JSON-RPC 2.0 `message/send` request (A2A protocol 0.3,
    served at the agent's root URL), sends it with trace headers, and
    extracts the result text from the response envelope.

    Args:
        base_url:     Agent base URL.
        message_text: JSON string payload for the task.
        task_id:      Optional message ID (auto-generated if not provided).
        timeout:      Seconds to wait before giving up.

    Returns:
        Parsed result dict from the agent, or {"error": ...} on failure.
    """
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "message/send",
        "params": {
            "message": {
                "messageId": task_id or str(uuid.uuid4()),
                "role":      "user",
                "parts":     [{"kind": "text", "text": message_text}],
            },
        },
    }

    url = f"{base_url.rstrip('/')}/"
    try:
        response = httpx.post(url, json=payload, headers=trace_headers(), timeout=timeout)
        response.raise_for_status()
        data = response.json()

        if data.get("error"):
            err = data["error"]
            return {"error": f"A2A error {err.get('code')}: {err.get('message')}"}

        result = data.get("result", {})
        for text in _text_parts(result):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}

        return result

    except httpx.TimeoutException:
        return {"error": f"Quiz service timed out after {timeout}s"}
    except httpx.ConnectError:
        return {"error": f"Cannot connect to quiz service at {url}"}
    except Exception as e:
        return {"error": f"A2A task failed: {type(e).__name__}: {e}"}


def delegate_quiz_task(
    topic: str,
    explanation: str,
    answers: list[str] | None = None,
    quiz_service_url: str = QUIZ_SERVICE_URL,
    questions: list[dict] | None = None,
) -> dict:
    """
    High-level helper: delegate a quiz task to the Quiz A2A service.

    This is the function the Progress Coach calls. It handles all the
    A2A protocol details, JSON-RPC envelope, response parsing, errors.

    Args:
        topic:            Topic to quiz on.
        explanation:      The Explainer's output (context for question gen).
        answers:          Optional list of pre-collected answers.
                          If None or empty, service returns questions only.
        quiz_service_url: URL of the Quiz A2A service.

    Returns:
        Result dict with keys:
          status:   "questions_ready" | "graded" | "error"
          topic:    the topic
          score:    average score (only when graded)
          weak_areas: list of missed concepts (only when graded)
          questions: list of question dicts (always present on success)
    """
    payload = json.dumps({
        "topic":       topic,
        "explanation": explanation,
        "answers":     answers or [],
        # Optional: the questions the answers belong to (second-opinion grading).
        "questions":   questions or [],
    })

    return send_task(quiz_service_url, payload)


def is_quiz_service_available(
    quiz_service_url: str = QUIZ_SERVICE_URL,
) -> bool:
    """
    Quick health check: is the quiz service reachable?

    Used by the Progress Coach to decide whether to use A2A or
    fall back to the local quiz generator.

    Returns True if the Agent Card can be fetched, False otherwise.
    """
    card = discover_agent(quiz_service_url)
    return bool(card)


STUDY_BUDDY_URL = os.getenv("STUDY_BUDDY_URL", "http://localhost:9002")


def request_study_assistance(
    topic: str,
    explanation: str,
    weak_areas: list[str] | None = None,
    study_buddy_url: str = STUDY_BUDDY_URL,
) -> dict:
    """
    Request supplementary study assistance from the CrewAI Study Buddy.

    Called by the Progress Coach when a student scores below the pass
    threshold and could benefit from a different explanation angle.

    Args:
        topic:           The topic the student is studying.
        explanation:     The original Explainer output (context).
        weak_areas:      Concepts the student struggled with.
        study_buddy_url: URL of the CrewAI Study Buddy A2A service.

    Returns:
        Result dict with keys:
          source:     "crewai_study_buddy"
          topic:      the topic
          assistance: supplementary explanation text
          status:     "complete" | "error"
    """
    payload = json.dumps({
        "topic":       topic,
        "explanation": explanation,
        "weak_areas":  weak_areas or [],
    })

    return send_task(study_buddy_url, payload, timeout=180.0)


def is_study_buddy_available(
    study_buddy_url: str = STUDY_BUDDY_URL,
) -> bool:
    """Check if the CrewAI Study Buddy service is reachable."""
    card = discover_agent(study_buddy_url)
    return bool(card)
