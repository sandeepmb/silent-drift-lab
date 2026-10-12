# Limitations

This file says where the lab's evidence stops. Read it before you take a number from the README to a meeting. Each item says what the limit is, why it is there, and what it means if you try to apply the pattern to your own system.

## 1. The student is synthetic

There are no real users. Quiz answers come from a second model call playing a persona (`good`, `average`, `weak`, defined in `app/student.py`). Signals that a product would get from people, such as regeneration, edits and abandonment, are either simulated or absent.

What this means for you: the three-layer model and the monitor transfer; the specific thresholds do not. On a real product, the behavioural layer is fed by real events, and the first thing to do is measure how noisy those are before setting any threshold.

## 2. n is small

There are two sets of 30 sessions: the seed set that feeds the evaluation dataset (10 goals, 3 personas, one segment, `python-fundamentals`) and the drift lab's own baseline of 30 healthy sessions (same goals and personas, split into two topic families: 21 `py-functions`, 9 `py-iteration`). The drift lab ran each of its six scenarios **3 times**, 18 injected sessions in all. Three sessions per scenario is enough to show a shape and not enough to claim a detection rate with a confidence interval worth printing: every verdict is a median of three sessions, and one odd session moves it.

The judge-agreement figures rest on 10 hand labels, and all 10 are pre-filled example rows of obvious cases; the other 30 rows of the label template are empty. The two experiments compare a 7B and a 3B grader on 20 dataset items each.

The analysis uses only the sessions listed in `data/baseline-runs.jsonl` and `data/drift-runs.jsonl`. Opik also holds 21 other `drift:*` sessions from earlier attempts (some run while other jobs shared the GPU); they are excluded, and 3 sessions that never reached scoring are dropped.

What this means for you: treat the drift figure as a demonstration that the method separates signal from dashboard noise on this system, not as a benchmark of the method.

## 3. Local-model variance is large

`qwen2.5:7b` (temperature 0.7 for the student, 0.3 for the explainer, 0.4 for question generation, 0.1 for grading) produces run-to-run differences in quiz score that can be as large as the effect of a mild injection. In the lab the student and grader are the same model and personas barely separate: seed quiz scores average 0.815 (good), 0.810 (average) and 0.817 (weak). The monitor's two-signal corroboration exists because of this: one signal moving on its own is routinely noise here.

What this means for you: the judge and the subject are the same model. On a system with a stronger judge, the agreement numbers will differ; on a system with a weaker one, the monitor will need a wider baseline window.

## 4. The Opik Compose stack is a laptop install

`make opik-up` uses Opik's own `opik.sh`, which brings up eight containers with Docker Compose and no authentication. Comet documents the Compose setup as not meant for production and points to its Helm chart for that. The footprint number in the README (4.60 GiB idle with about 640 stored traces, Apple Silicon, Docker Desktop at 8 GB) describes this install only.

In practice 8 GB is tight. On the reference machine the backend container was killed for lack of memory (exit 137, `OOMKilled`) twice in one day, once overnight and once while a session was running and an API read of 5,000 log lines was in flight; every request then returned 502 until `docker start opik-opik-src-backend-1`. Traces logged during an outage are lost. ClickHouse alone held about 2.5 GiB at the end. This is a measured observation, not a diagnosis; give Docker more than 8 GB if you can and do not run experiment judges while sessions are in flight.

What this means for you: do not expose the ports. For anything shared, use the Helm chart or Opik Cloud; the application code does not change, only `OPIK_URL_OVERRIDE` and an API key.

## 5. The OpenTelemetry track is HTTP-only and traces-only

Opik's OpenTelemetry endpoint accepts traces over OTLP/HTTP. The Collector in `collector/` listens on both gRPC (`:4317`) and HTTP (`:4318`) so your own instrumentation can use either on the way in, but only traces have anywhere to go on the Opik side. There are no metrics or logs on this track.

The mapping between `gen_ai.*` attributes and what the Opik UI shows is documented by Comet and changes with Opik releases, which are frequent. The lab pins Opik 2.2.96; on a newer version, expect some attributes to land in different fields.

What this means for you: the `otel` track is the portable one, and portability costs you some fidelity today (graph view, some usage fields). Measure the gap on your version rather than trusting this one.

