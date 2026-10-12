"""
scripts/footprint.py — record the memory footprint of the Opik stack on this machine.

    make footprint   →  reports/footprint.md

Numbers in the README and the handbook come from this script, not from memory.
"""

from __future__ import annotations

import datetime as dt
import platform
import subprocess
from pathlib import Path


def _to_mib(s: str) -> float:
    s = s.strip()
    for unit, f in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("GB", 1000 / 1.048576), ("MB", 1 / 1.048576), ("kB", 0.001 / 1.048576), ("B", 1 / 1048576)):
        if s.endswith(unit):
            return float(s[: -len(unit)]) * f
    return float(s)


def main() -> None:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        name, cpu, mem = line.split("\t")
        if "opik" not in name:
            continue
        used, limit = mem.split("/")
        rows.append((name.replace("opik-opik-src-", "").replace("opik-", "").rstrip("-1"), cpu, _to_mib(used), limit.strip()))
    rows.sort(key=lambda r: -r[2])
    total = sum(r[2] for r in rows)
    limit = rows[0][3] if rows else "?"

    md = [
        "# Opik stack footprint (measured)",
        "",
        f"- Date: {dt.datetime.now().isoformat(timespec='minutes')}",
        f"- Host: {platform.machine()} / {platform.platform()}",
        f"- Docker memory limit: {limit}",
        f"- Containers: {len(rows)}",
        f"- **Total idle memory: {total / 1024:.2f} GiB**",
        "",
        "| Container | CPU | Memory (MiB) |",
        "|---|---|---|",
    ]
    md += [f"| {n} | {c} | {m:,.0f} |" for n, c, m, _ in rows]
    Path("reports").mkdir(exist_ok=True)
    Path("reports/footprint.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
