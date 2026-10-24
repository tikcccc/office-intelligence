"""WSGI adapter for a single-process public workspace behind an HTTPS proxy."""

from datetime import datetime, timezone
from email.message import Message
from http import HTTPStatus
from io import BytesIO
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from . import config
from .server import APIError, DashboardApp, DashboardHandler


class UsageLimits:
    """Persist a shared daily admission budget; do not store questions or credentials."""

    def __init__(self, path: Path, *, daily_jobs=30, refresh_seconds=120, job_seconds=2):
        if daily_jobs < 1 or refresh_seconds < 0 or job_seconds < 0:
            raise ValueError("Invalid public usage limits.")
        self.path, self.daily_jobs = path, daily_jobs
        self.refresh_seconds, self.job_seconds = refresh_seconds, job_seconds
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS admissions (day TEXT PRIMARY KEY, jobs INTEGER NOT NULL, last_job REAL NOT NULL, last_refresh REAL NOT NULL)")

    def __call__(self, kind: str, use_llm: bool):
        if not use_llm and kind != "refresh":
            return
        now = time.time()
        day = datetime.now(timezone.utc).date().isoformat()
        with self.lock, sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT jobs,last_job,last_refresh FROM admissions WHERE day=?", (day,)).fetchone()
            count, last_job, last_refresh = row or (0, 0, 0)
            if count >= self.daily_jobs:
                raise APIError(429, "The shared daily analysis limit has been reached. Please return tomorrow (UTC).")
            wait = self.job_seconds - (now - last_job)
            if kind == "refresh":
                wait = max(wait, self.refresh_seconds - (now - last_refresh))
            if wait > 0:
                raise APIError(429, f"Please wait {math.ceil(wait)} seconds before starting another analysis.")
            db.execute("INSERT OR REPLACE INTO admissions VALUES (?,?,?,?)", (day, count + 1, now, now if kind == "refresh" else last_refresh))
            db.execute("DELETE FROM admissions WHERE day < ?", (day,))


class ProxyHandler(DashboardHandler):
    """Reuse the local endpoint logic without opening its development HTTP server."""

    def __init__(self, environ, app, origin):
        self.public_origin = origin
        self.public_host = urlsplit(origin).netloc.lower()
        self.server = SimpleNamespace(app=app)
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                self.headers[key[5:].replace("_", "-")] = value
        for key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if environ.get(key):
                self.headers[key.replace("_", "-")] = environ[key]
        self.path = environ.get("PATH_INFO", "/")
        if environ.get("QUERY_STRING"):
            self.path += "?" + environ["QUERY_STRING"]
        self.rfile = environ["wsgi.input"]
        self.wfile = BytesIO()
        self.response_headers = []
        self.response_status = 500

    def _guard(self):
        if self.headers.get("Host", "").lower() != self.public_host:
            raise APIError(403, "This request host is not allowed.")
        origin = self.headers.get("Origin")
        if origin and origin != self.public_origin:
            raise APIError(403, "This request origin is not allowed.")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise APIError(403, "Cross-site requests are not accepted.")

    def send_response(self, code, message=None):
        self.response_status = code

    def send_header(self, key, value):
        if key.lower() == "set-cookie":
            value += "; Secure"
        self.response_headers.append((key, value))

    def end_headers(self):
        self.response_headers.append(("Strict-Transport-Security", "max-age=31536000"))


class PublicApplication:
    def __init__(self, app, origin):
        parts = urlsplit(origin)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
            raise ValueError("OFFICE_AGENT_PUBLIC_ORIGIN must be an exact HTTPS origin without a path.")
        self.app, self.origin = app, origin

    def __call__(self, environ, start_response):
        if environ.get("PATH_INFO") == "/healthz" and environ.get("REQUEST_METHOD") in {"GET", "HEAD"}:
            start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "2"), ("Cache-Control", "no-store")])
            return [b"ok"] if environ["REQUEST_METHOD"] == "GET" else []
        handler = ProxyHandler(environ, self.app, self.origin)
        method = environ.get("REQUEST_METHOD", "")
        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()
        else:
            handler._json(405, {"error": "Method not allowed."})
        status = handler.response_status
        start_response(f"{status} {HTTPStatus(status).phrase}", handler.response_headers)
        return [handler.wfile.getvalue()]


def create_application():
    origin = os.environ.get("OFFICE_AGENT_PUBLIC_ORIGIN", "")
    # Validate the configured origin before creating writable quota state.
    PublicApplication(None, origin)
    limits = UsageLimits(config.DATA_DIR / "public_usage.sqlite", daily_jobs=int(os.environ.get("OFFICE_AGENT_DAILY_JOBS", "30")),
                         refresh_seconds=int(os.environ.get("OFFICE_AGENT_REFRESH_INTERVAL", "120")))
    return PublicApplication(DashboardApp(admit_job=limits, max_sessions=128), origin)
