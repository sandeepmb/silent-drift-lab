# Evaluation layer

How the Learning Accelerator is evaluated against self-hosted Opik 2.2.96:
a dataset built from traces, offline experiments with a local judge, a
hand-label check of the judge, online rules that score future traces, and an
alert that reaches a local webhook. Everything below was run on 2026-10-10 and 2026-10-11
against Opik at `http://localhost:5173` (workspace `default`, project
`silent-drift-lab`), Ollama `qwen2.5:7b` / `qwen2.5:3b`, with no API keys.

| Deliverable | File | Command |
|---|---|---|
| Seed sessions + dataset | `scripts/seed_dataset.py` | `make seed` |
| Custom metrics | `evals/metrics.py` | imported by the others |
| Offline experiments | `evals/run_experiment.py` | `make experiment` |
| Judge validation | `evals/validate_judge.py` | `.venv/bin/python evals/validate_judge.py` |
| Online rules, feedback definition, alert | `scripts/create_rules.py` | `make rules` |
| Alert webhook sink | `scripts/alert_webhook.py` | `.venv/bin/python scripts/alert_webhook.py` |
| Eval gate / unit tests | `tests/test_evals_gate.py`, `tests/test_metrics.py` | `make evals-test` / `make test` |

## 1. Seed sessions and the dataset (`make seed`)

`scripts/seed_dataset.py` runs the 30 scripted sessions in
`scenarios/sessions.jsonl` (10 goals x 3 personas, one topic each) through
`main.run_session(goal, auto_approve=True, student_mode="simulated",
persona=..., max_topics=1, tags=[...])` with `DRIFT_SCENARIO=none`, and appends
one line per session to `data/seed-runs.jsonl`:

```json
{"scenario_id": "s001", "session_id": "84f52aec", "goal": "...", "persona": "good",
 "segment": "python-fundamentals", "avg_score": 0.85, "topics_completed": 1,
 "elapsed_s": 141.8, "trace_ids": ["01a127da-...", "01a127db-..."], "error": null}
```

Each session produces two Opik traces (the first ends at the human-approval
interrupt, the second is the resumed run that explains, quizzes and coaches),
both in thread `session_id`, both carrying the feedback scores `quiz_score`,
`topics_completed`, `weak_area_count`, `session_error` logged by
`main.run_session`.

Flags: `--limit N` runs the first N pending scenarios, `--resume` skips
scenario ids already in `seed-runs.jsonl`, `--dataset-only` rebuilds the
dataset from whatever sessions exist, `--sessions-only` skips the dataset.
The script is re-runnable at any point: the dataset builder reads the runs file,
so a run interrupted after 12 sessions can be resumed and the dataset grows.

### Dataset schema

The dataset is built from spans, not from in-memory state. For every seed run
the script fetches `GET /api/v1/private/spans?project_name=...&trace_id=...`
and takes:

- every `quiz.grade_answer` span: `input` = `{question, expected, student_answer}`,
  `output` = the grader JSON `{correct, score, feedback, missing_concept}`
- the `quiz.run` span input for the topic
- the `explainer` node's final AI message (last `type == "ai"` message without
  `tool_calls`), truncated to 1500 characters

One dataset item per graded question (3 per session):

```json
{
  "input": {
    "goal": "Learn Python closures and decorators from scratch",
    "topic": "Python Basics",
    "question": "Explain why `x = 'local'` inside inner() ...?",
    "student_answer": "The assignment creates a new local variable ...",
    "expected_answer": "In Python, variables are scoped ...",
    "explanation": "Sure, let's dive into the basics ... (<= 1500 chars)",
    "session_id": "84f52aec", "scenario_id": "s001", "persona": "good", "question_index": 1
  },
  "expected_output": {"grade": "correct", "score": 0.85, "feedback": "Correctly identified ..."},
  "metadata": {"source_span_id": "01a127db-...", "source_trace_id": "01a127db-..."}
}
```

`expected_output.grade` is the grader's `correct` flag as `correct` /
`incorrect`; `score` is its 0-1 score. The item is what the app's own
LLM-as-judge decided during the live session, so an experiment that re-grades
the same answer measures grader consistency.

