#!/usr/bin/env python3
"""Local report server for the fund screener workspace."""

from __future__ import annotations

import argparse
import io
import json
import os
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pipeline_v6


WORKDIR = Path(pipeline_v6.WORKDIR)
OUT = Path(pipeline_v6.OUT)
HTML_PATH = OUT / "zmail_recommend.html"
JSON_PATH = OUT / "zmail_funds_data.json"
REFRESH_LOCK = threading.Lock()


class ReportHandler(BaseHTTPRequestHandler):
    server_version = "FundReportServer/0.1"

    def _send_bytes(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict):
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if not HTML_PATH.exists():
                self._send_json(404, {"ok": False, "error": "HTML report not generated yet"})
                return
            self._send_bytes(200, "text/html; charset=utf-8", HTML_PATH.read_bytes())
            return
        if self.path == "/api/data":
            if not JSON_PATH.exists():
                self._send_json(404, {"ok": False, "error": "JSON data not generated yet"})
                return
            self._send_bytes(200, "application/json; charset=utf-8", JSON_PATH.read_bytes())
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/api/refresh":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        if not REFRESH_LOCK.acquire(blocking=False):
            self._send_json(409, {"ok": False, "error": "refresh already running"})
            return
        log = io.StringIO()
        try:
            with redirect_stdout(log), redirect_stderr(log):
                ok = pipeline_v6.main()
            summary = {}
            if JSON_PATH.exists():
                with JSON_PATH.open("r", encoding="utf-8") as f:
                    summary = json.load(f).get("summary", {})
            self._send_json(200, {"ok": bool(ok), "summary": summary, "logTail": log.getvalue()[-4000:]})
        except Exception as exc:
            self._send_json(
                500,
                {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc()[-4000:],
                    "logTail": log.getvalue()[-4000:],
                },
            )
        finally:
            REFRESH_LOCK.release()

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser(description="Serve the fund screener report locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    os.chdir(WORKDIR)
    server = ThreadingHTTPServer((args.host, args.port), ReportHandler)
    print(f"基金筛选工作台: http://{args.host}:{args.port}/")
    print("点击页面上的“刷新数据”会调用 /api/refresh 并运行 pipeline_v6.py")
    server.serve_forever()


if __name__ == "__main__":
    main()
