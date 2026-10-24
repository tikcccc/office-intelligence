"""Plain-text rendering of skill results, used by slash commands and when no LLM is configured."""

from .domain import METRICS, fmt_value
from . import references
from .security import safe_text


def news(result: dict) -> str:
    lines = [f"London office news ({result['origin']}: {result['headlines_collected']} headlines, "
             f"{result['events_matching']} distinct stories)"]
    if result.get("warning"):
        lines.append(f"! {result['warning']}")
    counts = ", ".join(f"{k} {v}" for k, v in sorted(result["topic_counts"].items(), key=lambda x: -x[1]))
    if result.get("dropped_as_unrelated"):
        lines.append(f"Dropped as unrelated by Jev: {len(result['dropped_as_unrelated'])} "
                     f"(e.g. {result['dropped_as_unrelated'][0][:70]})")
    lines.append(f"Topics: {counts}\n")
    for n in result["items"]:
        subs = f" [{', '.join(n['submarkets'])}]" if n["submarkets"] else ""
        lines.append(f"[{n['impact'].upper():6}] {n['title']}{subs}")
        outlets = f" | {n['outlets']} outlets" if n["outlets"] > 1 else ""
        lines.append(f"         {n['source']} | {(n['published'] or '')[:10]} | {', '.join(n['topics'])}{outlets}")
        if url := references.display_url(n.get("url")):
            lines.append(f"         {url}")
    return "\n".join(lines)


def metrics(result: dict) -> str:
    lines = []
    catalog = references.catalog(result)
    ids = {(r["publisher"], r["period"], r["url"]):r["id"] for r in catalog}
    for metric, by_sub in result["metrics"].items():
        lines.append(f"\n{METRICS.get(metric, metric)}")
        for sub, readings in by_sub.items():
            for r in readings:
                stale = "  [stale]" if r["stale"] else ""
                note = f"  ({r['note']})" if r["note"] and not r["note"].startswith("Evidence") else ""
                lines.append(f"  {sub:16} {fmt_value(r['value'], r['unit']):>18}  {r['publisher']}, "
                             f"{r['period_label']} [{r['origin']}, {ids[references.key(r)]}]{stale}{note}")
    if result["changes_vs_previous_period"]:
        lines.append("\nWhat changed since each publisher's previous edition:")
        for c in result["changes_vs_previous_period"]:
            lines.append(f"  {METRICS.get(c['metric'], c['metric'])} / {c['submarket']} [{c['publisher']}]: "
                         f"{c['from']} -> {c['to']} ({c['change']})")
    if result["publisher_disagreements"]:
        lines.append("\nPublishers disagree (shown side by side, never averaged):")
        for d in result["publisher_disagreements"]:
            lines.append(f"  {METRICS.get(d['metric'], d['metric'])} / {d['submarket']}: "
                         + "; ".join(d["readings"]))
    if result.get("invalid_rows_skipped"):
        lines.append(f"! Skipped {result['invalid_rows_skipped']} invalid stored rows.")
    if catalog:
        lines.extend(["", "Sources:", *references.lines(result)])
    return "\n".join(lines).strip() or "No figures stored for that filter."


def macro(result: dict) -> str:
    lines = []
    if result.get("warning"):
        lines.append(f"! {result['warning']}")
    for m in result["indicators"]:
        change = f"  ({m['change']:+.2f} vs {m['previous_as_of']})" if "change" in m else ""
        lines.append(f"{m['label']:42} {m['latest']:>6g}{m['unit']}  as of {m['as_of']}{change}  [{m['source']}, {m['origin']}]")
        if url := references.display_url(m.get("url")):
            lines.append(f"  {url}")
    return "\n".join(lines)


def brief(result: dict) -> str:
    return f"{result['brief']}\n\n(saved to {result['saved_to']}, written by {result['written_by']})"


def refresh(result: dict) -> str:
    if "error" in result:
        return result["error"]
    lines = []
    for r in result["refreshed"]:
        if "error" in r:
            lines.append(f"{r['publisher']}: error - {r['error']}")
            continue
        lines.append(f"{r['publisher']}: {r['metrics_extracted']} figures stored ({r['flagged']} flagged for review)")
        lines += [f"    dropped: {d}" for d in r["dropped_by_review"]]
    lines.append("Reviewed by Jev." if result.get("reviewed_by_jev") else
                 "Review incomplete; check flagged/unreviewed rows." if result.get("review_enabled") else "Not reviewed (Jev off).")
    return "\n".join(lines)


BY_SKILL = {
    "get_market_news": news,
    "get_market_metrics": metrics,
    "get_macro_indicators": macro,
    "generate_weekly_brief": brief,
    "refresh_broker_reports": refresh,
}


def skill_result(name: str, result: dict) -> str:
    if "error" in result:
        return safe_text(f"Unable to complete {name}: {result['error']}")
    return safe_text(BY_SKILL[name](result))


def sources(result: dict) -> str:
    if "error" in result:
        return safe_text(result["error"])
    return "\n".join(["Market data source catalog:", *references.lines(result)])
