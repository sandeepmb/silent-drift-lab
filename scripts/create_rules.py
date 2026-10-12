"""
scripts/create_rules.py — online evaluation rules, feedback definition and alert via the Opik REST API.

  make rules                       # create everything (idempotent: existing objects are kept)
  python scripts/create_rules.py --recreate      # delete + recreate the rules and alert
  python scripts/create_rules.py --test-webhook  # POST a test payload to the alert webhook
  python scripts/create_rules.py --logs          # print the rules' execution logs

What it creates (self-hosted Opik 2.2.96, no auth):
  1. AI provider      POST /v1/private/llm-provider-key      provider=ollama, base_url=http://host.docker.internal:11434/v1
  2. Feedback def.    POST /v1/private/feedback-definitions  numerical "quiz_score" 0..1
  3. Rules            POST /v1/private/automations/evaluators/
       a) llm_as_judge                  "answer-relevance"   sampling 0.5, judge = custom-llm/ollama/qwen2.5:7b
       b) user_defined_metric_python    "format-compliance"  sampling 1.0, checks the grader keys in the trace output
       c) trace_thread_llm_as_judge     "thread-coherence"   sampling 1.0, scores the whole session thread
  4. Alert            POST /v1/private/alerts                trace:feedback_score, avg quiz_score < 0.5 over a window
                                                            -> webhook http://host.docker.internal:9009/alert

The JSON shapes come from the backend sources (apps/opik-backend/.../api/evaluators/*.java,
api/Alert*.java, api/ProviderApiKey.java) because this version does not serve /api/openapi.json.
Rules only score traces created after the rule exists; see docs/evals.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OPIK_API = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").split("#")[0].strip().rstrip("/")
OPIK_UI = OPIK_API[: -len("/api")] if OPIK_API.endswith("/api") else OPIK_API
WORKSPACE = os.getenv("OPIK_WORKSPACE", "default").split("#")[0].strip()
PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab").split("#")[0].strip()
JUDGE_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").split("#")[0].strip()
OLLAMA_FROM_DOCKER = os.getenv("OLLAMA_URL_FROM_DOCKER", "http://host.docker.internal:11434/v1")
WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "http://host.docker.internal:9009/alert")
ALERT_WINDOW_S = os.getenv("ALERT_WINDOW_SECONDS", "900")

# Opik routes "custom-llm/<provider_name>/<model>" to the named custom provider (here: ollama).
RULE_MODEL = f"custom-llm/ollama/{JUDGE_OLLAMA_MODEL}"
PROVIDER_MODELS = ",".join(f"custom-llm/ollama/{m}" for m in dict.fromkeys([JUDGE_OLLAMA_MODEL, "qwen2.5:7b", "qwen2.5:3b"]))

HEADERS = {"Comet-Workspace": WORKSPACE, "Content-Type": "application/json"}
client = httpx.Client(base_url=OPIK_API, headers=HEADERS, timeout=30)


def api(method: str, path: str, **kw) -> httpx.Response:
    r = client.request(method, path, **kw)
    if r.status_code >= 400:
        print(f"  !! {method} {path} -> {r.status_code}: {r.text[:500]}")
    return r


def project_id() -> str:
    r = api("GET", "/v1/private/projects", params={"name": PROJECT})
    r.raise_for_status()
    for p in r.json().get("content", []):
        if p["name"] == PROJECT:
            return p["id"]
    raise SystemExit(f"project '{PROJECT}' not found; run one session first")


# ── 1. AI provider (Ollama, OpenAI-compatible /v1) ─────────────────────────────

def ensure_provider() -> dict:
    r = api("GET", "/v1/private/llm-provider-key")
    for k in r.json().get("content", []):
        if k.get("provider") == "ollama":
            print(f"[provider] exists: ollama id={k['id']} base_url={k.get('base_url')} models={k.get('configuration', {}).get('models')}")
            return k
    body = {
        "provider": "ollama",
        "provider_name": "ollama",             # required for named providers
        "api_key": "ollama",                   # ignored by Ollama, must not be blank for the client
        "base_url": OLLAMA_FROM_DOCKER,        # Opik runs in Docker: host.docker.internal reaches the Mac
        "configuration": {"models": PROVIDER_MODELS},
    }
    r = api("POST", "/v1/private/llm-provider-key", json=body)
    r.raise_for_status()
    created = api("GET", "/v1/private/llm-provider-key").json()["content"]
    k = next(x for x in created if x.get("provider") == "ollama")
    print(f"[provider] created: ollama id={k['id']} -> {OLLAMA_FROM_DOCKER} models={PROVIDER_MODELS}")
    return k


# ── 2. Feedback definition ──────────────────────────────────────────────────────

def ensure_feedback_definition(name: str = "quiz_score") -> dict:
    r = api("GET", "/v1/private/feedback-definitions", params={"name": name})
    for d in r.json().get("content", []):
        if d["name"] == name:
            print(f"[feedback-definition] exists: {name} ({d['type']}) id={d['id']}")
            return d
    body = {"name": name, "type": "numerical", "details": {"min": 0, "max": 1},
            "description": "Average quiz score of a study session (logged by main.run_session)."}
    r = api("POST", "/v1/private/feedback-definitions", json=body)
    r.raise_for_status()
    d = next(x for x in api("GET", "/v1/private/feedback-definitions", params={"name": name}).json()["content"] if x["name"] == name)
    print(f"[feedback-definition] created: {name} numerical 0..1 id={d['id']}")
    return d


# ── 3. Rules ────────────────────────────────────────────────────────────────────

# Only traces that contain a graded quiz carry `user_answer` in their output; the first trace of a
# session (ends in the approval interrupt) does not, so rules skip it instead of logging a path error.
QUIZ_TRACE_FILTER = [{"field": "output", "operator": "contains", "key": "", "value": "user_answer"}]

ANSWER_RELEVANCE_PROMPT = """You are grading a student's quiz answer for RELEVANCE to the question.

