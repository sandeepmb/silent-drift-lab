# Reproducing every figure and number

Every number in the README and the handbook comes from a command in this file. If you get a different number, open an issue with your `reports/footprint.md` and the hardware line; a disagreement is a contribution.

Standing labels, used in the README benchmarks table:

- **MEASURED**: a `make` target produced it on the hardware stated.
- **CALIBRATED SYNTHETIC**: the data is generated, and the generator's settings are recorded here.
- **ILLUSTRATIVE**: shows the shape of a result; do not quote it.

Hardware for all MEASURED rows unless stated: one Apple Silicon laptop, Docker Desktop with 8 GB allocated (12 GB is recommended; see LIMITATIONS.md), Ollama serving `qwen2.5:7b`, Python 3.11, Opik 2.2.96.

```bash
sysctl -n machdep.cpu.brand_string hw.memsize     # Apple M4 Pro / 25769803776 (24 GiB)
ollama --version                                  # ollama version is 0.18.2
docker info | grep -E "Total Memory|CPUs"         # CPUs: 14, Total Memory: 7.748GiB
```

Measured on: Apple M4 Pro, 24 GiB unified memory, Ollama 0.18.2 (`qwen2.5:7b` 100 % on the GPU), Docker Desktop with 14 CPUs and 7.748 GiB, Python 3.11.14, Opik 2.2.96. Run one model-using job at a time: there is one GPU, and two jobs at once inflate latency and can time out the judge calls (see the seed note below).

## Opik stack idle memory: 4.60 GiB across 8 containers (MEASURED)

```bash
make opik-up
sleep 60                 # let ClickHouse and the backend settle
make footprint           # writes reports/footprint.md and prints it
```

`scripts/footprint.py` runs `docker stats --no-stream`, keeps the containers whose name contains `opik`, converts each memory figure to MiB and sums them. The per-container table in the handbook (chapter 3) is the same output. The number is for an idle stack; run a session first and run `make footprint` again to see it under load.

## One-topic traced session: median 86 s over 30 runs (MEASURED)

```bash
make services
time .venv/bin/python main.py "Learn Python closures" --auto-approve --student simulated --max-topics 1
```

The session summary prints `elapsed_s`; the same value is the `elapsed_s` field of the Opik thread's metadata and of every line in `data/baseline-runs.jsonl`. Over the 30 baseline sessions of the drift lab it is a median of 85.7 s, a mean of 111.4 s, a minimum of 59.0 s and a maximum of 302.3 s. The spread is real: latency roughly doubled part-way through that run for a reason I did not isolate (see [LIMITATIONS.md](../LIMITATIONS.md)). Expect tens of seconds of variation between runs, and more if the model was not already loaded in Ollama.

## `make demo` timing: two sessions back to back (MEASURED once)

Two runs of `make demo`, output of the second in `data/demo-output.txt`: 385 s wall clock (session times 203.4 s and 171.0 s, plus reading the traces back from Opik). The first run's sessions took 189.8 s and 235.5 s. In both, grounding in the notes fell (0.31 to 0.09 and 0.32 to 0.08) while errors stayed at 0, tokens stayed within 50% and the quiz score did not fall (72% to 80% in the second run, 85% to 85% in the first): a single pair of sessions is an anecdote, which the script says.

## Seed dataset: 30 sessions (CALIBRATED SYNTHETIC)

```bash
make seed
```

The generator is `scenarios/sessions.jsonl`: 10 goals × 3 personas, all in segment `python-fundamentals`, one topic each. The personas are the three system prompts in `app/student.py`; the student model is `OLLAMA_MODEL` at temperature 0.7. The dataset is built from the traces those sessions produce, so its items carry whatever the model said that day; the dataset is versioned in Opik, and the version id is printed by the seeder.

The dataset is `learning-accelerator-sessions`. Its first version was `v1` (hash `5f3ca124`, 3 items); after all 30 sessions it is `v5` (hash `35075fd7`, 84 items). `.venv/bin/python scripts/seed_dataset.py --dataset-only` rebuilds it from `data/seed-runs.jsonl` and reports 0 new items when it is current. Seed results (mean quiz score per persona, session seconds) are in [docs/evals.md](evals.md).

## Offline experiment and judge agreement (MEASURED)

```bash
make experiment
```

The two headline experiments are `grader-consistency-qwen2.5-7b-20261010-2200` and `grader-consistency-qwen2.5-3b-20261010-2205`: 20 items each from dataset version `v5`, nine metrics (per-metric means in [docs/evals.md](evals.md)). Judge agreement: `.venv/bin/python evals/validate_judge.py` writes `data/labels/judge_agreement.json`; it rests on 10 hand labels, all of them the pre-filled example rows (the other 30 rows of `data/labels/judge_labels.csv` are empty): exact-bucket 0.700, binary 0.800, mean absolute error 0.120, Spearman 0.714.

## Online rules and the alert (MEASURED)

```bash
.venv/bin/python scripts/alert_webhook.py &     # local receiver
make rules
```

Rules: `format-compliance` (python metric, sampling 1.0), `answer-relevance` (LLM judge, 0.5), `thread-coherence` (thread judge, 1.0); the two judge rules were off for all runs and re-enabled only at the end, see [docs/evals.md](evals.md). Alert `quiz-score-low`: average `quiz_score` below 0.5 over a 900 s window, webhook `http://host.docker.internal:9009/alert`. Payloads received are in `data/alerts.jsonl` (3 lines, 2 real); the Rules page screenshot is `docs/img/shot-09-online-rules.png` and the webhook render is `docs/img/shot-10a-alert-webhook.png`.

## The drift figure (MEASURED)

```bash
make drift-demo        # writes reports/drift-figure.png and drift-figure-light.png
```

The lab runs its own baseline of 30 healthy sessions (not the seed sessions), injects each of the six scenarios 3 times, and runs the monitor with a per-segment baseline, a robust z-score threshold of 2.0, CUSUM change detection and a two-family corroboration rule. The figure plots latency, cost and errors (top) against quiz score and grounding (bottom), with injection points marked. Run counts: 30 baseline (21 `py-functions`, 9 `py-iteration`), 3 per scenario, 18 drift sessions, 0 errors. Flagged: all 6 (2 page, 3 ticket, 1 annotate). False alarms on the healthy baseline: static rule 1 of 30 sessions; monitor 2 pages in 28 sliding 3-session windows. Full table: [docs/drift-lab.md](drift-lab.md) and `reports/drift-report.md`. To refresh from existing Opik data without running anything: `python drift/run_lab.py --analyse-only` (about three minutes).

## `make demo` (ILLUSTRATIVE)

```bash
make demo
```

One healthy session, one with `DEMO_SCENARIO` (default `stale_notes`) injected, and a verdict. With n = 1 each, the verdict can go either way on a given run; the script says so. It exists to be screen-recorded, not to be quoted.

## Versions

```bash
grep -E '^(opik|langgraph|langchain-core|langchain-ollama)==' requirements.txt
grep OPIK_VERSION Makefile
.venv/bin/python --version
ollama list | grep qwen2.5
```

The README's pinned-versions line must match this output. If you bump a pin, update both.
