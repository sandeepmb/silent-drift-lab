"""
drift/monitor.py — the monitor that catches silent drift.

Pure pandas/numpy: no LLM, no Opik. Input is the DataFrame drift/signals.py
produces (one row per session). Rules:

1. Per-segment rolling baseline. For every signal, median and MAD over the
   baseline population, keyed by (segment, persona) when that stratum has at
   least MIN_STRATUM sessions, else by segment, else global.

2. Robust deviation. Each session gets z = (x - median) / scale, where
   scale = max(1.4826 * MAD, floor). The floor keeps all-zero baselines
   (errors, tool failures) from turning one blip into infinity.

3. Change-point test. CUSUM on the z-series of baseline sessions followed by
   the window, with drift k and threshold h. The change point is "confirmed"
   when the alarm lands inside the window. A two-window mean-shift test is
   also reported.

4. Two-signal corroboration. Signals belong to families:
     user       quiz_score, weak_area_count          (user / behavioural quality)
     tool       tool_failures, empty_searches, parse_failures, explainer_iterations,
                a2a_unreachable (downstream)
     grounding  grounding, notes_overlap, refusal_rate, grader_agreement (agreement / grounding)
     shape      explanation_chars, sentence_count, hedging_rate (output shape)
     input      new segment with no baseline        (traffic)
   A family "moves" when the window's median z of any of its signals crosses
   Z_THRESH. System signals (latency, errors, tokens, cost) are reported but
   never corroborate: they are the signals that stay flat.

5. Tiered verdict.
     ok        nothing moved
     annotate  one family moved (write it on the trace, nobody wakes up)
     ticket    two or more families moved, no change-point confirmation
     page      two or more families moved AND CUSUM confirms a change point
   A window from a segment that has no baseline yet can ticket but never
   page: without a baseline there is nothing to confirm a change against,
   and the ticket is "build the baseline".

6. The naive comparison: a static threshold on quiz_score alone. Reported as
   the number of baseline sessions it would have paged on (false positives)
   and whether it fires on each drift window.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Signals that a session may legitimately lack (grader_agreement before the A2A
# re-grade existed) are NaN; pandas' median of an all-NaN column warns. Quiet it.
warnings.filterwarnings("ignore", category=RuntimeWarning, message="Mean of empty slice")

FAMILIES: dict[str, list[str]] = {
    "system": ["duration_ms", "error", "total_tokens", "est_cost_usd"],
    "user": ["quiz_score", "weak_area_count"],
    "tool": ["tool_failures", "empty_searches", "parse_failures", "explainer_iterations", "a2a_unreachable"],
    "grounding": ["grounding", "notes_overlap", "refusal_rate", "grader_agreement"],
    "shape": ["explanation_chars", "sentence_count", "hedging_rate"],
}
CORROBORATING_FAMILIES = ["user", "tool", "grounding", "shape", "input"]
ALL_SIGNALS = [s for sigs in FAMILIES.values() for s in sigs]

# Floor on the robust scale, per signal (units of the signal). Prevents a
# baseline with MAD == 0 from flagging any non-zero value as infinite drift,
# and keeps small-integer counts (iterations, weak areas) from paging on a
# one-unit wobble: with floor f and Z_THRESH 2, a shift of 2f is needed.
SCALE_FLOOR: dict[str, float] = {
    "duration_ms": 5_000.0, "error": 0.5, "total_tokens": 300.0, "est_cost_usd": 0.0002,
    "quiz_score": 0.08, "weak_area_count": 1.0,
    "tool_failures": 0.5, "empty_searches": 0.5, "parse_failures": 0.5, "explainer_iterations": 1.0,
    "grounding": 0.04, "notes_overlap": 0.04, "refusal_rate": 0.25, "grader_agreement": 0.08, "a2a_unreachable": 0.5,
    "explanation_chars": 150.0, "sentence_count": 2.0, "hedging_rate": 0.3,
}

Z_THRESH = 2.0        # |median z| of the window that counts as "moved"
MIN_STRATUM = 5       # sessions needed before a (segment, persona) stratum is trusted
MIN_SEGMENT = 5       # sessions needed before a segment baseline is trusted
CUSUM_K = 0.5
CUSUM_H = 4.0
STATIC_THRESHOLD = 0.6  # the naive rule: page when quiz_score < this


# ─────────────────────────────────────────────────────────────────────────────
# baseline
# ─────────────────────────────────────────────────────────────────────────────

def _mad(x: pd.Series) -> float:
    x = x.dropna()
    if x.empty:
        return float("nan")
    med = float(x.median())
    return float((x - med).abs().median())


def _stats(df: pd.DataFrame, signals: list[str]) -> dict[str, dict[str, float]]:
    out = {}
    for s in signals:
        if s not in df:
            continue
        col = pd.to_numeric(df[s], errors="coerce")
        out[s] = {"median": float(col.median()), "mad": _mad(col), "n": int(col.notna().sum())}
    return out


@dataclass
class Baseline:
    """Median/MAD per signal at three levels: (segment, persona), segment, global."""
    global_stats: dict[str, dict[str, float]]
    segment_stats: dict[str, dict[str, dict[str, float]]]
    stratum_stats: dict[tuple[str, str], dict[str, dict[str, float]]]
    segments: set[str] = field(default_factory=set)
    n: int = 0

    def lookup(self, signal: str, segment: str, persona: str | None = None) -> tuple[dict[str, float], str]:
        """Stats for one signal plus the level they came from."""
        if persona is not None:
            st = self.stratum_stats.get((segment, persona))
            if st and signal in st and st[signal]["n"] >= MIN_STRATUM:
                return st[signal], "segment+persona"
        sg = self.segment_stats.get(segment)
        if sg and signal in sg and sg[signal]["n"] >= MIN_SEGMENT:
            return sg[signal], "segment"
        return self.global_stats[signal], "global"

    def has_segment(self, segment: str) -> bool:
        sg = self.segment_stats.get(segment)
        return bool(sg) and any(v["n"] >= MIN_SEGMENT for v in sg.values())


def build_baseline(df_base: pd.DataFrame, signals: list[str] = ALL_SIGNALS) -> Baseline:
    signals = [s for s in signals if s in df_base]
    seg = {s: _stats(g, signals) for s, g in df_base.groupby("segment")}
    strat = {}
    if "persona" in df_base:
        strat = {k: _stats(g, signals) for k, g in df_base.groupby(["segment", "persona"])}
    return Baseline(_stats(df_base, signals), seg, strat, set(df_base["segment"].unique()), len(df_base))


def robust_z(x: float, median: float, mad: float, floor: float = 0.0) -> float:
    if x is None or np.isnan(x) or np.isnan(median):
        return float("nan")
    scale = max(1.4826 * (0.0 if np.isnan(mad) else mad), floor, 1e-12)
    return (x - median) / scale


def score_sessions(df: pd.DataFrame, baseline: Baseline, signals: list[str] = ALL_SIGNALS) -> pd.DataFrame:
    """Add a z_<signal> column per signal, standardised against the session's stratum baseline."""
    df = df.copy()
    for s in signals:
        if s not in df:
            continue
        zs = []
        for _, row in df.iterrows():
            st, _level = baseline.lookup(s, row["segment"], row.get("persona"))
            zs.append(robust_z(float(row[s]), st["median"], st["mad"], SCALE_FLOOR.get(s, 0.0)))
        df[f"z_{s}"] = zs
    return df


