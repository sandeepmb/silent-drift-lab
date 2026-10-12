"""Shared pytest config: marker registration and import path."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: fast tests with no LLM or network (make test)")
    config.addinivalue_line("markers", "eval: evaluation gate; needs Opik, Ollama and the seed dataset (make evals-test)")
