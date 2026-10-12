"""
drift/signals.py — turn Opik traces into one row of signals per session.

    from drift import signals
    df = signals.fetch_sessions(include_tags=["seed", "drift:"], exclude_tags=["smoke", "demo"])
    df.to_csv("reports/drift-signals.csv", index=False)

Everything comes from the Opik REST API (no SDK state): traces by tag, the
spans of each trace, the feedback scores main.run_session attached. A session
is one Opik thread (thread_id == session_id); it usually has two traces (the
graph run up to the approval interrupt, and the resumed run).

System-health signals (what a release dashboard shows):
    duration_ms, error, total_tokens, prompt_tokens, completion_tokens,
    est_cost_usd (shadow price table, Ollama reports no cost), llm_calls

Behavioural / quality signals (what the dashboard does not show):
    quiz_score, weak_area_count, topics_completed
    explanation_chars, sentence_count, word_count, hedging_rate
    refusal_rate, tool_calls, tool_failures, parse_failures
    grounding (overlap with the notes the agent actually retrieved)
    notes_overlap (overlap with the canonical notes corpus)
    explainer_iterations (retry proxy: LLM calls inside the explainer loop)
    grader_agreement (1 - |local grade - A2A re-grade|; NaN when the quiz service
                      did not answer or the session predates the score)
    a2a_unreachable (1 - a2a_reachable feedback score; NaN when absent)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OPIK_API = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")
PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab")
NOTES_DIR = ROOT / "study_materials" / "sample_notes"

# Shadow prices in USD per 1k tokens (input, output). Ollama is free; these are
# what the same tokens would cost on a hosted model of similar size, so the
# "cost" line on the dashboard has a scale. Override with DRIFT_PRICE_TABLE
# (JSON: {"model": [in, out], ...}).
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "qwen2.5:7b": (0.00030, 0.00060),
    "qwen2.5:3b": (0.00015, 0.00030),
    "qwen2.5:1.5b": (0.00010, 0.00020),
    "default": (0.00030, 0.00060),
}

HEDGE_PATTERNS = [
    r"\bit depends\b", r"\bin some cases\b", r"\bgenerally\b", r"\btypically\b",
    r"\bmay\b", r"\bmight\b", r"\bcould\b", r"\bpossibly\b", r"\bperhaps\b",
    r"\barguably\b", r"\bit is worth noting\b", r"\bit's worth noting\b",
    r"\bnot always\b", r"\busually\b", r"\bsomewhat\b", r"\bto some extent\b",
    r"\bin general\b", r"\boften\b", r"\bkeep in mind\b", r"\bhowever\b",
]
HEDGE_RE = re.compile("|".join(HEDGE_PATTERNS), re.IGNORECASE)

REFUSAL_PATTERNS = [
    r"\bI can(?:'|no)t\b", r"\bI am unable\b", r"\bI'm unable\b", r"\bunable to (?:find|locate|access)\b",
    r"\bnot able to\b", r"\bdon't have access\b", r"\bdo not have access\b",
    r"notes (?:don't|do not|didn't|did not) (?:cover|contain|include|mention)",
    r"\bno (?:relevant|matching) (?:notes|files|materials)\b", r"\bcouldn't find\b", r"\bcould not find\b",
    r"\bgeneral knowledge\b",
]
REFUSAL_RE = re.compile("|".join(REFUSAL_PATTERNS), re.IGNORECASE)

STOPWORDS = set("""
the a an and or but if then else when while for to of in on at by with from as is are was were be been being
this that these those it its into than so such not no yes you your yours we our they their them he she his her
can could may might will would should shall do does did done have has had having about above after again
against all am any because before below between both each few further here how more most other out over own
same some there through under until up very what which who whom why also just like use used using one two
example code function functions value values variable variables return returns call called calls python
""".split())


# ─────────────────────────────────────────────────────────────────────────────
# REST
# ─────────────────────────────────────────────────────────────────────────────

def _get(path: str, **params) -> dict:
    r = requests.get(f"{OPIK_API}/v1/private/{path}", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_traces(tag: str | None = None, project: str = PROJECT, page_size: int = 100, max_pages: int = 50) -> list[dict]:
    """All traces of the project, optionally filtered server-side by one tag (substring match)."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        params: dict[str, Any] = {"project_name": project, "page": page, "size": page_size}
        if tag:
            params["filters"] = json.dumps([{"field": "tags", "operator": "contains", "value": tag}])
        data = _get("traces", **params)
        content = data.get("content", [])
        out.extend(content)
        if len(content) < page_size:
            break
    return out


