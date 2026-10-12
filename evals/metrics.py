"""
evals/metrics.py — scoring metrics for the Learning Accelerator.

Three custom metrics (no LLM):
  GradeAgreement       does a re-grade agree with the grade stored in the dataset?
  ExplanationGrounded  fraction of explanation sentences with keyword overlap to the study notes
  FormatCompliance     the grader JSON has the required keys with the right types

Plus `Keyed`, a thin wrapper that lets Opik's built-in heuristics
(Contains / RegexMatch / LevenshteinRatio / IsJson) and LLM judges
(Hallucination / AnswerRelevance / TrajectoryAccuracy) read their arguments
from the keys our evaluation task produces, and factories that configure the
judges with the local Ollama model (LiteLLM string, default
`ollama/qwen2.5:7b`).

Every metric is a `BaseMetric` subclass returning a `ScoreResult`, so it can be
passed to `opik.evaluation.evaluate(scoring_metrics=[...])` and also used in
an online rule's Python code.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from opik.evaluation.metrics import (
    AnswerRelevance,
    Contains,
    Hallucination,
    IsJson,
    LevenshteinRatio,
    RegexMatch,
    TrajectoryAccuracy,
    base_metric,
    score_result,
)

JUDGE_MODEL = os.getenv("JUDGE_MODEL", "ollama/qwen2.5:7b").split("#")[0].strip()
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NOTES_DIR = ROOT / os.getenv("NOTES_PATH", "study_materials/sample_notes").split("#")[0].strip()

GRADER_REQUIRED_KEYS = {"correct": bool, "score": (int, float), "feedback": str, "missing_concept": str}


def _as_dict(value: Any) -> Optional[dict]:
    """Accept a dict or a JSON string; return a dict or None."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def grade_bucket(grade: Any) -> str:
    """'correct' / 'incorrect' from a grader dict (uses `correct`, falls back to score >= 0.5)."""
    g = _as_dict(grade) or {}
    if isinstance(g.get("correct"), bool):
        return "correct" if g["correct"] else "incorrect"
    try:
        return "correct" if float(g.get("score", 0.0)) >= 0.5 else "incorrect"
    except (TypeError, ValueError):
        return "incorrect"


# ─────────────────────────────────────────────────────────────────────────────
# Custom metrics
# ─────────────────────────────────────────────────────────────────────────────

class GradeAgreement(base_metric.BaseMetric):
    """
    1.0 when the re-grade lands in the same bucket (correct / incorrect) as the
    grade recorded in the dataset, else 0.0. The absolute score difference is
    reported in `metadata` so the experiment can also show drift in magnitude.

    score(grade=<grader dict or JSON>, expected_output={"grade": ..., "score": ...})
    """

    def __init__(self, name: str = "grade_agreement", track: bool = True, project_name: Optional[str] = None):
        super().__init__(name=name, track=track, project_name=project_name)

    def score(self, grade: Any, expected_output: Any, **ignored_kwargs: Any) -> score_result.ScoreResult:
        new = _as_dict(grade)
        expected = _as_dict(expected_output) or {}
        if new is None:
            return score_result.ScoreResult(name=self.name, value=0.0, reason="re-grade is not a JSON object", scoring_failed=False)
        got = grade_bucket(new)
        want = str(expected.get("grade") or grade_bucket(expected))
        try:
            delta = abs(float(new.get("score", 0.0)) - float(expected.get("score", 0.0)))
        except (TypeError, ValueError):
            delta = None
        agree = got == want
        return score_result.ScoreResult(
            name=self.name,
            value=1.0 if agree else 0.0,
            reason=f"re-grade={got} stored={want}" + (f" |Δscore|={delta:.2f}" if delta is not None else ""),
            metadata={"score_delta": delta, "regrade_bucket": got, "stored_bucket": want},
        )


_STOPWORDS = {
    "this", "that", "with", "from", "they", "them", "than", "then", "when", "what", "which", "where",
    "will", "would", "could", "should", "have", "has", "been", "being", "were", "your", "you", "into",
    "also", "just", "like", "more", "most", "some", "such", "very", "each", "other", "about", "there",
    "these", "those", "here", "their", "because", "while", "after", "before", "does", "doing", "done",
    "only", "over", "same", "still", "both", "even", "every", "make", "makes", "made", "using", "used",
    "want", "need", "example", "imagine", "think", "thing", "things", "something", "call", "called",
}
_WORD = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]{3,}")
_SENT = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if w.lower() not in _STOPWORDS}


