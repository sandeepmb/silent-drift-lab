"""Unit tests for evals/metrics.py — no LLM, no network.  Run: make test"""

import json

import pytest

from evals.metrics import (
    ExplanationGrounded,
    FormatCompliance,
    GradeAgreement,
    Keyed,
    grade_bucket,
    heuristic_metrics,
)
from opik.evaluation.metrics import Contains

pytestmark = pytest.mark.unit

GOOD = {"correct": True, "score": 0.85, "feedback": "Right idea.", "missing_concept": ""}
BAD = {"correct": False, "score": 0.1, "feedback": "Wrong.", "missing_concept": "scope"}


# ── grade_bucket ─────────────────────────────────────────────────────────────

def test_grade_bucket_uses_correct_flag_then_score():
    assert grade_bucket(GOOD) == "correct"
    assert grade_bucket(BAD) == "incorrect"
    assert grade_bucket({"score": 0.6}) == "correct"          # no flag: score >= 0.5
    assert grade_bucket({"score": "nan"}) == "incorrect"      # unparsable score
    assert grade_bucket("not json") == "incorrect"


# ── GradeAgreement ───────────────────────────────────────────────────────────

def test_grade_agreement_agrees_on_same_bucket():
    r = GradeAgreement(track=False).score(grade=GOOD, expected_output={"grade": "correct", "score": 0.7})
    assert r.value == 1.0
    assert r.metadata["score_delta"] == pytest.approx(0.15)
    assert r.name == "grade_agreement"


def test_grade_agreement_disagrees_across_buckets():
    r = GradeAgreement(track=False).score(grade=BAD, expected_output={"grade": "correct", "score": 0.9})
    assert r.value == 0.0
    assert "re-grade=incorrect stored=correct" in r.reason


def test_grade_agreement_accepts_json_strings_and_falls_back_to_score():
    r = GradeAgreement(track=False).score(grade=json.dumps(GOOD), expected_output={"score": 0.55})
    assert r.value == 1.0


def test_grade_agreement_invalid_regrade_is_zero():
    r = GradeAgreement(track=False).score(grade="garbage", expected_output={"grade": "correct"})
    assert r.value == 0.0 and not r.scoring_failed


# ── FormatCompliance ─────────────────────────────────────────────────────────

def test_format_compliance_passes_full_grader_json():
    assert FormatCompliance(track=False).score(grade=GOOD).value == 1.0
    assert FormatCompliance(track=False).score(output=json.dumps(BAD)).value == 1.0


@pytest.mark.parametrize("payload, fragment", [
    ({"score": 0.5, "feedback": "x", "missing_concept": ""}, "missing correct"),
    ({"correct": "yes", "score": 0.5, "feedback": "x", "missing_concept": ""}, "correct has type str"),
    ({"correct": True, "score": 1.5, "feedback": "x", "missing_concept": ""}, "score outside [0, 1]"),
    ({"correct": True, "score": 0.5, "feedback": 3, "missing_concept": ""}, "feedback has type int"),
    ("{not json", "not a JSON object"),
    (None, "not a JSON object"),
])
def test_format_compliance_reports_problems(payload, fragment):
    r = FormatCompliance(track=False).score(grade=payload)
    assert r.value == 0.0
    assert fragment in r.reason


# ── ExplanationGrounded ──────────────────────────────────────────────────────

NOTES = "A closure is a nested function that remembers variables from its enclosing scope. Use nonlocal to rebind."


def test_explanation_grounded_counts_overlapping_sentences():
    m = ExplanationGrounded(notes_text=NOTES, track=False)
    r = m.score(explanation="A closure remembers variables from the enclosing scope. Bananas are a yellow tropical fruit.")
    assert r.value == pytest.approx(0.5)
    assert r.metadata == {"sentences": 2, "grounded": 1}


def test_explanation_grounded_ignores_code_blocks_and_handles_empty():
    m = ExplanationGrounded(notes_text=NOTES, track=False)
    assert m.score(explanation="```python\nprint('unrelated')\n```").value == 0.0
    assert m.score(explanation="").reason == "no explanation sentences"
    assert m.score(output="Nested functions capture enclosing variables as a closure.").value == 1.0


def test_explanation_grounded_loads_sample_notes_by_default():
    m = ExplanationGrounded(track=False)
    assert "closure" in m.vocab and "decorator" in m.vocab


# ── Keyed wrapper and heuristics ─────────────────────────────────────────────

def test_keyed_routes_arguments_and_renames():
    m = Keyed(Contains(reference="score", track=False), {"output": "grade_json"}, name="has_score")
    r = m.score(grade_json='{"score": 0.5}', unrelated="ignored")
    assert r.value == 1.0 and r.name == "has_score"
    assert m.score(grade_json="{}").value == 0.0


def test_heuristic_metrics_on_grader_output():
    kwargs = {"grade_json": json.dumps(GOOD), "feedback": "Right idea.", "stored_feedback": "Right idea!"}
    scores = {m.name: m.score(**kwargs).value for m in heuristic_metrics()}
    assert scores["grade_is_json"] == 1.0
    assert scores["grade_score_regex"] == 1.0
    assert scores["grade_mentions_missing_concept"] == 1.0
    assert scores["feedback_similarity"] > 0.9
