---
name: Drift scenario
about: Propose a way the agent system degrades that the dashboards would not notice
title: "drift: "
labels: drift-scenario
assignees: ''
---

## Name and kind

- Proposed `DRIFT_SCENARIO` key (snake_case):
- Which of the six kinds it belongs to: model / prompt and context / retrieval / input and traffic / tool and dependency / behavioural

## What changes

<!-- What does the injection do to the running system? Be concrete: which file, prompt, tool, model or setting, and how. It must be reversible by `inject.clear()`. -->

## Why it is silent

<!-- Why would latency, error rate, token count and cost not move, or move inside normal variance? If one of them would move, say which and in which direction. -->

## What should catch it

| Layer | Expected to see |
|---|---|
| Infrastructure (CPU, memory, GPU) | |
| Application (latency, errors, tokens, cost) | |
| Behavioural quality (quiz score, groundedness, tool failures after generation, output length, format compliance) | |

## Which two signals should corroborate

<!-- The monitor raises only when two behavioural signals move together. Name the two you expect, and the direction. -->

## Real-world precedent

<!-- Optional. Where have you seen this happen? A changelog, a post-mortem, a provider notice. Credit by name; no vendor contrasts. -->

## I can

- [ ] write the injection in `drift/inject.py`
- [ ] write the offline apply/clear test
- [ ] run `make drift-demo` and attach the figure
- [ ] none of the above; I am proposing it for someone else to pick up