Opik calls used (SDK 2.2.96): `client.get_or_create_dataset(name, description)`,
`dataset.insert(items, deduplication=True)` (the SDK hashes item content and
skips exact repeats; the script also dedups locally on `(session_id, question)`
against the mirror file), `dataset.get_version_info()` (fields
`version_name`, `items_total`, `items_added`), `dataset.get_items(nb_samples=2)`.
Every insert of new items creates a new dataset version, visible in the UI
under the dataset's Versions tab; the script prints the before/after version:

```
[dataset] 'learning-accelerator-sessions' before: version=v1 items=3
[dataset] 'learning-accelerator-sessions' after:  version=v2 items=9 added=6
[dataset] new version created: v1 -> v2
[dataset] dataset.get_items(nb_samples=2) -> 2 item(s); first keys: ['expected_output', 'id', 'input', 'metadata']
```

A local mirror is written to `data/datasets/learning-accelerator-sessions.jsonl`.

### Seed run results

`data/seed-runs.jsonl`: **30 sessions, 0 errors, 30 topics completed** (10 goals x 3 personas, one topic each).

| persona | sessions | mean quiz score | min | max | mean session seconds | median session seconds |
|---|---:|---:|---:|---:|---:|---:|
| good | 10 | 0.815 | 0.767 | 0.850 | 407.8 | 348.9 |
| average | 10 | 0.810 | 0.717 | 0.850 | 346.8 | 213.8 |
| weak | 10 | 0.817 | 0.700 | 0.850 | 327.3 | 248.7 |
| all | 30 | 0.814 | 0.700 | 0.850 | 360.6 | 265.2 |

Two things to read before quoting these numbers:

- **The personas barely separate on quiz score** (good 0.815, average 0.810, weak 0.817). Per-session scores top out at 0.85 in this run and the weak persona is no lower than the good one, so the score is a ceiling-limited signal here (the student and the grader are the same 7B model); see [LIMITATIONS.md](../LIMITATIONS.md).
- **Session time is not comparable across the run.** The first 22 sessions shared Ollama with other jobs (mean 463.2 s, median 336.4 s); the last 8, run alone with `--resume`, took mean 78.5 s, median 79.7 s. The last 8 are also the first seed sessions that ran the A2A second opinion (all 8 reached the quiz service, `a2a_reachable` = 1.0 on each, `grader_agreement` 1.0 on 7 and 0.967 on one).

Dataset `learning-accelerator-sessions` (from `GET /api/v1/private/datasets/{id}/versions`):

| version | hash | items total | items added | created (UTC) |
|---|---|---:|---:|---|
| v1 | `5f3ca124` | 3 | 3 | 2026-10-10T22:09:18 |
| v2 | `e77f885b` | 9 | 6 | 2026-10-10T22:20:15 |
| v3 | `005388f4` | 12 | 3 | 2026-10-10T22:44:54 |
| v4 | `38194cd7` | 39 | 27 | 2026-10-11T00:11:36 |
| v5 | `35075fd7` | 84 | 45 | 2026-10-11T04:47:59 |

The final dataset has **84 items** from 30 sessions, not 30 x 3 = 90: s019, s020 and s027 each have one item: the quiz generator could not parse its question list and used its one-question fallback ("In your own words, explain the key concept of ...", `app/agents/quiz_generator.py`), so those sessions contribute one item instead of three. Local mirror: `data/datasets/learning-accelerator-sessions.jsonl`.

## 2. Metrics (`evals/metrics.py`)

All metrics subclass `opik.evaluation.metrics.BaseMetric` and return a
`ScoreResult(name, value, reason, metadata)`. `evaluate()` calls
`metric.score(**{**dataset_item, **task_output})`, so a metric declares the
keys it wants and swallows the rest with `**ignored_kwargs`.