# ─────────────────────────────────────────────────────────────────────────────
# change detection
# ─────────────────────────────────────────────────────────────────────────────

def cusum(z: np.ndarray | list[float], k: float = CUSUM_K, h: float = CUSUM_H) -> tuple[int | None, np.ndarray, np.ndarray]:
    """
    Two-sided CUSUM on an already standardised series. Returns the index of the
    first alarm (or None) and the S+ / S- paths.
    """
    z = np.asarray(z, dtype=float)
    sp = np.zeros(len(z))
    sn = np.zeros(len(z))
    alarm = None
    for i, v in enumerate(z):
        if np.isnan(v):
            v = 0.0
        sp[i] = max(0.0, (sp[i - 1] if i else 0.0) + v - k)
        sn[i] = max(0.0, (sn[i - 1] if i else 0.0) - v - k)
        if alarm is None and (sp[i] > h or sn[i] > h):
            alarm = i
    return alarm, sp, sn


def two_window_shift(series: np.ndarray | list[float], split: int, threshold: float = Z_THRESH) -> tuple[bool, float]:
    """
    Mean shift between series[:split] and series[split:], in units of the
    before-window's robust scale. True when |shift| >= threshold.
    """
    x = np.asarray(series, dtype=float)
    before, after = x[:split], x[split:]
    before, after = before[~np.isnan(before)], after[~np.isnan(after)]
    if len(before) < 2 or len(after) < 1:
        return False, float("nan")
    med = np.median(before)
    mad = np.median(np.abs(before - med))
    scale = max(1.4826 * mad, 1e-9)
    shift = (np.median(after) - med) / scale
    return bool(abs(shift) >= threshold), float(shift)


