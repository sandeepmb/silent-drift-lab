"""
Evaluation gate — run: make evals-test   (pytest -m eval)

Re-grades 5 dataset items with the app's grader through opik.evaluation.evaluate()
and asserts the grader agrees with its own recorded verdict at least 60% of the
time. Needs Opik at OPIK_URL_OVERRIDE, Ollama, and the seed dataset
(make seed). No LLM judges: the gate is about grader consistency, not judges.
"""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.eval

NB_SAMPLES = 5
MIN_AGREEMENT = 0.6


@pytest.fixture(scope="module")
def dataset():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import opik

    name = os.getenv("SEED_DATASET_NAME", "learning-accelerator-sessions")
    try:
        ds = opik.Opik().get_dataset(name)
    except Exception as e:  # Opik down or dataset missing: skip, do not fail
        pytest.skip(f"dataset '{name}' not available: {type(e).__name__}: {e}")
    if not ds.get_items(nb_samples=1):
        pytest.skip(f"dataset '{name}' is empty; run make seed")
    return ds


def test_grader_agrees_with_recorded_grades(dataset):
    from opik.evaluation import evaluate

    from evals.metrics import FormatCompliance, GradeAgreement
    from evals.run_experiment import make_task

    result = evaluate(
        dataset=dataset,
        task=make_task(),
        scoring_metrics=[GradeAgreement(), FormatCompliance()],
        experiment_name="evals-gate",
        experiment_config={"gate": "GradeAgreement >= 0.6", "nb_samples": NB_SAMPLES},
        nb_samples=NB_SAMPLES,
        task_threads=1,
        verbose=0,
    )
    scores = result.aggregate_evaluation_scores().aggregated_scores
    assert len(result.test_results) == NB_SAMPLES
    agreement = scores["grade_agreement"].mean
    print(f"\nevals gate: grade_agreement mean={agreement:.2f} "
          f"format_compliance mean={scores['format_compliance'].mean:.2f} -> {result.experiment_url}")
    assert scores["format_compliance"].mean == 1.0, "grader JSON lost a required key"
    assert agreement >= MIN_AGREEMENT, f"grader agreed with its own verdicts on only {agreement:.0%} of {NB_SAMPLES} items"