def fetch_spans(trace_id: str, project: str = PROJECT) -> list[dict]:
    out: list[dict] = []
    for page in range(1, 20):
        data = _get("spans", project_name=project, trace_id=trace_id, page=page, size=200)
        content = data.get("content", [])
        out.extend(content)
        if len(content) < 200:
            break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# helpers over the raw payloads
# ─────────────────────────────────────────────────────────────────────────────

def _tag_value(tags: Iterable[str], prefix: str) -> str | None:
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return None


def _scores(trace: dict) -> dict[str, float]:
    return {s["name"]: float(s["value"]) for s in (trace.get("feedback_scores") or [])}


def _llm_usage(span: dict) -> tuple[int, int, str]:
    """(prompt_tokens, completion_tokens, model) of a ChatOllama span."""
    u = span.get("usage") or {}
    model = span.get("model") or ""
    p = int(u.get("prompt_tokens") or 0)
    c = int(u.get("completion_tokens") or 0)
    if not (p or c) or not model:
        try:
            gen = span["output"]["generations"][0][0]
            info = gen.get("generation_info") or {}
            model = model or info.get("model") or info.get("model_name") or ""
            msg = gen.get("message") or {}
            um = msg.get("usage_metadata") or msg.get("kwargs", {}).get("usage_metadata") or {}
            p = p or int(um.get("input_tokens") or info.get("prompt_eval_count") or 0)
            c = c or int(um.get("output_tokens") or info.get("eval_count") or 0)
        except (KeyError, IndexError, TypeError):
            pass
    if not model:
        model = ((span.get("metadata") or {}).get("ls_model_name")) or ""
    return p, c, model


def _price_table() -> dict[str, tuple[float, float]]:
    raw = os.getenv("DRIFT_PRICE_TABLE")
    if not raw:
        return DEFAULT_PRICES
    try:
        table = {k: tuple(v) for k, v in json.loads(raw).items()}
        table.setdefault("default", DEFAULT_PRICES["default"])
        return table
    except (json.JSONDecodeError, TypeError, ValueError):
        return DEFAULT_PRICES


def _cost(p: int, c: int, model: str) -> float:
    table = _price_table()
    pin, pout = table.get(model, table["default"])
    return p / 1000 * pin + c / 1000 * pout


def _final_explanations(span: dict) -> list[str]:
    """The explainer node's final answer(s): last AI message without tool calls."""
    msgs = (span.get("output") or {}).get("messages") or []
    for m in reversed(msgs):
        if m.get("type") == "ai" and (m.get("content") or "").strip() and not m.get("tool_calls"):
            return [m["content"]]
    return []


def _tool_output_text(span: dict) -> str:
    out = span.get("output")
    if isinstance(out, dict):
        out = out.get("output", out)
    if out is None:
        return ""
    return out if isinstance(out, str) else json.dumps(out)


def _empty_search(name: str, text: str) -> bool:
    """A search that legitimately found nothing (the notes do not cover the topic)."""
    t = text.strip()
    return name == "tool_search_notes" and t in ("No matches found.", "[]")


