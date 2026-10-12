"""
scripts/seed_dataset.py — run the scripted sessions and build the Opik dataset.

  make seed                                  # run all 30 scenarios, then build the dataset
  python scripts/seed_dataset.py --limit 2   # smoke: two sessions
  python scripts/seed_dataset.py --resume    # skip scenarios already in data/seed-runs.jsonl
  python scripts/seed_dataset.py --dataset-only   # (re)build the dataset from what exists so far

Phase 1 — sessions.  Each line of scenarios/sessions.jsonl becomes one
run_session(...) call with DRIFT_SCENARIO=none, auto-approve, the simulated
student persona and max_topics=1. One line per session is appended to
data/seed-runs.jsonl as soon as it finishes, so the script can be interrupted
and resumed.

Phase 2 — dataset.  For every seed run, the Opik REST API is queried for the
session's spans. Each `quiz.grade_answer` span (input: question / expected /
student_answer, output: the grader JSON) becomes one dataset item; the topic
comes from the `quiz.run` span and the explanation from the explainer node's
final AI message. Items are inserted into the Opik dataset
"learning-accelerator-sessions" with deduplication, and mirrored to
data/datasets/learning-accelerator-sessions.jsonl. Every insert of new items
creates a new dataset version, which the script prints before and after.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
os.environ["DRIFT_SCENARIO"] = "none"          # seed runs are the healthy baseline
os.environ.setdefault("STUDENT_MODE", "simulated")

import httpx  # noqa: E402

SCENARIOS = ROOT / "scenarios" / "sessions.jsonl"
SEED_RUNS = ROOT / "data" / "seed-runs.jsonl"
MIRROR = ROOT / "data" / "datasets" / "learning-accelerator-sessions.jsonl"
DATASET_NAME = os.getenv("SEED_DATASET_NAME", "learning-accelerator-sessions")
PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab")
OPIK_API = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")
EXPLANATION_MAX_CHARS = 1500


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: sessions
# ─────────────────────────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_sessions(limit: int | None, resume: bool) -> None:
    from main import run_session  # imports app/ via main's sys.path tweak

    scenarios = load_jsonl(SCENARIOS)
    done = {r["scenario_id"] for r in load_jsonl(SEED_RUNS)} if resume else set()
    todo = [s for s in scenarios if s["id"] not in done]
    if limit:
        todo = todo[:limit]
    print(f"[seed] {len(todo)} scenario(s) to run ({len(done)} already done)")

    SEED_RUNS.parent.mkdir(parents=True, exist_ok=True)
    for i, sc in enumerate(todo, 1):
        print(f"\n[seed] ({i}/{len(todo)}) {sc['id']} persona={sc['persona']} goal={sc['goal']!r}")
        t0 = time.perf_counter()
        result = run_session(
            sc["goal"],
            auto_approve=True,
            student_mode="simulated",
            persona=sc["persona"],
            max_topics=sc.get("max_topics", 1),
            tags=list(sc.get("tags", [])) + [f"scenario:{sc['id']}", f"segment:{sc.get('segment', '')}"],
            metadata={"scenario_id": sc["id"], "segment": sc.get("segment")},
            quiet=True,
        )
        line = {
            "scenario_id": sc["id"],
            "session_id": result.session_id,
            "goal": sc["goal"],
            "persona": sc["persona"],
            "segment": sc.get("segment"),
            "avg_score": round(result.avg_score, 3),
            "topics_completed": len(result.quiz_results),
            "elapsed_s": round(time.perf_counter() - t0, 1),
            "trace_ids": result.trace_ids,
            "error": result.error,
        }
        with SEED_RUNS.open("a") as fh:
            fh.write(json.dumps(line) + "\n")
        print(f"[seed] {sc['id']} -> session {result.session_id} score={line['avg_score']} "
              f"traces={len(result.trace_ids)} elapsed={line['elapsed_s']}s error={result.error}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: dataset from traces
# ─────────────────────────────────────────────────────────────────────────────

def fetch_spans(trace_id: str) -> list[dict]:
    r = httpx.get(f"{OPIK_API}/v1/private/spans",
                  params={"project_name": PROJECT, "trace_id": trace_id, "size": 500}, timeout=30)
    r.raise_for_status()
    return r.json().get("content", [])


def final_ai_message(explainer_output: dict | None) -> str:
    """The explainer node's last AIMessage without tool calls (what the quiz was built from)."""
    msgs = (explainer_output or {}).get("messages") or []
    for m in reversed(msgs):
        if m.get("type") == "ai" and m.get("content") and not m.get("tool_calls"):
            return str(m["content"])
    return ""


