"""scripts/probe_usage.py — does OpikTracer record token usage for ChatOllama? Try with and without provider hint."""
import os, sys, time, json, urllib.request
sys.path.insert(0, "app")
from dotenv import load_dotenv; load_dotenv()
from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama
from opik.integrations.langchain import OpikTracer
from observability import ollama_usage  # noqa: F401
import opik
BASE="http://localhost:5173/api/v1/private"; P=os.getenv("OPIK_PROJECT_NAME","silent-drift-lab")
def get(p):
    with urllib.request.urlopen(f"{BASE}{p}", timeout=15) as r: return json.load(r)
llm = ChatOllama(model=os.getenv("OLLAMA_MODEL","qwen2.5:7b"), base_url=os.getenv("OLLAMA_BASE_URL","http://localhost:11434"), temperature=0)
for label, kwargs in (("default", {}), ("provider=ollama", {"provider": "ollama"})):
    tracer = OpikTracer(project_name=P, tags=["probe", label], **kwargs)
    resp = llm.invoke([HumanMessage(content="Say the word 'ok'.")], config={"callbacks": [tracer]})
    tracer.flush(); opik.Opik().flush()
    print(label, "→ usage_metadata on message:", getattr(resp, "usage_metadata", None))
    time.sleep(3)
    for t in get(f"/traces?project_name={P}&size=3")["content"]:
        if label in (t.get("tags") or []):
            sp = get(f"/spans?project_name={P}&trace_id={t['id']}&size=20")["content"]
            for s in sp:
                print(f"   span {s.get('type')} {s.get('name')} model={s.get('model')} provider={s.get('provider')} usage={s.get('usage')} cost={s.get('total_estimated_cost')}")
            break