Question:
{{question}}

Reference answer:
{{expected}}

Student answer:
{{answer}}

Score answer_relevance between 0.0 and 1.0:
- 1.0  the answer directly addresses the question and the concepts of the reference answer
- 0.5  the answer is on topic but misses the point of the question
- 0.0  the answer is off topic, empty or evasive
Explain in one sentence."""

FORMAT_COMPLIANCE_CODE = '''import json
from typing import Any
from opik.evaluation.metrics import base_metric, score_result

REQUIRED = {"question": str, "expected_answer": str, "user_answer": str, "correct": bool, "score": (int, float), "feedback": str}

class FormatCompliance(base_metric.BaseMetric):
    """1.0 when every graded question in the trace output has the grader keys with the right types."""
    def __init__(self, name: str = "format_compliance"):
        self.name = name

    def score(self, output: Any, **ignored_kwargs: Any):
        data = output
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return score_result.ScoreResult(value=0.0, name=self.name, reason="output is not JSON")
        results = (data or {}).get("quiz_results") or []
        questions = [q for r in results for q in (r.get("questions") or [])]
        if not questions:
            return score_result.ScoreResult(value=0.0, name=self.name, reason="no graded questions in output")
        problems = []
        for i, q in enumerate(questions):
            for key, typ in REQUIRED.items():
                if key not in q:
                    problems.append(f"q{i}: missing {key}")
                elif not isinstance(q[key], typ) or (key == "correct" and not isinstance(q[key], bool)):
                    problems.append(f"q{i}: {key} type {type(q[key]).__name__}")
            s = q.get("score")
            if isinstance(s, (int, float)) and not 0 <= s <= 1:
                problems.append(f"q{i}: score {s} outside [0,1]")
        ok = len(questions) - len({p.split(':')[0] for p in problems})
        return score_result.ScoreResult(
            value=ok / len(questions),
            name=self.name,
            reason="; ".join(problems) if problems else f"{len(questions)} questions, all grader keys present",
        )
'''

THREAD_COHERENCE_PROMPT = """You are reviewing one complete tutoring session between a Learning Accelerator agent and a student.
The conversation below alternates between the student (user) and the tutor (assistant).

{{context}}

