"""Chat CLI.  Run:  python -m office_agent  [--offline] [--no-llm]"""

import argparse
import sys
import time

from . import config, render, runlog, runtime
from .security import safe_text
from .agent import Agent
from .models import llm
from .skills import SKILLS

BANNER = """London Office Market Monitor
Ask a question in plain language, or use a command:
  /brief     generate this week's market brief      /news [topic]   latest news
  /metrics [submarket]   broker figures             /macro          rates & economy
  /refresh   re-read broker pages (needs LLM)       /offline on|off  switch data mode
  /runs [n]  recent runs: time, tokens, cost        /reset          clear conversation
  /sources [submarket]   source links, periods and confidence
  /quit
"""

COMMANDS = {
    "/brief": ("generate_weekly_brief", None),
    "/news": ("get_market_news", "topic"),
    "/metrics": ("get_market_metrics", "submarket"),
    "/sources": ("get_market_metrics", "submarket"),
    "/macro": ("get_macro_indicators", None),
    "/refresh": ("refresh_broker_reports", None),
}


def status(agent: Agent) -> str:
    mode = "offline snapshot" if config.offline() else "live data (snapshot fallback)"
    brain = ":".join(llm.model_for("chat")) if agent.use_llm else "keyword routing (no LLM key)"
    return f"[data: {mode} | reasoning: {brain}]"


def show_tool(name: str, args: dict) -> None:
    detail = ", ".join(f"{k}={v}" for k, v in args.items())
    print(safe_text(f"  > using skill {name}({detail})"), flush=True)


def handle_command(agent: Agent, line: str) -> bool:
    """Returns False to exit."""
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    if cmd in ("/quit", "/exit"):
        return False
    if cmd == "/help":
        print(BANNER)
    elif cmd == "/reset":
        agent.reset()
        print("Conversation cleared.")
    elif cmd == "/offline":
        config.set_offline(arg != "off")
        print(status(agent))
    elif cmd == "/runs":
        print(runlog.summary(int(arg) if arg.isdigit() else 10))
    elif cmd in COMMANDS:
        name, param = COMMANDS[cmd]
        args = {param: arg} if param and arg else {}
        if name == "generate_weekly_brief":
            args["use_llm"] = agent.use_llm
        show_tool(name, args)
        run = runlog.start("command", line)
        started = time.monotonic()
        try:
            with runtime.scope(run["_context"]):
                output = agent.run_skill(name, args)
                runlog.skill(run, name, args, time.monotonic() - started, output)
                run["check"] = output.get("figure_check")
                run["outcome"] = "failed" if output.get("error") else "done"
        except runtime.DeadlineExceeded:
            output = {"error": "Command deadline reached."}
            run["outcome"] = "deadline"
        finally:
            runlog.finish(run)
        print(render.sources(output) if cmd == "/sources" else render.skill_result(name, output))
    else:
        print(safe_text(f"Unknown command {cmd}. Type /help."))
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="London office market monitoring agent")
    parser.add_argument("--offline", action="store_true", help="use the bundled data snapshot only")
    parser.add_argument("--no-llm", action="store_true", help="don't call MiMo; route by keywords")
    parser.add_argument("--no-repair", action="store_true", help="skip the optional one-pass answer correction")
    parser.add_argument("-q", "--question", help="ask one question and exit")
    opts = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    if opts.offline:
        config.set_offline(True)
    agent = Agent(use_llm=False if opts.no_llm else None, repair_answers=not opts.no_repair)

    if opts.question:
        line = opts.question.strip()
        if line.startswith("/"):
            handle_command(agent, line)
        else:
            print(agent.ask(line, on_tool=show_tool))
        return

    print(BANNER)
    print(status(agent), f"skills: {', '.join(SKILLS)}\n")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            if not handle_command(agent, line):
                break
            continue
        print(f"\nagent> {agent.ask(line, on_tool=show_tool)}\n")


if __name__ == "__main__":
    main()
