"""
scripts/alert_webhook.py — a tiny HTTP sink for Opik alerts.

  python scripts/alert_webhook.py            # listens on 0.0.0.0:9009
  python scripts/alert_webhook.py --port 9009 --out data/alerts.jsonl

Opik (running in Docker) posts alert payloads to
http://host.docker.internal:9009/alert. Every request is printed and appended
as one JSON line to data/alerts.jsonl with the receive time, path, headers
(secrets masked) and the parsed body. GET /health returns 200 for liveness.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "alerts.jsonl"


class AlertHandler(BaseHTTPRequestHandler):
    out: Path = OUT

    def _record(self, body: bytes) -> None:
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = body.decode("utf-8", errors="replace")
        headers = {k: ("***" if k.lower() in ("authorization", "x-opik-signature") else v)
                   for k, v in self.headers.items()}
        line = {
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "path": self.path,
            "headers": headers,
            "body": parsed,
        }
        self.out.parent.mkdir(parents=True, exist_ok=True)
        with self.out.open("a") as fh:
            fh.write(json.dumps(line) + "\n")
        summary = parsed if isinstance(parsed, dict) else {"raw": str(parsed)[:200]}
        print(f"[alert] {line['received_at']} {self.path} "
              f"event={summary.get('event_type') or summary.get('type')} "
              f"alert={summary.get('alert_name') or (summary.get('alert') or {}).get('name')}")
        print(json.dumps(parsed, indent=2)[:2000] if parsed is not None else "(empty body)")
        sys.stdout.flush()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._record(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "alerts_file": str(self.out)}).encode())

    def log_message(self, fmt: str, *args) -> None:  # quiet default access log
        return


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=9009)
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args()
    AlertHandler.out = Path(args.out)
    server = HTTPServer(("0.0.0.0", args.port), AlertHandler)
    print(f"[alert_webhook] listening on http://0.0.0.0:{args.port}/alert -> {args.out}")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
