#!/usr/bin/env python
"""
scripts/otel_probe.py — a 30-second proof that the OTLP path into Opik works.

No LangGraph, no model call (unless --a2a). It hand-builds one trace:

  probe.session                 root: invoke_agent, thread_id, opik.tags, opik.metadata.*, input/output
  ├─ chat qwen2.5:7b            gen_ai.* chat attributes + token usage
  └─ execute_tool add           gen_ai.tool.* attributes

flushes it through the configured exporter, then polls Opik's REST API and
prints what landed (span types, model, provider, usage, thread, tags).

  .venv/bin/python scripts/otel_probe.py                              # → Collector :4318 → Opik
  OTEL_DIRECT_TO_OPIK=true .venv/bin/python scripts/otel_probe.py     # → Opik directly (no Collector)
  .venv/bin/python scripts/otel_probe.py --a2a                        # + a real call to the quiz service:
                                                                      #   proves the W3C traceparent hop (~30-60 s)
  TELEMETRY_MODE=native .venv/bin/python scripts/otel_probe.py --a2a  # same hop on the native track
                                                                      #   (opik_trace_id / opik_parent_span_id)
  .venv/bin/python scripts/otel_probe.py --nest-under-native          # OTel spans emitted inside a native
                                                                      #   @track function: OpikSpanProcessor
                                                                      #   nests them in the native trace

Exit code 0 when the trace (and, with --a2a, the service span) is found in Opik.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

# The probe is OTel-first: only an explicit shell `TELEMETRY_MODE=native` (or --mode)
# switches it, not the default in .env (which load_dotenv would otherwise fill in).
_SHELL_MODE = os.environ.get("TELEMETRY_MODE")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import httpx  # noqa: E402

OPIK_API = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")
WORKSPACE = os.getenv("OPIK_WORKSPACE", "default")
PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
HEADERS = {"Comet-Workspace": WORKSPACE}


# ── emit ────────────────────────────────────────────────────────────────────

def emit_otel_trace(thread_id: str, tags: list[str], a2a: bool) -> dict:
    """Root + LLM child + tool child (+ optional real A2A call inside the root)."""
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Status, StatusCode

    from observability import otel_setup

    tracer = otel_setup.get_tracer()
    out: dict = {}

    root = tracer.start_span(
        "probe.session",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "otel-probe",
            "gen_ai.framework": "none",
            "gen_ai.conversation.id": thread_id,
            "thread_id": thread_id,
            "opik.tags": tags,
            "opik.metadata.probe": "scripts/otel_probe.py",
            "opik.metadata.exporter": otel_setup.export_target(),
            "input": json.dumps({"goal": "prove OTLP → Opik", "thread": thread_id}),
        },
    )
    with trace.use_span(root, end_on_exit=False):
        with tracer.start_as_current_span(
            f"chat {MODEL}",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": "ollama",
                "gen_ai.provider.name": "ollama",
                "gen_ai.request.model": MODEL,
                "gen_ai.request.temperature": 0.2,
                "server.address": "localhost",
                "gen_ai.input.messages": json.dumps(
                    [
                        {"role": "system", "parts": [{"type": "text", "content": "You are terse."}]},
                        {"role": "user", "parts": [{"type": "text", "content": "What is a closure?"}]},
                    ]
                ),
            },
        ) as llm:
            llm.set_attribute(
                "gen_ai.output.messages",
                json.dumps(
                    [
                        {
                            "role": "assistant",
                            "parts": [{"type": "text", "content": "A function plus its captured scope."}],
                            "finish_reason": "stop",
                        }
                    ]
                ),
            )
            llm.set_attribute("gen_ai.response.model", MODEL)
            llm.set_attribute("gen_ai.response.finish_reasons", ["stop"])
            llm.set_attribute("gen_ai.usage.input_tokens", 42)
            llm.set_attribute("gen_ai.usage.output_tokens", 9)
            llm.set_status(Status(StatusCode.OK))

        with tracer.start_as_current_span(
            "execute_tool add",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "add",
                "gen_ai.tool.type": "function",
                "gen_ai.tool.call.arguments": json.dumps({"a": 2, "b": 3}),
            },
        ) as tool:
            tool.set_attribute("gen_ai.tool.call.result", "5")

        if a2a:
            out["a2a"] = call_quiz_service()

    root.set_attribute("output", json.dumps({"answer": "A function plus its captured scope.", **out}))
    root.set_status(Status(StatusCode.OK))
    root.end()
    otel_setup.flush_otel()
    out["otel_trace_id"] = format(root.get_span_context().trace_id, "032x")
    return out


def emit_native_trace_with_a2a(thread_id: str, tags: list[str]) -> dict:
    """Native track: a @track-style span, then the A2A call with opik_* headers."""
    import opik
    from opik import opik_context

    with opik.start_as_current_span("probe.session", type="general", project_name=PROJECT,
                                    input={"goal": "prove opik_* headers hop"}, tags=tags) as span:
        opik_context.update_current_trace(thread_id=thread_id, tags=tags)
        result = call_quiz_service()
        span.update(output={"a2a": result})
    opik.flush_tracker()
    return {"a2a": result}


def emit_nested_under_native(thread_id: str, tags: list[str]) -> dict:
    """OTel spans started inside a native @track function → OpikSpanProcessor links them."""
    import opik
    from opik import opik_context

    @opik.track(name="probe.native_parent", project_name=PROJECT)
    def native_parent() -> dict:
        opik_context.update_current_trace(thread_id=thread_id, tags=tags)
        return emit_otel_trace(thread_id, tags, a2a=False)

    out = native_parent()
    opik.flush_tracker()
    return out


def call_quiz_service() -> dict:
    """One real round trip to the Quiz A2A service (needs it running on :9001)."""
    from a2a_services.a2a_client import is_quiz_service_available, send_task

    url = os.getenv("QUIZ_SERVICE_URL", "http://localhost:9001")
    if not is_quiz_service_available(url):
        return {"error": f"quiz service not reachable at {url}"}
    t0 = time.time()
    # Same payload delegate_quiz_task() builds; send_task() so the timeout can be raised
    # (question generation on a shared 7B model can take minutes under contention).
    result = send_task(
        url,
        json.dumps({
            "topic": "Python closures",
            "explanation": "A closure is a function that remembers variables from the scope it was created in.",
            "answers": [],
        }),
        timeout=float(os.getenv("PROBE_A2A_TIMEOUT", "300")),
    )
    return {"status": result.get("status", result.get("error")), "questions": len(result.get("questions") or []),
            "elapsed_s": round(time.time() - t0, 1)}


# ── read back ───────────────────────────────────────────────────────────────

def find_trace(thread_id: str, timeout_s: float) -> dict | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = httpx.get(f"{OPIK_API}/v1/private/traces", params={"project_name": PROJECT, "size": 50},
                      headers=HEADERS, timeout=10)
        r.raise_for_status()
        for t in r.json().get("content", []):
            if t.get("thread_id") == thread_id:
                return t
        time.sleep(2)
    return None


def spans_of(trace_id: str) -> list[dict]:
    r = httpx.get(f"{OPIK_API}/v1/private/spans",
                  params={"project_name": PROJECT, "trace_id": trace_id, "size": 100},
                  headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get("content", [])


def short(value, n=70) -> str:
    s = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return s if len(s) <= n else s[: n - 1] + "…"


def report(trace: dict, spans: list[dict]) -> None:
    print(f"\nTRACE {trace['id']}")
    print(f"  name={trace['name']}  thread_id={trace.get('thread_id')}  tags={trace.get('tags')}")
    print(f"  usage={trace.get('usage')}  cost={trace.get('total_estimated_cost')}")
    print(f"  input={short(trace.get('input'))}")
    print(f"  output={short(trace.get('output'))}")
    print(f"  metadata={short(trace.get('metadata'), 160)}")
    print(f"\n  {'type':8} {'name':28} {'parent':8} {'model':12} {'provider':8} usage")
    by_id = {s["id"]: s for s in spans}
    for s in sorted(spans, key=lambda s: s["start_time"]):
        parent = by_id.get(s.get("parent_span_id") or "", {}).get("name", "-")
        print(f"  {s['type']:8} {s['name'][:28]:28} {parent[:8]:8} {str(s.get('model'))[:12]:12} "
              f"{str(s.get('provider'))[:8]:8} {s.get('usage')}")
        print(f"           in={short(s.get('input'), 90)}")
        print(f"           out={short(s.get('output'), 90)}")
        if s.get("tags"):
            print(f"           tags={s['tags']}")


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a2a", action="store_true", help="also call the quiz service inside the root span")
    ap.add_argument("--nest-under-native", action="store_true",
                    help="emit the OTel spans inside a native @track function (OpikSpanProcessor demo)")
    ap.add_argument("--timeout", type=float, default=60, help="seconds to wait for Opik to show the trace")
    ap.add_argument("--mode", choices=["otel", "native"], default=None,
                    help="track to probe (default: shell TELEMETRY_MODE if set, else otel)")
    args = ap.parse_args()

    mode = (args.mode or _SHELL_MODE or "otel").lower()
    if args.nest_under_native:
        mode = "nest"
    os.environ["TELEMETRY_MODE"] = "native" if mode == "native" else "otel"   # what a2a_client reads

    thread_id = f"probe-{uuid.uuid4().hex[:8]}"
    tags = ["otel-probe", f"mode:{mode}"]
    print(f"[probe] mode={mode}  thread_id={thread_id}  project={PROJECT}")

    if mode == "native":
        if not args.a2a:
            print("[probe] TELEMETRY_MODE=native only makes sense with --a2a; add it.")
            return 2
        emitted = emit_native_trace_with_a2a(thread_id, tags)
    elif mode == "nest":
        from observability import otel_setup
        otel_setup.configure_otel()
        emitted = emit_nested_under_native(thread_id, tags)
    else:
        from observability import otel_setup
        otel_setup.configure_otel()
        emitted = emit_otel_trace(thread_id, tags, a2a=args.a2a)

    print(f"[probe] emitted: {emitted}")
    print(f"[probe] polling {OPIK_API} for thread_id={thread_id} (up to {args.timeout:.0f}s) ...")

    trace = find_trace(thread_id, args.timeout)
    if trace is None:
        print("[probe] NOT FOUND. Check: Collector up (curl localhost:13133), Opik up (localhost:5173), "
              "OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_DIRECT_TO_OPIK.")
        return 1

    # the root span arrives last; give its children (and the remote service's) time to land
    spans = []
    for _ in range(int(args.timeout // 2) or 1):
        spans = spans_of(trace["id"])
        wanted = 3 + (1 if args.a2a else 0)
        if len(spans) >= wanted and (not args.a2a or any(s["name"] == "quiz_service.execute" for s in spans)):
            break
        time.sleep(2)

    report(trace, spans)

    names = {s["name"] for s in spans}
    checks: dict[str, bool] = {}
    if mode != "native":   # the native probe emits no hand-built LLM/tool spans
        checks["llm span typed llm with model+provider+usage"] = any(
            s["type"] == "llm" and s.get("model") and s.get("provider") and s.get("usage") for s in spans)
        checks["tool span typed tool"] = any(s["type"] == "tool" for s in spans)
    checks["thread id on trace"] = trace.get("thread_id") == thread_id
    checks["tags on trace"] = "otel-probe" in (trace.get("tags") or [])
    info: dict[str, bool] = {}
    if args.a2a:
        checks["quiz_service.execute span joined the trace"] = "quiz_service.execute" in names
        # Inner work of the service: on the OTel track its ChatOllama call appears as a
        # second "chat <model>" span; on the native track its @track span
        # quiz.generate_questions does. Which one you get depends on the TELEMETRY_MODE
        # the *service* was started with, so this is reported as info, not pass/fail.
        info["quiz service inner LLM work visible (needs service on the same track)"] = (
            sum(1 for s in spans if s["name"].startswith("chat ")) >= 2
            or "quiz.generate_questions" in names
        )
    if mode == "nest":
        checks["OTel spans nested in native trace"] = trace["name"] == "probe.native_parent" and "probe.session" in names

    print()
    ok = True
    for label, passed in checks.items():
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    for label, passed in info.items():
        print(f"  [{'yes' if passed else 'no '}] {label}")
    print(f"\n[probe] open in UI: http://localhost:5173/default/projects → {PROJECT} → thread {thread_id}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
