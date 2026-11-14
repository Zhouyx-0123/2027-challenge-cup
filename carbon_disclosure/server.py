"""Dependency-free, loopback-only static server for the disclosure ledger."""
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import mimetypes

from . import DIST


class LedgerHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, directory=DIST, **kwargs):
        self.directory = Path(directory).resolve()
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self._serve(False)

    def do_HEAD(self):
        self._serve(True)

    def do_POST(self):
        self.send_error(405, "This server accepts GET and HEAD only")

    def _serve(self, head):
        try:
            pathname = unquote(urlsplit(self.path).path, errors="strict")
        except (ValueError, UnicodeError):
            self.send_error(400, "Invalid URL")
            return
        if "\x00" in pathname:
            self.send_error(400, "Invalid URL")
            return
        if pathname == "/healthz":
            content, mime = b'{"status":"ok","app":"carbon-disclosure"}', "application/json"
        else:
            asset = (self.directory / pathname.lstrip("/").replace("\\", "/")).resolve()
            if pathname == "/":
                asset = self.directory / "index.html"
            if self.directory not in asset.parents:
                self.send_error(403, "Forbidden")
                return
            if not asset.is_file():
                self.send_error(404, "Not found")
                return
            try:
                content = asset.read_bytes()
            except OSError:
                self.send_error(404, "Not found")
                return
            mime = {".js": "text/javascript", ".csv": "text/csv"}.get(asset.suffix)
            mime = mime or mimetypes.guess_type(str(asset))[0] or "application/octet-stream"
        if mime.startswith("text/") or mime == "application/json":
            mime += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(content)


def create_server(port=4318, directory=DIST):
    return ThreadingHTTPServer(("127.0.0.1", port), partial(LedgerHandler, directory=directory))
