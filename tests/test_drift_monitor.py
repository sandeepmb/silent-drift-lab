"""
tests/test_drift_monitor.py — monitor logic on synthetic data. No LLM, no Opik.

    .venv/bin/python -m pytest tests/test_drift_monitor.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drift import monitor  # noqa: E402
from drift import inject  # noqa: E402
from drift.signals import overlap, refine_segment, session_signals  # noqa: E402

RNG = np.random.default_rng(7)
PERSONA_SCORE = {"good": 0.85, "average": 0.65, "weak": 0.35}


def make_sessions(n: int, scenario: str = "none", segment: str = "python-fundamentals", start: int = 0, **shift) -> pd.DataFrame:
    """Synthetic sessions: system signals flat, quality signals optionally shifted."""
    rows = []
    personas = ["good", "average", "weak"]
    for i in range(n):
        persona = personas[i % 3]
        row = {
            "session_id": f"{scenario}-{start + i:03d}",
            "segment": segment,
            "scenario": scenario,
            "persona": persona,
            "timestamp": pd.Timestamp("2026-10-10T10:00:00Z") + pd.Timedelta(minutes=3 * (start + i)),
            "duration_ms": 150_000 + RNG.normal(0, 8_000),
            "error": 0.0,
            "total_tokens": 8_000 + RNG.normal(0, 400),
            "est_cost_usd": 0.003 + RNG.normal(0, 0.0002),
            "quiz_score": float(np.clip(PERSONA_SCORE[persona] + RNG.normal(0, 0.05), 0, 1)),
            "weak_area_count": float(RNG.integers(0, 3)),
            "tool_failures": 0.0,
            "empty_searches": 0.0,
            "parse_failures": 0.0,
            "explainer_iterations": 4.0 + RNG.integers(0, 2),
            "grounding": 0.35 + RNG.normal(0, 0.03),
            "notes_overlap": 0.45 + RNG.normal(0, 0.03),
            "refusal_rate": 0.0,
            "explanation_chars": 1_900 + RNG.normal(0, 150),
            "sentence_count": 22 + RNG.normal(0, 2),
            "hedging_rate": 0.4 + RNG.normal(0, 0.15),
        }
        for k, v in shift.items():
            row[k] = v(row[k]) if callable(v) else v
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def baseline() -> pd.DataFrame:
    return make_sessions(30)


# ── corroboration rule ──────────────────────────────────────────────────────

def test_baseline_window_is_ok(baseline):
    win = make_sessions(3, scenario="none", start=100)
    res = monitor.evaluate_window(baseline, win)
    assert res["families_moved"] == {}
    assert res["verdict"] == "ok"
    assert res["system_signals_moved"] == []


def test_single_family_only_annotates(baseline):
    # quiz score collapses, nothing else moves → one family → annotate, never page
    win = make_sessions(3, scenario="x", start=100, quiz_score=lambda v: v - 0.5)
    res = monitor.evaluate_window(baseline, win)
    assert set(res["families_moved"]) == {"user"}
    assert res["verdict"] == "annotate"


def test_two_families_with_changepoint_pages(baseline):
    # retrieval drift: grounding falls, tool failures appear, quiz score falls; system flat
    win = make_sessions(3, scenario="stale", start=100,
                        quiz_score=lambda v: v - 0.35, grounding=0.05, tool_failures=2.0, empty_searches=1.0)
    res = monitor.evaluate_window(baseline, win)
    assert {"user", "grounding", "tool"} <= set(res["families_moved"])
    assert res["changepoint_confirmed"]
    assert res["verdict"] == "page"
    assert res["system_signals_moved"] == [], "system health must stay green"


def test_two_families_without_changepoint_tickets():
    base = make_sessions(30)
    # one-session window: two families move, but CUSUM cannot accumulate past h in one step
    win = make_sessions(1, scenario="blip", start=100, grounding=0.20, hedging_rate=1.5)
    res = monitor.evaluate_window(base, win)
    assert len(res["families_moved"]) >= 2
    assert not res["changepoint_confirmed"]
    assert res["verdict"] == "ticket"


def test_shape_shift_with_rising_scores_is_caught(baseline):
    # behavioural drift: lenient grader + verbose hedging explainer. Scores go UP.
    win = make_sessions(3, scenario="verbosity", start=100,
                        quiz_score=lambda v: min(v + 0.3, 1.0), explanation_chars=5_500,
                        sentence_count=60, hedging_rate=3.0)
    res = monitor.evaluate_window(baseline, win)
    assert "shape" in res["families_moved"]
    assert "user" in res["families_moved"]
    assert res["signals"]["quiz_score"]["z"] > 0  # moved the "good" way and still flagged
    assert res["verdict"] in ("ticket", "page")
    assert res["static"]["fired"] is False


def test_system_signals_never_corroborate(baseline):
    # latency triples, tokens double: a system incident, not behavioural drift
    win = make_sessions(3, scenario="slow", start=100, duration_ms=450_000, total_tokens=16_000)
    res = monitor.evaluate_window(baseline, win)
    assert set(res["system_signals_moved"]) == {"duration_ms", "total_tokens"}
    assert res["families_moved"] == {}
    assert res["verdict"] == "ok"


def test_new_segment_counts_as_input_family(baseline):
    win = make_sessions(3, scenario="traffic_shift", segment="sql", start=100, grounding=0.08, refusal_rate=1.0)
    res = monitor.evaluate_window(baseline, win)
    assert res["new_segment"] is True
    assert "input" in res["families_moved"]
    assert "grounding" in res["families_moved"]
    assert res["verdict"] == "ticket"  # no baseline for the segment → never a page, always a ticket


def test_families_moved_helper_and_verdict():
    moved = monitor.families_moved({"quiz_score": -3.0, "grounding": -0.5, "hedging_rate": 2.5})
    assert moved == {"user": ["quiz_score"], "shape": ["hedging_rate"]}
    assert monitor.verdict({}, False) == "ok"
    assert monitor.verdict({"user": ["quiz_score"]}, True) == "annotate"
    assert monitor.verdict(moved, False) == "ticket"
    assert monitor.verdict(moved, True) == "page"
    assert monitor.verdict(moved, True, new_segment=True) == "ticket"


# ── static threshold false positives ───────────────────────────────────────

def test_static_threshold_false_positives_on_baseline(baseline):
    st = monitor.static_threshold(baseline, threshold=0.6)
    # every "weak" persona session sits below 0.6 → a third of the healthy baseline pages
    assert st["false_positives"] >= 9
    assert st["false_positive_rate"] >= 0.3


def test_monitor_self_check_does_not_page_on_baseline(baseline):
    chk = monitor.baseline_self_check(baseline, window=3)
    assert chk["windows"] > 0
    assert chk["page"] == 0
    assert chk["ticket"] == 0


def test_static_threshold_misses_verbosity_but_fires_on_weak_persona(baseline):
    win_up = make_sessions(3, scenario="verbosity", start=100, quiz_score=0.95)
    assert monitor.static_threshold(baseline, win_up)["fired"] is False
    win_weak = baseline[baseline["persona"] == "weak"].head(3)
    assert monitor.static_threshold(baseline, win_weak)["fired"] is True  # healthy sessions, false page


# ── change-point detection ─────────────────────────────────────────────────

def test_cusum_detects_shift_and_ignores_noise():
    z = np.concatenate([RNG.normal(0, 1, 30), RNG.normal(-3, 1, 10)])
    alarm, sp, sn = monitor.cusum(z, k=0.5, h=4.0)
    assert alarm is not None and 30 <= alarm <= 34
    quiet = RNG.normal(0, 0.8, 40)
    assert monitor.cusum(quiet, k=0.5, h=4.0)[0] is None


def test_two_window_shift():
    series = np.concatenate([RNG.normal(0.7, 0.05, 20), RNG.normal(0.3, 0.05, 5)])
    moved, shift = monitor.two_window_shift(series, split=20)
    assert moved and shift < -2
    flat = RNG.normal(0.7, 0.05, 25)
    assert monitor.two_window_shift(flat, split=20)[0] is False


def test_robust_z_uses_floor_when_mad_is_zero():
    assert monitor.robust_z(1.0, 0.0, 0.0, floor=0.5) == pytest.approx(2.0)
    assert np.isnan(monitor.robust_z(float("nan"), 0.0, 0.1))


def test_baseline_strata_fall_back():
    base = make_sessions(30)
    b = monitor.build_baseline(base)
    _, level = b.lookup("quiz_score", "python-fundamentals", "good")
    assert level == "segment+persona"
    _, level = b.lookup("quiz_score", "python-fundamentals", "unknown-persona")
    assert level == "segment"
    _, level = b.lookup("quiz_score", "sql", "good")
    assert level == "global"


def test_run_monitor_end_to_end():
    df = pd.concat([
        make_sessions(30),
        make_sessions(3, scenario="stale_notes", start=100, quiz_score=lambda v: v - 0.3, grounding=0.05, refusal_rate=1.0),
        make_sessions(3, scenario="slow", start=200, duration_ms=400_000),
    ], ignore_index=True)
    rep = monitor.run_monitor(df)
    assert rep["baseline_n"] == 30
    assert rep["scenarios"]["stale_notes"]["verdict"] == "page"
    assert rep["scenarios"]["slow"]["verdict"] == "ok"
    assert rep["static_on_baseline"]["false_positives"] >= 9
    assert rep["monitor_on_baseline"]["page"] == 0


def test_optional_scores_are_nan_not_zero(baseline):
    # grader_agreement / a2a_unreachable absent on old sessions → NaN → ignored, never "moved"
    base = baseline.copy()
    base["grader_agreement"] = np.nan
    base["a2a_unreachable"] = np.nan
    win = make_sessions(3, scenario="x", start=100)
    win["grader_agreement"] = 0.9
    win["a2a_unreachable"] = 0.0
    res = monitor.evaluate_window(base, win)
    assert res["verdict"] == "ok" and np.isnan(res["signals"]["grader_agreement"]["z"])
    # present on both sides: the A2A service going away + grader disagreement corroborate
    base["grader_agreement"] = 0.95
    base["a2a_unreachable"] = 0.0
    win["grader_agreement"] = 0.4
    win["a2a_unreachable"] = 1.0
    res = monitor.evaluate_window(base, win)
    assert res["families_moved"] == {"tool": ["a2a_unreachable"], "grounding": ["grader_agreement"]}
    row = session_signals([{"thread_id": "t", "start_time": "2026-10-10T10:00:00Z", "duration": 1, "tags": [],
                            "feedback_scores": [{"name": "quiz_score", "value": 0.5}]}], [])
    assert np.isnan(row["grader_agreement"]) and np.isnan(row["a2a_unreachable"])
    row = session_signals([{"thread_id": "t", "start_time": "2026-10-10T10:00:00Z", "duration": 1, "tags": [],
                            "feedback_scores": [{"name": "grader_agreement", "value": 0.8}, {"name": "a2a_reachable", "value": 0.0}]}], [])
    assert row["grader_agreement"] == 0.8 and row["a2a_unreachable"] == 1.0


# ── signals helpers (pure functions) ───────────────────────────────────────

def test_overlap_and_session_signals_from_raw_payloads():
    assert overlap("closures remember enclosing scope variables", "A closure remembers variables from its enclosing scope") > 0.5
    assert overlap("", "anything") == 0.0
    traces = [{"thread_id": "t1", "start_time": "2026-10-10T10:00:00Z", "duration": 1000.0,
               "tags": ["drift:tool_fault", "persona:weak", "segment:python-fundamentals"],
               "input": {"goal": "Learn closures"},
               "feedback_scores": [{"name": "quiz_score", "value": 0.4}, {"name": "session_error", "value": 0.0}]}]
    spans = [
        {"name": "explainer", "type": "general", "output": {"messages": [
            {"type": "ai", "content": "", "tool_calls": [{"name": "tool_read_file"}]},
            {"type": "ai", "content": "Your notes don't cover this. It depends, but generally a closure may capture scope. However, it might not."},
        ]}},
        {"name": "explainer.execute_tool", "type": "tool", "input": {"tool_call": {"name": "tool_read_file", "args": {}}}, "output": {"output": ""}},
        {"name": "explainer.execute_tool", "type": "tool", "input": {"tool_call": {"name": "tool_search_notes", "args": {}}}, "output": {"output": "<html>503</html>"}},
        {"name": "explainer.execute_tool", "type": "tool", "input": {"tool_call": {"name": "tool_read_file", "args": {}}}, "output": {"output": "# Closures\n\nA closure captures variables from its enclosing scope and remembers them."}},
        {"name": "quiz.grade_answer", "output": {"correct": False, "score": 0.0, "feedback": "Could not grade automatically"}},
        {"name": "ChatOllama", "type": "llm", "metadata": {"langgraph_node": "explainer"},
         "output": {"generations": [[{"generation_info": {"model": "qwen2.5:3b"}, "message": {"usage_metadata": {"input_tokens": 100, "output_tokens": 50}}}]]}},
        {"name": "ChatOllama", "type": "llm", "usage": {"prompt_tokens": 20, "completion_tokens": 5}, "model": "qwen2.5:7b"},
    ]
    row = session_signals(traces, spans)
    assert row["scenario"] == "tool_fault" and row["persona"] == "weak" and row["segment"] == "python-fundamentals"
    assert row["tool_calls"] == 3 and row["tool_failures"] == 2 and row["empty_searches"] == 0
    assert row["parse_failures"] == 1
    assert row["refusal_rate"] == 1.0 and row["hedging_rate"] > 0
    assert row["total_tokens"] == 175 and row["explainer_iterations"] == 1
    assert row["models"] == "qwen2.5:3b|qwen2.5:7b"
    assert row["est_cost_usd"] > 0 and row["grounding"] > 0


def test_refine_segment_by_goal():
    assert refine_segment("python-fundamentals", "Learn Python closures and decorators from scratch") == "py-functions"
    assert refine_segment("python-fundamentals", "Understand Python generators and the iterator protocol") == "py-iteration"
    assert refine_segment("", "Learn SQL window functions") == "sql"
    assert refine_segment("sql", "anything") == "sql"                       # already fine-grained: untouched
    assert refine_segment("python-fundamentals", "Learn Rust ownership") == "python-fundamentals"  # no rule: keep


# ── inject: env hooks round-trip ───────────────────────────────────────────

def test_inject_apply_and_clear_round_trip(monkeypatch):
    monkeypatch.setenv("NOTES_PATH", "study_materials/sample_notes")
    monkeypatch.delenv("DRIFT_TOOL_FAULT_P", raising=False)
    for name in inject.SCENARIOS:
        hooks = inject.apply(name)
        assert hooks["DRIFT_SCENARIO"] == name
        assert inject.current() == name
    inject.clear()
    assert inject.current() == "none"
    import os
    assert os.environ["NOTES_PATH"] == "study_materials/sample_notes"
    assert "DRIFT_TOOL_FAULT_P" not in os.environ
    assert "DRIFT_EXPLAINER_PROMPT_SUFFIX" not in os.environ
    assert inject.segment_for("traffic_shift") == "sql" and inject.segment_for("stale_notes") == "py-functions"
    with pytest.raises(KeyError):
        inject.apply("not-a-scenario")
