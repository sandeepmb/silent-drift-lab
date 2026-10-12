# Roadmap

Six months, kept modest. One maintainer, evenings and weekends. Dates are targets, not promises; the CHANGELOG records what actually shipped.

## 0.1.0 — October 2026: public, reproducible

- Repository public before the freeCodeCamp handbook is submitted.
- `make demo` and `make drift-demo` run clean on a fresh clone.
- Every number in the README produced by a `make` target and listed in `docs/reproducing.md`.
- CI green on Python 3.11 and 3.12; Dependabot on.

## 0.2.0 — November 2026: the talk

- The drift figure on the MLOps World slides comes from `make drift-demo`, not from an illustration.
- Demo capture in the README.
- The eval gate (`make evals-test`) runs on a self-hosted runner with Ollama, on a schedule.
- Good-first-issues opened and triaged.

## 0.3.0 — December 2026: more scenarios, better baselines

- Two community-contributed drift scenarios, or two more of our own: a retrieval reranker change and a tool schema change are the candidates.
- The monitor's baseline window and corroboration rule exposed as settings with documented defaults.
- Per-scenario run counts large enough to print a detection rate with an interval (`LIMITATIONS.md` §2).

## 0.4.0 — January 2027: a second segment

- A second study-materials segment (the clinical trials notes already in `study_materials/`) so that input drift and per-segment baselines have something real to separate.
- Hand-labelled set for judge agreement extended and published with its labelling guide.

## 0.5.0 — February 2027: the portable track

- Metrics on the OpenTelemetry track (session count, score histograms) exported from the Collector to any OTLP backend.
- A documented second exporter in `collector/` so that the same spans fan out to two backends.

## 0.6.0 — March 2027: pinned bump

- Move the Opik pin forward one minor version, with the attribute-mapping differences recorded in `LIMITATIONS.md` §5.
- Python 3.13 in the CI matrix if the pinned dependencies allow it.

## Not planned

- Hosted anything. The lab runs on your laptop.
- A plugin for any particular observability product. The `otel` track is the extension point.
- Replacing Ollama with a hosted model as the default. Keyless is a feature.
