"""
drift/run_lab.py — the whole drift lab in one command.

    make drift-demo                       # = python drift/run_lab.py
    python drift/run_lab.py --quick       # one session per scenario
    python drift/run_lab.py --n 3 --scenarios stale_notes tool_fault
    python drift/run_lab.py --analyse-only   # skip the runs, rebuild the reports

Steps
  1. Wait for the baseline population: ≥ MIN_BASELINE sessions in
     data/seed-runs.jsonl (written by scripts/seed_dataset.py). If the file is
     still absent after --wait-min minutes, run 10 baseline sessions here,
     tagged `baseline`.
  2. For each scenario: inject.apply(), run N sessions (personas cycle
     good/average/weak so the window matches the baseline mix), inject.clear().
     Sessions are tagged drift:<scenario>, segment:<s>, persona:<p>, lab.
  3. Pull every seed/baseline/drift session from Opik → reports/drift-signals.csv
  4. Run the monitor → reports/drift-report.md (+ drift-report.json)
  5. Draw reports/drift-figure.png (dark) and reports/drift-figure-light.png
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from drift import inject, monitor, signals  # noqa: E402

SEED_RUNS = ROOT / "data" / "seed-runs.jsonl"
BASELINE_RUNS = ROOT / "data" / "baseline-runs.jsonl"
DRIFT_RUNS = ROOT / "data" / "drift-runs.jsonl"
REPORTS = ROOT / "reports"
MIN_BASELINE = 10
PERSONAS = ["average", "good", "weak"]


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _opik_session_count(tag: str) -> int:
    try:
        traces = signals.fetch_traces(tag=tag)
    except Exception:
        return 0
    return len({t.get("thread_id") for t in traces if t.get("thread_id")})


# ─────────────────────────────────────────────────────────────────────────────
# 1. baseline
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_baseline(min_sessions: int, wait_min: float, quiet: bool) -> str:
    """Return the tag that identifies the baseline population ('seed' or 'baseline')."""
    deadline = time.time() + wait_min * 60
    while True:
        n_file = _count_jsonl(SEED_RUNS)
        n_opik = _opik_session_count("seed") if n_file else 0
        if n_file >= min_sessions and n_opik >= min_sessions:
            print(f"[lab] baseline ready: {n_file} seed sessions in file, {n_opik} in Opik")
            return "seed"
        if time.time() >= deadline:
            if n_file:
                print(f"[lab] only {n_file} seed sessions after {wait_min} min; using them plus local baseline runs")
            break
        print(f"[lab] waiting for seed sessions ({n_file} in file, {n_opik} in Opik, need {min_sessions}) …")
        time.sleep(30)

    have = _count_jsonl(BASELINE_RUNS)
    need = max(min_sessions - _count_jsonl(SEED_RUNS) - have, 0)
    if need:
        print(f"[lab] running {need} baseline sessions myself (tag `baseline`)")
        from main import run_session  # noqa: WPS433

        plan = [json.loads(ln) for ln in (ROOT / "scenarios" / "sessions.jsonl").read_text().splitlines() if ln.strip()]
        inject.clear()
        for sc in plan[:need]:
            r = run_session(sc["goal"], auto_approve=True, student_mode="simulated", persona=sc["persona"],
                            max_topics=1, tags=["baseline", f"persona:{sc['persona']}", f"segment:{sc.get('segment', 'python-fundamentals')}"],
                            metadata={"segment": sc.get("segment"), "persona": sc["persona"]}, quiet=quiet)
            _append(BASELINE_RUNS, {**r.summary(), "segment": sc.get("segment"), "persona": sc["persona"], "scenario": "none",
                                    "ts": datetime.now(timezone.utc).isoformat()})
    return "baseline"


# ─────────────────────────────────────────────────────────────────────────────
# 2. injections
# ─────────────────────────────────────────────────────────────────────────────

def _done_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    if DRIFT_RUNS.exists():
        for ln in DRIFT_RUNS.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if not row.get("error"):
                counts[row.get("scenario", "")] = counts.get(row.get("scenario", ""), 0) + 1
    return counts


def run_scenarios(scenarios: list[str], n: int, quiet: bool, extra_tags: list[str] | None = None,
                  round_robin: bool = True, resume: bool = False) -> list[dict]:
    """
    Run n sessions per scenario. Round-robin order (persona-major: every scenario
    with persona A, then every scenario with persona B, …) keeps coverage even if
    the run is cut short; sessions are grouped by scenario again at analysis time.
    With resume=True, (scenario, i) pairs already recorded in data/drift-runs.jsonl
    are skipped, so an interrupted run continues where it stopped.
    """
    from main import run_session  # noqa: WPS433

    plan = [(sc, i) for i in range(n) for sc in scenarios] if round_robin else [(sc, i) for sc in scenarios for i in range(n)]
    if resume:
        done = _done_counts()
        skipped = [(sc, i) for sc, i in plan if i < done.get(sc, 0)]
        plan = [(sc, i) for sc, i in plan if i >= done.get(sc, 0)]
        print(f"[lab] resume: skipping {len(skipped)} finished session(s), {len(plan)} to go")
    results = []
    for k, (scenario, i) in enumerate(plan, 1):
        hooks = inject.apply(scenario)
        segment = inject.segment_for(scenario)
        goals = inject.goals_for(scenario)
        persona = PERSONAS[i % len(PERSONAS)]
        goal = goals[i % len(goals)]
        tags = ["lab", f"segment:{segment}", f"persona:{persona}"] + (extra_tags or [])
        started = time.time()
        print(f"\n[lab] ({k}/{len(plan)}) ▶ {scenario} persona={persona} goal={goal!r}\n[lab]   hooks: {hooks}", flush=True)
        try:
            r = run_session(goal, auto_approve=True, student_mode="simulated", persona=persona, max_topics=1,
                            tags=tags, metadata={"segment": segment, "persona": persona, "drift_scenario": scenario},
                            quiet=quiet)
        finally:
            inject.clear()
        row = {**r.summary(), "scenario": scenario, "segment": segment, "persona": persona,
               "trace_ids": r.trace_ids, "ts": datetime.now(timezone.utc).isoformat()}
        _append(DRIFT_RUNS, row)
        results.append(row)
        print(f"[lab]   → quiz={row['avg_quiz_score']:.2f} elapsed={time.time() - started:.0f}s error={row['error']}", flush=True)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 4. report
# ─────────────────────────────────────────────────────────────────────────────

SIGNAL_ORDER = [
    ("system", "duration_ms"), ("system", "error"), ("system", "total_tokens"), ("system", "est_cost_usd"),
    ("user", "quiz_score"), ("user", "weak_area_count"),
    ("tool", "tool_failures"), ("tool", "empty_searches"), ("tool", "parse_failures"), ("tool", "explainer_iterations"), ("tool", "a2a_unreachable"),
    ("grounding", "grounding"), ("grounding", "notes_overlap"), ("grounding", "refusal_rate"), ("grounding", "grader_agreement"),
    ("shape", "explanation_chars"), ("shape", "sentence_count"), ("shape", "hedging_rate"),
]


def _fmt(v: float, signal: str) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    if signal == "duration_ms":
        return f"{v / 1000:.0f}s"
    if signal == "est_cost_usd":
        return f"${v:.4f}"
    if signal in ("total_tokens", "explanation_chars"):
        return f"{v:,.0f}"
    return f"{v:.2f}"


def write_report(df: pd.DataFrame, rep: dict, path: Path, args: argparse.Namespace) -> str:
    base = df[df["scenario"] == "none"]
    sc = rep["scenarios"]
    names = [s for s in inject.SCENARIOS if s in sc] + [s for s in sc if s not in inject.SCENARIOS]
    st = rep["static_on_baseline"]
    chk = rep.get("monitor_on_baseline") or {}

    L: list[str] = []
    L.append("# Silent Drift lab — monitor report\n")
    L.append(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · baseline n={rep['baseline_n']} "
             f"(segments: {', '.join(rep['baseline_segments'])}) · drift sessions={int((df['scenario'] != 'none').sum())} · "
             f"Z_THRESH={args.z} · static threshold quiz_score<{st['threshold']}_\n")
    L.append("## Verdicts\n")
    L.append("| scenario | taxonomy | n | families moved (signals) | change-point | verdict | static threshold fired | system signals moved |")
    L.append("|---|---|---:|---|---|---|---|---|")
    for s in names:
        r = sc[s]
        fams = "; ".join(f"**{f}** ({', '.join(sig)})" for f, sig in r["families_moved"].items()) or "none"
        stat = r["static"]
        L.append(f"| {s} | {inject.TAXONOMY.get(s, '–')} | {r['n_window']} | {fams} | "
                 f"{'yes' if r['changepoint_confirmed'] else 'no'} | **{r['verdict'].upper()}** | "
                 f"{'yes' if stat.get('fired') else 'no'} (window median {stat.get('window_median', float('nan')):.2f}) | "
                 f"{', '.join(r['system_signals_moved']) or 'none'} |")
    L.append("")
    L.append("## The naive rule, for comparison\n")
    L.append(f"- Static threshold `quiz_score < {st['threshold']}` on the **healthy** baseline: "
             f"**{st['false_positives']} / {st['baseline_n']} sessions would have paged** "
             f"({st['false_positive_rate']:.0%} false-positive rate; they are the weak-persona students).")
    if chk:
        L.append(f"- The corroborating monitor on the same baseline, sliding 3-session windows: "
                 f"{chk['windows']} windows → ok {chk['ok']}, annotate {chk['annotate']}, ticket {chk['ticket']}, **page {chk['page']}**.")
    L.append("")
    L.append("## Scenario × signal\n")
    L.append("Baseline median → drifted median (robust z of the window). Bold = moved (|z| ≥ Z_THRESH). "
             "System signals are shown but never corroborate.\n")
    header = "| family | signal | baseline | " + " | ".join(names) + " |"
    L.append(header)
    L.append("|---|---|---|" + "---|" * len(names))
    for fam, sig in SIGNAL_ORDER:
        cells = []
        base_med = None
        for s in names:
            v = sc[s]["signals"].get(sig)
            if not v:
                cells.append("–")
                continue
            base_med = v["baseline_median"]
            cell = f"{_fmt(v['drift_median'], sig)} (z {v['z']:+.1f})"
            cells.append(f"**{cell}**" if v["moved"] else cell)
        L.append(f"| {fam} | {sig} | {_fmt(base_med, sig)} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("## Per-scenario detail\n")
    for s in names:
        r = sc[s]
        L.append(f"### {s} — {inject.SCENARIOS.get(s, '')}\n")
        L.append(f"- segment `{r['segment']}`{' (new segment: no baseline → input family moved)' if r['new_segment'] else ''}, "
                 f"window n={r['n_window']}, verdict **{r['verdict']}**, change-point confirmed: {r['changepoint_confirmed']}")
        cps = {k: v for k, v in r["changepoints"].items() if v["cusum_in_window"]}
        if cps:
            L.append("- CUSUM alarms inside the window on: " + ", ".join(f"{k} (idx {v['cusum_alarm_index']})" for k, v in cps.items()))
        L.append("- static threshold: fired=" + str(r["static"].get("fired")) + f", {r['static'].get('sessions_below', 0)}/{r['n_window']} sessions below")
        win = df[df["scenario"] == s]
        L.append("- sessions: " + ", ".join(f"`{row.session_id}` ({row.persona}, quiz {row.quiz_score:.2f}, ground {row.grounding:.2f}, {row.explanation_chars} chars, {row.duration_ms / 1000:.0f}s)" for row in win.itertuples()))
        L.append("")
    L.append("## Baseline population\n")
    L.append(f"- n={len(base)}; personas: {base['persona'].value_counts().to_dict()}")
    for sig in ("duration_ms", "total_tokens", "est_cost_usd", "quiz_score", "grounding", "explanation_chars", "hedging_rate", "tool_failures"):
        col = pd.to_numeric(base[sig], errors="coerce")
        L.append(f"- {sig}: median {_fmt(col.median(), sig)}, MAD {_fmt(monitor._mad(col), sig)}, min {_fmt(col.min(), sig)}, max {_fmt(col.max(), sig)}")
    text = "\n".join(L) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


# ─────────────────────────────────────────────────────────────────────────────
# 5. figure
# ─────────────────────────────────────────────────────────────────────────────

def draw_figure(df: pd.DataFrame, path: Path, dark: bool = True, scenario_order: list[str] | None = None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    drift = df[df["scenario"] != "none"]
    # Baseline sessions of the segment(s) the drift sessions belong to, in time order
    # (a different topic family has its own baseline and would only add noise here).
    segs = set(drift["segment"].unique()) or set(df["segment"].unique())
    base = df[(df["scenario"] == "none") & (df["segment"].isin(segs))].sort_values("timestamp")
    if len(base) < 5:
        base = df[df["scenario"] == "none"].sort_values("timestamp")
    order = [s for s in (scenario_order or list(inject.SCENARIOS)) if s in set(drift["scenario"])]
    order += [s for s in drift["scenario"].unique() if s not in order]
    parts = [base] + [drift[drift["scenario"] == s].sort_values("timestamp") for s in order]
    seq = pd.concat(parts, ignore_index=True)
    x = np.arange(len(seq))
    inj = len(base)

    bg, fg, grid = ("#0f1115", "#e6e6e6", "#2a2d34") if dark else ("#ffffff", "#1f1f1f", "#e3e3e3")
    muted = "#9aa0a6" if dark else "#6b7075"
    amber, amber2 = "#f2a900", "#ffd166"
    grey, green, red = ("#8f969e", "#3ddc97", "#ff6b6b") if dark else ("#6b7075", "#1b9e77", "#d62828")

    def roll(s: pd.Series, w: int = 3) -> pd.Series:
        return s.rolling(w, min_periods=1, center=True).median()

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True, facecolor=bg, gridspec_kw={"hspace": 0.22})
    for ax in (ax1, ax2):
        ax.set_facecolor(bg)
        for sp in ax.spines.values():
            sp.set_color(grid)
        ax.tick_params(colors=muted, labelsize=9)
        ax.yaxis.label.set_color(fg)
        ax.xaxis.label.set_color(fg)
        ax.grid(True, color=grid, linewidth=0.6, alpha=0.8)
        ax.set_axisbelow(True)

    # ── top: system health, indexed to the baseline median (1.0 = baseline) ──
    def idx(col: str) -> pd.Series:
        med = pd.to_numeric(base[col], errors="coerce").median()
        return pd.to_numeric(seq[col], errors="coerce") / (med if med else 1.0)

    lat, cost = idx("duration_ms"), idx("est_cost_usd")
    ax1.plot(x, roll(lat), color=grey, lw=2, label="latency (÷ baseline median)")
    ax1.scatter(x, lat, color=grey, s=14, alpha=0.5)
    ax1.plot(x, roll(cost), color=green, lw=2, label="cost / tokens (÷ baseline median)")
    ax1.scatter(x, cost, color=green, s=14, alpha=0.5)
    err = pd.to_numeric(seq["error"], errors="coerce").fillna(0) > 0
    ax1.scatter(x[err], np.full(err.sum(), 1.0), marker="x", color=red, s=50, label=f"session error ({int(err.sum())})", zorder=5)
    ax1.axhline(1.0, color=muted, lw=0.8, ls=":")
    ax1.set_ylim(0, max(2.2, float(np.nanmax(np.concatenate([lat.to_numpy(), cost.to_numpy()]))) * 1.1))
    ax1.set_ylabel("system health (indexed)")
    ax1.set_title("What the dashboard saw: latency, errors, cost — flat", color=fg, loc="left", fontsize=12, fontweight="bold", pad=8)

    # ── bottom: quality ────────────────────────────────────────────────────
    q = pd.to_numeric(seq["quiz_score"], errors="coerce")
    g = pd.to_numeric(seq["grounding"], errors="coerce")
    ax2.plot(x, roll(q), color=amber, lw=2, label="quiz score (rolling median)")
    ax2.scatter(x, q, color=amber, s=14, alpha=0.55)
    ax2.plot(x, roll(g), color=amber2, lw=2, ls="--", label="grounding proxy (rolling median)")
    ax2.scatter(x, g, color=amber2, s=14, alpha=0.55, marker="s")
    for col, lab in ((q, "quiz"), (g, "grounding")):
        med = col.iloc[:inj].median()
        ax2.axhline(med, color=amber if lab == "quiz" else amber2, lw=0.8, ls=":", alpha=0.7)
    ax2.set_ylim(-0.02, 1.05)
    ax2.set_ylabel("behavioural quality")
    ax2.set_xlabel("sessions, in time order")
    ax2.set_title("What the monitor saw: quiz score and grounding — falling (or changing shape)", color=fg, loc="left", fontsize=12, fontweight="bold", pad=8)

    # ── injection point + scenario bands ───────────────────────────────────
    pos = inj
    for ax in (ax1, ax2):
        ax.axvline(inj - 0.5, color=red, lw=1.4, ls="--")
    ax1.text(inj - 0.3, ax1.get_ylim()[1] * 0.97, "injection →", color=red, fontsize=9, va="top")
    for i, s in enumerate(order):
        n = int((drift["scenario"] == s).sum())
        for ax in (ax1, ax2):
            ax.axvspan(pos - 0.5, pos + n - 0.5, color=amber if i % 2 == 0 else grey, alpha=0.07 if dark else 0.08, lw=0)
        ax2.text(pos + n / 2 - 0.5, 1.03, s.replace("_", "\n"), color=muted, fontsize=8, ha="center", va="top")
        pos += n
    ax2.text(inj / 2, 1.03, f"baseline · {'/'.join(sorted(base['segment'].unique()))} (n={inj})", color=muted, fontsize=8, ha="center", va="top")

    for ax, loc in ((ax1, "upper left"), (ax2, "lower left")):
        leg = ax.legend(loc=loc, fontsize=8, frameon=True, facecolor=bg, edgecolor=grid, labelcolor=fg, ncol=3)
        leg.get_frame().set_alpha(0.9)
    fig.suptitle("Silent drift: system signals flat, quality signals fall", color=fg, x=0.01, ha="left", fontsize=14, fontweight="bold", y=0.985)
    fig.text(0.99, 0.005, "Learning Accelerator · Opik · local qwen2.5 · drift lab", color=muted, fontsize=8, ha="right")
    fig.savefig(path, dpi=150, facecolor=bg, bbox_inches="tight")
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# 3+4+5. analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyse(args: argparse.Namespace) -> dict:
    REPORTS.mkdir(exist_ok=True)
    print("[lab] pulling sessions from Opik …")
    df = signals.fetch_sessions(include_tags=["seed", "baseline", "drift:"], exclude_tags=["smoke", "demo", "probe", "otel", "mode:"])
    if df.empty:
        raise SystemExit("[lab] no sessions found in Opik")
    df.loc[df["scenario"].isna(), "scenario"] = "none"
    # Unfinished or crashed-before-scoring sessions carry no feedback scores: drop them.
    unfinished = df["quiz_score"].isna()
    if unfinished.any():
        print(f"[lab] dropping {int(unfinished.sum())} unfinished session(s): {', '.join(df.loc[unfinished, 'session_id'])}")
        df = df[~unfinished].reset_index(drop=True)
    # The lab's own run log says which drift sessions belong to this run.
    if not args.all_drift and DRIFT_RUNS.exists():
        mine = {json.loads(ln)["session_id"] for ln in DRIFT_RUNS.read_text(encoding="utf-8").splitlines() if ln.strip()}
        stray = (df["scenario"] != "none") & ~df["session_id"].isin(mine)
        if stray.any():
            print(f"[lab] ignoring {int(stray.sum())} drift session(s) not in data/drift-runs.jsonl (use --all-drift to keep them)")
            df = df[~stray].reset_index(drop=True)
    df.to_csv(REPORTS / "drift-signals.csv", index=False)
    print(f"[lab] {len(df)} sessions → reports/drift-signals.csv "
          f"(baseline {int((df['scenario'] == 'none').sum())}, drift {int((df['scenario'] != 'none').sum())})")

    monitor.STATIC_THRESHOLD = args.static_threshold
    rep = monitor.run_monitor(df, z_thresh=args.z)
    rep["static_on_baseline"] = monitor.static_threshold(df[df["scenario"] == "none"], threshold=args.static_threshold)
    (REPORTS / "drift-report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    text = write_report(df, rep, REPORTS / "drift-report.md", args)
    print(text.split("## The naive rule")[0])
    if not df[df["scenario"] != "none"].empty:
        draw_figure(df, REPORTS / "drift-figure.png", dark=True)
        draw_figure(df, REPORTS / "drift-figure-light.png", dark=False)
        print("[lab] figures → reports/drift-figure.png, reports/drift-figure-light.png")
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description="Silent Drift lab: inject, measure, monitor, report.")
    ap.add_argument("--n", type=int, default=int(os.getenv("DRIFT_LAB_N", "3")), help="sessions per scenario")
    ap.add_argument("--quick", action="store_true", help="one session per scenario")
    ap.add_argument("--scenarios", nargs="*", default=list(inject.SCENARIOS), choices=list(inject.SCENARIOS))
    ap.add_argument("--analyse-only", action="store_true", help="skip all runs; rebuild CSV, report and figure from Opik")
    ap.add_argument("--runs-only", action="store_true", help="run the injections now, analyse later (do not wait for the baseline)")
    ap.add_argument("--resume", action="store_true", help="skip scenario runs already recorded in data/drift-runs.jsonl")
    ap.add_argument("--all-drift", action="store_true", help="analyse every drift:* session in Opik, not only those in data/drift-runs.jsonl")
    ap.add_argument("--wait-min", type=float, default=10.0, help="minutes to wait for data/seed-runs.jsonl")
    ap.add_argument("--min-baseline", type=int, default=MIN_BASELINE)
    ap.add_argument("--z", type=float, default=monitor.Z_THRESH, help="robust-z threshold for a moved signal")
    ap.add_argument("--static-threshold", type=float, default=monitor.STATIC_THRESHOLD)
    ap.add_argument("--tag", action="append", default=[], help="extra tag for the drift sessions (repeatable)")
    ap.add_argument("--verbose", action="store_true", help="print the full session transcripts")
    args = ap.parse_args()
    n = 1 if args.quick else args.n

    if not args.analyse_only:
        if not args.runs_only:
            wait_for_baseline(args.min_baseline, args.wait_min, quiet=not args.verbose)
        run_scenarios(args.scenarios, n, quiet=not args.verbose, extra_tags=args.tag, resume=args.resume)
        print("[lab] letting Opik ingest the last traces …")
        time.sleep(5)
    if not args.runs_only:
        analyse(args)


if __name__ == "__main__":
    main()
