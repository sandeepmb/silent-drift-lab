# Evaluation layer

How the Learning Accelerator is evaluated against self-hosted Opik 2.2.96:
a dataset built from traces, offline experiments with a local judge, a
hand-label check of the judge, online rules that score future traces, and an
alert that reaches a local webhook. Everything below was run on 2026-10-10
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

<!-- SEED_RESULTS -->

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

<!-- EXPERIMENT_RESULTS -->

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

<!-- JUDGE_RESULTS -->

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

<!-- RULES_RESULTS -->

### Limitations found in self-hosted 2.2.96

<!-- LIMITATIONS -->

## 6. Tests

- `make test` -> `tests/test_metrics.py` (`-m unit`): the custom metrics,
  `grade_bucket`, `Keyed`, the heuristics; no LLM, no network. 17 tests.
- `make evals-test` -> `tests/test_evals_gate.py` (`-m eval`): `evaluate()`
  with `nb_samples=5`, `task_threads=1`, metrics `GradeAgreement` and
  `FormatCompliance`, no judges; asserts mean `grade_agreement >= 0.6` and
  `format_compliance == 1.0`. Skips (does not fail) when Opik or the dataset is
  unavailable. Markers are registered in `tests/conftest.py`.

<!-- GATE_RESULTS -->
