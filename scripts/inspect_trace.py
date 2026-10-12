"""scripts/inspect_trace.py — print the span tree of the latest trace (or a given trace id)."""
import json
import sys
import urllib.request
from collections import Counter

BASE = "http://localhost:5173/api/v1/private"
PROJECT = "silent-drift-lab"


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=15) as r:
        return json.load(r)


def main() -> None:
    tid = sys.argv[1] if len(sys.argv) > 1 else get(f"/traces?project_name={PROJECT}&size=1")["content"][0]["id"]
    sp = get(f"/spans?project_name={PROJECT}&trace_id={tid}&size=500")["content"]
    print("trace", tid, "spans:", len(sp), dict(Counter(s.get("type") for s in sp)))
    by_parent: dict = {}
    for s in sp:
        by_parent.setdefault(s.get("parent_span_id"), []).append(s)

    def walk(pid, depth=0):
        for s in sorted(by_parent.get(pid, []), key=lambda x: x.get("start_time", "")):
            u = s.get("usage") or {}
            print(f"{'  ' * depth}{s.get('type', '?'):8} {s.get('name', '')[:44]:44} "
                  f"model={s.get('model')} tok={u.get('total_tokens')} cost={s.get('total_estimated_cost')}")
            walk(s["id"], depth + 1)

    walk(None)


if __name__ == "__main__":
    main()