Score thread_coherence between 0.0 and 1.0: does the tutor stay on the student's stated goal, explain before
quizzing, grade consistently with its own feedback, and give a coaching summary that matches the quiz results?
1.0 = fully coherent session, 0.0 = incoherent or contradictory. Explain in one sentence."""


def rule_payloads(pid: str) -> list[dict]:
    model = {"name": RULE_MODEL, "temperature": 0.0}
    return [
        {
            "name": "answer-relevance",
            "type": "llm_as_judge",
            "project_id": pid,
            "sampling_rate": 0.5,
            "enabled": True,
            "filters": QUIZ_TRACE_FILTER,
            "code": {
                "model": model,
                "messages": [{"role": "USER", "content": ANSWER_RELEVANCE_PROMPT}],
                "variables": {
                    "question": "output.quiz_results[0].questions[0].question",
                    "expected": "output.quiz_results[0].questions[0].expected_answer",
                    "answer": "output.quiz_results[0].questions[0].user_answer",
                },
                "schema": [{"name": "answer_relevance", "type": "DOUBLE",
                            "description": "Relevance of the student's first answer to the question (0-1)"}],
            },
        },
        {
            "name": "format-compliance",
            "type": "user_defined_metric_python",
            "project_id": pid,
            "sampling_rate": 1.0,
            "enabled": True,
            "filters": QUIZ_TRACE_FILTER,
            "code": {"metric": FORMAT_COMPLIANCE_CODE, "arguments": {"output": "output"}},
        },
        {
            "name": "thread-coherence",
            "type": "trace_thread_llm_as_judge",
            "project_id": pid,
            "sampling_rate": 1.0,
            "enabled": True,
            "code": {
                "model": model,
                "messages": [{"role": "USER", "content": THREAD_COHERENCE_PROMPT}],
                "schema": [{"name": "thread_coherence", "type": "DOUBLE",
                            "description": "Coherence of the whole tutoring session (0-1)"}],
            },
        },
    ]


def list_rules(pid: str) -> list[dict]:
    r = api("GET", "/v1/private/automations/evaluators/", params={"project_id": pid, "size": 100})
    r.raise_for_status()
    return r.json().get("content", [])


def ensure_rules(pid: str, recreate: bool) -> list[dict]:
    existing = {r["name"]: r for r in list_rules(pid)}
    if recreate and existing:
        api("POST", "/v1/private/automations/evaluators/delete",
            params={"project_id": pid}, json={"ids": [r["id"] for r in existing.values()]})
        print(f"[rules] deleted {len(existing)} existing rule(s)")
        existing = {}
    accepted: list[dict] = []
    for payload in rule_payloads(pid):
        if payload["name"] in existing:
            r = existing[payload["name"]]
            print(f"[rule] exists: {r['name']} type={r['type']} sampling={r['sampling_rate']} id={r['id']}")
            accepted.append(r)
            continue
        resp = api("POST", "/v1/private/automations/evaluators/", json=payload)
        if resp.status_code in (200, 201):
            rule = next(x for x in list_rules(pid) if x["name"] == payload["name"])
            print(f"[rule] created: {rule['name']} type={rule['type']} sampling={rule['sampling_rate']} id={rule['id']}")
            accepted.append(rule)
        else:
            print(f"[rule] REJECTED: {payload['name']} type={payload['type']} ({resp.status_code})")
    return accepted


# ── 4. Alert ───────────────────────────────────────────────────────────────────

def alert_payload(pid: str) -> dict:
    return {
        "name": "quiz-score-low",
        "enabled": True,
        "alert_type": "general",
        "project_id": pid,                      # scope: this project only (Opik adds the scope:project config)
        "webhook": {"name": "local-alert-sink", "url": WEBHOOK_URL},
        "triggers": [{
            "event_type": "trace:feedback_score",
            "trigger_configs": [{
                "type": "threshold:feedback_score",
                # MetricsAlertJob: avg(feedback score `name`) over the last `window` seconds, compared with operator
                "config_value": {"name": "quiz_score", "operator": "<", "threshold": "0.5", "window": ALERT_WINDOW_S},
            }],
        }],
    }


def ensure_alert(pid: str, recreate: bool) -> dict | None:
    r = api("GET", "/v1/private/alerts", params={"size": 100})
    existing = [a for a in r.json().get("content", []) if a["name"] == "quiz-score-low"]
    if existing and recreate:
        api("POST", "/v1/private/alerts/delete", json={"ids": [a["id"] for a in existing]})
        print(f"[alert] deleted {len(existing)} existing alert(s)")
        existing = []
    if existing:
        a = existing[0]
        print(f"[alert] exists: {a['name']} id={a['id']} webhook={a['webhook']['url']}")
        return a
    resp = api("POST", "/v1/private/alerts", json=alert_payload(pid))
    if resp.status_code not in (200, 201):
        print(f"[alert] REJECTED ({resp.status_code})")
        return None
    a = next(x for x in api("GET", "/v1/private/alerts", params={"size": 100}).json()["content"] if x["name"] == "quiz-score-low")
    cfg = a["triggers"][0]["trigger_configs"]
    print(f"[alert] created: {a['name']} id={a['id']} webhook={a['webhook']['url']} configs={json.dumps(cfg)[:300]}")
    return a


def test_webhook(pid: str) -> None:
    resp = api("POST", "/v1/private/alerts/webhooks/tests", json=alert_payload(pid))
    print(f"[alert] webhook test -> {resp.status_code} {resp.text[:400]}")


def set_enabled(pid: str, names: list[str], enabled: bool) -> None:
    """PATCH /automations/evaluators/{id}: the update body must carry the full rule (name, sampling_rate, code...)."""
    for rule in list_rules(pid):
        if rule["name"] not in names:
            continue
        body = {k: rule[k] for k in ("type", "name", "sampling_rate", "code") if k in rule}
        body["filters"] = rule.get("filters") or []
        body["project_ids"] = [p["project_id"] for p in rule.get("projects") or []] or [pid]
        body["enabled"] = enabled
        resp = api("PATCH", f"/v1/private/automations/evaluators/{rule['id']}", json=body)
        print(f"[rule] {rule['name']} enabled={enabled} -> {resp.status_code}")


def print_logs(pid: str) -> None:
    for rule in list_rules(pid):
        r = api("GET", f"/v1/private/automations/evaluators/{rule['id']}/logs", params={"size": 20})
        items = r.json().get("content", []) if r.status_code == 200 else []
        print(f"\n[logs] {rule['name']} ({rule['type']}): {len(items)} entries")
        for it in items[:8]:
            print(f"  {it.get('timestamp', '')[:19]} {it.get('level')}: {str(it.get('message'))[:220].replace(chr(10), ' ')}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--recreate", action="store_true", help="delete and recreate rules and alert")
    p.add_argument("--test-webhook", action="store_true", help="send a test payload to the alert webhook")
    p.add_argument("--logs", action="store_true", help="print rule execution logs and exit")
    p.add_argument("--disable", nargs="+", metavar="RULE", help="disable these rules by name and exit")
    p.add_argument("--enable", nargs="+", metavar="RULE", help="enable these rules by name and exit")
    args = p.parse_args()

    pid = project_id()
    print(f"[opik] {OPIK_API} workspace={WORKSPACE} project={PROJECT} id={pid}")
    if args.logs:
        print_logs(pid)
        sys.exit(0)
    if args.disable or args.enable:
        if args.disable:
            set_enabled(pid, args.disable, False)
        if args.enable:
            set_enabled(pid, args.enable, True)
        sys.exit(0)
    if args.test_webhook:
        test_webhook(pid)
        sys.exit(0)

    ensure_provider()
    ensure_feedback_definition()
    rules = ensure_rules(pid, args.recreate)
    alert = ensure_alert(pid, args.recreate)

    print("\nSummary")
    print(f"  rules accepted : {[r['name'] + ':' + r['type'] for r in rules]}")
    print(f"  alert          : {'created' if alert else 'none'} -> {WEBHOOK_URL}")
    print(f"  UI rules       : {OPIK_UI}/{WORKSPACE}/projects/{pid}/rules")
    print(f"  UI alerts      : {OPIK_UI}/{WORKSPACE}/configuration?tab=alerts")
    print("  Rules score only traces logged from now on; run a session, then `python scripts/create_rules.py --logs`.")
