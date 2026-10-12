"""
drift/inject.py — six ways to break an agent without touching a dashboard.

    from drift import inject
    inject.apply("stale_notes")   # set the environment hooks for one scenario
    ...run sessions...
    inject.clear()                # restore everything

Every scenario is a handful of environment variables that the app reads *at
call time* (see the `# drift hook` lines in app/agents/explainer.py,
app/agents/quiz_generator.py and app/mcp_servers/filesystem_server.py).
Nothing is redeployed, no config file changes, no version bump: exactly the
kind of change a release dashboard never sees.

Scenario → taxonomy:

    model_swap       model drift            explainer+grader silently run qwen2.5:3b
    prompt_mutation  prompt & context drift explainer told to be terse and ignore notes
    stale_notes      retrieval drift        NOTES_PATH → truncated / unrelated notes
    traffic_shift    input / traffic drift  goals from a new segment with no notes
    tool_fault       tool & dependency      search/read return empty or junk, p=0.7
    verbosity        behavioural drift      lenient grader, verbose hedging explainer
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STALE_NOTES = ROOT / "study_materials" / "stale_notes"

SCENARIOS: dict[str, str] = {
    "model_swap": "Model drift: explainer and grader quietly switch to qwen2.5:3b (no config deploy).",
    "prompt_mutation": "Prompt/context drift: explainer template loses its format block and gains 'two sentences, ignore the notes'.",
    "stale_notes": "Retrieval drift: NOTES_PATH points at a copy whose relevant files are truncated or off-topic.",
    "traffic_shift": "Input/traffic drift: goals arrive from a new segment (SQL) that the notes do not cover.",
    "tool_fault": "Tool/dependency drift: search_notes/read_study_file return empty or malformed output with p=0.7, never raise.",
    "verbosity": "Behavioural drift: lenient grader + verbose, hedging explainer; scores may rise, shape changes.",
}

# Families the talk's taxonomy assigns each scenario to (used in the report).
TAXONOMY: dict[str, str] = {
    "model_swap": "model drift",
    "prompt_mutation": "prompt & context drift",
    "stale_notes": "retrieval drift",
    "traffic_shift": "input / traffic drift",
    "tool_fault": "tool & dependency drift",
    "verbosity": "behavioural drift",
}

# Every env var any scenario may touch. clear() restores all of them.
HOOK_VARS = (
    "DRIFT_SCENARIO",
    "DRIFT_EXPLAINER_MODEL",
    "DRIFT_GRADER_MODEL",
    "DRIFT_EXPLAINER_PROMPT_SUFFIX",
    "DRIFT_EXPLAINER_PROMPT_STRIP",
    "DRIFT_GRADER_PROMPT_SUFFIX",
    "DRIFT_GRADER_PROMPT_STRIP",
    "DRIFT_TOOL_FAULT_P",
    "DRIFT_SEGMENT",
    "NOTES_PATH",
)

# prompt_mutation = a template edit: the EXPLANATION FORMAT block is gone and a
# terse instruction took its place. (A one-line suffix appended to the intact
# template was ignored by qwen2.5:7b in two trial runs: the 300-500 word format
# block won every time. Dropping the block is what a real bad prompt deploy does.)
FORMAT_BLOCK_RE = r"EXPLANATION FORMAT:.*?Target length: 300-500 words\n"
TERSE_SUFFIX = (
    "EXPLANATION FORMAT: reply with exactly two short sentences and nothing else. "
    "No analogy, no code example, no gotcha, no headings. Do not quote, mention or "
    "rely on the notes or any file; keep it generic."
)

# verbosity = the same kind of template edit in the other direction: the format
# block is replaced by a verbose, hedging one, and the grading rubric's score
# bands are replaced by a lenient policy.
VERBOSE_SUFFIX = (
    "EXPLANATION FORMAT:\n"
    "- Be exhaustive: at least 900 words, five or more sections with headings\n"
    "- Hedge every claim: use 'it depends', 'in some cases', 'generally', 'may', "
    "'might', 'arguably', 'it is worth noting', 'however' throughout\n"
    "- Add caveats and alternative viewpoints to every paragraph\n"
    "- Restate the key points in a closing summary"
)
GRADER_RUBRIC_RE = r"Grade the student's answer honestly\..*?Fundamentally wrong: 0\.0-0\.2\n"
LENIENT_SUFFIX = (
    "Grading policy: be lenient and encouraging. Any answer that shows effort or "
    "mentions a related idea gets score 0.9 or higher and correct=true. Only a blank "
    "or off-topic answer scores below 0.8."
)

# Goals per scenario. Baseline goals are the python-fundamentals segment that the
# notes cover; traffic_shift sends a new segment the notes know nothing about.
PYTHON_GOALS = [
    "Learn Python closures and decorators from scratch",
    "Understand how decorators with arguments are built",
    "Understand variable scope and the LEGB rule in Python",
]
SQL_GOALS = [
    "Learn SQL window functions",
    "Understand SQL joins and when each type applies",
    "Learn how SQL indexes speed up queries",
]

_snapshot: dict[str, str | None] | None = None


def _hooks(scenario: str) -> dict[str, str]:
    if scenario == "model_swap":
        return {"DRIFT_EXPLAINER_MODEL": "qwen2.5:3b", "DRIFT_GRADER_MODEL": "qwen2.5:3b"}
    if scenario == "prompt_mutation":
        return {"DRIFT_EXPLAINER_PROMPT_STRIP": FORMAT_BLOCK_RE, "DRIFT_EXPLAINER_PROMPT_SUFFIX": TERSE_SUFFIX}
    if scenario == "stale_notes":
        if not STALE_NOTES.exists():
            from drift.make_stale_notes import build  # noqa: WPS433

            build()
        return {"NOTES_PATH": str(STALE_NOTES)}
    if scenario == "traffic_shift":
        return {"DRIFT_SEGMENT": "sql"}
    if scenario == "tool_fault":
        return {"DRIFT_TOOL_FAULT_P": os.getenv("DRIFT_TOOL_FAULT_P_OVERRIDE", "0.7")}
    if scenario == "verbosity":
        return {
            "DRIFT_EXPLAINER_PROMPT_STRIP": FORMAT_BLOCK_RE,
            "DRIFT_EXPLAINER_PROMPT_SUFFIX": VERBOSE_SUFFIX,
            "DRIFT_GRADER_PROMPT_STRIP": GRADER_RUBRIC_RE,
            "DRIFT_GRADER_PROMPT_SUFFIX": LENIENT_SUFFIX,
        }
    raise KeyError(f"unknown scenario '{scenario}'; known: {sorted(SCENARIOS)}")


def apply(scenario: str) -> dict[str, str]:
    """Set the environment hooks for `scenario`. Returns the vars that were set."""
    global _snapshot
    if scenario in (None, "", "none"):
        clear()
        return {}
    hooks = _hooks(scenario)
    if _snapshot is None:
        _snapshot = {k: os.environ.get(k) for k in HOOK_VARS}
    else:  # switching scenario without clear(): restore first so hooks don't leak
        _restore()
    os.environ["DRIFT_SCENARIO"] = scenario
    for k, v in hooks.items():
        os.environ[k] = v
    return {"DRIFT_SCENARIO": scenario, **hooks}


def _restore() -> None:
    for k, v in (_snapshot or {}).items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def clear() -> None:
    """Restore every hook variable to what it was before the first apply()."""
    global _snapshot
    if _snapshot is None:
        # Never applied in this process: just make sure nothing is left over.
        for k in HOOK_VARS:
            if k.startswith("DRIFT_"):
                os.environ.pop(k, None)
        os.environ["DRIFT_SCENARIO"] = "none"
        return
    _restore()
    _snapshot = None
    os.environ.setdefault("DRIFT_SCENARIO", "none")


def current() -> str:
    return os.getenv("DRIFT_SCENARIO", "none")


def segment_for(scenario: str) -> str:
    """Topic family of the scenario's goals (see drift.signals.SEGMENT_RULES)."""
    return "sql" if scenario == "traffic_shift" else "py-functions"


def goals_for(scenario: str) -> list[str]:
    return SQL_GOALS if scenario == "traffic_shift" else PYTHON_GOALS


def tags_for(scenario: str, persona: str = "average") -> list[str]:
    return [f"drift:{scenario}", f"segment:{segment_for(scenario)}", f"persona:{persona}"]


if __name__ == "__main__":
    for name, desc in SCENARIOS.items():
        print(f"{name:<16} {TAXONOMY[name]:<24} {desc}")
        print(f"{'':<16} hooks: {apply(name)}")
        clear()