| Metric | Type | score() arguments | Value |
|---|---|---|---|
| `GradeAgreement` | custom | `grade` (re-grade dict/JSON), `expected_output` | 1.0 if the re-grade's bucket (`correct`/`incorrect`, from the `correct` flag, else score >= 0.5) equals the stored grade; `metadata.score_delta` = abs score difference |
| `FormatCompliance` | custom | `grade` (or `output`) | 1.0 if the grader JSON has `correct: bool`, `score: number in [0,1]`, `feedback: str`, `missing_concept: str`; reason lists what is missing |
| `ExplanationGrounded` | custom, heuristic | `explanation` (or `output`) | fraction of explanation sentences (code blocks dropped) whose content words overlap the vocabulary of `study_materials/sample_notes/*.md` by >= 30 % |
| `grade_is_json` | `IsJson` via `Keyed` | `grade_json` | built-in |
| `grade_score_regex` | `RegexMatch` via `Keyed` | `grade_json` | `"score": <0..1>` present |
| `grade_mentions_missing_concept` | `Contains` via `Keyed` | `grade_json` | built-in |
| `feedback_similarity` | `LevenshteinRatio` via `Keyed` | `feedback`, `stored_feedback` | similarity of the new feedback sentence to the one recorded in the session |
| `feedback_hallucination` | `Hallucination` judge via `Keyed` | `question`, `feedback`, `context` | Opik's judge: 0.0 = faithful to the context (reference + student answer), 1.0 = unfaithful |
| `feedback_relevance` | `AnswerRelevance` judge via `Keyed` | `question`, `feedback`, `context` | Opik's judge: 1.0 = the feedback addresses the question |
| `trajectory_judge()` | `TrajectoryAccuracy` via `Keyed` | `goal`, `trajectory`, `final_result` | provided, not used in the experiment: dataset items are single Q&A pairs, not agent trajectories |

`Keyed(inner, {inner_arg: source_key})` is the wrapper that lets a built-in
metric read its arguments from the task's output keys and renames the score.

Judges are configured with the LiteLLM string from `.env`
(`JUDGE_MODEL=ollama/qwen2.5:7b`). The judge classes accept either a model
string (`Hallucination(model="ollama/qwen2.5:7b")`) or an
`opik.evaluation.models.LiteLLMChatModel`; `evals.metrics.judge_model()`
builds the latter with `timeout=JUDGE_TIMEOUT_S` (default 300 s) because the
SDK's default read timeout is 60 s and a busy local Ollama exceeds it (one
`feedback_relevance` call failed with `litellm.Timeout ... 60.0 seconds`
while the seed run and the online rules shared the GPU).

Other judge classes that exist in this SDK version and take `model=`: `GEval`,
`Usefulness`, `Moderation`, `ContextPrecision`, `ContextRecall`,
`StructuredOutputCompliance`, `AgentTaskCompletionJudge`,
`AgentToolCorrectnessJudge`, `TrajectoryAccuracy`, the `Conversation*`
thread metrics and the `*BiasJudge` family.

`make test` runs `tests/test_metrics.py` (17 tests, no LLM) for the custom
metrics and the `Keyed` wrapper.

## 3. Offline experiments (`make experiment`)

`evals/run_experiment.py` calls

```python
evaluate(dataset=dataset, task=task, scoring_metrics=all_metrics(), experiment_name=name,
         project_name="silent-drift-lab", experiment_config={...}, nb_samples=20, task_threads=1)
```

The task imports the app's grader, `app/agents/quiz_generator.grade_answer`,
re-grades `input.student_answer` against `input.expected_answer`, and returns
the new grader JSON plus flat keys for the heuristics and judges. The second
experiment swaps the grader model (`quiz_generator.MODEL_NAME = "qwen2.5:3b"`;
`--temperature` additionally wraps the `ChatOllama` class because the grading
temperature is hard-coded at 0.1 inside `grade_answer`). Both experiments run
on the same dataset version, so the Opik UI compares them item by item
(Experiments -> select both -> Compare). `nb_samples` is capped at 20 to bound
judge calls (2 judge calls per item, each 20-90 s on a shared 7B model).

Flags: `--samples N`, `--variant <ollama model>|none`, `--temperature T`, `--no-judges`.
Results are appended to `data/experiments.jsonl` and the experiment URL is printed.

### Experiment results

Run with `make experiment` on dataset version `v5` (84 items), `nb_samples` = 20 (the first 20 items of the dataset), `task_threads` = 1, judge `ollama/qwen2.5:7b`. Both experiments are in Opik under Experiments for dataset `learning-accelerator-sessions`.

| metric | `qwen2.5:7b` grader (baseline) | `qwen2.5:3b` grader (variant) |
|---|---:|---:|
| `explanation_grounded` | 0.687 | 0.687 |
| `feedback_hallucination` | 0.690 | 0.705 |
| `feedback_relevance` | 0.760 | 0.775 |
| `feedback_similarity` | 0.882 | 0.466 |
| `format_compliance` | 1.000 | 1.000 |
| `grade_agreement` | 0.950 | 0.800 |
| `grade_is_json` | 1.000 | 1.000 |
| `grade_mentions_missing_concept` | 1.000 | 1.000 |
| `grade_score_regex` | 1.000 | 1.000 |

