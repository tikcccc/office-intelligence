"""Run log: one JSON line per question or command in data/runs.jsonl.

Each line records what was asked, which models and skills ran, how long each took, the tokens and list-price
cost, the figure check, and how it ended (answered, deadline, fallback). It is what you read to answer "why
was that slow", "what did that cost", or "which answers had unchecked figures".
"""

import json
import threading
import time
import os
import hashlib
from datetime import datetime, timezone

from . import config, runtime
from .models import llm
from .security import safe_text

_lock = threading.Lock()


def start(kind: str, text: str) -> dict:
    context = runtime.RunContext(time.monotonic() + config.QUESTION_DEADLINE_SECONDS,
                                 offline=config.offline(), parent=runtime.current())
    keep_input = os.environ.get("OFFICE_AGENT_LOG_INPUT", "0").lower() in {"1", "true", "yes"}
    return {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
            "run_id": context.run_id, "input": safe_text(text)[:500] if keep_input else "[input omitted]",
            "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "offline": config.offline(), "models": {r: ":".join(llm.model_for(r)) for r in llm.ROLES},
            "skills": [], "_t0": time.monotonic(), "_context": context}


def skill(run: dict, name: str, args: dict, seconds: float, output: dict) -> None:
    with _lock:  # skills from one turn report from parallel threads
        if run.get("_finished"):
            return
        run["skills"].append({"name": name, "args": args, "seconds": round(seconds, 2),
                              "error": output.get("error") if isinstance(output, dict) else None})


def finish(run: dict) -> dict:
    context = run.pop("_context")
    with context.lock:
        context.closed = True
        context.cancelled.set()
        calls = list(context.calls)
    with _lock:
        run["_finished"] = True
    cost: dict[str, float] = {}
    for c in calls:
        if c.get("cost") is not None:
            cost[c["currency"]] = round(cost.get(c["currency"], 0) + c["cost"], 5)
    run.update(seconds=round(time.monotonic() - run.pop("_t0"), 2), llm_calls=calls, cost=cost,
               tokens={"input": sum(c["input_tokens"] for c in calls), "output": sum(c["output_tokens"] for c in calls)})
    run.setdefault("outcome", "done")
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _lock, open(config.RUN_LOG_PATH, "a", encoding="utf-8") as f:
            public = {k: v for k, v in run.items() if not k.startswith("_")}
            f.write(safe_text(json.dumps(public, ensure_ascii=False, default=str)) + "\n")
    except OSError:
        pass  # logging must never break an answer
    return run


def recent(n: int = 10) -> list[dict]:
    try:
        lines = config.RUN_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    results = []
    for line in lines[-max(1, min(n, 1000)):]:
        try:
            results.append(json.loads(line))
        except (ValueError, TypeError):
            continue  # tolerate a partial final line after an interrupted process
    return results


def summary(n: int = 10) -> str:
    runs = recent(n)
    if not runs:
        return "No runs logged yet."
    lines = [f"Last {len(runs)} runs (data/runs.jsonl):"]
    totals: dict[str, float] = {}
    for r in runs:
        for cur, amount in r.get("cost", {}).items():
            totals[cur] = totals.get(cur, 0) + amount
        cost = ", ".join(f"{v:.4f} {k}" for k, v in r.get("cost", {}).items()) or "-"
        check = r.get("check") or {}
        flags = sum(len(check.get(key, [])) for key in ("not_found", "wrong_source", "unverified"))
        lines.append(f"  {r['time'][11:19]} {r['kind']:8} {r['outcome']:20} {r['seconds']:6.1f}s  "
                     f"skills {len(r['skills'])}  tokens {r['tokens']['input']:,}/{r['tokens']['output']:,}  "
                     f"cost {cost}  unchecked figures {flags}  | {r['input'][:50]}")
    lines.append("Total list-price cost: " + (", ".join(f"{v:.4f} {k}" for k, v in totals.items()) or "-"))
    return safe_text("\n".join(lines))