def items_for_run(run: dict) -> list[dict]:
    """Turn one seed run (possibly several traces) into dataset items."""
    spans: list[dict] = []
    for tid in run.get("trace_ids", []):
        spans.extend(fetch_spans(tid))
    spans.sort(key=lambda s: s.get("start_time") or "")

    explanation = ""
    topic = ""
    for s in spans:
        if s.get("name") == "explainer" and not explanation:
            explanation = final_ai_message(s.get("output"))
        if s.get("name") == "quiz.run" and not topic:
            topic = str((s.get("input") or {}).get("topic", ""))

    items = []
    graders = [s for s in spans if s.get("name") == "quiz.grade_answer"]
    for n, s in enumerate(graders, 1):
        inp, out = s.get("input") or {}, s.get("output") or {}
        if not inp.get("question") or not isinstance(out, dict):
            continue
        try:
            score = float(out.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        items.append({
            "input": {
                "goal": run["goal"],
                "topic": topic,
                "question": inp["question"],
                "student_answer": inp.get("student_answer", ""),
                "expected_answer": inp.get("expected", ""),
                "explanation": explanation[:EXPLANATION_MAX_CHARS],
                "session_id": run["session_id"],
                "scenario_id": run["scenario_id"],
                "persona": run["persona"],
                "question_index": n,
            },
            "expected_output": {
                "grade": "correct" if out.get("correct") else "incorrect",
                "score": score,
                "feedback": out.get("feedback", ""),
            },
            "metadata": {"source_span_id": s["id"], "source_trace_id": s["trace_id"]},
        })
    return items


def build_dataset() -> None:
    import opik

    runs = [r for r in load_jsonl(SEED_RUNS) if not r.get("error")]
    print(f"[dataset] building from {len(runs)} successful seed run(s)")

    mirror_items = load_jsonl(MIRROR)
    seen = {(it["input"]["session_id"], it["input"]["question"]) for it in mirror_items}
    new_items: list[dict] = []
    for run in runs:
        for it in items_for_run(run):
            key = (it["input"]["session_id"], it["input"]["question"])
            if key in seen:
                continue
            seen.add(key)
            new_items.append(it)
    print(f"[dataset] {len(new_items)} new item(s) ({len(mirror_items)} already mirrored)")

    client = opik.Opik()
    dataset = client.get_or_create_dataset(
        DATASET_NAME,
        description="Quiz Q&A pairs with the app grader's verdict, pulled from Opik traces of seed sessions.",
    )
    before = dataset.get_version_info()
    print(f"[dataset] '{DATASET_NAME}' before: version={getattr(before, 'version_name', None)} "
          f"items={getattr(before, 'items_total', None)}")

    if new_items:
        # deduplication=True: the SDK hashes item content and skips exact repeats.
        dataset.insert(new_items, deduplication=True)
        MIRROR.parent.mkdir(parents=True, exist_ok=True)
        with MIRROR.open("a") as fh:
            for it in new_items:
                fh.write(json.dumps(it) + "\n")

    after = dataset.get_version_info()
    print(f"[dataset] '{DATASET_NAME}' after:  version={getattr(after, 'version_name', None)} "
          f"items={getattr(after, 'items_total', None)} added={getattr(after, 'items_added', None)}")
    if new_items and before is not None and getattr(before, 'version_name', None) != getattr(after, 'version_name', None):
        print(f"[dataset] new version created: {before.version_name} -> {after.version_name}")

    sample = dataset.get_items(nb_samples=2)
    print(f"[dataset] dataset.get_items(nb_samples=2) -> {len(sample)} item(s); first keys: "
          f"{sorted(sample[0].keys()) if sample else []}")
    url = f"{OPIK_API.replace('/api', '')}/default/datasets/{dataset.id}/items"
    print(f"[dataset] UI: {url}")
    client.flush()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int, default=None, help="run at most N scenarios")
    p.add_argument("--resume", action="store_true", help="skip scenarios already in data/seed-runs.jsonl")
    p.add_argument("--dataset-only", action="store_true", help="skip phase 1, build the dataset from existing runs")
    p.add_argument("--sessions-only", action="store_true", help="skip phase 2")
    args = p.parse_args()

    if not args.dataset_only:
        run_sessions(args.limit, args.resume)
    if not args.sessions_only:
        build_dataset()
