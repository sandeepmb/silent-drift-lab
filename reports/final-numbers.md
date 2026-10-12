# Final numbers

Every number below was read from the file or command named beside it on 2026-10-11/12. Nothing is estimated. Where a number is confounded or thin, the line says so.

## 1. Seed sessions (`data/seed-runs.jsonl`)

- n = 30 sessions, 0 errors, 30 topics completed; 10 goals x 3 personas, segment `python-fundamentals` for all.
- Mean quiz score by persona (`avg_score`) and mean session seconds (`elapsed_s`):

| persona | n | mean quiz score | mean session s | median session s |
|---|---:|---:|---:|---:|
| good | 10 | 0.815 | 407.8 | 348.9 |
| average | 10 | 0.810 | 346.8 | 213.8 |
| weak | 10 | 0.817 | 327.3 | 248.7 |
| all | 30 | 0.814 | 360.6 | 265.2 |

- Session time is not comparable across the seed run: the first 22 sessions shared Ollama with other jobs (mean 463.2 s), the last 8 ran alone (mean 78.5 s). Source: `docs/evals.md` section 1, computed from the same file.
- Personas barely separate on quiz score (0.815 / 0.810 / 0.817): the student and grader are the same 7B model.

## 2. Drift-lab baseline (`data/baseline-runs.jsonl`, the population the drift monitor uses)

- n = 30; 21 `py-functions` + 9 `py-iteration` (`reports/drift-report.json`, `baseline_segments`); run alone, tag `baseline`.
- Mean quiz score 0.775; session seconds: mean 111.4, median 85.7, min 59.0, max 302.3.
  - good: n=10, mean quiz 0.810, mean seconds 107.7
  - average: n=10, mean quiz 0.710, mean seconds 112.1
  - weak: n=10, mean quiz 0.803, mean seconds 114.2
- Latency roughly doubled in the second half of the baseline (from about the 23rd session onward; `reports/drift-signals.csv`, `duration_ms`) and stayed high through the drift sessions. Cause not isolated.

## 3. Dataset (`learning-accelerator-sessions`; Opik REST `GET /api/v1/private/datasets`, `docs/evals.md`)

- Rebuilt from all 30 seeds with `scripts/seed_dataset.py --dataset-only` on 2026-10-11: 0 new items, version v5, 84 items (s019, s020, s027 yield one item each, the rest three).
- Versions: v1 `5f3ca124` 3 items; v2 `e77f885b` 9; v3 `005388f4` 12; v4 `38194cd7` 39; v5 `35075fd7` 84. Mirror: `data/datasets/learning-accelerator-sessions.jsonl`.

## 4. Headline experiments (`data/experiments.jsonl`; both on dataset v5, 20 items each, judge `ollama/qwen2.5:7b`; dataset version confirmed through `GET /api/v1/private/experiments`)

- Baseline `grader-consistency-qwen2.5-7b-20261010-2200`: http://localhost:5173/opik/default/experiments/01a127dd-03f6-70b4-932e-4091380f3e92/compare?experiments=%5B%2201a12955-c8ad-76da-bdde-719534396c34%22%5D
- Variant `grader-consistency-qwen2.5-3b-20261010-2205`: http://localhost:5173/opik/default/experiments/01a127dd-03f6-70b4-932e-4091380f3e92/compare?experiments=%5B%2201a12959-cec3-7728-bfbb-9ff61ee7c752%22%5D

| metric | 7B grader | 3B grader |
|---|---:|---:|
| explanation_grounded | 0.687 | 0.687 |
| feedback_hallucination | 0.690 | 0.705 |
| feedback_relevance | 0.760 | 0.775 |
| feedback_similarity | 0.882 | 0.466 |
| format_compliance | 1.000 | 1.000 |
| grade_agreement | 0.950 | 0.800 |
| grade_is_json | 1.000 | 1.000 |
| grade_mentions_missing_concept | 1.000 | 1.000 |
| grade_score_regex | 1.000 | 1.000 |

- These ran on 2026-10-10 22:00-22:10 PDT, after v5 was created (21:47 PDT), so they did not need a rerun. An earlier identical 7B run scored `grade_agreement` 1.00 and `feedback_similarity` 0.802: differences of a few hundredths between 20-item experiments are noise.

## 5. Judge agreement (`data/labels/judge_agreement.json`, `evals/validate_judge.py`)

