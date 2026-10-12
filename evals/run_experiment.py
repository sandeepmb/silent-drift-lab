"""
evals/run_experiment.py — offline evaluation with opik.evaluation.evaluate().

  make experiment                              # baseline (qwen2.5:7b) + variant (qwen2.5:3b), 20 samples each
  python evals/run_experiment.py --samples 5 --no-judges
  python evals/run_experiment.py --variant none         # baseline only
  python evals/run_experiment.py --variant qwen2.5:3b --temperature 0.8

The task re-grades the stored student answer with the app's own grader
(app/agents/quiz_generator.grade_answer) so the experiment measures grader
consistency: does the grader, run again, agree with the verdict it produced
during the live session? Custom metrics (GradeAgreement, FormatCompliance,
ExplanationGrounded), built-in heuristics and two Ollama LLM judges score
every item. A second experiment with a different grader model (or
temperature) shows experiment comparison in the Opik UI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import logging  # noqa: E402

for _n in ("httpx", "httpcore", "opik", "urllib3", "LiteLLM"):
    logging.getLogger(_n).setLevel(logging.WARNING)

import opik  # noqa: E402
from opik.evaluation import evaluate  # noqa: E402

from evals import metrics as M  # noqa: E402

DATASET_NAME = os.getenv("SEED_DATASET_NAME", "learning-accelerator-sessions")
PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab").split("#")[0].strip()
BASE_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").split("#")[0].strip()


def make_task(model: str | None = None, temperature: float | None = None):
    """
    Build the evaluation task around the app's grade_answer().

    quiz_generator reads MODEL_NAME at call time, so a model swap is one
    attribute; the grading temperature is hard-coded (0.1) inside grade_answer,
    so a temperature variant wraps the ChatOllama class it instantiates.
    """
    from agents import quiz_generator as qg

    if model:
        qg.MODEL_NAME = model
    if temperature is not None:
        real_cls = qg.ChatOllama

        class _TempChatOllama(real_cls):  # type: ignore[misc,valid-type]
            def __init__(self, *a: Any, **kw: Any) -> None:
                kw["temperature"] = temperature
                super().__init__(*a, **kw)

        qg.ChatOllama = _TempChatOllama

    def task(item: dict[str, Any]) -> dict[str, Any]:
        inp = item["input"]
        exp = item.get("expected_output") or {}
        grade = qg.grade_answer(inp["question"], inp["expected_answer"], inp["student_answer"])
        feedback = str(grade.get("feedback", "")) if isinstance(grade, dict) else ""
        return {
            # for the custom metrics
            "grade": grade,
            "grade_json": json.dumps(grade),
            "explanation": inp.get("explanation", ""),
            # for the built-in heuristics and judges (flat string keys)
            "question": inp["question"],
            "student_answer": inp["student_answer"],
            "feedback": feedback,
            "stored_feedback": str(exp.get("feedback", "")),
            "context": [f"Reference answer: {inp['expected_answer']}", f"Student answer: {inp['student_answer']}"],
            "output": feedback,
        }

    return task


def run(model: str | None, temperature: float | None, samples: int, with_judges: bool, label: str):
    client = opik.Opik()
    dataset = client.get_dataset(DATASET_NAME)
    grader_model = model or BASE_MODEL
    name = f"grader-consistency-{label}-{time.strftime('%Y%m%d-%H%M')}"
    config = {
        "task": "re-grade stored student answers with app grade_answer()",
        "grader_model": grader_model,
        "grader_temperature": temperature if temperature is not None else 0.1,
        "judge_model": M.JUDGE_MODEL if with_judges else None,
        "nb_samples": samples,
        "dataset": DATASET_NAME,
        "dataset_version": getattr(dataset.get_version_info(), "version_name", None),
    }
    print(f"\n[experiment] {name}  config={json.dumps(config)}")
    result = evaluate(
        dataset=dataset,
        task=make_task(model, temperature),
        scoring_metrics=M.all_metrics(with_judges=with_judges),
        experiment_name=name,
        project_name=PROJECT,
        experiment_config=config,
        nb_samples=samples,
        task_threads=1,            # one Ollama; keep calls sequential
        verbose=1,
    )
    agg = result.aggregate_evaluation_scores()
    rows = {}
    for metric_name, stats in sorted(agg.aggregated_scores.items()):
        rows[metric_name] = {"mean": round(stats.mean, 3), "min": round(stats.min, 3), "max": round(stats.max, 3)}
    print(f"[experiment] {name}: {len(result.test_results)} items")
    for k, v in rows.items():
        print(f"   {k:32s} mean={v['mean']:.3f}  min={v['min']:.3f}  max={v['max']:.3f}")
    print(f"[experiment] URL: {result.experiment_url}")
    return {"name": name, "url": result.experiment_url, "items": len(result.test_results), "scores": rows, "config": config}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--samples", type=int, default=20, help="nb_samples per experiment (judge calls are bounded by this)")
    p.add_argument("--variant", default="qwen2.5:3b", help="grader model for the second experiment, or 'none'")
    p.add_argument("--temperature", type=float, default=None, help="grading temperature for the variant")
    p.add_argument("--no-judges", action="store_true", help="skip the LLM judges (custom + heuristic metrics only)")
    p.add_argument("--out", default=str(ROOT / "data" / "experiments.jsonl"))
    args = p.parse_args()
    samples = max(1, min(args.samples, 20))

    results = [run(None, None, samples, not args.no_judges, label=BASE_MODEL.replace(":", "-"))]
    if args.variant and args.variant.lower() != "none":
        label = args.variant.replace(":", "-") + (f"-t{args.temperature}" if args.temperature is not None else "")
        results.append(run(args.variant, args.temperature, samples, not args.no_judges, label=label))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as fh:
        for r in results:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **r}) + "\n")

    if len(results) > 1:
        print("\nComparison (mean per metric)")
        names = [r["name"] for r in results]
        print(f"   {'metric':32s} " + "  ".join(f"{n[-28:]:>28s}" for n in names))
        for m in sorted({k for r in results for k in r["scores"]}):
            print(f"   {m:32s} " + "  ".join(f"{r['scores'].get(m, {}).get('mean', float('nan')):>28.3f}" for r in results))
    print(f"\nResults appended to {out}")


if __name__ == "__main__":
    main()
