"""Business-facing dashboard data, derived from existing skills without mixing broker definitions."""

import json
from datetime import datetime, timezone

from . import config, references, runtime
from .domain import METRICS, SUBMARKETS, period_info, valid_metric
from .skills import broker_reports, macro, news


def select_cards(rows: list[dict], economy: dict, sources=()) -> list[dict]:
    """Select exact measures, relabelling narrower coverage rather than conflating definitions."""
    cards = []
    for market, preferred, variants in [
        ("City", "JLL", [("vacancy_rate", "City vacancy (all grades)", ""),
                          ("grade_a_vacancy_rate", "City Grade A vacancy", "Grade A only; all-grade vacancy unavailable.")]),
        ("West End", "Savills", [("vacancy_rate", "West End vacancy (all grades)", ""),
                                 ("grade_a_vacancy_rate", "West End Grade A vacancy", "Grade A only; all-grade vacancy unavailable.")]),
        ("City", "JLL", [("prime_rent_psf", "City prime headline rent", ""),
                          ("grade_a_rent_psf", "City average Grade A rent", "Average achieved rent; prime headline rent unavailable.")]),
    ]:
        card = {"label":variants[0][1], "reading":None, "coverage_note":"No verified reading for this measure in the retrieved sources."}
        for metric, label, note in variants:
            matches = [r for r in rows if r.get("metric")==metric and r.get("submarket")==market and valid_metric(r)
                       and "column not verified" not in (r.get("note") or "").lower()
                       and "review flag" not in (r.get("note") or "").lower()]
            if matches:
                latest = max(matches, key=lambda r:(period_info(r["period"])[1], r.get("publisher")==preferred, r.get("publisher", "")))
                card = {"label":label, "reading":latest, "coverage_note":note}
                break
        cards.append(card)
    rate = next((r for r in economy.get("indicators",[]) if r["indicator"]=="bank_rate"), None)
    cards.append({"label":"UK Bank Rate", "reading":None if rate is None else {
        **rate, "value":rate["latest"], "publisher":rate["source"], "period_label":rate["as_of"], "stale":False,
    }, "coverage_note":"" if rate else "No current Bank Rate reading was retrieved."})
    source_ids = {references.key(source):source["id"] for source in sources}
    for card in cards:
        if card["reading"]:
            reading = card["reading"]
            card["reading"] = {**reading, "source_id":source_ids.get(references.key(reading),reading.get("source_id"))}
    return cards


def build_dashboard(*, metrics=None, economy=None, headlines=None) -> dict:
    metrics = broker_reports.get_market_metrics() if metrics is None else metrics
    economy = macro.get_macro_indicators() if economy is None else economy
    headlines = news.get_market_news(days=30, limit=12) if headlines is None else headlines
    rows = []
    for metric, by_market in metrics["metrics"].items():
        for market, readings in by_market.items():
            rows.extend({**reading, "metric":metric, "submarket":market} for reading in readings)
    register = references.catalog([rows, economy, headlines])
    ids = {(r["publisher"], r["period"], r["url"]):r["id"] for r in register}
    for row in rows:
        row["source_id"] = ids.get(references.key(row))
    cards = select_cards(rows, economy, register)
    captured = {}
    if not runtime.live_only():
        for name in ("news", "macro"):
            path = config.SNAPSHOT_DIR/f"{name}.json"
            if path.exists():
                captured[name] = json.loads(path.read_text(encoding="utf-8")).get("captured_at")
    return {"generated_at":datetime.now(timezone.utc).isoformat(), "offline":config.offline(),
            "snapshot_captured_at":captured, "cards":cards, "rows":rows,
            "metrics":METRICS, "submarkets":SUBMARKETS, "macro":economy, "news":headlines,
            "sources":register, "changes":metrics["changes_vs_previous_period"],
            "disagreements":metrics["publisher_disagreements"], "invalid_rows_skipped":metrics["invalid_rows_skipped"]}
