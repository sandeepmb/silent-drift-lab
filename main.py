"""
main.py — run one study session of the Learning Accelerator, fully traced.

  python main.py "Learn Python closures and decorators from scratch"
  python main.py "Learn Python closures" --auto-approve --student simulated --max-topics 1
  python main.py --resume a3f1b2c4

Every session becomes one Opik thread; every node, LLM call and tool call a
span. run_session() is also the programmatic entry point used by the dataset
seeder, the experiments and the drift lab.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))

import logging

from dotenv import load_dotenv

load_dotenv()

# Keep the console readable: HTTP client chatter goes to WARNING.
for _name in ("httpx", "httpcore", "opik", "urllib3"):
    logging.getLogger(_name).setLevel(logging.WARNING)

from langgraph.types import Command  # noqa: E402

import student  # noqa: E402
from graph.state import QuizResult, StudyRoadmap, initial_state  # noqa: E402
from graph.workflow import graph  # noqa: E402
from observability import opik_setup  # noqa: E402


@dataclass
class SessionResult:
    session_id: str
    goal: str
    state: dict
    elapsed_s: float
    trace_ids: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def quiz_results(self) -> list[QuizResult]:
        raw = self.state.get("quiz_results", []) if self.state else []
        return [QuizResult.from_dict(r) if isinstance(r, dict) else r for r in raw]

    @property
    def avg_score(self) -> float:
        qs = self.quiz_results
        return sum(q.score for q in qs) / len(qs) if qs else 0.0

    def summary(self) -> dict:
        qs = self.quiz_results
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "topics_completed": len(qs),
            "avg_quiz_score": round(self.avg_score, 3),
            "weak_areas": list(self.state.get("weak_areas", [])) if self.state else [],
            "a2a_scores": list(self.state.get("a2a_scores", [])) if self.state else [],
            "elapsed_s": round(self.elapsed_s, 1),
            "error": self.error,
        }


def _print_roadmap(roadmap: StudyRoadmap) -> None:
    print(f"\n{'=' * 60}\nProposed Study Plan\n{'=' * 60}")
    print(f"Goal: {roadmap.goal}")
    print(f"Duration: {roadmap.total_weeks} weeks @ {roadmap.weekly_hours} hrs/week\n")
    for i, topic in enumerate(roadmap.topics, 1):
        prereqs = f" (needs: {', '.join(topic.prerequisites)})" if topic.prerequisites else ""
        print(f"  {i}. {topic.title} ({topic.estimated_minutes} min){prereqs}")
        print(f"     {topic.description}")


def print_session_summary(result: SessionResult) -> None:
    qs = result.quiz_results
    if not qs:
        return
    print(f"\n{'=' * 60}\nSession Summary\n{'=' * 60}")
    print(f"Goal: {result.goal}")
    print(f"Topics covered: {len(qs)}")
    print(f"Average score: {result.avg_score:.0%}\n")
    for r in qs:
        status = "✓" if r.score >= 0.5 else "✗"
        weak = f", review: {', '.join(r.weak_areas)}" if r.weak_areas else ""
        print(f"  {status} {r.topic}: {r.score:.0%}{weak}")
    print(f"{'=' * 60}\n")


def run_session(
    goal: str,
    session_id: str | None = None,
    auto_approve: bool = False,
    student_mode: str | None = None,
    persona: str | None = None,
    answers: list[str] | None = None,
    max_topics: int | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    quiet: bool = False,
) -> SessionResult:
    """Run (or resume) one study session and return its result."""
    is_resume = session_id is not None
    session_id = session_id or str(uuid.uuid4())[:8]

    if student_mode or persona or answers is not None:
        student.configure(mode=student_mode, persona=persona, answers=answers)
    if max_topics is not None:
        os.environ["MAX_TOPICS"] = str(max_topics)

    config = opik_setup.get_run_config(session_id, graph, tags=tags, metadata=metadata)

    if not quiet:
        print(f"\n{'=' * 60}\nLearning Accelerator\nSession ID: {session_id}")
        print("Resuming existing session..." if is_resume else f"Goal: {goal}")
        print(f"{'=' * 60}")

    state = None if is_resume else initial_state(goal, session_id)
    started = time.perf_counter()
    error: str | None = None

    try:
        result = graph.invoke(state, config=config)

        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            raw = payload.get("roadmap")
            roadmap = StudyRoadmap.from_dict(raw) if isinstance(raw, dict) else raw
            if roadmap and not quiet:
                _print_roadmap(roadmap)
            if auto_approve:
                decision = "yes"
                if not quiet:
                    print("\n[auto-approve] yes")
            else:
                print(f"\n{payload.get('prompt', 'Continue?')}")
                decision = input("> ").strip()
            result = graph.invoke(Command(resume=decision), config=config)

        if result.get("error"):
            error = str(result["error"])
            print(f"\n[ERROR] {error}")
    except Exception as e:  # keep the trace, record the failure
        error = f"{type(e).__name__}: {e}"
        result = {}
        print(f"\n[ERROR] {error}")

    elapsed = time.perf_counter() - started
    sr = SessionResult(session_id=session_id, goal=goal, state=result, elapsed_s=elapsed, error=error)

    if not quiet:
        print_session_summary(sr)

    # Feedback scores from code: the numbers the app already knows.
    scores = {
        "quiz_score": sr.avg_score,
        "topics_completed": float(len(sr.quiz_results)),
        "weak_area_count": float(len(sr.summary()["weak_areas"])),
        "session_error": 1.0 if error else 0.0,
    }
    # Grader agreement: 1 - mean |local - remote| over quizzes the A2A service re-graded.
    pairs = [e for e in (result.get("a2a_scores", []) if result else []) if e.get("remote") is not None]
    if pairs:
        scores["grader_agreement"] = 1.0 - sum(abs(e["local"] - e["remote"]) for e in pairs) / len(pairs)
        scores["a2a_reachable"] = 1.0
    else:
        scores["a2a_reachable"] = 0.0
    try:
        opik_setup.log_session_scores(session_id, scores, reason="logged by main.run_session")
        sr.trace_ids = opik_setup.created_trace_ids(session_id)
    except Exception as e:
        print(f"[Observability] could not log feedback scores: {e}")
    finally:
        opik_setup.flush(session_id)

    return sr


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Learning Accelerator, fully traced into Opik.")
    parser.add_argument("goal", nargs="?", default="Learn Python closures and decorators from scratch")
    parser.add_argument("--resume", metavar="SESSION_ID")
    parser.add_argument("--auto-approve", action="store_true", help="approve the study plan without asking")
    parser.add_argument("--student", choices=["interactive", "simulated", "scripted"], default=os.getenv("STUDENT_MODE", "interactive"))
    parser.add_argument("--persona", choices=["good", "average", "weak"], default=os.getenv("STUDENT_PERSONA", "average"))
    parser.add_argument("--max-topics", type=int, default=None, help="stop after N topics (demo and lab runs)")
    parser.add_argument("--tag", action="append", default=[], help="extra Opik tag (repeatable)")
    args = parser.parse_args()

    if args.resume:
        run_session(goal="", session_id=args.resume, auto_approve=args.auto_approve, student_mode=args.student, persona=args.persona, max_topics=args.max_topics, tags=args.tag)
    else:
        run_session(args.goal, auto_approve=args.auto_approve, student_mode=args.student, persona=args.persona, max_topics=args.max_topics, tags=args.tag)
