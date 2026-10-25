"""Local HTTP server: serves the viewer page and a small JSON API.

Binds to 127.0.0.1 only. The source endpoint serves only files the scanner
found, so it cannot be used to read arbitrary paths.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .model import Config, build, fingerprint


class State:
    def __init__(self, cfg: Config, editor: str):
        self.cfg = cfg
        self.editor = editor
        self._lock = threading.Lock()
        self._fp = None
        self._model = None

    def current(self) -> tuple[str, dict]:
        fp = fingerprint(self.cfg)
        with self._lock:
            if fp != self._fp or self._model is None:
                self._model = build(self.cfg)
                self._model["editor"] = self.editor
                self._fp = fp
                self._model["version"] = fp
            return self._fp, self._model

    def version(self) -> str:
        return fingerprint(self.cfg)


def _static_root():
    return resources.files("umlscope") / "static"


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        server_version = "umlscope"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, status, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=HTTPStatus.OK):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            try:
                if url.path in ("/", "/index.html"):
                    return self._file("index.html")
                if url.path.startswith("/static/"):
                    return self._file(url.path[len("/static/"):])
                if url.path == "/api/version":
                    return self._json({"version": state.version()})
                if url.path == "/api/model":
                    _, model = state.current()
                    return self._json(model)
                if url.path == "/api/source":
                    return self._source(q.get("id", [""])[0])
            except Exception as exc:  # surface errors to the page instead of a dead socket
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _file(self, rel: str):
            if ".." in Path(rel).parts:
                return self._json({"error": "bad path"}, HTTPStatus.BAD_REQUEST)
            res = _static_root().joinpath(*Path(rel).parts)
            if not res.is_file():
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(HTTPStatus.OK, res.read_bytes(), ctype)

        def _source(self, node_id: str):
            _, model = state.current()
            node = model["nodes"].get(node_id)
            if not node or not node.get("file"):
                return self._json({"error": "unknown module"}, HTTPStatus.NOT_FOUND)
            path = (state.cfg.root / node["file"]).resolve()
            text = path.read_text(encoding="utf-8", errors="replace")
            self._json({"id": node_id, "file": node["file"], "abs": str(path), "text": text})

    return Handler


def serve(cfg: Config, host: str, port: int, editor: str):
    state = State(cfg, editor)
    state.current()  # fail fast on a broken config, and warm the cache
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    return httpd, state
