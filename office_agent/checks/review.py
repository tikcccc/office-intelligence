"""Optional structured judgement of headline relevance and extracted broker figures.

Thresholds are heuristic and do not guarantee factual correctness. Unavailable or failed provider calls
degrade to an unchecked result. Borderline extraction classifications remain visible as review flags.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

from ..models import jev
from .. import runtime
from ..domain import METRICS, fmt_value

RELEVANT_MIN = 0.5      # headline relevance below this is dropped
FORECAST_MAX = 0.8      # a figure judged a forecast above this is dropped
SLICE_MAX = 0.8         # a figure judged to describe only part of the market above this is dropped
SLICE_FLAG = 0.6        # ...and above this is kept but marked lower confidence
METRIC_MIN = 0.3        # ambiguous metric classifications are flagged rather than silently dropped
# Metrics that are defined as a slice of the market, so "describes only part of the market" is expected.
SEGMENT_METRICS = {"ai_sector_take_up_sqft"}

NEWS_QUESTION = ("This headline is about the London office property market: office leasing or occupier moves, "
                 "rents, vacancy or supply, development or refurbishment of offices, office investment deals, or "
                 "policy that affects office property. It is not about an unrelated event that merely mentions "
                 "an office.")
FORECAST_Q = "The figure {value} in this sentence is a forecast, expectation or projection, not something already recorded."
SLICE_Q = ("The figure {value} covers a narrower part of the market than '{label}' for the {submarket} office "
           "market - for example only a size band (deals under 10,000 sq ft), a building type (towers), a deal type "
           "(lease regears or renewals), a sector, or a single building.")
METRIC_Q = "What does the figure {value} in this sentence measure?"

_relevance_cache: dict[str, float] = {}
_lock = threading.Lock()


def enabled() -> bool:
    context = runtime.current()
    while context:
        if not context.allow_review:
            return False
        context = context.parent
    return jev.available() and os.environ.get("OFFICE_AGENT_USE_JEV", "1").lower() not in {"0", "false", "no"}


def news_relevance(titles: list[str]) -> dict[str, float | None]:
    """Probability each headline is office-market news; None where Jev could not answer."""
    with _lock:
        todo = [t for t in dict.fromkeys(titles) if t not in _relevance_cache]

    def ask(title):
        try:
            return jev.judge(title, {"q": {"type": "noul", "instructions": NEWS_QUESTION}})["q"]["noul"]
        except runtime.DeadlineExceeded:
            raise
        except Exception:
            return None

    with ThreadPoolExecutor(8) as pool:
        futures = [runtime.submit(pool, ask, title) for title in todo]
        for title, future in zip(todo, futures):
            p = future.result()
            if p is not None:
                with _lock:
                    _relevance_cache[title] = p
                    if len(_relevance_cache) > 1024:
                        del _relevance_cache[next(iter(_relevance_cache))]
    return {t: _relevance_cache.get(t) for t in titles}


def extraction_questions(metric: str, submarket: str, value: float, unit: str | None) -> dict:
    v = fmt_value(value, unit or "")
    return {"forecast": {"type": "noul", "instructions": FORECAST_Q.format(value=v)},
            "slice": {"type": "noul", "instructions": SLICE_Q.format(value=v, submarket=submarket,
                                                                       label=METRICS[metric].split(" (")[0])},
            "metric": {"type": "choice", "instructions": METRIC_Q.format(value=v), "criteria": dict(METRICS)}}


def review_row(row: dict, evidence: str) -> tuple[str, str]:
    """('keep' | 'flag' | 'drop', reason) for one extracted figure."""
    a = jev.judge(evidence, extraction_questions(row["metric"], row["submarket"], row["value"], row.get("unit")))
    metric_p = a["metric"].get("probabilities", {}).get(row["metric"], 0.0)
    slice_p = 0.0 if row["metric"] in SEGMENT_METRICS else a["slice"]["noul"]
    if a["forecast"]["noul"] >= FORECAST_MAX:
        return "drop", f"looks like a forecast (p={a['forecast']['noul']:.2f})"
    if slice_p >= SLICE_MAX:
        return "drop", f"describes only part of the market (p={slice_p:.2f})"
    if metric_p < METRIC_MIN:
        return "flag", f"figure may measure {a['metric'].get('choice')} rather than {row['metric']}"
    if slice_p >= SLICE_FLAG:
        return "flag", f"may describe only part of the market (p={slice_p:.2f})"
    return "keep", ""


def review_extraction(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (rows to store, rows dropped with reasons). Flagged rows get a note the agent reads."""
    def check(row):
        try:
            runtime.checkpoint()
            evidence = row["note"].split('"', 1)[1].rsplit('"', 1)[0]
            return review_row(row, evidence)
        except runtime.DeadlineExceeded:
            raise
        except Exception as exc:  # Jev down: keep the row, unreviewed
            return "flag", f"not reviewed ({type(exc).__name__})"

    with ThreadPoolExecutor(8) as pool:
        verdicts = [f.result() for f in [runtime.submit(pool, check, row) for row in rows]]
    kept, dropped = [], []
    for row, (verdict, reason) in zip(rows, verdicts):
        if verdict == "drop":
            dropped.append({**row, "review": reason})
        elif verdict == "flag":
            kept.append({**row, "note": f"Review flag - {reason}. {row['note']}"})
        else:
            kept.append(row)
    return kept, dropped
