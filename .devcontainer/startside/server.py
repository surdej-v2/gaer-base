"""The Codespace start page on :5170 — standard library only, so it runs before setup.sh has installed anything.

GET /        the page (index.html with the codespace's name filled in)
GET /status  JSON: how far setup and the dev stack have come
GET /log     the last lines of the logs in .devcontainer/logs/ (the port is private to the codespace's owner)

The page itself decides what to show: in the browser (an *.app.github.dev address) how to open VS Code Desktop,
in VS Code Desktop (localhost) the progress, and then it switches to the app on localhost:5173.
"""

import http.server
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE.parent / "logs"
PORT = 5170


def _state(name: str) -> str:
    try:
        return (LOGS / f"{name}.state").read_text().strip() or "waiting"
    except OSError:
        return "waiting"


def _tcp(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def status() -> dict:
    setup = _state("setup")
    migrate = _state("migrate")
    postgres = _tcp(5432)
    api = _http("http://127.0.0.1:8000/health")
    vite = _http("http://127.0.0.1:5173/")

    def step(key, label, ok, failed=False, running=False):
        state = "done" if ok else "failed" if failed else "running" if running else "waiting"
        return {"key": key, "label": label, "state": state}

    setup_done = setup == "done"
    steps = [
        step("setup", "Python-pakker og npm", setup_done, setup == "failed", setup == "running"),
        step("postgres", "Postgres og NATS", postgres, running=setup_done),
        step("migrate", "Migreringer", migrate == "done", migrate == "failed", migrate == "running"),
        step("api", "API på :8000", api, running=migrate == "done"),
        step("vite", "Vite på :5173", vite, running=migrate == "done"),
    ]
    return {"ready": api and vite, "steps": steps}


def tail(lines: int = 60) -> str:
    out = []
    for name in ("setup.log", "compose.log", "server.log", "vite.log"):
        path = LOGS / name
        if not path.exists():
            continue
        text = path.read_text(errors="replace").splitlines()[-lines:]
        out.append(f"==> {name} <==\n" + "\n".join(text))
    return "\n\n".join(out) or "Ingen logs endnu."


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body: str, ctype: str) -> None:
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/status":
            self._send(json.dumps(status()), "application/json")
        elif path == "/log":
            self._send(tail(), "text/plain")
        elif path in ("/", "/index.html"):
            name = json.dumps(os.environ.get("CODESPACE_NAME", ""))
            html = (HERE / "index.html").read_text().replace('"__CODESPACE_NAME__"', name)
            self._send(html, "text/html")
        else:
            self.send_error(404)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
