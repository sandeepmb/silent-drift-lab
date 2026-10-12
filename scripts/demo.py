"""
scripts/demo.py — the two-minute demo.

    make demo

Act 1  A healthy session: one topic, simulated student, everything traced.
Act 2  The same session with drift injected (DRIFT_SCENARIO), still "healthy"
       on latency, errors and cost.
Act 3  The monitor compares the two: system-health signals flat, quality
       signals down. Prints the verdict and the Opik links.

Designed to be screen-recorded: short lines, no scrolling walls of text.
Set DEMO_FAST=1 to use the smaller qwen2.5:3b model for the run.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from dotenv import load_dotenv

load_dotenv()

if os.getenv("DEMO_FAST") == "1":
    os.environ["OLLAMA_MODEL"] = "qwen2.5:3b"

GOAL = os.getenv("DEMO_GOAL", "Learn Python closures and decorators from scratch")
SCENARIO = os.getenv("DEMO_SCENARIO", "stale_notes")
UI = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").replace("/api", "")


def banner(text: str) -> None:
    print(f"\n\033[1;33m{'─' * 64}\n  {text}\n{'─' * 64}\033[0m")


def line(label: str, value: str) -> None:
    print(f"  {label:<28} {value}")


def main() -> None:
    from main import run_session

    banner("Act 1 · a healthy session, fully traced into Opik")
    os.environ["DRIFT_SCENARIO"] = "none"
    healthy = run_session(GOAL, auto_approve=True, student_mode="simulated", persona="average",
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
    drifted = run_session(GOAL, auto_approve=True, student_mode="simulated", persona="average",
                          max_topics=1, tags=["demo", f"drift:{SCENARIO}"], quiet=True)
    inject.clear()
    d = drifted.summary()
    line("session", drifted.session_id)
    line("latency (s)", f"{d['elapsed_s']}   (Δ {d['elapsed_s'] - h['elapsed_s']:+.1f})")
    line("errors", "0" if not d["error"] else d["error"])
    line("quiz score", f"{d['avg_quiz_score']:.0%}   (Δ {(d['avg_quiz_score'] - h['avg_quiz_score']) * 100:+.0f} pts)")

    banner("Act 3 · what the dashboards saw vs what the monitor saw")
    time.sleep(1)
    flat = abs(d["elapsed_s"] - h["elapsed_s"]) / max(h["elapsed_s"], 1) < 0.5 and not d["error"]
    line("system health", "GREEN  (latency within range, no errors)" if flat else "changed")
    dropped = d["avg_quiz_score"] < h["avg_quiz_score"] - 0.1
    line("behavioural quality", "DOWN   (quiz score fell)" if dropped else "no change detected on one run")
    print()
    print("  A single run is an anecdote. `make drift-demo` runs the full lab:")
    print("  30 baseline sessions, six injections, per-segment baselines,")
    print("  two-signal corroboration, and the figure in reports/drift-figure.png.")
    print(f"\n  Opik → {UI}\n")


if __name__ == "__main__":
    main()
