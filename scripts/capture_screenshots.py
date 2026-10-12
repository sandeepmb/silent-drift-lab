#!/usr/bin/env python3
"""Capture the handbook screenshots from a running Opik UI.

    pip install playwright pillow && playwright install chromium
    python scripts/capture_screenshots.py                       # all shots
    python scripts/capture_screenshots.py --only 10a 4 6        # a subset
    python scripts/capture_screenshots.py --base-url http://localhost:5173 \
        --project silent-drift-lab --workspace default --out docs/img

Read-only: it only issues GET requests against the Opik REST API and browses
the UI. It never starts a model session and never writes to Opik.

Shots (file name -> what it shows):
  01 trace list              02 trace detail + span tree + LLM span
  03 trace with graph panel  04 traces table with feedback-score columns
  05 OpenTelemetry-track trace, LLM span open
  06 filter quiz_score < 0.5 07 dataset + versions tab
  08 two experiments compared  09 online evaluation rules
  10a alert webhook terminal render (no Opik needed)
  10b Configuration -> Alerts page

Status: 10a is rendered from data/alert_webhook.log and needs no Opik backend.
The UI shots (01-09, 10b) discover ids through the REST API and deep-link; the
selectors are best effort and should be re-checked against the running Opik
version (written against 2.2.x). Use --headed to watch.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 1_000_000  # freeCodeCamp image limit
VIEWPORT = {"width": 1440, "height": 900}


# --------------------------------------------------------------------------- API
class Api:
    def __init__(self, base: str, workspace: str):
        self.base, self.workspace = base.rstrip("/"), workspace

    def get(self, path: str, **params):
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(
            f"{self.base}/api/v1/private/{path}" + (f"?{qs}" if qs else ""),
            headers={"Comet-Workspace": self.workspace, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def project_id(self, name: str) -> str:
        for p in self.get("projects", name=name, size=50).get("content", []):
            if p["name"] == name:
                return p["id"]
        raise SystemExit(f"project {name!r} not found")


def sanitize(text: str) -> str:
    """Remove absolute home-directory paths so screenshots carry no username."""
    homes = re.escape(str(Path.home().parent))  # the directory that holds user homes on this OS
    text = re.sub(homes + r"/[^/\s\"']+/(?:[^\s\"']*/)?(data|scripts|docs)/", r"\1/", text)
    return re.sub(homes + r"/[^/\s\"']+", "~", text)


# ------------------------------------------------------------------ terminal shot
TERM_CSS = """
body{margin:0;background:#1e1f26;font:15px/1.45 'SF Mono',Menlo,Consolas,'DejaVu Sans Mono',monospace;color:#d7dae0}
.win{width:1100px;border-radius:10px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.35)}
.bar{background:#2b2d36;padding:10px 14px;display:flex;gap:8px;align-items:center;color:#8b90a0;font-size:13px}
.dot{width:12px;height:12px;border-radius:50%}
pre{margin:0;padding:16px 20px 20px;white-space:pre-wrap;word-break:break-word}
.p{color:#7ee787}.h{color:#79c0ff;font-weight:600}.k{color:#ffa657}.d{color:#8b90a0}
"""


def last_alert_blocks(log: str, n: int) -> list[str]:
    parts = re.split(r"(?m)^(?=\[alert\] )", log)
    return [p.rstrip() for p in parts if p.startswith("[alert] ")][-n:]


def render_terminal(log_path: Path, alerts_path: Path, project: str, out: Path, n: int, browser) -> None:
    log = sanitize(log_path.read_text())
    blocks = last_alert_blocks(log, n)
    listen = next((ln for ln in log.splitlines() if ln.startswith("[alert_webhook]")), "")
    rows = []
    if alerts_path.exists():
        for line in alerts_path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    mine = [r for r in rows if project in json.dumps(r.get("body", {}))][-n:]

    def colour_json(s: str) -> str:
        s = html.escape(s)
        return re.sub(r'(&quot;[\w_]+&quot;)(:)', r'<span class="k">\1</span>\2', s)

    body = ['<span class="p">$</span> python scripts/alert_webhook.py', f'<span class="d">{html.escape(listen)}</span>', ""]
    for b in blocks:
        head, _, rest = b.partition("\n")
        body.append(f'<span class="h">{html.escape(head)}</span>')
        body.append(colour_json(rest))
        body.append("")
    body.append(f'<span class="d"># {len(mine)} delivery(ies) for project "{html.escape(project)}" in data/alerts.jsonl</span>')
    doc = (f"<html><head><style>{TERM_CSS}</style></head><body><div class='win'><div class='bar'>"
           "<span class='dot' style='background:#ff5f56'></span><span class='dot' style='background:#ffbd2e'></span>"
           "<span class='dot' style='background:#27c93f'></span><span style='margin-left:10px'>alert_webhook.py</span></div>"
           f"<pre>{chr(10).join(body)}</pre></div></body></html>")
    ctx = browser.new_context(viewport={"width": 1100, "height": 400}, device_scale_factor=2)
    page = ctx.new_page()
    page.set_content(doc)
    page.locator(".win").screenshot(path=str(out))
    ctx.close()


# ------------------------------------------------------------------------- UI shots
SCROLL_PX = 1400
HSCROLL_PX = 5000
SETTLE_MS = 6000  # Opik polls, so "networkidle" never fires; wait for skeletons to clear
TRACE_COLS_PLAIN = ["start_time", "input", "output", "duration", "usage.total_tokens", "total_estimated_cost", "tags"]
TRACE_COLS_SCORES = ["start_time", "duration", "usage.total_tokens",
                     "feedback_scores.quiz_score", "feedback_scores.topics_completed",
                     "feedback_scores.weak_area_count"]


class Shooter:
    def __init__(self, args, browser):
        self.a, self.browser = args, browser
        self.api = Api(args.base_url, args.workspace)
        self.out = Path(args.out)
        self.pid = None

    # -- plumbing -----------------------------------------------------------
    def project(self) -> str:
        if not self.pid:
            self.pid = self.api.project_id(self.a.project)
        return self.pid

    def url(self, path: str) -> str:
        return f"{self.a.base_url.rstrip('/')}/{self.a.workspace}/projects/{self.project()}/{path.lstrip('/')}"

    def open(self, path: str, cols=None, wait_text=None):
        """New light-theme 1440x900@2x context, navigate, dismiss popups."""
        ctx = self.browser.new_context(viewport=VIEWPORT, device_scale_factor=2, color_scheme="light")
        ls = {"mcp-announcement-dismissed-v1": "true"}
        if cols:
            ls["traces-selected-columns-v2"] = json.dumps(cols)
            ls["traces-columns-order"] = json.dumps(["id", *cols])
            scores = [c for c in cols if c.startswith("feedback_scores.")]
            ls["traces-dynamic-columns"] = json.dumps(scores)
            ls["traces-scores-columns-order"] = json.dumps(scores)
        ctx.add_init_script("try{const o=%s;for(const k in o)localStorage.setItem(k,o[k])}catch(e){}" % json.dumps(ls))
        p = ctx.new_page()
        p.goto(self.url(path), wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(SETTLE_MS)
        skip = p.get_by_text("Skip and go to Opik")
        if skip.count():  # first-run welcome dialog
            skip.first.click()
            p.wait_for_timeout(1000)
        try:  # metric tiles read "N/A" until the (sometimes slow) queries return
            p.wait_for_function("() => !document.body.innerText.includes('N/A')", timeout=45000)
        except Exception:
            pass
        if wait_text:  # a row/label that proves the table has rendered
            p.get_by_text(wait_text).first.wait_for(timeout=45000)
        p.wait_for_timeout(1500)
        return ctx, p

    def save(self, page, name: str, clip=None):
        path = self.out / name
        page.screenshot(path=str(path), clip=clip)
        shrink(path)
        print("saved", path.name, path.stat().st_size // 1024, "KiB")

    def traces(self, flt=None, size=100):
        params = {"project_id": self.project(), "size": size}
        if flt:
            params["filters"] = json.dumps(flt)
        return self.api.get("traces", **params).get("content", [])

    def llm_span(self, trace_id: str):
        spans = self.api.get("spans", project_id=self.project(), trace_id=trace_id, type="llm", size=5).get("content", [])
        return spans[0] if spans else None

    def native_trace(self):
        """A complete native-track session trace with a readable span tree."""
        flt = [{"field": "tags", "operator": "contains", "value": "baseline"}]
        for t in self.traces(flt):
            if 30 <= (t.get("span_count") or 0) <= 40 and not t.get("error_info"):
                return t
        raise SystemExit("no suitable native-track trace")

    def collapse_graph(self, p):
        """Collapse the 'Agent graph' panel at the bottom of the span tree."""
        hdr = p.get_by_text("Agent graph")
        if hdr.count():  # traces without a graph definition (e.g. the OTel track) have no panel
            hdr.first.locator("xpath=ancestor::div[1]").locator("button").last.click()
            p.wait_for_timeout(800)

    # -- shots --------------------------------------------------------------
    def shot_1(self):
        ctx, p = self.open("logs?logsType=traces", cols=TRACE_COLS_PLAIN)
        self.save(p, "shot-01-trace-list.png")
        ctx.close()

    def shot_2(self):
        t = self.native_trace()
        ctx, p = self.open(f"logs?logsType=traces&trace={t['id']}")
        self.collapse_graph(p)
        p.get_by_text("ChatOllama").nth(3).click()  # last explainer call: has a text answer
        p.wait_for_timeout(1500)
        self.save(p, "shot-02-trace-detail.png")
        ctx.close()

    def shot_3(self):
        t = self.native_trace()
        ctx, p = self.open(f"logs?logsType=traces&trace={t['id']}")
        # The Agent graph panel is open by default; drag its splitter up to give it room.
        p.mouse.move(580, 575)
        p.mouse.down()
        p.mouse.move(580, 330, steps=8)
        p.mouse.up()
        p.wait_for_timeout(1000)
        self.save(p, "shot-03-trace-graph.png")
        ctx.close()

    def shot_3b(self):
        """Same trace, graph expanded to a dialog (the panel's expand button)."""
        t = self.native_trace()
        ctx, p = self.open(f"logs?logsType=traces&trace={t['id']}")
        p.get_by_text("Agent graph").first.locator("xpath=ancestor::div[1]").locator("button").first.click()
        p.wait_for_timeout(2000)
        self.save(p, "shot-03b-trace-graph-expanded.png")
        ctx.close()

    def shot_4(self):
        ctx, p = self.open("logs?logsType=traces", cols=TRACE_COLS_SCORES)
        self.save(p, "shot-04-feedback-columns.png")
        ctx.close()

    def shot_5(self):
        flt = [{"field": "tags", "operator": "contains", "value": "otel-track"}]
        otel = self.traces(flt)
        if not otel:
            raise SystemExit("no trace tagged otel-track")
        t = max(otel, key=lambda x: x.get("span_count") or 0)
        # a short LLM span, so input/output/metadata/usage all fit without scrolling
        spans = self.api.get("spans", project_id=self.project(), trace_id=t["id"], type="llm", size=50).get("content", [])
        spans = [x for x in spans if (x.get("usage") or {}).get("total_tokens")]
        span = min(spans, key=lambda x: x["usage"]["total_tokens"])
        ctx, p = self.open(f"logs?logsType=traces&trace={t['id']}&span={span['id']}")
        self.collapse_graph(p)
        p.get_by_role("tab", name="Details").or_(p.get_by_text("Details", exact=True)).first.click()
        p.wait_for_timeout(1500)
        self.save(p, "shot-05-otel-llm-span.png")
        ctx.close()

    def shot_6(self):
        flt = [{"id": "f1", "field": "feedback_scores", "key": "quiz_score", "operator": "<", "value": "0.5"}]
        q = urllib.parse.quote(json.dumps(flt))
        ctx, p = self.open(f"logs?logsType=traces&traces_filters={q}", cols=TRACE_COLS_SCORES)
        self.save(p, "shot-06-filter-quiz-score.png")
        ctx.close()

    def shot_7(self):
        ds = next(d for d in self.api.get("datasets", size=100)["content"] if d["name"] == "learning-accelerator-sessions")
        ctx, p = self.open(f"datasets/{ds['id']}/items")
        p.get_by_role("tab", name=re.compile("Version", re.I)).first.click()
        p.wait_for_timeout(2500)
        self.save(p, "shot-07-dataset-versions.png", clip={"x": 0, "y": 0, "width": 1440, "height": 560})
        ctx.close()

    def shot_8(self):
        exps = self.api.get("experiments", size=50)["content"]
        mine = [e for e in exps if e.get("dataset_name") == "learning-accelerator-sessions"]
        # the 7b and 3b grader runs (newest of each)
        pick = []
        for tag in ("qwen2.5-7b", "qwen2.5-3b"):
            e = next((e for e in mine if tag in e["name"]), None)
            if e:
                pick.append(e)
        if len(pick) < 2:
            raise SystemExit("need two experiments on the dataset")
        q = urllib.parse.quote(json.dumps([e["id"] for e in pick]))
        ctx, p = self.open(f"experiments/{pick[0]['dataset_id']}/compare?experiments={q}")
        p.mouse.move(700, 600)  # scroll right so the per-item score columns sit beside the experiment names
        p.mouse.wheel(HSCROLL_PX, 0)
        p.wait_for_timeout(5000)
        p.mouse.move(1000, 20)  # park the pointer so no row is hover-highlighted
        p.wait_for_timeout(500)
        self.save(p, "shot-08-experiments-compare.png")
        p.mouse.wheel(-HSCROLL_PX, 0)
        p.get_by_role("tab", name="Feedback scores").or_(p.get_by_text("Feedback scores", exact=True)).first.click()
        p.wait_for_timeout(2500)
        self.save(p, "shot-08b-experiments-feedback-scores.png", clip={"x": 0, "y": 0, "width": 1440, "height": 580})
        ctx.close()

    def shot_9(self):
        ctx, p = self.open("online-evaluation", wait_text="format-compliance")
        self.save(p, "shot-09-online-rules.png", clip={"x": 0, "y": 0, "width": 1440, "height": 360})
        ctx.close()

    def shot_9b(self):
        """A rule's score on a trace (format_compliance, written by the enabled rule)."""
        flt = [{"field": "feedback_scores", "key": "format_compliance", "operator": ">=", "value": "0"}]
        ts = self.traces(flt, size=5)
        if not ts:
            raise SystemExit("no trace carries format_compliance")
        ctx, p = self.open(f"logs?logsType=traces&trace={ts[0]['id']}")
        self.collapse_graph(p)
        p.get_by_text("Feedback scores", exact=True).first.click()
        p.wait_for_timeout(1500)
        self.save(p, "shot-09b-rule-score-on-trace.png")
        ctx.close()

    def shot_10b(self):
        ctx, p = self.open("alerts", wait_text="quiz-score-low")
        self.save(p, "shot-10b-alerts-config.png", clip={"x": 0, "y": 0, "width": 1440, "height": 360})
        ctx.close()

    def shot_10a(self):
        out = self.out / "shot-10a-alert-webhook.png"
        render_terminal(ROOT / "data" / "alert_webhook.log", ROOT / "data" / "alerts.jsonl",
                        self.a.project, out, self.a.entries, self.browser)
        shrink(out)
        print("saved", out.name)


def shrink(path: Path) -> None:
    """Keep each PNG under MAX_BYTES by quantising, then downscaling."""
    from PIL import Image
    img = Image.open(path)
    img.save(path, optimize=True)
    while path.stat().st_size > MAX_BYTES:
        img = Image.open(path).convert("RGB")
        img = img.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        img.save(path, optimize=True)
        if path.stat().st_size <= MAX_BYTES:
            break
        img = Image.open(path)
        img = img.resize((int(img.width * 0.85), int(img.height * 0.85)), Image.LANCZOS)
        img.save(path, optimize=True)


ORDER = ["1", "2", "3", "4", "5", "6", "7", "8", "3b", "9", "9b", "10a", "10b"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:5173")
    ap.add_argument("--project", default="silent-drift-lab")
    ap.add_argument("--workspace", default="default")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img"))
    ap.add_argument("--only", nargs="*", default=ORDER, help="shot ids, e.g. 1 4 10a")
    ap.add_argument("--entries", type=int, default=1, help="alert entries to show in the terminal render")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    failed = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        shooter = Shooter(args, browser)
        for sid in args.only:
            try:
                getattr(shooter, f"shot_{sid}")()
            except (SystemExit, Exception) as exc:  # keep going; report at the end
                failed.append((sid, str(exc)))
                print(f"FAILED shot {sid}: {exc}", file=sys.stderr)
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