def load_notes_vocab(notes_dir: Union[str, Path] = DEFAULT_NOTES_DIR) -> set[str]:
    """Keyword vocabulary of all markdown notes under notes_dir."""
    vocab: set[str] = set()
    for p in sorted(Path(notes_dir).glob("*.md")):
        vocab |= _tokens(p.read_text(encoding="utf-8", errors="ignore"))
    return vocab


class ExplanationGrounded(base_metric.BaseMetric):
    """
    Heuristic groundedness: the fraction of explanation sentences whose content
    words overlap the study notes' vocabulary by at least `min_ratio` (and at
    least one word). No LLM; a cheap proxy for "the tutor explained from the
    notes rather than from thin air".

    score(explanation=<str>)   (falls back to `output` when it is a string)
    """

    def __init__(
        self,
        notes_dir: Union[str, Path] = DEFAULT_NOTES_DIR,
        notes_text: Optional[str] = None,
        min_ratio: float = 0.3,
        name: str = "explanation_grounded",
        track: bool = True,
        project_name: Optional[str] = None,
    ):
        super().__init__(name=name, track=track, project_name=project_name)
        self.min_ratio = min_ratio
        self.vocab = _tokens(notes_text) if notes_text is not None else load_notes_vocab(notes_dir)

    @staticmethod
    def sentences(text: str) -> list[str]:
        # drop fenced code blocks: code lines are not "claims"
        text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
        return [s.strip() for s in _SENT.split(text) if len(_tokens(s)) > 0]

    def score(self, explanation: Optional[str] = None, output: Any = None, **ignored_kwargs: Any) -> score_result.ScoreResult:
        text = explanation if isinstance(explanation, str) else (output if isinstance(output, str) else "")
        sents = self.sentences(text)
        if not sents:
            return score_result.ScoreResult(name=self.name, value=0.0, reason="no explanation sentences")
        grounded = 0
        for s in sents:
            toks = _tokens(s)
            overlap = len(toks & self.vocab)
            if overlap >= 1 and overlap / len(toks) >= self.min_ratio:
                grounded += 1
        value = grounded / len(sents)
        return score_result.ScoreResult(
            name=self.name,
            value=value,
            reason=f"{grounded}/{len(sents)} sentences overlap the notes (min_ratio={self.min_ratio})",
            metadata={"sentences": len(sents), "grounded": grounded},
        )