# ─────────────────────────────────────────────────────────────────────────────
# corroboration + verdict
# ─────────────────────────────────────────────────────────────────────────────

def families_moved(window_z: dict[str, float], z_thresh: float = Z_THRESH, new_segment: bool = False) -> dict[str, list[str]]:
    """Which corroborating families moved, and on which signals."""
    moved: dict[str, list[str]] = {}
    for fam, sigs in FAMILIES.items():
        if fam == "system":
            continue
        hits = [s for s in sigs if s in window_z and not np.isnan(window_z[s]) and abs(window_z[s]) >= z_thresh]
        if hits:
            moved[fam] = hits
    if new_segment:
        moved["input"] = ["segment"]
    return moved


def verdict(moved: dict[str, list[str]], changepoint: bool, new_segment: bool = False) -> str:
    n = len(moved)
    if n == 0:
        return "ok"
    if n == 1:
        return "annotate"
    return "page" if (changepoint and not new_segment) else "ticket"


def static_threshold(df_base: pd.DataFrame, df_window: pd.DataFrame | None = None, threshold: float = STATIC_THRESHOLD) -> dict:
    """What 'alert if quiz_score < threshold' would have done."""
    base = pd.to_numeric(df_base["quiz_score"], errors="coerce").dropna()
    fp = int((base < threshold).sum())
    out = {"threshold": threshold, "baseline_n": int(len(base)), "false_positives": fp,
           "false_positive_rate": (fp / len(base)) if len(base) else float("nan")}
    if df_window is not None and not df_window.empty:
        w = pd.to_numeric(df_window["quiz_score"], errors="coerce").dropna()
        out["window_median"] = float(w.median()) if len(w) else float("nan")
        out["fired"] = bool(len(w) and w.median() < threshold)
        out["sessions_below"] = int((w < threshold).sum())
    return out


