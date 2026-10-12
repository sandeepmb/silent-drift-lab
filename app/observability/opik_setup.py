"""
app/observability/opik_setup.py

Opik wiring for the Learning Accelerator.

One function matters: get_run_config(session_id, graph). It returns the
LangGraph run config with an OpikTracer attached, so that every node,
every LLM call and every tool call in a study session lands in Opik as
one trace, grouped into a thread by session id.

TELEMETRY_MODE (env) selects the track:
  native : OpikTracer callback (full fidelity: graph view, usage, cost)
  otel   : OpenTelemetry only, exported to the Collector → Opik over OTLP/HTTP
           (see app/observability/otel_setup.py)
  off    : no tracing

Nothing in the agents imports this module. Observability stays at the edge.
"""

from __future__ import annotations

import os
from typing import Any

MODE = os.getenv("TELEMETRY_MODE", "native").lower()
PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# Keep one tracer per session so flush() and created_traces() work.
_tracers: dict[str, Any] = {}


def telemetry_mode() -> str:
    return MODE


def _tags(extra: list[str] | None) -> list[str]:
    tags = ["learning-accelerator", f"model:{MODEL}"]
    scenario = os.getenv("DRIFT_SCENARIO", "none")
    if scenario and scenario != "none":
        tags.append(f"drift:{scenario}")
    if extra:
        tags.extend(extra)
    return tags


def get_run_config(
    session_id: str,
    graph: Any,
    user_id: str = "local",
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Build the LangGraph run config for one study session.

    configurable.thread_id is used twice: by LangGraph for checkpoints and
    by Opik to group every trace of the session into one Thread.
    """
    config: dict = {"configurable": {"thread_id": session_id}}

    if MODE == "native":
        from opik.integrations.langchain import OpikTracer

        from observability import ollama_usage  # noqa: F401  registers the Ollama usage extractor

        tracer = OpikTracer(
            graph=graph.get_graph(xray=True),      # renders the graph in the Opik UI
            project_name=PROJECT,
            thread_id=session_id,
            tags=_tags(tags),
            metadata={
                "user_id": user_id,
                "model": MODEL,
                "framework": "langgraph",
                "drift_scenario": os.getenv("DRIFT_SCENARIO", "none"),
                "student_mode": os.getenv("STUDENT_MODE", "interactive"),
                **(metadata or {}),
            },
        )
        _tracers[session_id] = tracer
        config["callbacks"] = [tracer]
        print(f"[Observability] Opik native tracing → project '{PROJECT}', thread {session_id}")

    elif MODE == "otel":
        from observability.otel_setup import get_otel_callbacks  # noqa: WPS433

        config["callbacks"] = get_otel_callbacks(session_id, tags=_tags(tags))
        print(f"[Observability] OpenTelemetry tracing → {os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')}")

    else:
        print("[Observability] TELEMETRY_MODE=off, running without tracing")

    return config


def created_trace_ids(session_id: str) -> list[str]:
    """Trace ids Opik created for this session (native mode only)."""
    tracer = _tracers.get(session_id)
    if tracer is None:
        return []
    try:
        return [t.id for t in tracer.created_traces()]
    except Exception:
        return []


def log_session_scores(session_id: str, scores: dict[str, float], reason: str = "") -> None:
    """
    Attach deterministic feedback scores to every trace of the session.

    These are the "feedback scores from code": no judge, no user, just the
    numbers the application already knows (quiz score, topics completed,
    tool failures). Online evaluation rules add the judged scores later.
    """
    if MODE != "native":
        return
    trace_ids = created_trace_ids(session_id)
    if not trace_ids:
        return
    import opik

    client = opik.Opik()
    client.log_traces_feedback_scores(
        [
            {"id": tid, "name": name, "value": float(value), "reason": reason or None}
            for tid in trace_ids
            for name, value in scores.items()
        ],
        project_name=PROJECT,
    )
    client.log_threads_feedback_scores(
        [{"id": session_id, "name": name, "value": float(value)} for name, value in scores.items()],
        project_name=PROJECT,
    )
    client.flush()


def flush(session_id: str | None = None) -> None:
    """Flush pending traces before the process exits."""
    if MODE == "native":
        tracers = [_tracers[session_id]] if session_id in _tracers else list(_tracers.values())
        for t in tracers:
            try:
                t.flush()
            except Exception:
                pass
        try:
            import opik

            opik.Opik().flush()
        except Exception:
            pass
    elif MODE == "otel":
        from observability.otel_setup import flush_otel

        flush_otel()