- Labelled 10 of 40; **all 10 are the pre-filled example rows** (obvious cases). The other 30 are unlabelled. This is a smoke test of the procedure, not an accuracy claim.
- exact-bucket 0.700; binary @0.5 0.800; mean absolute error 0.120; Spearman rho 0.714. Three disagreements, all with the judge more generous than the human.

## 6. Online rules and alert (Opik REST; `docs/evals.md` section 5)

| rule | type | sampling | scored (feedback_scores, source online_scoring) |
|---|---|---:|---|
| `format-compliance` | python metric | 1.0 | 100 trace scores, mean 1.00 (2026-10-10 22:16 to 2026-10-12 03:09 UTC) |
| `thread-coherence` | thread LLM judge | 1.0 | 9 thread scores, mean 1.00 (2026-10-10 22:41 to 23:36 UTC); logged `request timed out` errors from 23:32 UTC |
| `answer-relevance` | LLM judge | 0.5 | 0 scores; `request timed out` errors from 23:14 UTC |

- Both judge rules were disabled for all seed/drift/demo runs (to keep the GPU free) and re-enabled at the very end of the final pass, not re-verified. Only `format-compliance` was active for everything in `reports/`.
- Alert `quiz-score-low`: `trace:feedback_score`, `quiz_score < 0.5`, average over a 900 s window, webhook `host.docker.internal:9009/alert`. `data/alerts.jsonl`: 3 payloads received; 1 is the `--test-webhook` sample (0.7500, workspace `demo_workspace_name`), 2 are real (0.4833 against threshold 0.5000, project `silent-drift-lab`, received 2026-10-10 15:51:31 and 15:56:30 PDT, five minutes apart = the 300 s `MetricsAlertJob` cadence). The 0.4833 was not traced to specific sessions.

## 7. Gate and tests

- `make evals-test`: `1 passed, 37 deselected` twice (227.83 s, then 46.76 s). Gate output line (`data/evals-gate-output.txt`): `evals gate: grade_agreement mean=1.00 format_compliance mean=1.00 -> http://localhost:5173/opik/default/experiments/01a127dd-03f6-70b4-932e-4091380f3e92/compare?experiments=%5B%2201a12e2f-c29d-7bc8-b609-b3d18e4775cf%22%5D` (5 items, dataset v5).
- `make test`: `37 passed, 1 deselected`; the same 37 pass in a fresh venv (`uv venv` + `uv pip install -r requirements.txt`, TELEMETRY_MODE=off, OPIK_TRACK_DISABLE=true). `make lint` (ruff 0.17.0): all checks passed.

## 8. Drift verdicts (`reports/drift-report.md` / `.json`; baseline n=30, 3 sessions per scenario, 18 drift sessions, 0 errors)

| scenario | n | families moved (signals) | change-point | verdict | static threshold fired | system signals moved | duration_ms | total_tokens | est_cost_usd | quiz_score | grounding (baseline -> drifted median) |
|---|---:|---|---|---|---|---|---|---|---|---|---|
| model_swap | 3 | shape (explanation_chars) | no | ANNOTATE | no | duration_ms, est_cost_usd | 94s -> 251s | 8,254 -> 7,329 | $0.0030 -> $0.0019 | 0.80 -> 0.67 | 0.33 -> 0.24 |
| prompt_mutation | 3 | grounding (grounding, notes_overlap); shape (explanation_chars, sentence_count) | yes | PAGE | no | duration_ms | 94s -> 204s | 8,254 -> 5,717 | $0.0030 -> $0.0021 | 0.80 -> 0.77 | 0.33 -> 0.75 |
| stale_notes | 3 | grounding (grounding); shape (explanation_chars, sentence_count) | no | TICKET | no | duration_ms | 94s -> 222s | 8,254 -> 9,724 | $0.0030 -> $0.0035 | 0.80 -> 0.85 | 0.33 -> 0.12 |
| tool_fault | 3 | tool (empty_searches); grounding (grounding, notes_overlap); shape (explanation_chars, sentence_count) | yes | PAGE | no | duration_ms | 94s -> 239s | 8,254 -> 7,182 | $0.0030 -> $0.0026 | 0.80 -> 0.77 | 0.33 -> 0.00 |
| traffic_shift | 3 | tool (empty_searches); grounding (refusal_rate); input (segment) | no | TICKET | no | duration_ms | 86s -> 233s | 8,041 -> 6,236 | $0.0029 -> $0.0023 | 0.80 -> 0.85 | 0.26 -> 0.00 |
| verbosity | 3 | grounding (grounding); shape (explanation_chars, sentence_count) | no | TICKET | no | duration_ms, total_tokens | 94s -> 306s | 8,254 -> 9,912 | $0.0030 -> $0.0036 | 0.80 -> 0.88 | 0.33 -> 0.55 |

