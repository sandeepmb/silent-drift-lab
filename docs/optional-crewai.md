# Optional: the CrewAI study buddy

The upstream Learning Accelerator has a fifth participant: a CrewAI agent, exposed over A2A on `:9002`, that the progress coach calls for a second explanation when a student scores poorly. It is in this tree at `app/crewai_agent/study_buddy.py` and it is off by default.

## Why it is off

CrewAI 1.13.0 pins `opentelemetry-sdk` below 1.35. Opik 2.2.96 requires a newer one. `uv` refuses to resolve both into one environment, and it is right to. Rather than loosen either pin, the buddy runs in its own virtual environment and talks to the main application over HTTP, which is what A2A is for.

## Running it in a second environment

```bash
# 1. A separate venv with the upstream pins (CrewAI included, Opik absent)
uv venv --python 3.11 .venv-crewai
VIRTUAL_ENV=.venv-crewai uv pip install -r requirements.upstream.txt

# 2. Start the buddy
.venv-crewai/bin/python app/crewai_agent/study_buddy.py      # :9002
curl http://localhost:9002/.well-known/agent-card.json

# 3. Tell the main application to use it
#    in .env:
#    USE_STUDY_BUDDY=true
#    STUDY_BUDDY_URL=http://localhost:9002
make run
```

`requirements.upstream.txt` is the original project's pin set and includes Langfuse; it is harmless in the buddy's environment and is kept unchanged so that the upstream install stays reproducible.

## What you lose

- The buddy's own model calls are not traced into Opik, because the Opik SDK is not in its environment. The A2A call from the progress coach is traced as a span with the request and the response; what happened inside the buddy is opaque.
- On the `otel` track you can instrument the buddy with the OpenTelemetry SDK version CrewAI allows and point it at the Collector on `:4318`. Context propagation over the A2A hop then needs the trace headers forwarded by `app/a2a_services/a2a_client.py`; that path is exercised for the quiz service, not for the buddy.

<!-- TODO(crewai-otel): if someone gets the buddy exporting to the Collector, record the opentelemetry-sdk version that worked and link the PR. -->

## When you would want it

Only if you are reproducing the upstream handbook's cross-framework demonstration. For the observability story this lab tells, the A2A quiz service carries the cross-process hop and the buddy adds nothing the monitor can see.