## 6. There is no cost, only a shadow price

Ollama reports no cost, and the Opik SDK therefore records none. Where the lab shows cost it uses a shadow price table that assigns a per-token price so that the cost dashboard has something to be flat about during the drift demo. The numbers are illustrative.

The table is `DEFAULT_PRICES` in `drift/signals.py` (USD per 1k tokens, input/output: `qwen2.5:7b` 0.00030 / 0.00060, `qwen2.5:3b` 0.00015 / 0.00030). It can be overridden with `DRIFT_PRICE_TABLE`. Because cost is derived from token counts and the model name, it moves when either does: it dropped under `model_swap` because the 3B model is priced at half the 7B rate.

## 7. The CrewAI study buddy is off by default

The upstream Learning Accelerator included a CrewAI agent called over A2A when a student scored poorly. CrewAI 1.13 pins `opentelemetry-sdk` below 1.35; Opik 2.2 needs a newer one. The two cannot share a virtual environment, so the buddy is disabled (`USE_STUDY_BUDDY=false`) and the progress coach skips it. [docs/optional-crewai.md](docs/optional-crewai.md) shows how to run it in a second environment.

What this means for you: the cross-framework hop that the upstream project demonstrated is not traced here. The cross-process story is carried by the A2A quiz service instead.

## 8. Mapping rules and SDK surfaces change often

Opik releases almost daily. This lab is pinned to 2.2.96 for the server and the SDK, and the handbook was written against the same version. Online rule definitions, the dataset API and the OpenTelemetry attribute mapping are the surfaces most likely to move.

What this means for you: `make opik-up` pins the version on purpose. Bump `OPIK_VERSION` in the Makefile deliberately and re-run `make evals-test` after you do.

## 9. One machine, one run

Every MEASURED number was taken on one Apple Silicon laptop. Timing figures (a one-topic session took a median 86 s and a mean 111 s across the 30 baseline sessions, with a range of 59 s to 302 s) depend on the GPU, the memory Docker has, and what else is running. Latency roughly doubled part-way through the baseline and stayed high through the drift sessions; the cause was not isolated, so the lab's latency signal is confounded and the verdicts do not use it. The `make footprint` target writes your own numbers to `reports/footprint.md` so that you do not have to trust these.

## 10. The online judge rules compete with sessions for the GPU

`make rules` creates three rules: `format-compliance` (a Python metric) and two LLM-judge rules (`answer-relevance`, `thread-coherence`). The judge rules logged `request timed out` errors when sessions and judge calls shared the one GPU, so they were switched off for the seed, drift and demo runs and re-enabled only at the very end of the final pass (not re-verified afterwards). `answer-relevance` never produced a score; `thread-coherence` scored 9 threads, all 1.0. Everything in `reports/` was produced with only `format-compliance` active. If you record `make demo` with the judge rules on, expect slower sessions: `scripts/create_rules.py --disable answer-relevance thread-coherence`. Details are in [docs/evals.md](docs/evals.md).

## 11. The monitor pages on healthy data

On the 30 healthy baseline sessions, sliding 3-session windows gave 2 false pages, 2 tickets and 5 annotates in 28 windows. Both pages are the last windows of the baseline: three consecutive sessions for one goal whose first search phrase ("Decorators basics") did not substring-match the notes, so grounding was 0.00 and the explainer read nothing. The baseline segments by topic family, not by goal, and a 3-session window is usually one goal, so one unlucky goal looks like drift. The static rule `quiz_score < 0.6` paged once in 30 sessions, on a session that had crashed. With this n neither rate is a benchmark. Details are in [docs/drift-lab.md](docs/drift-lab.md), "What the monitor got wrong on healthy data".

What this means for you: calibrate the baseline against your own history before you trust a page, segment by whatever makes requests exchangeable (here, the goal), and read a page as "look at this window", not as a verdict.

## Non-goals

- A production monitoring product. The monitor in `drift/` is a few hundred lines meant to be read and copied, not deployed.
- A benchmark of observability platforms. The lab runs on Opik and emits OpenTelemetry; it does not rank anything.
- A tutorial on LangGraph, MCP or A2A. The agents are the vehicle; the upstream project and its handbook cover them.
