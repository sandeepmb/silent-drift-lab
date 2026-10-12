"""scripts/probe_usage.py — does OpikTracer record token usage for ChatOllama? Try with and without provider hint."""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, "app")

import opik  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_ollama import ChatOllama  # noqa: E402
from observability import ollama_usage  # noqa: E402, F401
from opik.integrations.langchain import OpikTracer  # noqa: E402

load_dotenv()
BASE = "http://localhost:5173/api/v1/private"
PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab")


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=15) as r:
        return json.load(r)


def main() -> None:
    llm = ChatOllama(model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
                     base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"), temperature=0)
    for label, kwargs in (("default", {}), ("provider=ollama", {"provider": "ollama"})):
        tracer = OpikTracer(project_name=PROJECT, tags=["probe", label], **kwargs)
        resp = llm.invoke([HumanMessage(content="Say the word 'ok'.")], config={"callbacks": [tracer]})
        tracer.flush()
        opik.Opik().flush()
        print(label, "→ usage_metadata on message:", getattr(resp, "usage_metadata", None))
        time.sleep(3)
        for t in get(f"/traces?project_name={PROJECT}&size=3")["content"]:
            if label in (t.get("tags") or []):
                for s in get(f"/spans?project_name={PROJECT}&trace_id={t['id']}&size=20")["content"]:
                    print(f"   span {s.get('type')} {s.get('name')} model={s.get('model')} provider={s.get('provider')} "
                          f"usage={s.get('usage')} cost={s.get('total_estimated_cost')}")
                break


if __name__ == "__main__":
    main()
