#!/usr/bin/env python3
"""Run the APTG web application.

    python3 aptg/serve.py [--port 8732] [--db aptg/out/aptg.sqlite]

Kept at the project root so the server can be started without setting PYTHONPATH.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

from aptg_engine.server import main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if not any(a.startswith("--db") for a in argv):
        argv += ["--db", str(HERE / "out" / "aptg.sqlite")]
    raise SystemExit(main(argv))
