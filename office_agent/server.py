"""Loopback-only dashboard server. Run: python -m office_agent.server [--allow-llm] [--allow-live]."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import config, references, runlog, runtime, web
from .agent import Agent
from .dashboard import build_dashboard
from .models import llm
from .security import safe_text
from .skills.weekly_brief import generate_weekly_brief
from .skills import broker_reports, macro, news
from .workspace import Workspace, analyze, empty_dashboard, facts as workspace_facts, has_data, timestamp

STATIC = Path(__file__).resolve().parent / "frontend"
READ_SKILLS = frozenset({"get_market_metrics", "get_macro_indicators", "get_market_news"})
TERMINAL = {"done", "failed", "cancelled"}
PROGRESS = {"get_market_metrics":"Reading broker metrics", "get_macro_indicators":"Reading macro indicators", "get_market_news":"Finding market news"}


class APIError(ValueError):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)


@dataclass
class Job:
    kind: str
    context: runtime.RunContext
    id: str = field(default_factory=lambda:secrets.token_hex(16))
    status: str = "queued"
    events: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    seconds: float = 0
    steps: list[dict] = field(default_factory=list)


@dataclass
class Session:
    id: str = field(default_factory=lambda:secrets.token_hex(24))
    csrf: str = field(default_factory=lambda:secrets.token_urlsafe(32))
    touched: float = field(default_factory=time.monotonic)
    agent: Agent = field(default_factory=lambda:Agent(use_llm=False, deadline_seconds=90,
                                                     max_output_tokens=6000, allowed_skills=READ_SKILLS, english_only=True))
    jobs: dict[str, Job] = field(default_factory=dict)
    active: str | None = None
    mode: tuple | None = None


class DashboardApp:
    def __init__(self, *, allow_llm: bool = True, allow_live: bool = True):
        self.allow_llm, self.allow_live = allow_llm, allow_live
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dashboard")
        self.slots = threading.BoundedSemaphore(4)
        self.cache: dict[bool, tuple[float, dict]] = {}
        self.workspace = Workspace()
        self.source_pool = ThreadPoolExecutor(max_workers=3,thread_name_prefix="live-source")
        self.source_slots = threading.BoundedSemaphore(3)
        self.refresh_job: Job | None = None
        self.refresh_owner: str | None = None
        self.auto_attempted = False

    def session(self, cookie: str | None) -> tuple[Session, bool]:
        parsed = SimpleCookie()
        try:
            parsed.load(cookie or "")
        except Exception:
            parsed = SimpleCookie()
        key = parsed["office_session"].value if "office_session" in parsed else ""
        with self.lock:
            expired = [key for key, value in self.sessions.items() if not value.active and time.monotonic()-value.touched>3600]
            for item in expired:
                del self.sessions[item]
            if key in self.sessions:
                session = self.sessions[key]
                session.touched = time.monotonic()
                return session, False
            if len(self.sessions)>=32:
                raise APIError(503, "The workspace is at session capacity. Please try again later.")
            session = Session()
            self.sessions[session.id] = session
            return session, True

    def validate_options(self, payload: dict, *, role: str = "chat") -> tuple[bool, bool]:
        offline, use_llm = payload.get("offline", False), payload.get("use_llm", False)
        if type(offline) is not bool or type(use_llm) is not bool:
            raise APIError(400, "Invalid data mode options.")
        if offline:
            raise APIError(400,"Saved source mode is unavailable. This workspace uses live data only.")
        if not offline and not self.allow_live:
            raise APIError(403, "Live sources are not enabled on this server.")
        if use_llm and (not self.allow_llm or not llm.available(role)):
            raise APIError(403, "AI analysis is not enabled. You can use source data instead.")
        return offline, use_llm

    def dashboard(self, *, offline: bool = False, force: bool = False) -> dict:
        if offline:
            raise APIError(400,"Saved source mode is unavailable. This workspace uses live data only.")
        saved = self.workspace.read()
        with runtime.scope(runtime.RunContext(time.monotonic()+10,live_only=True)):
            data = saved["data"] or empty_dashboard()
        return {**data,"workspace":{"has_data":has_data(data),"analysis":saved["analysis"],
                "history":saved["history"],"activity":self.public_job(self.refresh_job) if self.refresh_job else None}}

    def start_refresh(self, session: Session, *, automatic: bool = False, analysis_only: bool = False) -> dict:
        with self.lock:
            if self.refresh_job and self.refresh_job.status not in TERMINAL:
                return {"id":self.refresh_job.id,"owner":self.refresh_owner==session.id,"joined":True}
            if automatic and (self.auto_attempted or has_data(self.workspace.read()["data"] or {})):
                return {"skipped":True}
            use_llm = self.allow_llm and llm.available("chat")
            if analysis_only and not use_llm:
                raise APIError(403,"AI analysis is unavailable on this server.")
            if analysis_only and not has_data(self.workspace.read()["data"] or {}):
                raise APIError(409,"Get live market data before running AI analysis.")
            job = self.start_job(session,"analysis" if analysis_only else "refresh",{"use_llm":use_llm})
            self.refresh_job,self.refresh_owner,self.auto_attempted = job,session.id,True
            return {"id":job.id,"owner":True,"joined":False}

    def start_job(self, session: Session, kind: str, payload: dict) -> Job:
        offline, use_llm = self.validate_options(payload, role="brief" if kind=="brief" else "chat")
        question = payload.get("question", "")
        if kind=="ask" and (not isinstance(question, str) or not question.strip() or len(question)>config.MAX_QUESTION_CHARS):
            raise APIError(400, "Enter a question of 1–8,000 characters.")
        with self.lock:
            if session.active:
                raise APIError(409, "A task is already running. Wait for it or cancel it.")
            if not self.slots.acquire(blocking=False):
                raise APIError(503, "All analysis slots are busy. Please try again shortly.")
            if len(session.jobs)>=8:
                oldest = next(iter(session.jobs))
                del session.jobs[oldest]
            budget = 180 if kind in {"refresh","analysis"} else 90
            job = Job(kind, runtime.RunContext(time.monotonic()+budget, offline=False, allow_review=False,live_only=True))
            session.jobs[job.id], session.active = job, job.id
            try:
                self.pool.submit(self._perform, session, job, safe_text(question), offline, use_llm)
            except Exception:
                session.active = None
                del session.jobs[job.id]
                self.slots.release()
                raise
            return job

    def _progress(self, job: Job, message: str) -> None:
        with self.lock:
            if len(job.events)<16 and job.status not in TERMINAL:
                job.events.append(message)

    def _perform(self, session: Session, job: Job, question: str, offline: bool, use_llm: bool) -> None:
        started = time.monotonic()
        try:
            with self.lock:
                job.status = "running"
                job.started_at = timestamp()
            with runtime.scope(job.context):
                runtime.checkpoint()
                saved_data = self.workspace.read()["data"]
                if saved_data:
                    job.context.source_data = {"macro":saved_data["macro"],"news":saved_data["news"]}
                if job.kind in {"refresh","analysis"}:
                    result = self._pipeline(job,use_llm,analysis_only=job.kind=="analysis")
                elif job.kind=="ask":
                    if not saved_data or not has_data(saved_data):
                        raise APIError(409,"Get live market data from the overview before asking a question.")
                    if session.mode!=(offline,use_llm):
                        session.agent.reset()
                        session.mode = (offline,use_llm)
                    session.agent.use_llm = use_llm
                    self._progress(job, "Preparing your answer")
                    answer = session.agent.ask(question, on_tool=lambda name,args:self._progress(job, PROGRESS.get(name,"Checking sources")))
                    run = session.agent.last_run or {}
                    if run.get("outcome") in {"failed", "deadline", "context_limit", "tool_limit", "max_rounds"}:
                        raise APIError(503, "The analysis could not finish. Try a narrower question.")
                    facts = list(session.agent.last_results)
                    if use_llm and run.get("outcome")=="answered":
                        facts += [json.loads(m["content"]) for m in session.agent.messages if m["role"]=="tool"]
                    result = {"answer":answer, "check":run.get("check"), "sources":references.catalog(facts),
                              "outcome":run.get("outcome"), "used_llm":use_llm and run.get("outcome")=="answered"}
                else:
                    if not saved_data or not has_data(saved_data):
                        raise APIError(409,"Get live market data before generating a brief.")
                    self._progress(job, "Gathering broker readings, macro data and news")
                    run = runlog.start("web_brief", "Generate weekly brief")
                    try:
                        with runtime.scope(run["_context"]):
                            output = generate_weekly_brief(use_llm=use_llm, save=False, max_output_tokens=6000,facts=workspace_facts(saved_data))
                            run["check"] = output["figure_check"]
                        result = {"brief":output["brief"], "check":output["figure_check"],
                                  "sources":output["sources"], "used_llm":output["written_by"]!="template",
                                  "filename":f"london-office-brief_{date.today().isoformat()}.md"}
                    finally:
                        runlog.finish(run)
                runtime.checkpoint()
                result.update(offline=False, seconds=round(time.monotonic()-started,1))
                with self.lock:
                    if job.context.cancelled.is_set():
                        job.status = "cancelled"
                    else:
                        job.result, job.status = result, "done"
        except Exception as exc:
            with self.lock:
                if isinstance(exc, runtime.DeadlineExceeded) or job.context.cancelled.is_set():
                    job.status, job.error = "cancelled", "The task was cancelled or reached its time limit."
                else:
                    job.status = "failed"
                    job.error = exc.message if isinstance(exc, APIError) else "Unable to complete the task. Please try again."
        finally:
            with self.lock:
                job.context.closed = True
                session.active = None
                job.completed_at = timestamp()
                job.seconds = round(time.monotonic()-started,1)
                for step in job.steps:
                    if step["status"] in {"queued","running"}:
                        step.update(status="cancelled" if job.status=="cancelled" else "failed",completed_at=job.completed_at)
                if job.kind in {"refresh","analysis"}:
                    record = self.public_job(job)
                    record.pop("result",None)
                    # Operational history records cancellation without committing late source data.
                    try:
                        self.workspace.save(record=record)
                    except (OSError,ValueError):
                        pass
            self.slots.release()

    def _step(self, job: Job, name: str, status: str, *, detail: str | None = None, count: int | None = None):
        with self.lock:
            step = next(s for s in job.steps if s["id"]==name)
            step["status"] = status
            if status=="running":
                step["started_at"] = timestamp()
            elif status not in {"queued"}:
                step["completed_at"] = timestamp()
            if detail is not None:
                step["detail"] = safe_text(detail)
            if count is not None:
                step["count"] = count
        self._progress(job,detail or step["label"])

    def _source(self, job: Job, name: str):
        self._step(job,name,"running")
        context = runtime.RunContext(time.monotonic()+(100 if name=="brokers" else 55),parent=job.context,
                                     live_only=True,allow_review=False)
        try:
            with runtime.scope(context):
                if name=="macro":
                    result = macro.get_macro_indicators()
                    count = len(result["indicators"])
                elif name=="news":
                    result = news.get_market_news(days=30,limit=25)
                    count = len(result["items"])
                else:
                    if not self.allow_llm or not llm.available("extract"):
                        self._step(job,name,"skipped",detail="Broker extraction needs a configured AI provider.")
                        return None
                    result = broker_reports.refresh_broker_reports(max_output_tokens=6000,on_progress=lambda text:self._progress(job,text))
                    count = sum(r.get("metrics_extracted",0) for r in result.get("refreshed",[]))
                    errors = sum(bool(r.get("error")) for r in result.get("refreshed",[]))
                    if errors or not count:
                        reasons = "; ".join(f"{r['publisher']}: {r['error']}" for r in result.get("refreshed",[]) if r.get("error"))
                        result["warning"] = f"{count} validated readings extracted. "+(reasons or "No validated broker readings returned.")
                json.dumps(result,allow_nan=False)
                warning = result.get("warning")
                self._step(job,name,"partial" if warning and count else "failed" if warning else "done",
                           detail=warning or f"{count} {name} readings retrieved",count=count)
                return result
        except Exception as exc:
            job.context.check()
            self._step(job,name,"failed",detail=f"Live retrieval failed ({type(exc).__name__}). No replacement data was used.")
            return None
        finally:
            context.closed = True

    def _pipeline(self, job: Job, use_llm: bool, *, analysis_only: bool) -> dict:
        names = [("macro","Macro indicators"),("news","Market news"),("brokers","Broker report extraction"),("analysis","AI market analysis")]
        with self.lock:
            job.steps = [{"id":key,"label":label,"status":"reused" if analysis_only and key!="analysis" else "queued"} for key,label in names]
        if not analysis_only:
            # Explicit updates bypass the HTTP cache; source failures remain visible.
            web.clear_cache()
            job.context.source_data = None
            reserved = 0
            for _ in range(3):
                if not self.source_slots.acquire(blocking=False):
                    for _ in range(reserved):
                        self.source_slots.release()
                    raise APIError(503,"Previous source requests are still finishing. Try again shortly.")
                reserved += 1
            futures = {}
            try:
                for name in ("macro","news","brokers"):
                    future = runtime.submit(self.source_pool,self._source,job,name)
                    futures[future] = name
                    future.add_done_callback(lambda _:self.source_slots.release())
            except Exception:
                for _ in range(3-len(futures)):
                    self.source_slots.release()
                raise
            results = {futures[future]:future.result() for future in as_completed(futures,timeout=120)}
            runtime.checkpoint()
            metrics = broker_reports.get_market_metrics()
            data = build_dashboard(metrics=metrics,economy=results.get("macro") or {"indicators":[]},
                                   headlines=results.get("news") or {"origin":"unavailable","items":[],"topic_counts":{}})
            data.update(retrieved_at=timestamp(),source_status=[dict(s) for s in job.steps if s["id"]!="analysis"])
            if not has_data(data):
                raise APIError(503,"No live data was retrieved. Review the source errors and try again.")
            self.workspace.save(data=data)
        else:
            data = self.workspace.read()["data"]
        if use_llm:
            self._step(job,"analysis","running",detail="Analyzing retrieved evidence with AI")
            try:
                analysis = analyze(data)
                self.workspace.save(analysis=analysis)
                self._step(job,"analysis","done",detail="AI analysis completed")
            except Exception as exc:
                runtime.checkpoint()
                self._step(job,"analysis","failed",detail=f"AI analysis failed ({type(exc).__name__}). Retrieved data is still available.")
        else:
            self._step(job,"analysis","skipped",detail="AI analysis is unavailable on this server. Live data is available.")
        check = (self.workspace.read().get("analysis") or {}).get("check") or {}
        return {"has_data":True,"partial":any(s["status"] in {"failed","partial","skipped"} for s in job.steps),
                "review_required":any(check.get(key) for key in ("not_found","wrong_source","unverified"))}

    def get_job(self, session: Session, job_id: str) -> Job:
        with self.lock:
            if job_id not in session.jobs:
                raise APIError(404, "This task is unavailable or has expired.")
            return session.jobs[job_id]

    def public_job(self, job: Job) -> dict:
        with self.lock:
            return {"id":job.id,"kind":job.kind,"status":job.status,"events":list(job.events),"result":job.result,"error":job.error,
                    "started_at":job.started_at,"completed_at":job.completed_at,"seconds":job.seconds,"steps":[dict(s) for s in job.steps]}

    def close(self) -> None:
        with self.lock:
            for session in self.sessions.values():
                for job in session.jobs.values():
                    job.context.cancelled.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.source_pool.shutdown(wait=False,cancel_futures=True)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, app: DashboardApp):
        self.app = app
        self.connections = threading.BoundedSemaphore(32)
        super().__init__(address, DashboardHandler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "OfficeDashboard"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, fmt, *args):
        pass  # URLs, cookies and questions are not copied into an HTTP access log.

    def _guard(self) -> None:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "").lower() not in hosts:
            raise APIError(403, "This request host is not allowed.")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            raise APIError(403, "This request origin is not allowed.")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise APIError(403, "Cross-site requests are not accepted.")

    def _send(self, status: int, content: bytes, kind: str, *, cookie: str | None = None, download: str | None = None):
        self.send_response(status)
        for key,value in {
            "Content-Type":kind,"Content-Length":str(len(content)),"Cache-Control":"no-store",
            "X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","X-Frame-Options":"DENY",
            "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
        }.items():
            self.send_header(key,value)
        if cookie:
            self.send_header("Set-Cookie", f"office_session={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, payload: dict, **kwargs):
        self._send(status,json.dumps(payload,ensure_ascii=False,allow_nan=False).encode("utf-8"),"application/json; charset=utf-8",**kwargs)

    def do_GET(self):
        try:
            self._guard()
            parts = urlsplit(self.path)
            static = {"/":("index.html","text/html"),"/app.js":("app.js","text/javascript"),"/styles.css":("styles.css","text/css"),"/favicon.svg":("favicon.svg","image/svg+xml")}
            if parts.path in static:
                name,kind = static[parts.path]
                self._send(200,(STATIC/name).read_bytes(),kind+"; charset=utf-8")
                return
            session, created = self.server.app.session(self.headers.get("Cookie"))
            cookie = session.id if created else None
            if parts.path=="/api/bootstrap":
                self._json(200,{"csrf":session.csrf,"ai_available":self.server.app.allow_llm and llm.available("chat"),
                                "brief_ai_available":self.server.app.allow_llm and llm.available("brief"),
                                "live_available":self.server.app.allow_live,"server_time":timestamp()},cookie=cookie)
            elif parts.path=="/api/dashboard":
                query = parse_qs(parts.query)
                if set(query)-{"offline","force"} or any(v not in (["0"],["1"]) for v in query.values()):
                    raise APIError(400,"Invalid data request options.")
                self._json(200,self.server.app.dashboard(offline=query.get("offline",["0"])[0]=="1"),cookie=cookie)
            elif parts.path=="/api/activity":
                self._json(200,{"activity":self.server.app.public_job(self.server.app.refresh_job) if self.server.app.refresh_job else None,
                                "retrieved_at":(self.server.app.workspace.read()["data"] or {}).get("retrieved_at")},cookie=cookie)
            elif parts.path.startswith("/api/jobs/"):
                pieces = parts.path.strip("/").split("/")
                if len(pieces) not in (3,4):
                    raise APIError(404,"Endpoint not found.")
                job = self.server.app.get_job(session,pieces[2])
                if len(pieces)==4:
                    if pieces[3]!="download" or job.kind!="brief" or job.status!="done" or not job.result:
                        raise APIError(404,"The brief is not ready to download.")
                    self._send(200,job.result["brief"].encode("utf-8"),"text/markdown; charset=utf-8",download=job.result["filename"])
                else:
                    self._json(200,self.server.app.public_job(job),cookie=cookie)
            else:
                raise APIError(404,"Page not found.")
        except APIError as exc:
            self._json(exc.status,{"error":exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json(500,{"error":"Data is temporarily unavailable. Please try again."})

    def do_POST(self):
        try:
            self._guard()
            session, created = self.server.app.session(self.headers.get("Cookie"))
            if created or not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""),session.csrf):
                raise APIError(403,"Your session has expired. Refresh the page.")
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type","").split(";")[0]!="application/json":
                raise APIError(415,"The request must use JSON.")
            try:
                length = int(self.headers.get("Content-Length","0"))
            except ValueError:
                raise APIError(400,"Invalid request length.")
            if not 0<length<=40000:
                raise APIError(413,"The request is empty or too large.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload,dict):
                raise APIError(400,"Invalid request format.")
            path = urlsplit(self.path).path
            if path in {"/api/refresh","/api/analyze"}:
                if payload.keys()-{"automatic"} or type(payload.get("automatic",False)) is not bool:
                    raise APIError(400,"Invalid update options.")
                result = self.server.app.start_refresh(session,automatic=payload.get("automatic",False),analysis_only=path.endswith("analyze"))
                self._json(200 if result.get("skipped") else 202,result)
            elif path in {"/api/ask","/api/brief"}:
                allowed = {"offline","use_llm","question"} if path.endswith("ask") else {"offline","use_llm"}
                if payload.keys()-allowed:
                    raise APIError(400,"The request contains unknown options.")
                job = self.server.app.start_job(session,"ask" if path.endswith("ask") else "brief",payload)
                self._json(202,{"id":job.id})
            elif path=="/api/reset":
                with self.server.app.lock:
                    if session.active:
                        raise APIError(409,"Cancel the running task first.")
                    session.agent.reset()
                self._json(200,{"ok":True})
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                pieces = path.strip("/").split("/")
                if len(pieces)!=4:
                    raise APIError(404,"Endpoint not found.")
                job = self.server.app.get_job(session,pieces[2])
                with self.server.app.lock:
                    if job.status not in TERMINAL:
                        job.context.cancelled.set()
                        job.events.append("Finishing the task")
                self._json(200,{"ok":True})
            else:
                raise APIError(404,"Endpoint not found.")
        except APIError as exc:
            self._json(exc.status,{"error":exc.message})
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._json(400,{"error":"Invalid JSON request."})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json(500,{"error":"The service is temporarily unavailable. Please try again."})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--allow-llm",action="store_true",help="compatibility flag; configured AI is enabled by default")
    parser.add_argument("--allow-live",action="store_true",help="compatibility flag; live sources are enabled by default")
    parser.add_argument("--no-ai",action="store_true",help="disable model calls, including broker extraction")
    args = parser.parse_args()
    if not 0<=args.port<=65535:
        parser.error("port must be 0–65535")
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8")
    app = DashboardApp(allow_llm=not args.no_ai,allow_live=True)
    server = DashboardServer(("127.0.0.1",args.port),app)
    print(f"London Office dashboard: http://127.0.0.1:{server.server_port}",flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__=="__main__":
    main()
