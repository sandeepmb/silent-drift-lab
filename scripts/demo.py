"""
scripts/demo.py — the two-minute demo.

    make demo

Act 1  A healthy session: one topic, simulated student, everything traced.
Act 2  The same session with drift injected (DRIFT_SCENARIO), still "healthy"
       on latency, errors and cost.
Act 3  Compare the two: what a dashboard sees (latency, errors, tokens) against
       what a quality signal sees (retrieval grounding of the explanation, read
       back from the Opik traces). Prints the verdict and the Opik links.

Designed to be screen-recorded: short lines, no scrolling walls of text.
Set DEMO_FAST=1 to use the smaller qwen2.5:3b model for the run.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ.setdefault("OPIK_CONSOLE_LOGGING_LEVEL", "ERROR")  # no "Started logging traces ..." lines mid-demo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from dotenv import load_dotenv

load_dotenv()

# Screen-recording hygiene: no HTTP client chatter, no library warnings.
for _name in ("httpx", "httpcore", "opik", "urllib3", "LiteLLM", "langchain", "langgraph"):
    logging.getLogger(_name).setLevel(logging.ERROR)

if os.getenv("DEMO_FAST") == "1":
    os.environ["OLLAMA_MODEL"] = "qwen2.5:3b"

GOAL = os.getenv("DEMO_GOAL", "Learn Python closures and decorators from scratch")
SCENARIO = os.getenv("DEMO_SCENARIO", "stale_notes")
UI = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").replace("/api", "")


def banner(text: str) -> None:
    print(f"\n\033[1;33m{'─' * 64}\n  {text}\n{'─' * 64}\033[0m")


def line(label: str, value: str) -> None:
    print(f"  {label:<28} {value}")


def read_signals(session_id: str) -> dict | None:
    """Drift signals for one demo session, read back from Opik (the same code the lab uses)."""
    from drift import signals  # noqa: WPS433

    for _ in range(6):
        try:
            trs = [t for t in signals.fetch_traces(tag="demo") if t.get("thread_id") == session_id]
            if trs:
                spans = [sp for t in trs for sp in signals.fetch_spans(t["id"])]
                row = signals.session_signals(trs, spans)
                if row.get("explanation_chars"):
                    return row
        except Exception:  # Opik still ingesting, or briefly unreachable: try again
            pass
        time.sleep(4)
    return None


def run_quietly(*args, **kwargs):
    """Run one session with the agents' own console output (and library noise) swallowed."""
    from main import run_session

    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        return run_session(*args, **kwargs)


def main() -> None:
    banner("Act 1 · a healthy session, fully traced into Opik")
    os.environ["DRIFT_SCENARIO"] = "none"
    print("  running one session (one to three minutes) ...", flush=True)
    healthy = run_quietly(GOAL, auto_approve=True, student_mode="simulated", persona="average",
                          max_topics=1, tags=["demo", "baseline"], quiet=True)
    h = healthy.summary()
    line("session", healthy.session_id)
    line("latency (s)", f"{h['elapsed_s']}")
    line("errors", "0" if not h["error"] else h["error"])
    line("quiz score", f"{h['avg_quiz_score']:.0%}")
    line("thread in Opik", f"{UI}/default/projects  → Threads → {healthy.session_id}")

    banner(f"Act 2 · same session with drift injected: {SCENARIO}")
    os.environ["DRIFT_SCENARIO"] = SCENARIO
    from drift import inject  # noqa: WPS433

    inject.apply(SCENARIO)
    print("  running the same session with the drift applied ...", flush=True)
    drifted = run_quietly(GOAL, auto_approve=True, student_mode="simulated", persona="average",
                          max_topics=1, tags=["demo", f"drift:{SCENARIO}"], quiet=True)
    inject.clear()
    d = drifted.summary()
    line("session", drifted.session_id)
    line("latency (s)", f"{d['elapsed_s']}   (Δ {d['elapsed_s'] - h['elapsed_s']:+.1f})")
    line("errors", "0" if not d["error"] else d["error"])
    line("quiz score", f"{d['avg_quiz_score']:.0%}   (Δ {(d['avg_quiz_score'] - h['avg_quiz_score']) * 100:+.0f} pts)")

    banner("Act 3 · what the dashboards saw vs what a quality signal saw")
    print("  reading both sessions back from Opik ...", flush=True)
    hs, ds = read_signals(healthy.session_id), read_signals(drifted.session_id)
    tok_h, tok_d = (hs or {}).get("total_tokens"), (ds or {}).get("total_tokens")
    line("", f"{'healthy':>12} {'drifted':>12}")
    line("latency (s)", f"{h['elapsed_s']:>12} {d['elapsed_s']:>12}")
    line("errors", f"{0 if not h['error'] else 1:>12} {0 if not d['error'] else 1:>12}")
    if tok_h and tok_d:
        line("tokens", f"{tok_h:>12,} {tok_d:>12,}")
    line("quiz score", f"{h['avg_quiz_score']:>12.0%} {d['avg_quiz_score']:>12.0%}")
    if hs and ds:
        line("grounding in the notes", f"{hs['grounding']:>12.2f} {ds['grounding']:>12.2f}")
        line("explanation length (chars)", f"{hs['explanation_chars']:>12,} {ds['explanation_chars']:>12,}")
    print()
    healthy_dash = not d["error"] and abs(d["elapsed_s"] - h["elapsed_s"]) / max(h["elapsed_s"], 1) < 0.5 \
        and (not (tok_h and tok_d) or abs(tok_d - tok_h) / tok_h < 0.5)
    line("dashboard view", "GREEN  (no error; latency and tokens within 50%)" if healthy_dash else "changed (latency, tokens or errors moved)")
    if hs and ds and ds["grounding"] < hs["grounding"] - 0.1:
        line("quality view", f"DOWN   (grounding {hs['grounding']:.2f} -> {ds['grounding']:.2f})")
    elif d["avg_quiz_score"] < h["avg_quiz_score"] - 0.1:
        line("quality view", "DOWN   (quiz score fell)")
    else:
        line("quality view", "no drop detected on this single run")
    print()
    print("  A single run is an anecdote. `make drift-demo` runs the full lab:")
    print("  30 baseline sessions, six injections, per-segment baselines,")
    print("  two-signal corroboration, and the figure in reports/drift-figure.png.")
    print(f"\n  Opik → {UI}\n")


if __name__ == "__main__":
    main()