def _tool_failed(name: str, text: str) -> bool:
    """Error or malformed tool output — the LLM has to 'hallucinate around' it.
    An empty search result is *not* a failure (see _empty_search)."""
    t = text.strip()
    if not t:
        return True
    if t.startswith("Error"):
        return True
    if t.startswith("<html") or t.startswith("<!DOCTYPE"):
        return True
    if name == "tool_search_notes":
        if _empty_search(name, t):
            return False
        try:
            parsed = json.loads(t)
            return not isinstance(parsed, list)
        except json.JSONDecodeError:
            return True
    if name == "tool_read_file":
        # Notes are markdown; a bare JSON object is not a notes file.
        if t.startswith("{") and t.endswith("}"):
            return True
        return len(t) < 40
    return False


def _stem(w: str) -> str:
    """Very light stemming so 'closures'/'closure', 'remembers'/'remember' match."""
    for suf in ("ing", "ies", "es", "ed", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def _words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z][a-z_']+", text.lower()) if len(w) > 3 and w not in STOPWORDS]


def overlap(explanation: str, reference: str) -> float:
    """Fraction of the explanation's distinctive words that occur in the reference."""
    ew = set(_words(explanation))
    if not ew:
        return 0.0
    rw = set(_words(reference))
    return len(ew & rw) / len(ew)


def _canonical_notes() -> str:
    try:
        return "\n".join(p.read_text(encoding="utf-8") for p in sorted(NOTES_DIR.glob("*.md")))
    except OSError:
        return ""


_CANON: str | None = None


def canonical_notes() -> str:
    global _CANON
    if _CANON is None:
        _CANON = _canonical_notes()
    return _CANON


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if s.strip()])


# ─────────────────────────────────────────────────────────────────────────────
# session → signals
# ─────────────────────────────────────────────────────────────────────────────

