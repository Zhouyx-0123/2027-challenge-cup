"""Run with Python only: python -m carbon_disclosure."""
import argparse
from pathlib import Path
import sys
import webbrowser

from . import build_html
from .server import create_server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Carbon disclosure ledger (no pip dependencies)")
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--export-html", type=Path, metavar="FILE")
    args = parser.parse_args(argv)
    if args.export_html:
        args.export_html.write_text(build_html(), encoding="utf-8")
        print("Saved:", args.export_html.resolve())
        return 0
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        server = create_server(args.port)
    except OSError as exc:
        print("Cannot start the ledger:", exc, file=sys.stderr)
        print("Try: python -m carbon_disclosure --port 4319", file=sys.stderr)
        return 1
    url = "http://127.0.0.1:%s" % server.server_port
    print("Carbon disclosure ledger:", url, flush=True)
    print("Keep this window open. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            print("Open the URL above in your browser.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