class FormatCompliance(base_metric.BaseMetric):
    """
    1.0 when the grader output is a JSON object with the four required keys
    (`correct` bool, `score` number in [0, 1], `feedback` str,
    `missing_concept` str); 0.0 otherwise, with the problems in `reason`.

    score(grade=<grader dict or JSON string>)   (falls back to `output`)
    """

    def __init__(self, name: str = "format_compliance", track: bool = True, project_name: Optional[str] = None):
        super().__init__(name=name, track=track, project_name=project_name)

    def score(self, grade: Any = None, output: Any = None, **ignored_kwargs: Any) -> score_result.ScoreResult:
        d = _as_dict(grade if grade is not None else output)
        problems: list[str] = []
        if d is None:
            problems.append("not a JSON object")
        else:
            for key, typ in GRADER_REQUIRED_KEYS.items():
                if key not in d:
                    problems.append(f"missing {key}")
                elif not isinstance(d[key], typ) or (key == "correct" and not isinstance(d[key], bool)):
                    problems.append(f"{key} has type {type(d[key]).__name__}")
            if d is not None and isinstance(d.get("score"), (int, float)) and not 0.0 <= float(d["score"]) <= 1.0:
                problems.append("score outside [0, 1]")
        return score_result.ScoreResult(
            name=self.name,
            value=0.0 if problems else 1.0,
            reason="; ".join(problems) if problems else "all required keys present and typed",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Wrappers around Opik's built-in metrics
# ─────────────────────────────────────────────────────────────────────────────

class Keyed(base_metric.BaseMetric):
    """
    Route keys from the evaluation inputs into a built-in metric's score() args.

        Keyed(Hallucination(model=...), {"input": "question", "output": "feedback", "context": "context"})

    `mapping` is {inner_arg: source_key}. Keys missing from the inputs are
    omitted so the inner metric's defaults apply.
    """

    def __init__(self, inner: base_metric.BaseMetric, mapping: dict[str, str], name: Optional[str] = None):
        super().__init__(name=name or inner.name, track=False)
        self.inner = inner
        self.mapping = mapping

    def _kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {arg: kwargs[src] for arg, src in self.mapping.items() if src in kwargs}

    def score(self, **kwargs: Any) -> Union[score_result.ScoreResult, list[score_result.ScoreResult]]:
        res = self.inner.score(**self._kwargs(kwargs))
        results = res if isinstance(res, list) else [res]
        for r in results:
            r.name = self.name
        return res


def heuristic_metrics() -> list[base_metric.BaseMetric]:
    """Built-in heuristics, keyed to the experiment task's outputs. No LLM calls."""
    return [
        Keyed(IsJson(track=False), {"output": "grade_json"}, name="grade_is_json"),
        Keyed(RegexMatch(regex=r'"score"\s*:\s*(0(\.\d+)?|1(\.0+)?)', track=False), {"output": "grade_json"}, name="grade_score_regex"),
        Keyed(Contains(reference="missing_concept", case_sensitive=False, track=False), {"output": "grade_json"}, name="grade_mentions_missing_concept"),
        Keyed(LevenshteinRatio(track=False), {"output": "feedback", "reference": "stored_feedback"}, name="feedback_similarity"),
    ]


JUDGE_TIMEOUT_S = float(os.getenv("JUDGE_TIMEOUT_S", "300"))


def judge_model(model: str = JUDGE_MODEL, temperature: float = 0.0, timeout_s: float = JUDGE_TIMEOUT_S):
    """
    The judge as an Opik model object. A plain LiteLLM string (`model="ollama/qwen2.5:7b"`)
    also works for every judge class, but the SDK then applies its 60 s read timeout,
    which a busy local Ollama exceeds; LiteLLMChatModel forwards `timeout` to litellm.completion.
    """
    from opik.evaluation.models import LiteLLMChatModel

    return LiteLLMChatModel(model_name=model, temperature=temperature, timeout=timeout_s)


def judge_metrics(model: str = JUDGE_MODEL, temperature: float = 0.0) -> list[base_metric.BaseMetric]:
    """
    LLM judges on the grader's feedback sentence:
      feedback_hallucination  Opik Hallucination: 0.0 = faithful to the context, 1.0 = unfaithful
      feedback_relevance      Opik AnswerRelevance: 1.0 = the feedback addresses the question
    Context = the reference answer and the student's answer. Judge = local Ollama via LiteLLM.
    """
    jm = judge_model(model, temperature)
    return [
        Keyed(Hallucination(model=jm, track=True),
              {"input": "question", "output": "feedback", "context": "context"}, name="feedback_hallucination"),
        Keyed(AnswerRelevance(model=jm, track=True),
              {"input": "question", "output": "feedback", "context": "context"}, name="feedback_relevance"),
    ]


def trajectory_judge(model: str = JUDGE_MODEL) -> base_metric.BaseMetric:
    """
    TrajectoryAccuracy needs goal / trajectory / final_result. The seed dataset
    stores single Q&A items, not agent trajectories, so this is provided for
    trace-level use (see docs/evals.md) and is not part of the default experiment.
    """
    return Keyed(TrajectoryAccuracy(model=judge_model(model), track=True),
                 {"goal": "goal", "trajectory": "trajectory", "final_result": "final_result"},
                 name="trajectory_accuracy")


def custom_metrics(notes_dir: Union[str, Path] = DEFAULT_NOTES_DIR) -> list[base_metric.BaseMetric]:
    return [GradeAgreement(), FormatCompliance(), ExplanationGrounded(notes_dir=notes_dir)]


def all_metrics(with_judges: bool = True, model: str = JUDGE_MODEL) -> list[base_metric.BaseMetric]:
    metrics: list[base_metric.BaseMetric] = custom_metrics() + heuristic_metrics()
    if with_judges:
        metrics += judge_metrics(model=model)
    return metrics


__all__ = [
    "GradeAgreement", "ExplanationGrounded", "FormatCompliance", "Keyed",
    "heuristic_metrics", "judge_metrics", "judge_model", "trajectory_judge", "custom_metrics", "all_metrics",
    "grade_bucket", "load_notes_vocab", "JUDGE_MODEL", "JUDGE_TIMEOUT_S", "GRADER_REQUIRED_KEYS",
]
