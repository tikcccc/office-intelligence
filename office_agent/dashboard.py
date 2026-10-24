"""Business-facing dashboard data, derived from existing skills without mixing broker definitions."""

import json
from datetime import datetime, timezone

from . import config, references, runtime
from .domain import METRICS, SUBMARKETS, period_info
from .skills import broker_reports, macro, news


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
    cards = []
    for name, metric, market, publisher in [
        ("City vacancy rate", "vacancy_rate", "City", "JLL"),
        ("West End vacancy rate", "vacancy_rate", "West End", "Savills"),
        ("City prime rent", "prime_rent_psf", "City", "JLL"),
    ]:
        matches = [r for r in rows if r["metric"]==metric and r["submarket"]==market]
        preferred = [r for r in matches if r["publisher"]==publisher] or matches
        latest = max(preferred, key=lambda r:period_info(r["period"])[1]) if preferred else None
        cards.append({"label":name, "reading":latest})
    rate = next((r for r in economy["indicators"] if r["indicator"]=="bank_rate"), None)
    cards.append({"label":"UK Bank Rate", "reading":None if rate is None else {
        **rate, "value":rate["latest"], "publisher":rate["source"], "period_label":rate["as_of"],
        "source_id":ids.get(references.key(rate)), "stale":False,
    }})
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
