"""
evals/validate_judge.py — does the LLM grader agree with humans?

  python evals/validate_judge.py                 # (re)build the label CSV, then report
  python evals/validate_judge.py --report-only   # just compute agreement from the CSV

The app's grader (quiz.grade_answer, an LLM-as-judge) produced a score for
every quiz answer in the dataset. This script writes a 40-item hand-label
template, data/labels/judge_labels.csv:

  key, scenario_id, persona, question, expected, student_answer, judge_score, judge_grade, human_score, note

judge_score comes from the dataset; human_score is left blank for a person to
fill in (0.0-1.0, same rubric as the grader). Rows listed in
data/labels/example_labels.json are pre-filled and marked note=example so the
report has something to compute before a full labelling pass.

Agreement, computed over rows where human_score is present:
  exact-bucket agreement   3 buckets from the grader rubric: wrong < 0.3 <= partial < 0.7 <= correct
  binary agreement         pass/fail at 0.5
  Spearman rho             rank correlation of judge_score vs human_score (average ranks for ties)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LABELS_DIR = ROOT / "data" / "labels"
CSV_PATH = LABELS_DIR / "judge_labels.csv"
EXAMPLES_PATH = LABELS_DIR / "example_labels.json"
REPORT_PATH = LABELS_DIR / "judge_agreement.json"
MIRROR = ROOT / "data" / "datasets" / "learning-accelerator-sessions.jsonl"
DATASET_NAME = os.getenv("SEED_DATASET_NAME", "learning-accelerator-sessions")
N_ITEMS = 40
COLUMNS = ["key", "scenario_id", "persona", "question", "expected", "student_answer",
           "judge_score", "judge_grade", "human_score", "note"]


def item_key(question: str, student_answer: str) -> str:
    return hashlib.sha1(f"{question}\n{student_answer}".encode()).hexdigest()[:10]


def load_items(from_opik: bool) -> list[dict]:
    """Dataset items: from the Opik dataset if reachable, else the local mirror."""
    items: list[dict] = []
    if from_opik:
        try:
            import opik
            items = opik.Opik().get_dataset(DATASET_NAME).get_items()
        except Exception as e:  # offline: fall back to the mirror
            print(f"[labels] Opik dataset unavailable ({type(e).__name__}); using local mirror")
    if not items and MIRROR.exists():
        items = [json.loads(l) for l in MIRROR.read_text().splitlines() if l.strip()]
    return items


def build_csv(items: list[dict]) -> list[dict]:
    """Write/refresh the CSV, keeping human_score values already entered."""
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    if CSV_PATH.exists():
        with CSV_PATH.open(newline="") as fh:
            existing = {r["key"]: r for r in csv.DictReader(fh)}
    examples = json.loads(EXAMPLES_PATH.read_text()) if EXAMPLES_PATH.exists() else {}

    # Spread the 40 items over personas so the template has obvious highs and lows.
    by_persona: dict[str, list[dict]] = {}
    for it in items:
        by_persona.setdefault(it["input"].get("persona", "?"), []).append(it)
    picked: list[dict] = []
    while len(picked) < N_ITEMS and any(by_persona.values()):
        for p in sorted(by_persona):
            if by_persona[p] and len(picked) < N_ITEMS:
                picked.append(by_persona[p].pop(0))

    rows: list[dict] = []
    for it in picked:
        inp, exp = it["input"], it.get("expected_output") or {}
        key = item_key(inp["question"], inp["student_answer"])
        old = existing.get(key, {})
        human = old.get("human_score", "")
        note = old.get("note", "")
        if key in examples:
            human, note = str(examples[key]["human_score"]), "example: " + examples[key].get("why", "")
        rows.append({
            "key": key,
            "scenario_id": inp.get("scenario_id", ""),
            "persona": inp.get("persona", ""),
            "question": inp["question"],
            "expected": inp["expected_answer"],
            "student_answer": inp["student_answer"],
            "judge_score": exp.get("score", ""),
            "judge_grade": exp.get("grade", ""),
            "human_score": human,
            "note": note,
        })
    with CSV_PATH.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"[labels] wrote {len(rows)} rows to {CSV_PATH} ({sum(1 for r in rows if r['human_score'] != '')} with human_score)")
    return rows


# ── agreement ────────────────────────────────────────────────────────────────

def bucket3(x: float) -> str:
    return "wrong" if x < 0.3 else ("partial" if x < 0.7 else "correct")


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def report(rows: list[dict]) -> dict:
    labelled = [r for r in rows if str(r.get("human_score", "")).strip() != ""]
    if not labelled:
        print("[report] no human_score values yet; fill data/labels/judge_labels.csv and re-run")
        return {"labelled": 0}
    j = [float(r["judge_score"]) for r in labelled]
    h = [float(r["human_score"]) for r in labelled]
    exact = sum(bucket3(x) == bucket3(y) for x, y in zip(j, h)) / len(labelled)
    binary = sum((x >= 0.5) == (y >= 0.5) for x, y in zip(j, h)) / len(labelled)
    mae = sum(abs(x - y) for x, y in zip(j, h)) / len(labelled)
    rho = spearman(j, h)
    examples = sum(1 for r in labelled if str(r.get("note", "")).startswith("example"))
    out = {
        "labelled": len(labelled), "of": len(rows), "examples_prefilled": examples,
        "exact_bucket_agreement": round(exact, 3), "binary_agreement": round(binary, 3),
        "mean_abs_error": round(mae, 3), "spearman_rho": round(rho, 3) if rho is not None else None,
        "disagreements": [
            {"key": r["key"], "persona": r["persona"], "judge": float(r["judge_score"]), "human": float(r["human_score"])}
            for r in labelled if bucket3(float(r["judge_score"])) != bucket3(float(r["human_score"]))
        ],
    }
    print("\nJudge vs human agreement")
    print(f"  labelled items          {out['labelled']}/{out['of']} ({examples} pre-filled examples)")
    print(f"  exact-bucket agreement  {out['exact_bucket_agreement']:.3f}   (wrong <0.3 <= partial <0.7 <= correct)")
    print(f"  binary agreement @0.5   {out['binary_agreement']:.3f}")
    print(f"  mean |judge - human|    {out['mean_abs_error']:.3f}")
    print(f"  Spearman rho            {out['spearman_rho']}")
    for d in out["disagreements"]:
        print(f"    disagree {d['key']} persona={d['persona']} judge={d['judge']:.2f} human={d['human']:.2f}")
    REPORT_PATH.write_text(json.dumps(out, indent=2))
    print(f"  written to {REPORT_PATH}")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report-only", action="store_true", help="do not rebuild the CSV")
    p.add_argument("--local", action="store_true", help="read items from the local mirror, not Opik")
    args = p.parse_args()

    if args.report_only:
        with CSV_PATH.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
    else:
        rows = build_csv(load_items(from_opik=not args.local))
    report(rows)
