# silent-drift-lab

An open-source lab that shows how a multi-agent LLM system gets worse while its error rate stays at zero, and the observability stack (Opik + OpenTelemetry) plus the drift monitor that catches it.

Built on the Learning Accelerator from [sandeepmb/freecodecamp-multi-agent-ai-system](https://github.com/sandeepmb/freecodecamp-multi-agent-ai-system), the multi-agent study app from the freeCodeCamp handbook.

[![CI](https://github.com/sandeepmb/silent-drift-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/sandeepmb/silent-drift-lab/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/github/license/sandeepmb/silent-drift-lab)](LICENSE)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](.github/workflows/ci.yml)

Opik is an open-source LLM observability platform from Comet; this lab runs it self-hosted. OpenTelemetry is the vendor-neutral standard for traces. Everything runs locally on Ollama (a local model runner), so there are no API keys and no signup.

```bash
git clone https://github.com/sandeepmb/silent-drift-lab && cd silent-drift-lab
make setup                # .venv with pinned deps; needs uv, Python 3.11, Docker Desktop with 12 GB (8 GB works but is tight)
ollama pull qwen2.5:7b    # the one model: app and judge
make opik-up              # self-hosted Opik at http://localhost:5173 (first run pulls about 2 GB of images)
make services             # the quiz service on :9001 (a separate process, so traces cross a boundary)
make demo                 # healthy session, then drift injected: errors stay zero, grounding in the notes drops
```

**Works with what you already use.** `TELEMETRY_MODE=otel make run` sends the same spans through an OpenTelemetry Collector (`make otel-up`), so any backend that accepts OTLP (OpenTelemetry's wire protocol) can receive them.

**4.6 GiB idle** is what the full self-hosted Opik stack cost on the day of measurement (a fresh stack is smaller: ClickHouse grows with stored traces). MEASURED: 8 containers, about 640 stored traces, Apple Silicon, Docker Desktop at 8 GB, via `make footprint` ([how to reproduce](docs/reproducing.md)). A one-topic traced session took a median of 86 seconds on `qwen2.5:7b` on the same machine (n = 30, range 59 to 302 s), and `make demo`, which runs two of them, took 385 seconds in its last run.

**Drift result:** the monitor flagged all 6 injected drifts (2 page, 3 ticket, 1 annotate) over a 30-session healthy baseline and 3 sessions per scenario; a static `quiz_score < 0.6` rule flagged 0 of 6. The monitor is not free of false alarms: on sliding windows over the healthy baseline it paged 2 times in 28, against 1 page in 30 sessions for the static rule. Latency rose with host load before any injection, so it could not be used. MEASURED, n = 3 per scenario, a synthetic student: the shape of the evidence, not a benchmark ([docs/drift-lab.md](docs/drift-lab.md)).

![Drift figure: system signals on top, quality signals below, injection points marked](docs/img/drift-figure-light.png)

Read [LIMITATIONS.md](LIMITATIONS.md) before you decide this applies to your system. The student is synthetic, the sample is small, and the local model is noisy. The limits are the point of the lab, not a footnote.

---

## Architecture

![architecture](docs/img/architecture.svg)

One study session, end to end:

1. `main.py` starts a LangGraph session; the session id is both the checkpoint thread and the Opik thread.
2. The Curriculum Planner proposes a plan and the graph pauses for human approval (`--auto-approve` in lab runs).
3. The Explainer reads your notes through MCP file and memory tools in a loop until it is satisfied.
4. The Quiz Generator asks questions over an A2A hop to the service on `:9001`; the student (you, or a simulated persona) answers; a grading call scores each answer.
5. The Progress Coach decides whether to re-teach or move on. Every node, model call and tool call lands in Opik as one trace; the quiz score is attached to that trace as a feedback score.

## What it measures

Observability for an agent system has three layers. Most teams have the first two.

| Layer | Examples | Who produces it here |
|---|---|---|
| Infrastructure | CPU, memory, GPU, queue depth | Docker and Ollama |
| Application | Latency, error rate, tokens, cost per request | The traces themselves; Opik's project dashboards |
| Behavioural quality | Quiz score, grader agreement, groundedness of explanations, regeneration and abandonment, tool-call failures after generation | Feedback scores from code, online evaluation rules (`make rules`), the drift monitor (`make drift-demo`) |

The drift lab injects six kinds of degradation, one at a time, and asks which layer notices. Each injection is selected with `DRIFT_SCENARIO`. The two right-hand columns are expectations from the drift taxonomy; what the monitor measured goes in the benchmarks table once `make drift-demo` has run.

| Drift | What changes | `DRIFT_SCENARIO` | Layers 1 and 2 (expected) | Layer 3 (expected) |
|---|---|---|---|---|
| Model | The model is swapped for a smaller one; nobody deployed anything | `model_swap` | Latency may move, in the good direction | Quiz score and groundedness fall |
| Prompt and context | The system prompt is mutated; a template variable renders empty | `prompt_mutation` | Nothing | Score falls for one segment |
| Retrieval | The notes go stale; the explainer is handed worse material | `stale_notes` | Nothing | Groundedness falls while the model is fine |
| Tool and dependency | The MCP tool returns an empty or malformed result | `tool_fault` | Nothing; the model talks around it | Tool-call failures after generation, then score |
| Behavioural | The explainer gets verbose and hedges | `verbosity` | Token counts shift, inside normal variance | Output length distribution shifts, format compliance drops |
| Input and traffic | Goals arrive from a new segment (SQL) that the notes do not cover | `traffic_shift` | The overall average stays fine | One segment is on fire |

The monitor keeps a rolling baseline per segment and only raises when two behavioural signals move together. One signal moving is noise on a 7B model; that is a measured fact about this lab, not a design preference.

## Quickstart

After the install block above:

```bash
make run          # one interactive session, traced; open http://localhost:5173
make seed         # 30 scripted sessions (scenarios/sessions.jsonl) become an Opik dataset built from traces
make experiment   # offline evaluation over that dataset with the local judge
make rules        # online evaluation rules on sampled live traffic, plus one alert to a local webhook
make drift-demo   # inject each drift, run the monitor, write reports/drift-figure.png
```

`make test` runs the unit tests without Ollama or Opik. `make evals-test` runs the evaluation gate and needs both.

## Configuration

Five settings matter. The full list, with comments, is in [.env.example](.env.example); `make setup` copies it to `.env`.

| Variable | Default | What it does |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:7b` | The model the four agents and the simulated student use |
| `JUDGE_MODEL` | `ollama/qwen2.5:7b` | The model Opik's evaluators use as judge (a LiteLLM model string) |
| `TELEMETRY_MODE` | `native` | `native` (Opik SDK callback), `otel` (OpenTelemetry through the Collector), or `off` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | Where the `otel` track sends spans; the Collector forwards them to Opik |
| `DRIFT_SCENARIO` | `none` | Which degradation to inject; the lab sets this for you |

## Benchmarks

Every number here names its standing. MEASURED means `make <target>` produced it on the hardware stated. CALIBRATED SYNTHETIC means the data is generated, with the generator's settings recorded. ILLUSTRATIVE means it shows the shape of a result and should not be quoted. Commands for each row are in [docs/reproducing.md](docs/reproducing.md).

| Figure | Value | Standing | Produced by |
|---|---|---|---|
| Opik stack, idle memory | 4.60 GiB across 8 containers (ClickHouse 2,477 MiB, backend 910 MiB) | MEASURED (Apple Silicon, Docker Desktop 8 GB, about 640 stored traces) | `make footprint` → `reports/footprint.md` |
| One-topic traced session | median 86 s, mean 111 s, range 59 s to 302 s | MEASURED (`qwen2.5:7b`, the 30 drift-lab baseline sessions; the slowest ran after a host slowdown) | `data/baseline-runs.jsonl` (`elapsed_s`), from `make drift-demo` |
| Seed dataset | 30 sessions: 10 goals × 3 personas, 84 graded items (dataset v5) | CALIBRATED SYNTHETIC (simulated student, personas in `app/student.py`) | `make seed` |
| Drift detection | 6 of 6 flagged (2 page, 3 ticket, 1 annotate); static quiz-score rule 0 of 6; false alarms on the healthy baseline: monitor 2 pages in 28 windows, static rule 1 in 30 sessions | MEASURED, n = 3 per scenario, 30-session baseline | `make drift-demo` → `reports/drift-report.md` |
| Judge agreement with hand labels | 10 labelled items (the pre-filled examples; 30 more rows of the 40-row template are unlabelled): exact-bucket 0.70, binary 0.80, mean abs. error 0.12, Spearman 0.71 | MEASURED, n = 10, obvious cases only: a smoke test of the procedure, not an accuracy claim | `.venv/bin/python evals/validate_judge.py` → `data/labels/judge_agreement.json` |
| `make demo` verdict | one healthy run against one drifted run | ILLUSTRATIVE (n = 1 each; an anecdote by design) | `make demo` |

The figure at the top of this README is `reports/drift-figure-light.png` (dark variant: `reports/drift-figure.png`); `make drift-demo` regenerates both. Top panel: what a dashboard sees. Bottom panel: what the monitor sees. The quiz score staying mostly flat while grounding moves is the honest result on this system, because the student is synthetic.

## Limitations and non-goals

- The student is a second model call with a persona, not a person. "User" signals are simulated.
- n is small everywhere: 30 baseline sessions in two topic families, 3 sessions per injection, a 7B model with run-to-run variance that is larger than some of the effects you are looking for.
- The Compose install of Opik is for a laptop. Comet documents it as not for production.
- Opik's OpenTelemetry endpoint accepts traces over OTLP/HTTP only; the `otel` track has no metrics or logs.
- Ollama reports no cost, so cost is a shadow price table, not a bill.
- The CrewAI study buddy from the upstream project is off by default because of an `opentelemetry-sdk` version conflict ([docs/optional-crewai.md](docs/optional-crewai.md)).
- The attribute mapping between OpenTelemetry GenAI conventions and Opik changes with Opik releases, which are frequent.

The long form, with what each limit means for your system, is in [LIMITATIONS.md](LIMITATIONS.md).

## Prior art and acknowledgments

- **Opik**, by Comet, is the observability platform the lab runs on: tracing, threads, datasets, experiments, online rules and alerts all come from it.
- **OpenTelemetry** and its GenAI semantic conventions define the span attributes the `otel` track emits.
- **LangGraph**, by LangChain, is the agent framework; the Opik LangChain callback is what makes the native track one line.
- **The Learning Accelerator** by Sandeep Bharadwaj Mannapur ([sandeepmb/freecodecamp-multi-agent-ai-system](https://github.com/sandeepmb/freecodecamp-multi-agent-ai-system), MIT) is the application under observation; the four agents, the MCP servers and the A2A service are vendored from it with their licence kept ([NOTICE](NOTICE)).
- The lab is the companion to a freeCodeCamp handbook. <!-- TODO(links): add the handbook URL once published -->

Pinned versions, the same ones the handbook was written against: Opik 2.2.96 (server and SDK), LangGraph 1.1.0, langchain-core 1.0.0, langchain-ollama 1.0.0, `qwen2.5:7b`, Python 3.11. Opik releases often; if the UI looks different on a newer version, that is why.

## Docs map

| Document | What it is for |
|---|---|
| [LIMITATIONS.md](LIMITATIONS.md) | Where the lab's evidence stops and what that means for you |
| [docs/reproducing.md](docs/reproducing.md) | Every figure and number, and the command that produces it |
| [docs/optional-crewai.md](docs/optional-crewai.md) | Re-enabling the CrewAI study buddy in a separate environment |
| [docs/good-first-issues.md](docs/good-first-issues.md) | Scoped first contributions |
| [ROADMAP.md](ROADMAP.md) | The next six months, kept modest |
| [CHANGELOG.md](CHANGELOG.md) | What changed, by version |
| [.env.example](.env.example) | Every setting, with a comment each |

## Contributing, security, license

- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Commits carry a DCO sign-off (`git commit -s`). The best first contribution is a new drift scenario; there is an [issue template](.github/ISSUE_TEMPLATE/drift_scenario.md) for it.
- Security: report privately through [GitHub's vulnerability form](https://github.com/sandeepmb/silent-drift-lab/security/advisories/new); details in [SECURITY.md](SECURITY.md).
- License: new code is [Apache-2.0](LICENSE). Vendored Learning Accelerator code keeps its [MIT licence](LICENSE.upstream-learning-accelerator); see [NOTICE](NOTICE).
- Trademarks: Opik, Comet, OpenTelemetry, LangGraph, CrewAI and Ollama are the names of their respective projects and owners. This lab is not affiliated with or endorsed by any of them.
