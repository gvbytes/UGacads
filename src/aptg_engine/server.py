"""Local web application: static page plus a small JSON API over the CSED engine.

Uses only the standard library, so there is nothing to install and the server behaves
identically wherever the repository is checked out.

Endpoints
    GET  /                 the application page
    GET  /api/catalogue    programmes, minors, departments and policy limits for the form
    GET  /api/courses?q=   course lookup for the completed-courses picker
    POST /api/plan         a student profile and preferences in, a roadmap out
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .api import State, catalogue_payload, plan, search_courses, template_courses

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "ui" / "app.html"
DB = ROOT / "out" / "aptg.sqlite"


class Handler(BaseHTTPRequestHandler):
    server_version = "aptg"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            if not PAGE.exists():
                self._send(500, b"ui/app.html is missing", "text/plain")
                return
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/catalogue":
            self._json(catalogue_payload())
        elif url.path == "/api/template":
            pid = parse_qs(url.query).get("programme_id", [""])[0]
            self._json({"courses": template_courses(pid)})
        elif url.path == "/api/courses":
            q = parse_qs(url.query).get("q", [""])[0]
            self._json({"courses": search_courses(q)})
        else:
            self._json({"error": "not found"}, 404)

    def do_HEAD(self):
        # Health checks and supervising processes probe with HEAD; without this the
        # base handler answers 501 and the server looks down when it is not.
        url = urlparse(self.path)
        ok = url.path in ("/", "/index.html", "/api/catalogue", "/api/template", "/api/courses")
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/plan":
            self._json({"error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            self._json(plan(body))
        except Exception as exc:  # noqa: BLE001 - report the fault to the caller
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)


def main(argv: list[str] | None = None) -> int:
    import argparse

    import os

    ap = argparse.ArgumentParser(prog="aptg-server")
    ap.add_argument("--db", default=str(DB))
    # PORT lets a supervising process assign a free port without editing the command.
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8732))
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)

    State.init(Path(a.db))
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"APTG running on http://{a.host}:{a.port}")
    print(f"  catalogue: {len(State.catalogue.courses)} courses, "
          f"{len(State.catalogue.programmes)} programmes, "
          f"{len(State.catalogue.minors)} minors")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
