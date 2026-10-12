## What this changes

<!-- One paragraph. Link the issue. -->

Closes #

## Evidence

A reviewer should be able to check each ticked box from this PR alone.

- [ ] `make test` passes locally (paste the summary line below)
- [ ] If the change touches a traced path: `make run` with `--max-topics 1 --student simulated` produced a thread in Opik, and the thread id is pasted below
- [ ] If the change touches the `otel` track: the same session arrived through the Collector (`TELEMETRY_MODE=otel`)
- [ ] If the change touches evaluation: `make evals-test` passed on a machine with Ollama (paste the summary line)
- [ ] If the change adds or alters a drift scenario: `reports/drift-figure.png` is attached and the monitor's verdict is stated (caught / missed / one signal only)
- [ ] If the change adds or changes a number in any doc: the command that produced it is in `docs/reproducing.md` with hardware and n
- [ ] No vendor comparisons added to docs
- [ ] `CHANGELOG.md` updated under Unreleased
- [ ] Commits are signed off (`git commit -s`)

```text
<test summary line, thread id, or figure caption>
```

## Risk

<!-- What could this break for someone on a fresh clone? What did you not test? -->