# ─────────────────────────────────────────────────────────────────────────────
# evaluate one window against the baseline
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_window(
    df_base: pd.DataFrame,
    df_window: pd.DataFrame,
    baseline: Baseline | None = None,
    z_thresh: float = Z_THRESH,
    signals: list[str] = ALL_SIGNALS,
) -> dict:
    """
    Compare a window of sessions (one scenario) with the baseline population.
    Returns per-signal stats, the families that moved, change-point results
    and the verdict.
    """
    baseline = baseline or build_baseline(df_base, signals)
    segments = set(df_window["segment"].unique())
    new_segment = any(not baseline.has_segment(s) for s in segments)
    segment = sorted(segments)[0] if segments else ""

    zb = score_sessions(df_base, baseline, signals)
    zw = score_sessions(df_window, baseline, signals)

    per_signal: dict[str, dict] = {}
    changepoints: dict[str, dict] = {}
    for s in signals:
        if s not in df_window:
            continue
        st, level = baseline.lookup(s, segment)
        base_vals = pd.to_numeric(df_base.loc[df_base["segment"] == segment, s], errors="coerce") if baseline.has_segment(segment) else pd.to_numeric(df_base[s], errors="coerce")
        wz = pd.to_numeric(zw[f"z_{s}"], errors="coerce")
        wvals = pd.to_numeric(df_window[s], errors="coerce")
        z_med = float(wz.median()) if wz.notna().any() else float("nan")
        per_signal[s] = {
            "baseline_median": float(base_vals.median()) if base_vals.notna().any() else float("nan"),
            "baseline_mad": _mad(base_vals),
            "drift_median": float(wvals.median()) if wvals.notna().any() else float("nan"),
            "z": z_med,
            "moved": bool(not np.isnan(z_med) and abs(z_med) >= z_thresh),
            "level": level,
        }
        # change point on baseline (time order) followed by the window
        seq = np.concatenate([
            pd.to_numeric(zb.sort_values("timestamp")[f"z_{s}"], errors="coerce").to_numpy(),
            wz.to_numpy(),
        ])
        alarm, _, _ = cusum(seq)
        shifted, shift = two_window_shift(
            np.concatenate([pd.to_numeric(df_base.sort_values("timestamp")[s], errors="coerce").to_numpy(), wvals.to_numpy()]),
            split=len(df_base), threshold=z_thresh,
        )
        changepoints[s] = {
            "cusum_alarm_index": alarm,
            "cusum_in_window": bool(alarm is not None and alarm >= len(df_base)),
            "two_window_shift": shift,
            "two_window_moved": shifted,
        }

    window_z = {s: v["z"] for s, v in per_signal.items()}
    moved = families_moved(window_z, z_thresh, new_segment=new_segment)
    moved_signals = [s for sigs in moved.values() for s in sigs if s != "segment"]
    changepoint = any(changepoints[s]["cusum_in_window"] for s in moved_signals if s in changepoints)
    system_moved = [s for s in FAMILIES["system"] if s in per_signal and per_signal[s]["moved"]]

    return {
        "segment": segment,
        "new_segment": new_segment,
        "n_window": int(len(df_window)),
        "n_baseline": int(len(df_base)),
        "signals": per_signal,
        "changepoints": changepoints,
        "families_moved": moved,
        "changepoint_confirmed": changepoint,
        "system_signals_moved": system_moved,
        "verdict": verdict(moved, changepoint, new_segment),
        "static": static_threshold(df_base, df_window),
    }


def baseline_self_check(df_base: pd.DataFrame, window: int = 3, z_thresh: float = Z_THRESH) -> dict:
    """
    Slide a window over the baseline itself (each window scored against the rest)
    and count how often the monitor would have paged: the monitor's own
    false-positive rate, to set beside the static threshold's.
    """
    df = df_base.sort_values("timestamp").reset_index(drop=True)
    verdicts = []
    for start in range(0, max(len(df) - window + 1, 0)):
        win = df.iloc[start:start + window]
        rest = df.drop(win.index)
        if len(rest) < MIN_SEGMENT:
            continue
        verdicts.append(evaluate_window(rest, win, z_thresh=z_thresh)["verdict"])
    counts = {v: verdicts.count(v) for v in ("ok", "annotate", "ticket", "page")}
    return {"windows": len(verdicts), **counts}


def run_monitor(df: pd.DataFrame, baseline_scenarios: tuple[str, ...] = ("none",), z_thresh: float = Z_THRESH) -> dict:
    """Evaluate every drift scenario in `df` against the baseline sessions."""
    df = df.copy()
    df["scenario"] = df["scenario"].fillna("none")
    base = df[df["scenario"].isin(baseline_scenarios)]
    baseline = build_baseline(base)
    results = {}
    for scenario, win in df[~df["scenario"].isin(baseline_scenarios)].groupby("scenario"):
        results[scenario] = evaluate_window(base, win, baseline, z_thresh=z_thresh)
    return {
        "baseline_n": int(len(base)),
        "baseline_segments": sorted(base["segment"].unique()),
        "static_on_baseline": static_threshold(base),
        "monitor_on_baseline": baseline_self_check(base, z_thresh=z_thresh) if len(base) >= MIN_SEGMENT + 3 else {},
        "scenarios": results,
    }
