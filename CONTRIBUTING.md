# Contributing

Thank you for looking. The most useful contributions, in order: a drift scenario the lab does not have, a measurement that disagrees with one in the README, a fix to something that did not run on your machine.

## Before you start

- Open an issue first for anything larger than a typo. There are templates for bugs, features and drift scenarios.
- Keep pull requests small. One scenario, one fix, one doc.
- Every number you add to a README or doc must come from a command someone else can run. Say which command, on what hardware. See [docs/reproducing.md](docs/reproducing.md).

## Developer Certificate of Origin

Commits must carry a DCO sign-off. It is a one-line statement that you wrote the change or have the right to submit it, under the [Developer Certificate of Origin 1.1](https://developercertificate.org/).

```bash
git commit -s -m "drift: add retrieval-reranker scenario"
```

That adds `Signed-off-by: Your Name <you@example.com>` to the message. Use your real name. If you forget, `git commit --amend -s` fixes the last commit.

## Setting up

```bash
make setup                    # .venv with pinned deps; needs uv and Python 3.11
ollama pull qwen2.5:7b
make opik-up                  # only needed for traced runs and the eval gate
make services
```

## Running tests

```bash
make test        # unit tests; no Ollama, no Opik, runs in CI
make evals-test  # the evaluation gate; needs Ollama and Opik up, runs on a self-hosted runner
```

Tests under `tests/` are marked `eval` when they need a model. Everything else must run offline, because that is what CI runs. If your change needs a model to be exercised, add an offline test for the logic and an `eval` test for the behaviour.

## Adding a drift scenario

The lab's value is in its scenarios. A good one is a way an agent system degrades that a latency, error or cost dashboard would not notice.

1. Open an issue with the drift scenario template. Say which of the six drift kinds it belongs to, what you expect layers 1 and 2 to see (usually nothing), and which behavioural signals you expect to move.
2. Add an injection function in `drift/inject.py` and register its name so that `DRIFT_SCENARIO=<your_name>` selects it. `inject.apply(name)` must be reversible by `inject.clear()`; the demo and the lab rely on that.
3. Add the name to the `DRIFT_SCENARIO` comment in `.env.example` and to the scenario table in the README.
4. Add an offline test that `apply` and `clear` round-trip without a model.
5. Run `make drift-demo`, attach `reports/drift-figure.png` to the pull request, and state what the monitor did: caught it, missed it, or caught it only with one signal. A scenario the monitor misses is a valid contribution; say so, and the gap goes into [LIMITATIONS.md](LIMITATIONS.md).

## Pull requests

The PR template has an evidence checklist. Fill it in; a reviewer should be able to reproduce your claim from the PR alone.

## Style

- Python 3.11, standard library first. Pin any new dependency in `requirements.txt` and run `make lock`.
- Observability stays at the edge. Agents under `app/agents/` do not import Opik or OpenTelemetry directly; `@track` on business logic is the one exception, and it is deliberate.
- Comments explain why, not what.
- No vendor comparisons in docs. Describe what this does; credit prior art by name.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
