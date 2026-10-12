---
name: Bug report
about: Something did not run, or ran and produced the wrong thing
title: "bug: "
labels: bug
assignees: ''
---

## What happened

<!-- One or two sentences. Paste the command and the last 20 lines of output in the block below. -->

```text

```

## What you expected

## How to reproduce

```bash
git clone https://github.com/sandeepmb/silent-drift-lab && cd silent-drift-lab
make setup
# the steps after that
```

## Environment

- OS and chip:
- Docker Desktop memory limit:
- `ollama --version` and `ollama list | grep qwen2.5`:
- `grep OPIK_VERSION Makefile`:
- `TELEMETRY_MODE` (native / otel / off):
- Output of `make footprint`, if Opik was up:

## Anything else

<!-- Opik logs (`make opik-logs`), the thread id in the Opik UI, a screenshot. -->
