"""macro_data skill: UK rates and economy from the Bank of England and ONS (free, no API key)."""

import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from .. import config, runtime, web
from .base import Skill

BOE_URL = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes"
           "&Datefrom={start}&Dateto=now&SeriesCodes={codes}&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
ONS_URL = "https://www.ons.gov.uk/{path}/timeseries/{cdid}/{dataset}/data"

INDICATORS = {
    "bank_rate": {"label": "Bank Rate", "unit": "%", "source": "Bank of England", "boe": "IUDBEDR",
                  "why": "Sets borrowing costs; drives property yields and investor appetite."},
    "sonia": {"label": "SONIA overnight rate", "unit": "%", "source": "Bank of England", "boe": "IUDSOIA",
              "why": "Benchmark for floating-rate real estate debt."},
    "gilt_10y": {"label": "10-year gilt yield (nominal zero-coupon)", "unit": "%", "source": "Bank of England",
                 "boe": "IUDMNZC", "why": "Risk-free rate that prime office yields are priced against."},
    "cpi": {"label": "CPI inflation (annual rate)", "unit": "%", "source": "ONS", "cdid": "d7g7", "dataset": "mm23",
            "path": "economy/inflationandpriceindices", "why": "Drives rate expectations and indexed rent reviews."},
    "unemployment": {"label": "Unemployment rate (16+)", "unit": "%", "source": "ONS", "cdid": "mgsx",
                     "dataset": "lms", "path": "employmentandlabourmarket/peoplenotinwork/unemployment",
                     "why": "Office-based employment growth drives space demand."},
    "gdp_qoq": {"label": "GDP growth (quarter on quarter)", "unit": "%", "source": "ONS", "cdid": "ihyq",
                "dataset": "qna", "path": "economy/grossdomesticproductgdp",
                "why": "Business confidence and expansion plans."},
}


def _fetch_boe(keys: list[str]) -> dict:
    codes = [INDICATORS[k]["boe"] for k in keys]
    start = date(date.today().year - 1, 1, 1).strftime("%d/%b/%Y")
    text = web.get_text(BOE_URL.format(start=start, codes=",".join(codes)))
    rows = list(csv.DictReader(io.StringIO(text)))
    out = {}
    for key, code in zip(keys, codes):
        # The newest rows can be blank for a day or two while the Bank publishes; keep non-empty values only.
        series = [(datetime.strptime(r["DATE"], "%d %b %Y").date().isoformat(), float(r[code]))
                  for r in rows if r.get(code, "").strip()]
        out[key] = series
    return out


def _fetch_ons(key: str) -> list[tuple[str, float]]:
    spec = INDICATORS[key]
    data = web.get_json(ONS_URL.format(path=spec["path"], cdid=spec["cdid"], dataset=spec["dataset"]))
    points = data.get("months") or data.get("quarters") or data.get("years")
    return [(p["date"], float(p["value"])) for p in points if p.get("value") not in (None, "")]


def fetch_live(keys: list[str]) -> dict:
    # Warm the cache for every ONS series in parallel; the per-series parsers below then read from it.
    web.get_many([ONS_URL.format(path=INDICATORS[k]["path"], cdid=INDICATORS[k]["cdid"],
                                 dataset=INDICATORS[k]["dataset"]) for k in keys if "cdid" in INDICATORS[k]])
    series = {}
    boe_keys = [k for k in keys if "boe" in INDICATORS[k]]
    if boe_keys:
        series.update(_fetch_boe(boe_keys))
    for k in keys:
        if "cdid" in INDICATORS[k]:
            series[k] = _fetch_ons(k)
    return series


def _summarise(key: str, points: list[tuple[str, float]], origin: str) -> dict:
    spec = INDICATORS[key]
    latest_date, latest = points[-1]
    if "boe" in spec:
        start = date(date.today().year - 1, 1, 1).strftime("%d/%b/%Y")
        url = BOE_URL.format(start=start, codes=spec["boe"])
    else:
        url = ONS_URL.format(path=spec["path"], cdid=spec["cdid"], dataset=spec["dataset"])
    summary = {"indicator": key, "label": spec["label"], "latest": latest, "unit": spec["unit"], "url":url,
               "as_of": latest_date, "source": spec["source"], "why_it_matters": spec["why"], "origin": origin}
    if len(points) > 1:
        # Daily series: compare with ~1 month ago; monthly/quarterly series: with the previous release.
        back = 22 if "boe" in spec else 1
        prev_date, prev = points[max(0, len(points) - 1 - back)]
        summary.update(previous=prev, previous_as_of=prev_date, change=round(latest - prev, 3))
    summary["recent"] = points[-6:] if "boe" not in spec else points[-1::-22][:6][::-1]
    return summary


def load_snapshot() -> dict:
    path = config.SNAPSHOT_DIR / "macro.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: [tuple(p) for p in v] for k, v in data["series"].items()}


def get_macro_indicators(indicators: list[str] | None = None) -> dict:
    keys = [k for k in (indicators or INDICATORS) if k in INDICATORS]
    provided = runtime.source_data("macro")
    if provided is not None:
        return {**provided, "indicators":[r for r in provided["indicators"] if r["indicator"] in keys]}
    if runtime.live_only():
        series, errors = {}, []
        boe_keys = [k for k in keys if "boe" in INDICATORS[k]]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [("Bank of England",runtime.submit(pool,_fetch_boe,boe_keys))] if boe_keys else []
            futures += [(k,runtime.submit(pool,_fetch_ons,k)) for k in keys if "cdid" in INDICATORS[k]]
            for key,future in futures:
                try:
                    value = future.result()
                    if key=="Bank of England":
                        series.update(value)
                    else:
                        series[key] = value
                except runtime.DeadlineExceeded:
                    raise
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}")
        out = {"indicators":[_summarise(k,series[k],"live") for k in keys if series.get(k)]}
        if errors:
            out["warning"] = "Live macro sources unavailable: "+"; ".join(errors)+". No replacement data was used."
        return out
    origin, warning = "live", None
    if config.offline():
        series, origin = load_snapshot(), "snapshot"
    else:
        try:
            series = fetch_live(keys)
        except runtime.DeadlineExceeded:
            raise
        except Exception as exc:  # network down, site changed: degrade to the bundled snapshot
            if runtime.live_only():
                return {"indicators":[], "warning":f"Live macro retrieval failed ({type(exc).__name__}). No replacement data was used."}
            series, origin = load_snapshot(), "snapshot"
            warning = f"Live fetch failed ({type(exc).__name__}); showing bundled snapshot."
    results = [_summarise(k, series[k], origin) for k in keys if series.get(k)]
    out = {"indicators": results}
    if warning:
        out["warning"] = warning
    return out


SKILL = Skill(
    name="get_macro_indicators",
    description=(
        "Latest UK macro indicators that move the London office market: Bank Rate, SONIA, 10-year gilt yield "
        "(Bank of England) and CPI inflation, unemployment, GDP growth (ONS). Returns latest value, date, "
        "change vs the previous reading, a short history and why each matters. Use for questions about "
        "interest rates, the economy, financing conditions or investment yields."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "indicators": {
                "type": "array",
                "items": {"type": "string", "enum": list(INDICATORS)},
                "description": "Subset to return; omit for all.",
            }
        },
        "additionalProperties": False,
    },
    run=get_macro_indicators,
)
