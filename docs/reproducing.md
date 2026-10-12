# Reproducing every figure and number

Every number in the README and the handbook comes from a command in this file. If you get a different number, open an issue with your `reports/footprint.md` and the hardware line; a disagreement is a contribution.

Standing labels, used in the README benchmarks table:

- **MEASURED**: a `make` target produced it on the hardware stated.
- **CALIBRATED SYNTHETIC**: the data is generated, and the generator's settings are recorded here.
- **ILLUSTRATIVE**: shows the shape of a result; do not quote it.

Hardware for all MEASURED rows unless stated: one Apple Silicon laptop, Docker Desktop with 8 GB allocated, Ollama serving `qwen2.5:7b`, Python 3.11, Opik 2.2.96.

<!-- TODO(hardware): add the exact chip and RAM, and the Ollama version, from `system_profiler SPHardwareDataType` and `ollama --version`. -->

## Opik stack idle memory: 3.4 GiB across 8 containers (MEASURED)

```bash
make opik-up
sleep 60                 # let ClickHouse and the backend settle
make footprint           # writes reports/footprint.md and prints it
```

`scripts/footprint.py` runs `docker stats --no-stream`, keeps the containers whose name contains `opik`, converts each memory figure to MiB and sums them. The per-container table in the handbook (chapter 3) is the same output. The number is for an idle stack; run a session first and run `make footprint` again to see it under load.

## One-topic traced session: about 2.7 minutes (MEASURED, n = 1)

```bash
make services
time .venv/bin/python main.py "Learn Python closures" --auto-approve --student simulated --max-topics 1
```

The session summary prints `elapsed_s`; the same value is the `elapsed_s` field of the Opik thread's metadata. This is one run. Expect variation of tens of seconds between runs on the same machine, and more if the model was not already loaded in Ollama.

## `make demo` timing: two sessions, roughly six minutes (derived)

Two one-topic sessions back to back, so about twice the figure above, plus the model load. Not independently measured.

## Seed dataset: 30 sessions (CALIBRATED SYNTHETIC)

```bash
make seed
```

The generator is `scenarios/sessions.jsonl`: 10 goals × 3 personas, all in segment `python-fundamentals`, one topic each. The personas are the three system prompts in `app/student.py`; the student model is `OLLAMA_MODEL` at temperature 0.7. The dataset is built from the traces those sessions produce, so its items carry whatever the model said that day; the dataset is versioned in Opik, and the version id is printed by the seeder.

<!-- TODO(seed-output): paste the dataset name and the first version id the seeder prints, so readers can confirm they are looking at the same thing. -->

## Offline experiment and judge agreement (MEASURED once run)

```bash
make experiment
```

<!-- TODO(experiment): record the experiment name, the metric list, the number of hand-labelled items and the agreement figure the script prints. Hand labels live in evals/. -->

## Online rules and the alert (MEASURED once run)

```bash
.venv/bin/python scripts/alert_webhook.py &     # local receiver
make rules
```

<!-- TODO(rules): record the rule names, the sampling rate, and the alert condition; add the screenshot path for the Opik Rules page (docs/img/rules.png) and the alert payload the webhook printed. -->

## The drift figure (MEASURED once run)

```bash
make drift-demo        # writes reports/drift-figure.png
```

The lab runs a baseline over the 30 seed sessions, injects each scenario in turn, and runs the monitor with a per-segment rolling baseline and two-signal corroboration. The figure plots latency, error rate and cost (flat) against quiz score and groundedness (falling after the injection point).

<!-- TODO(drift-figure): record the run counts per scenario, which scenarios were flagged, the false-positive count on the baseline, and the monitor settings (window, corroboration rule). Copy the figure to docs/img/drift-figure.png for the README. -->

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