- Baseline experiment: `grader-consistency-qwen2.5-7b-20261010-2200`
  http://localhost:5173/opik/default/experiments/01a127dd-03f6-70b4-932e-4091380f3e92/compare?experiments=%5B%2201a12955-c8ad-76da-bdde-719534396c34%22%5D
- Variant experiment: `grader-consistency-qwen2.5-3b-20261010-2205`
  http://localhost:5173/opik/default/experiments/01a127dd-03f6-70b4-932e-4091380f3e92/compare?experiments=%5B%2201a12959-cec3-7728-bfbb-9ff61ee7c752%22%5D

What the numbers say, and what they do not:

- `grade_agreement` is the share of items where the re-grade lands in the same correct/incorrect bucket as the grade stored during the live session: 0.95 for the 7B grader grading itself again (19/20), 0.80 for the 3B grader (16/20). `feedback_similarity` (Levenshtein ratio of the new feedback sentence to the stored one) separates them more clearly: 0.882 against 0.466. `format_compliance`, `grade_is_json`, `grade_score_regex` and `grade_mentions_missing_concept` are 1.000 for both: the 3B model still emits valid grader JSON, which is exactly why a format check alone would not notice the swap.
- The LLM-judge metrics do not separate the two graders: `feedback_hallucination` 0.690 vs 0.705 (0 = faithful) and `feedback_relevance` 0.760 vs 0.775. A 7B judge scoring 7B and 3B output on 20 items is too noisy for a difference of this size, and a mean hallucination score near 0.7 (unfaithful) on grader feedback is more likely a limitation of the 7B judge than of the grader; that was not checked by hand.
- `explanation_grounded` is identical because it reads the stored explanation, not the re-grade.
- Run-to-run noise is real: an earlier run of the identical baseline configuration (`grader-consistency-qwen2.5-7b-20261010-2149`, made before the model-swap fix below) scored `grade_agreement` 1.00 and `feedback_similarity` 0.802. Differences of a few hundredths between two 20-item experiments are noise.

**A bug found and fixed while running this.** `grade_answer()` was changed earlier to read its model from `DRIFT_GRADER_MODEL` / `OLLAMA_MODEL` at call time (the drift hook), but `evals/run_experiment.py` still swapped `quiz_generator.MODEL_NAME`, which the grader no longer reads. The first "3B" experiment of this session (`grader-consistency-qwen2.5-3b-20261010-2153`) therefore graded with the 7B model and showed `grade_agreement` 1.000. `make_task()` now sets `DRIFT_GRADER_MODEL`; the invalid experiment was deleted from Opik and is marked `invalid` in `data/experiments.jsonl`. The earlier two smoke experiments (2 samples, dataset v2) in that file are kept and labelled.

## 4. Judge validation (`evals/validate_judge.py`)

The app's grader is an LLM-as-judge; `validate_judge.py` checks it against
humans. It writes `data/labels/judge_labels.csv` (40 items, spread evenly over
the three personas) with the columns `key, scenario_id, persona, question,
expected, student_answer, judge_score, judge_grade, human_score, note`.
`judge_score` is the dataset's `expected_output.score`; `human_score` is for a
person to fill in on the grader's own rubric (0.0-1.0). Re-running the script
refreshes the rows but keeps any `human_score` already entered.
`data/labels/example_labels.json` pre-fills 10 obvious cases (marked
`note=example: ...`) so the report has data before a full labelling pass.

Agreement over labelled rows: exact-bucket agreement (grader rubric buckets
`wrong < 0.3 <= partial < 0.7 <= correct`), binary agreement at 0.5, mean
absolute error, and Spearman rho (average ranks for ties; no scipy needed).
The report is printed and written to `data/labels/judge_agreement.json`.

### Agreement numbers

`.venv/bin/python evals/validate_judge.py` on dataset v5; `data/labels/judge_agreement.json`:

| measure | value |
|---|---:|
| items in the label template | 40 |
| items with a human score | 10 |
| of which pre-filled examples | 10 |
| exact-bucket agreement (wrong / partial / correct) | 0.700 |
| binary agreement at 0.5 | 0.800 |
| mean absolute error | 0.120 |
| Spearman rho | 0.714 |

