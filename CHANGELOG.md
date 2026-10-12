# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - unreleased

First public version. Companion to the freeCodeCamp handbook and the MLOps World 2026 talk *Silent Drift*.

### Added

- The Learning Accelerator (four LangGraph agents, MCP tools, an A2A quiz service) vendored from the upstream project under its MIT licence, with Langfuse wiring removed.
- Opik native tracing: `OpikTracer` on the graph, one thread per study session, tags and metadata for model and drift scenario, `@track` on business-logic functions, feedback scores from code attached to every trace (`app/observability/opik_setup.py`, `main.py`).
- OpenTelemetry track: `TELEMETRY_MODE=otel` emits spans with the GenAI semantic conventions to an OpenTelemetry Collector that forwards to Opik over OTLP/HTTP (`app/observability/otel_setup.py`, `collector/`, `make otel-up`).
- A simulated student with three personas so the lab runs unattended (`app/student.py`).
- Thirty scripted sessions and a seeder that turns their traces into an Opik dataset (`scenarios/sessions.jsonl`, `make seed`).
- Offline experiments with heuristic and judge-based metrics on a local Ollama judge (`make experiment`).
- Online evaluation rules on sampled traffic and an alert to a local webhook (`make rules`, `scripts/alert_webhook.py`).
- The drift lab: six injectable degradations, a monitor with per-segment rolling baselines and two-signal corroboration, and the figure it produces (`make drift-demo`, `reports/drift-figure.png`).
- A scripted demo (`make demo`: a healthy session, then the same session with `stale_notes` injected, compared on latency, errors, tokens, quiz score and grounding) and a footprint recorder (`make footprint`).
- A drift-lab baseline of 30 healthy sessions run alone on the GPU (`data/baseline-runs.jsonl`), 3 runs per scenario, a two-panel figure in dark and light variants (`reports/drift-figure*.png`) and a final-numbers report (`reports/final-numbers.md`).
- Lint configuration (`pyproject.toml`, `make lint`, a CI step) for the lab's own code; the vendored `app/` is not linted except `app/observability/` and `app/student.py`.
- Evaluations as tests: a fast offline suite for CI and an `eval`-marked gate for a machine with Ollama.
- Launch documentation: README, LIMITATIONS, reproducing guide, security policy, contributing guide with DCO, issue and PR templates, CI with pinned actions, Dependabot, roadmap.

### Fixed

- The offline experiment's model-swap variant graded with the 7B model because the grader reads its model at call time; `evals/run_experiment.py` now sets `DRIFT_GRADER_MODEL`. The invalid experiment was deleted and is marked in `data/experiments.jsonl`.
- The judge-label template no longer drops pre-filled example labels when the dataset grows.
- The A2A second-opinion hop is verified in real sessions (`a2a_reachable`, `grader_agreement` feedback scores).

### Removed

- The CrewAI study buddy from the default install, because CrewAI 1.13 pins `opentelemetry-sdk` below the version Opik 2.2 needs. It remains in the tree as an optional component; see `docs/optional-crewai.md`.

[Unreleased]: https://github.com/sandeepmb/silent-drift-lab/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sandeepmb/silent-drift-lab/releases/tag/v0.1.0