**False pages on the healthy baseline**

- Static rule `quiz_score < 0.6`: 1 of 30 sessions (3%); the session had quiz score 0.00.
- Monitor, sliding 3-session windows: 28 windows -> ok 19, annotate 5, ticket 2, page 2. Two false pages in 28 is a worse page rate than the static rule's 1 in 30; the windows were not investigated.
- The static rule fired on 0 of 6 injected windows.

**Reading it honestly.** All six injections got a non-ok verdict (2 page, 3 ticket, 1 annotate); `model_swap` is the weak one (one family, lowest verdict). Quiz score never moved (|z| < 2 everywhere). Grounding moved in both directions (down: stale_notes 0.12, tool_fault 0.00; up: prompt_mutation 0.75, verbosity 0.55). Duration moved in all six but is confounded by the host slowdown that began inside the baseline; tokens moved only in verbosity (z +2.5), cost only in model_swap (lower, z -2.5). 0 of 18 injected sessions errored.

## 9. Footprint (`make footprint` -> `reports/footprint.md`, 2026-10-11 20:38)

- 4.60 GiB across 8 containers (ClickHouse 2,477 MiB, backend 910, python-backend 784, zookeeper 202, mysql 166, minio 144, frontend 23, redis 5), Docker limit 7.748 GiB, about 640 stored traces. The 3.4 GiB in earlier drafts was a fresh-stack figure that I could not re-verify from a file; do not quote it.
- The Opik backend container was OOM-killed (exit 137, `OOMKilled: true`) twice on 2026-10-10/11, causing 502s until restarted with `docker start`. Other containers from unrelated projects shared the Docker VM at the time.

## 10. Timing

- One-topic traced session, baseline n=30: median 85.7 s, mean 111.4 s (`data/baseline-runs.jsonl`).
- `make demo` (`data/demo-output.txt`): 385 s wall clock for two sessions (203.4 s and 171.0 s); an earlier run took 189.8 s and 235.5 s. Grounding 0.31 -> 0.09 (earlier run 0.32 -> 0.08); errors 0; tokens 8,342 -> 7,694; quiz score 72% -> 80%.
- Session time of the drift lab: baseline 55.7 min, drift 81.7 min (sum of `elapsed_s`).

## 11. A2A second opinion (Opik traces of the 48 lab sessions: baseline + drift)

- `grader_agreement` present on 46 of 48 sessions, mean 0.987, minimum 0.683 (`reports/drift-signals.csv`). `a2a_unreachable` = 1 on 2 of 48 sessions.
- Added time: the `coach.a2a_quiz_delegation` span lasted a mean of 25.1 s, median 18.9 s, min 3.5 s, max 120.6 s over the 47 sessions that have it (it contains the remote re-grade by the 7B model, and includes the slowdown period). Seed sessions: all 8 sessions run alone with the hop reached the service.

## 12. What failed, was cut, or is thin

- Only 10 judge labels exist and all are the pre-filled examples.
- 3 sessions per scenario; verdicts are medians of three.
- Latency is unusable as evidence: it roughly doubled inside the baseline and stayed high; cause not isolated.
- The monitor produced 2 false pages in 28 healthy windows (not investigated).
- `answer-relevance` never scored; the judge rules timed out on a shared GPU, were off for all runs, and were re-enabled at the end without re-verification.
- Opik backend OOM-killed twice (8 GB Docker); traces during outages are lost.
- 21 earlier `drift:*` sessions are in Opik but excluded from the analysis; 3 unfinished sessions dropped.
- The `make evals-test` first run took 227.83 s, the second 46.76 s; difference not investigated.
- Demo GIF and published-link TODOs remain by design.
- The `docs/img/shot-*.png` screenshot files in the repo directory were produced by another worker for the handbook, not by this pass.