**Only 10 of the 40 labels exist, and all 10 are the pre-filled examples** from `data/labels/example_labels.json` (`note=example: ...`), not a labelling pass by a person over the full template. The other 30 rows have an empty `human_score`. With n = 10, one item moves exact-bucket agreement by 10 points, so treat 70% / 80% as a smoke test of the procedure, not as the grader's accuracy. The 10 examples were chosen as obvious cases, which flatters the grader. To get a real figure, fill `human_score` for the remaining rows in `data/labels/judge_labels.csv` and re-run with `--report-only`.

Disagreements (judge bucket differs from human bucket):

| key | persona | judge | human |
|---|---|---:|---:|
| `d6b56b4b67` | good | 0.70 | 0.30 |
| `556b1bceef` | average | 0.70 | 0.40 |
| `9d4f0c20dc` | good | 0.85 | 0.60 |

In all three the grader was more generous than the human. `validate_judge.py` also changed in this pass: the label template now always keeps the items that carry a pre-filled example label. Before, growing the dataset from 39 to 84 items re-sampled the 40 rows and silently dropped all 10 labelled ones.

## 5. Online evaluation rules, feedback definition, alert (`make rules`)

Self-hosted 2.2.96 does not serve `/api/openapi.json` (404), so the payloads in
`scripts/create_rules.py` follow the backend sources
(`apps/opik-backend/src/main/java/com/comet/opik/api/evaluators/*.java`,
`api/Alert*.java`, `api/ProviderApiKey.java`, `api/FeedbackDefinition.java`).
The script is idempotent (`--recreate` deletes and recreates; `--logs` prints
each rule's execution log from `GET /automations/evaluators/{id}/logs`;
`--test-webhook` posts a sample payload through
`POST /alerts/webhooks/tests`).

### What the REST API accepted (all 201/204 on first try)

| Object | Endpoint | Payload shape that worked |
|---|---|---|
| AI provider for rules | `POST /v1/private/llm-provider-key` | `{"provider":"ollama","provider_name":"ollama","api_key":"ollama","base_url":"http://host.docker.internal:11434/v1","configuration":{"models":"custom-llm/ollama/qwen2.5:7b,custom-llm/ollama/qwen2.5:3b"}}` |
| Feedback definition | `POST /v1/private/feedback-definitions` | `{"name":"quiz_score","type":"numerical","details":{"min":0,"max":1}}` |
| Rule (a) | `POST /v1/private/automations/evaluators/` | `type: "llm_as_judge"`, `name: "answer-relevance"`, `sampling_rate: 0.5`, `code.model.name: "custom-llm/ollama/qwen2.5:7b"`, `code.messages: [{"role":"USER","content": "...{{question}}...{{answer}}..."}]`, `code.variables: {"question":"output.quiz_results[0].questions[0].question", ...}`, `code.schema: [{"name":"answer_relevance","type":"DOUBLE","description":...}]`, `filters: [{"field":"output","operator":"contains","value":"user_answer"}]` |
| Rule (b) | same | `type: "user_defined_metric_python"`, `name: "format-compliance"`, `sampling_rate: 1.0`, `code.metric: <python source of a BaseMetric subclass>`, `code.arguments: {"output":"output"}`, same filter |
| Rule (c) | same | `type: "trace_thread_llm_as_judge"`, `name: "thread-coherence"`, `sampling_rate: 1.0`, `code.model`, `code.messages` with `{{context}}`, `code.schema` (no `variables`) |
| Alert | `POST /v1/private/alerts` | `{"name":"quiz-score-low","alert_type":"general","project_id":<id>,"webhook":{"name":"local-alert-sink","url":"http://host.docker.internal:9009/alert"},"triggers":[{"event_type":"trace:feedback_score","trigger_configs":[{"type":"threshold:feedback_score","config_value":{"name":"quiz_score","operator":"<","threshold":"0.5","window":"900"}}]}]}` |

Details worth knowing:

- Ollama is a *named custom provider*. The model string in a rule must be
  `custom-llm/<provider_name>/<model>`; the backend matches it against the
  comma-separated `configuration.models` of the provider key and calls
  `<base_url>/chat/completions` (OpenAI-compatible, hence the `/v1` suffix).
  Opik runs in Docker, so the base URL is `host.docker.internal`, not
  `localhost`. `api_key` is not validated for Ollama but must not be blank.
