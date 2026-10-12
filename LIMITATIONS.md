# Limitations

This file says where the lab's evidence stops. Read it before you take a number from the README to a meeting. Each item says what the limit is, why it is there, and what it means if you try to apply the pattern to your own system.

## 1. The student is synthetic

There are no real users. Quiz answers come from a second model call playing a persona (`good`, `average`, `weak`, defined in `app/student.py`). Signals that a product would get from people, such as regeneration, edits and abandonment, are either simulated or absent.

What this means for you: the three-layer model and the monitor transfer; the specific thresholds do not. On a real product, the behavioural layer is fed by real events, and the first thing to do is measure how noisy those are before setting any threshold.

## 2. n is small

The seed set is 30 sessions: 10 goals, 3 personas, one segment (`python-fundamentals`). The drift lab runs its baseline over those 30 and then injects each scenario a handful of times. That is enough to show a shape and not enough to claim a detection rate with a confidence interval worth printing.

<!-- TODO(n): once make drift-demo is stable, record the exact run counts per scenario here and in docs/reproducing.md. -->

What this means for you: treat the drift figure as a demonstration that the method separates signal from dashboard noise on this system, not as a benchmark of the method.

## 3. Local-model variance is large

`qwen2.5:7b` at temperature 0.7 for the student and the generators produces run-to-run differences in quiz score that can be as large as the effect of a mild injection. The monitor's two-signal corroboration exists because of this: one signal moving on its own is routinely noise here.

What this means for you: the judge and the subject are the same model. On a system with a stronger judge, the agreement numbers will differ; on a system with a weaker one, the monitor will need a wider baseline window.

## 4. The Opik Compose stack is a laptop install

`make opik-up` uses Opik's own `opik.sh`, which brings up eight containers with Docker Compose and no authentication. Comet documents the Compose setup as not meant for production and points to its Helm chart for that. The footprint number in the README (3.4 GiB idle, Apple Silicon, Docker Desktop at 8 GB) describes this install only.

What this means for you: do not expose the ports. For anything shared, use the Helm chart or Opik Cloud; the application code does not change, only `OPIK_URL_OVERRIDE` and an API key.

## 5. The OpenTelemetry track is HTTP-only and traces-only

Opik's OpenTelemetry endpoint accepts traces over OTLP/HTTP. The Collector in `collector/` listens on both gRPC (`:4317`) and HTTP (`:4318`) so your own instrumentation can use either on the way in, but only traces have anywhere to go on the Opik side. There are no metrics or logs on this track.

The mapping between `gen_ai.*` attributes and what the Opik UI shows is documented by Comet and changes with Opik releases, which are frequent. The lab pins Opik 2.2.96; on a newer version, expect some attributes to land in different fields.

What this means for you: the `otel` track is the portable one, and portability costs you some fidelity today (graph view, some usage fields). Measure the gap on your version rather than trusting this one.

## 6. There is no cost, only a shadow price

Ollama reports no cost, and the Opik SDK therefore records none. Where the lab shows cost it uses a shadow price table that assigns a per-token price so that the cost dashboard has something to be flat about during the drift demo. The numbers are illustrative.

<!-- TODO(shadow-price): link the table and its source once evals/ lands. -->

## 7. The CrewAI study buddy is off by default

The upstream Learning Accelerator included a CrewAI agent called over A2A when a student scored poorly. CrewAI 1.13 pins `opentelemetry-sdk` below 1.35; Opik 2.2 needs a newer one. The two cannot share a virtual environment, so the buddy is disabled (`USE_STUDY_BUDDY=false`) and the progress coach skips it. [docs/optional-crewai.md](docs/optional-crewai.md) shows how to run it in a second environment.

What this means for you: the cross-framework hop that the upstream project demonstrated is not traced here. The cross-process story is carried by the A2A quiz service instead.

## 8. Mapping rules and SDK surfaces change often

Opik releases almost daily. This lab is pinned to 2.2.96 for the server and the SDK, and the handbook was written against the same version. Online rule definitions, the dataset API and the OpenTelemetry attribute mapping are the surfaces most likely to move.

What this means for you: `make opik-up` pins the version on purpose. Bump `OPIK_VERSION` in the Makefile deliberately and re-run `make evals-test` after you do.

## 9. One machine, one run

Every MEASURED number was taken on one Apple Silicon laptop. Timing figures (2.7 minutes per one-topic session) depend on the GPU, the memory Docker has, and what else is running. The `make footprint` target writes your own numbers to `reports/footprint.md` so that you do not have to trust these.

## Non-goals

- A production monitoring product. The monitor in `drift/` is a few hundred lines meant to be read and copied, not deployed.
- A benchmark of observability platforms. The lab runs on Opik and emits OpenTelemetry; it does not rank anything.
- A tutorial on LangGraph, MCP or A2A. The agents are the vehicle; the upstream project and its handbook cover them.
