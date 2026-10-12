# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's vulnerability reporting form:

https://github.com/sandeepmb/silent-drift-lab/security/advisories/new

Do not open a public issue for a security problem. You will get an acknowledgement within five working days and a fix or a public advisory within ninety days, sooner where the fix is small.

## What is in scope

- Code in this repository: the application under `app/`, the scripts, the evaluation and drift code, the Makefile and the CI workflow.
- The Collector configuration under `collector/` and the Compose file it ships with.

## What is out of scope

- Opik, Ollama, LangGraph, the OpenTelemetry Collector and the other dependencies. Report those upstream; if you tell us too, we will pin a fixed version.
- Running the self-hosted Opik Compose stack on a network. It is a laptop install with no authentication, and Comet documents it as not for production. Exposing its ports beyond your machine is a configuration choice this project does not support.
- Prompt injection against the agents through the study notes. The notes directory is yours; the lab reads it on purpose.

## Supported versions

Only the latest tagged release and `main` receive fixes.

## Practical notes

- There are no API keys in this project. If you add a cloud backend, keep the key in `.env` (ignored by git) and never in `.env.example`.
- `make opik-up` clones Opik from GitHub at a pinned tag and runs its own `opik.sh`, which pulls container images. Check the pin in the Makefile before you run it on a machine you care about.