- Rule variables are JsonPath into the trace `input.` / `output.` /
  `metadata.` sections; indexed access (`[0]`) is allowed, recursive descent
  (`..`) and filter predicates (`[?(`) are rejected with 400. A bare `output`
  passes the whole section. Values are always handed over as strings
  (containers are JSON-serialised), so the Python metric does `json.loads`.
- Python rules run in the `opik-python-backend` container; the code must be a
  self-contained `BaseMetric` subclass (no project imports). Scores named by
  the metric's `self.name`.
- Filters: both trace rules filter `output contains "user_answer"` so the
  interrupt-only first trace of each session is skipped (`... was skipped for
  rule ... as it does not match the configured filters` in the logs) instead
  of producing a path error.
- `trigger_scope` defaults to `production`: rules do not fire on
  `evaluate()` traces (the `format_compliance` score on experiment traces
  comes from the offline metric of the same name).
- The alert's `threshold:feedback_score` config is evaluated by
  `MetricsAlertJob` every `WEBHOOK_METRICS_FIXED_DELAY` (default 300 s, first
  run 300 s after backend start): it computes the **average** of the named
  score over the last `window` seconds across the alert's projects and
  compares with `operator`. It is not per-trace. Webhook deliveries are
  debounced (60 s window) and retried 3 times.

### What was observed

Rules created by `make rules` on 2026-10-10 (all three exist; the script is idempotent and reports `exists` on re-run):

| rule | type | sampling | what it scores | state at the end of the 2026-10-11 pass |
|---|---|---:|---|---|
| `format-compliance` | python metric | 1.0 | grader JSON has all required keys | enabled |
| `answer-relevance` | LLM judge (`qwen2.5:7b`) | 0.5 | is the answer relevant to the question | re-enabled at the very end; not re-verified |
| `thread-coherence` | thread LLM judge (`qwen2.5:7b`) | 1.0 | coherence of a whole session | re-enabled at the very end; not re-verified |

What each one scored, counted from the `feedback_scores` on the project's traces and threads (source `online_scoring`):

| score | records | mean | first / last (UTC) |
|---|---:|---:|---|
| `format_compliance` (trace) | 100 | 1.00 | 2026-10-10 22:16 / 2026-10-12 03:09 |
| `thread_coherence` (thread) | 9 | 1.00 | 2026-10-10 22:41 / 2026-10-10 23:36 |
| `answer_relevance` (trace) | 0 | n/a | never scored |

- `format_compliance` scored every trace that passed the rule's filter (the second trace of each session) since the rule was created, and every score is 1.0: the grader JSON never lost a key in live traffic, which matches the offline `format_compliance` of 1.000 in section 3.
- `thread_coherence` scored 9 threads, all 1.0. A 7B judge giving a perfect score on every thread says more about the judge than about the sessions.
- `answer_relevance` scored nothing. Its execution log shows `request timed out` errors from 2026-10-10 23:14 UTC, when seed sessions were sharing Ollama with the judge. `thread-coherence` logged the same error from 23:32 UTC.
- **Why the two judge rules were off.** They were switched off (`scripts/create_rules.py --disable answer-relevance thread-coherence`) after those timeouts, so that seed, drift and demo sessions would not compete with judge calls for the one GPU. The moment of the switch is not recorded; the rule logs show "skipped ... as the rule is disabled" entries from 2026-10-11 06:48 UTC on, and no score from either rule exists after 23:36 UTC on 2026-10-10. Everything in `reports/` and every number in this file except the 9 `thread_coherence` scores was therefore produced with only `format-compliance` active. At the very end of the 2026-10-11 pass, with the GPU idle, both were re-enabled (`--enable answer-relevance thread-coherence`, HTTP 204 each); no session was run afterwards, so whether they score without timing out is untested. Expect judge timeouts and slower sessions whenever a session and a judge call overlap; `--disable` switches them off again.

The alert `quiz-score-low` (`trace:feedback_score`, `quiz_score < 0.5`, average over a 900 s window, webhook `http://host.docker.internal:9009/alert`) is enabled. `data/alerts.jsonl` holds **3 received payloads**:

| received (PDT) | source | `quiz_score` average | threshold | window |
|---|---|---:|---:|---:|
| 2026-10-10 15:15:10 | `--test-webhook` sample payload (project names `Demo Project`, workspace `demo_workspace_name`) | 0.7500 | 0.5 | 3600 s |
| 2026-10-10 15:51:31 | real alert, project `silent-drift-lab` | 0.4833 | 0.5000 | 900 s |
| 2026-10-10 15:56:30 | real alert, same condition | 0.4833 | 0.5000 | 900 s |

Only the last two are real alerts; the first is the sample the test endpoint sends. The two real ones are five minutes apart, which is the `MetricsAlertJob` period (300 s). I did not trace the 0.4833 average back to specific sessions. The webhook sink (`scripts/alert_webhook.py`) is not left running; start it before `make rules` if you want to receive alerts.


### Limitations found in self-hosted 2.2.96

Observed while building this layer (Opik 2.2.96, Compose install, Docker Desktop at 7.75 GiB):

- **The backend container was killed for lack of memory, twice.** `opik-opik-src-backend-1` exited with code 137 and `OOMKilled: true`: once overnight (found stopped on 2026-10-11 after 19 hours) and once at 2026-10-12 03:21 UTC while a session was running and I was reading rule logs with `size=5000`. Every request then returned `502 Bad Gateway` from the frontend's nginx, and traces logged during the outage are lost. `docker start opik-opik-src-backend-1` brought it back in about 20 seconds, with data intact. Other containers from unrelated projects were running in the same Docker VM at the time. This is a measured observation, not a diagnosis: 8 GB is tight for Opik plus a busy session, and ClickHouse alone held 2.2 to 2.8 GiB. Raise Docker's memory limit, and do not run the experiment judges or large API reads while sessions are in flight.
- The rule execution-log endpoint (`GET /automations/evaluators/{id}/logs`) returned repeated entries when paged with `page=`; only `size=` behaves, and the last 20 entries are all the script prints. Counting log lines overstates what happened; count `feedback_scores` instead (above).
- 2.2.96 does not serve `/api/openapi.json` (404), so the payloads follow the backend sources.
- Rules score only traces created after the rule exists, and by default only `production`-scope traces, not `evaluate()` traces.
- The alert threshold is an average over the window, evaluated every 300 s, not a per-trace condition; webhook delivery is debounced (60 s) and retried.
- LLM-judge rules on a local 7B model time out whenever the GPU is shared; the Python-metric rule has no such dependency.


## 6. Tests

- `make test` -> `tests/test_metrics.py` (`-m unit`): the custom metrics,
  `grade_bucket`, `Keyed`, the heuristics; no LLM, no network. 17 tests.
- `make evals-test` -> `tests/test_evals_gate.py` (`-m eval`): `evaluate()`
  with `nb_samples=5`, `task_threads=1`, metrics `GradeAgreement` and
  `FormatCompliance`, no judges; asserts mean `grade_agreement >= 0.6` and
  `format_compliance == 1.0`. Skips (does not fail) when Opik or the dataset is
  unavailable. Markers are registered in `tests/conftest.py`.

Two runs of `make evals-test` on 2026-10-11, both passing (`1 passed, 37 deselected`):

| run | wall time | result |
|---|---:|---|
| first | 227.83 s | passed |
| second (after the machine had sped up again; the cause of the difference was not investigated), `-s` to capture the gate's own line (`data/evals-gate-output.txt`) | 46.76 s | passed |

The gate's output line from the second run:

```
evals gate: grade_agreement mean=1.00 format_compliance mean=1.00 -> http://localhost:5173/opik/default/experiments/01a127dd-03f6-70b4-932e-4091380f3e92/compare?experiments=%5B%2201a12e2f-c29d-7bc8-b609-b3d18e4775cf%22%5D
```

Five dataset items, dataset version `v5`, thresholds `grade_agreement >= 0.6` and `format_compliance == 1.0`. The first run's experiment (`evals-gate`, id `01a12e1a-c76a-7e6d-9d0d-78ec3fe7952a`) also scored 1.0 on both. With five items the gate is a tripwire for a broken grader, not a quality measurement; the 20-item experiments in section 3 are the better evidence. `make test` (`-m "not eval"`) gave `37 passed, 1 deselected` on the same day, and the same 37 pass in a fresh virtual environment built from `requirements.txt`.