def session_signals(traces: list[dict], spans: list[dict]) -> dict[str, Any]:
    """Compute every signal for one session (its traces + all their spans)."""
    traces = sorted(traces, key=lambda t: t.get("start_time") or "")
    tags: list[str] = sorted({t for tr in traces for t in (tr.get("tags") or [])})
    meta = {}
    for tr in traces:
        meta.update(tr.get("metadata") or {})
    scores: dict[str, float] = {}
    for tr in traces:
        scores.update(_scores(tr))

    goal = ""
    for tr in traces:
        inp = tr.get("input") or {}
        if isinstance(inp, dict) and inp.get("goal"):
            goal = inp["goal"]
            break

    scenario = _tag_value(tags, "drift:") or meta.get("drift_scenario") or "none"
    persona = _tag_value(tags, "persona:") or meta.get("persona") or "unknown"
    segment = _tag_value(tags, "segment:") or meta.get("segment") or ""

    # ── system ────────────────────────────────────────────────────────────
    duration_ms = sum(float(tr.get("duration") or 0) for tr in traces)
    p_tok = c_tok = 0
    cost = 0.0
    llm_calls = 0
    models: set[str] = set()
    explainer_iters = 0
    for s in spans:
        if s.get("type") == "llm":
            p, c, model = _llm_usage(s)
            p_tok += p
            c_tok += c
            cost += _cost(p, c, model)
            llm_calls += 1
            if model:
                models.add(model)
            if (s.get("metadata") or {}).get("langgraph_node") == "explainer":
                explainer_iters += 1
    error = scores.get("session_error")
    if error is None:
        error = 1.0 if any(tr.get("error_info") for tr in traces) else 0.0

    # ── behavioural ───────────────────────────────────────────────────────
    explanations: list[str] = []
    for s in spans:
        if s.get("name") == "explainer" and not s.get("parent_span_id"):
            explanations.extend(_final_explanations(s))
    explanation = "\n\n".join(explanations)
    words = explanation.split()
    word_count = len(words)
    hedges = len(HEDGE_RE.findall(explanation))
    hedging_rate = hedges / word_count * 100 if word_count else 0.0
    refusal_rate = (sum(1 for e in explanations if REFUSAL_RE.search(e)) / len(explanations)) if explanations else 0.0

    tool_calls = tool_failures = empty_searches = 0
    retrieved: list[str] = []
    for s in spans:
        if s.get("name") != "explainer.execute_tool":
            continue
        name = ((s.get("input") or {}).get("tool_call") or {}).get("name", "")
        text = _tool_output_text(s)
        if name in ("tool_search_notes", "tool_read_file"):
            tool_calls += 1
            if _empty_search(name, text):
                empty_searches += 1
            elif _tool_failed(name, text):
                tool_failures += 1
            elif name == "tool_read_file":
                retrieved.append(text)

    parse_failures = 0
    for s in spans:
        if s.get("name") == "quiz.grade_answer":
            out = s.get("output")
            if not isinstance(out, dict) or "score" not in out or str(out.get("feedback", "")).startswith("Could not grade"):
                parse_failures += 1
        elif s.get("name") == "quiz.generate_questions":
            out = s.get("output")
            qs = out.get("output", out) if isinstance(out, dict) else out
            if isinstance(qs, list) and len(qs) == 1 and "In your own words" in str(qs[0].get("question", "")):
                parse_failures += 1

    grounding = overlap(explanation, "\n".join(retrieved)) if retrieved else 0.0
    notes_overlap = overlap(explanation, canonical_notes())

    return {
        "session_id": traces[0].get("thread_id"),
        "segment": segment,
        "scenario": scenario,
        "persona": persona,
        "goal": goal,
        "timestamp": traces[0].get("start_time"),
        "tags": "|".join(tags),
        "models": "|".join(sorted(models)),
        "n_traces": len(traces),
        # system
        "duration_ms": duration_ms,
        "error": float(error),
        "prompt_tokens": p_tok,
        "completion_tokens": c_tok,
        "total_tokens": p_tok + c_tok,
        "est_cost_usd": round(cost, 6),
        "llm_calls": llm_calls,
        # behavioural
        "quiz_score": scores.get("quiz_score", float("nan")),
        "topics_completed": scores.get("topics_completed", float("nan")),
        "weak_area_count": scores.get("weak_area_count", float("nan")),
        "explanation_chars": len(explanation),
        "word_count": word_count,
        "sentence_count": _sentences(explanation),
        "hedging_rate": round(hedging_rate, 3),
        "refusal_rate": refusal_rate,
        "tool_calls": tool_calls,
        "tool_failures": tool_failures,
        "empty_searches": empty_searches,
        "parse_failures": parse_failures,
        "grounding": round(grounding, 4),
        "notes_overlap": round(notes_overlap, 4),
        "explainer_iterations": explainer_iters,
        # agreement between the local grader and the A2A re-grade (absent → NaN, never 0)
        "grader_agreement": scores.get("grader_agreement", float("nan")),
        "a2a_unreachable": (1.0 - scores["a2a_reachable"]) if "a2a_reachable" in scores else float("nan"),
    }


SYSTEM_SIGNALS = ["duration_ms", "error", "total_tokens", "est_cost_usd"]
BEHAVIOURAL_SIGNALS = [
    "quiz_score", "weak_area_count", "explanation_chars", "sentence_count", "hedging_rate",
    "refusal_rate", "tool_failures", "parse_failures", "grounding", "notes_overlap", "explainer_iterations",
    "grader_agreement", "a2a_unreachable",
]


# ─────────────────────────────────────────────────────────────────────────────
# segments
#
# A segment is a request-time property: which topic family the goal belongs to.
# The seed tag `segment:python-fundamentals` is too coarse for grounding
# signals, because the notes cover functions/closures/scope but not generators
# or context managers, so one baseline mixes "retrieval found notes" with
# "retrieval found nothing". The rules below refine it by goal keywords.
# ─────────────────────────────────────────────────────────────────────────────

SEGMENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("sql", ("sql", "window function", "join", "index")),
    ("py-functions", ("closure", "decorator", "scope", "legb", "nonlocal", "global", "lambda",
                      "first-class", "higher-order", "functools", "partial", "wraps")),
    ("py-iteration", ("generator", "iterator", "context manager", "comprehension")),
]
COARSE_SEGMENTS = {"", "python-fundamentals"}


def refine_segment(segment: str, goal: str) -> str:
    """Refine a coarse segment tag into a topic family using the goal text."""
    if segment not in COARSE_SEGMENTS:
        return segment
    g = (goal or "").lower()
    for name, keys in SEGMENT_RULES:
        if any(k in g for k in keys):
            return name
    return segment


# ─────────────────────────────────────────────────────────────────────────────
# segment lookup for sessions that were not tagged with segment:<s>
# ─────────────────────────────────────────────────────────────────────────────

def _segment_lookup() -> dict[str, str]:
    """session_id → segment and goal → segment from the seed files, if present."""
    lookup: dict[str, str] = {}
    for path in (ROOT / "scenarios" / "sessions.jsonl", ROOT / "data" / "seed-runs.jsonl", ROOT / "data" / "drift-runs.jsonl"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            seg = row.get("segment")
            if not seg:
                continue
            for key in ("session_id", "id", "goal"):
                if row.get(key):
                    lookup[str(row[key])] = seg
    return lookup


# ─────────────────────────────────────────────────────────────────────────────
# public entry point
# ─────────────────────────────────────────────────────────────────────────────

def fetch_sessions(
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    project: str = PROJECT,
    default_segment: str = "python-fundamentals",
    verbose: bool = True,
) -> pd.DataFrame:
    """
    One row per session (Opik thread). `include_tags` are prefixes: a session is kept
    if any of its tags starts with one of them (e.g. "seed", "drift:"). `exclude_tags`
    drops sessions carrying any tag starting with one of them (e.g. "smoke").
    """
    traces = [t for t in fetch_traces(project=project) if t.get("thread_id")]
    by_thread: dict[str, list[dict]] = {}
    for t in traces:
        by_thread.setdefault(t["thread_id"], []).append(t)

    def keep(tags: list[str]) -> bool:
        if exclude_tags and any(t.startswith(x) for t in tags for x in exclude_tags):
            return False
        if include_tags:
            return any(t.startswith(x) for t in tags for x in include_tags)
        return True

    lookup = _segment_lookup()
    rows = []
    for thread_id, trs in by_thread.items():
        tags = sorted({t for tr in trs for t in (tr.get("tags") or [])})
        if not keep(tags):
            continue
        spans: list[dict] = []
        for tr in trs:
            spans.extend(fetch_spans(tr["id"], project=project))
        row = session_signals(trs, spans)
        if not row["segment"]:
            row["segment"] = lookup.get(thread_id) or lookup.get(row["goal"]) or default_segment
        row["segment"] = refine_segment(row["segment"], row["goal"])
        rows.append(row)
        if verbose:
            print(f"[signals] {len(rows):>3} {thread_id} {row['scenario']:<16} {row['persona']:<8} "
                  f"quiz={row['quiz_score']:.2f} ground={row['grounding']:.2f} chars={row['explanation_chars']} "
                  f"tokens={row['total_tokens']} dur={row['duration_ms'] / 1000:.0f}s")

    df = pd.DataFrame(rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Pull sessions from Opik and compute drift signals.")
    ap.add_argument("--include", nargs="*", default=["seed", "baseline", "drift:"])
    ap.add_argument("--exclude", nargs="*", default=["smoke", "demo"])
    ap.add_argument("--out", default=str(ROOT / "reports" / "drift-signals.csv"))
    args = ap.parse_args()
    df = fetch_sessions(include_tags=args.include, exclude_tags=args.exclude)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"{len(df)} sessions → {args.out}")
    if not df.empty:
        print(df[["session_id", "scenario", "persona", "quiz_score", "grounding", "explanation_chars", "total_tokens"]].to_string())
