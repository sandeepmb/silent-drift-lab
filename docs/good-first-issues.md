# Good first issues to open at launch

Eight scoped issues, each doable in an evening by someone who has run `make demo` once. Open them with the `good first issue` label the day the repository goes public. Each has a definition of done that a reviewer can check without a conversation.

## 1. Record your footprint on another machine

`make footprint` writes `reports/footprint.md`. We have one data point (Apple Silicon, Docker Desktop 8 GB). Run it on Linux, on Windows with WSL, or on an Intel Mac, and add a row to a new table in `docs/reproducing.md`.

Done when: a PR adds a row with chip, RAM, Docker memory limit, container count and total, and the raw `reports/footprint.md` is attached to the PR.

## 2. `make setup` should say what is missing

If `uv`, `docker` or `ollama` is not on the path, `make setup` fails with a tool-specific error. Add a preflight check to the Makefile (or a small `scripts/preflight.py`) that lists what is missing and where to get it.

Done when: running `make setup` on a machine without `uv` prints a one-line message naming `uv` and the install URL, and the existing path is unchanged.

## 3. A `weak`-persona regression test for the grader

`app/agents/quiz_generator.py` grades answers with a low-temperature model call. Add an offline test that feeds a fixed set of (question, expected, answer) triples through the grading prompt builder and asserts the prompt contains the expected answer and the rubric, without calling a model.

Done when: `make test` runs the new test, it passes offline, and it fails if the rubric text is removed.

## 4. Document the Opik UI pages with screenshots

`docs/img/` needs one screenshot each for a trace, a thread, a dataset, an experiment, a rule and an alert, taken on Opik 2.2.96 with the lab's project open. Add them and reference them from `docs/reproducing.md`.

Done when: six PNGs under 500 KB each are in `docs/img/`, each referenced once.

## 5. Make `inject.clear()` idempotent and tested

`scripts/demo.py` calls `drift.inject.apply(name)` and then `inject.clear()`. Add an offline test that `apply` followed by `clear` leaves the environment and the notes directory as they were, and that calling `clear` twice is safe.

Done when: the test is in `tests/`, runs in `make test`, and covers every scenario name listed in `.env.example`.

## 6. Add the shadow price table to the README

Ollama reports no cost; the lab assigns a per-token shadow price so that the cost dashboard has something to show. Document the table and its source in a short section of `LIMITATIONS.md` §6, and link it from the README benchmarks table.

Done when: the table appears with a one-line justification per row and a date.

## 7. A `make doctor` target

A target that checks, in order: Ollama reachable at `OLLAMA_BASE_URL` and the model present; Opik reachable at `OPIK_URL_OVERRIDE`; the quiz service answering on `:9001`; the Collector on `:4318` if `TELEMETRY_MODE=otel`. Print one line per check.

Done when: `make doctor` exits non-zero if any required service is down and names it.

## 8. A seventh drift scenario proposal

Pick one from the roadmap (a retrieval reranker change, a tool schema change) or your own, and open it with the drift scenario issue template. No code required for the issue; the template asks what each layer should see.

Done when: the issue has the three expectation fields filled in and a maintainer has labelled it `accepted`.
