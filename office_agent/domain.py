"""Shared market vocabulary, period parsing and validation; no skill dependencies."""

import calendar
import math
import re
from datetime import date

METRICS = {
    "take_up_sqft": "Leasing take-up in the period (sq ft)",
    "take_up_ytd_sqft": "Year-to-date take-up (sq ft)",
    "vacancy_rate": "Vacancy / availability rate, all grades (%)",
    "grade_a_vacancy_rate": "Vacancy rate of Grade A space only (%)",
    "prime_rent_psf": "Prime headline rent (GBP per sq ft per year)",
    "grade_a_rent_psf": "Average achieved Grade A rent (GBP per sq ft per year)",
    "prime_rent_growth_yoy": "Prime rent change year-on-year (%)",
    "prime_rent_growth_qoq": "Prime rent change quarter-on-quarter (%)",
    "under_offer_sqft": "Space under offer (sq ft)",
    "active_demand_change_qoq": "Change in active occupier demand vs previous quarter (%)",
    "active_demand_change_mom": "Change in active occupier demand vs previous month (%)",
    "under_construction_spec_sqft": "Speculative space under construction (sq ft)",
    "new_starts_sqft": "New construction starts (sq ft)",
    "refurb_share_of_starts": "Refurbishments as share of construction starts (%)",
    "completions_sqft": "Space completed / delivered (sq ft)",
    "pre_let_share_of_completions": "Share of upcoming completions already pre-let (%)",
    "ai_sector_take_up_sqft": "Take-up by AI-sector occupiers (sq ft)",
    "investment_volume_gbp": "Office investment volume (GBP)",
}
# Running totals: two readings cover different lengths of time, so their difference is not a market change.
CUMULATIVE_METRICS = {"take_up_ytd_sqft"}
SUBMARKETS = ["Central London", "City", "West End", "Canary Wharf", "Midtown", "King's Cross", "Southbank",
              "Fringe / Tech Belt"]

SUBMARKET_ALIASES = {
    "Central London": r"\bcentral london\b|中伦敦|伦敦中心区",
    "City": r"\bcity(?: of london)?\b|金融城|伦敦城",
    "West End": r"\bwest end\b|西区|西端",
    "Canary Wharf": r"\bcanary wharf\b|金丝雀码头|金丝雀区",
    "Midtown": r"\bmidtown\b|中城区",
    "King's Cross": r"\bking['’]?s cross\b|国王十字",
    "Southbank": r"\bsouth\s?bank\b|南岸",
    "Fringe / Tech Belt": r"\b(?:fringe|tech belt)\b|科技带",
}


def mentioned_submarkets(text: str) -> list[str]:
    return [market for market, pattern in SUBMARKET_ALIASES.items() if re.search(pattern, text, re.I)]


def fmt_value(value: float, unit: str) -> str:
    if unit == "sq ft":
        return f"{value / 1e6:.2f}m sq ft" if value >= 1e6 else f"{value:,.0f} sq ft"
    if unit == "%":
        return f"{value:g}%"
    if unit == "GBP":
        return f"GBP {value:,.2f}"
    if unit and unit.startswith("GBP"):
        return f"GBP {value:,.2f}/sq ft"
    return f"{value:g} {unit or ''}".strip()


def period_kind(period: str) -> str:
    if re.search(r"Q\d", period):
        return "quarter"
    if re.search(r"H\d", period):
        return "half-year"
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return "month"
    return "year"


def period_info(period: str) -> tuple[str, date]:
    """Human label and last day of the period, e.g. '2026-Q3' -> ('Q3 2026', 2026-09-30)."""
    if not isinstance(period, str) or not re.fullmatch(r"(?:19|20)\d{2}(?:-Q[1-4]|-H[12]|-(?:0[1-9]|1[0-2]))?", period):
        raise ValueError("Invalid market period.")
    year = int(period[:4])
    if m := re.search(r"Q(\d)", period):
        q = int(m.group(1))
        return f"Q{q} {year}", date(year, 3 * q, calendar.monthrange(year, 3 * q)[1])
    if m := re.search(r"H(\d)", period):
        month = 6 * int(m.group(1))
        return f"H{m.group(1)} {year}", date(year, month, calendar.monthrange(year, month)[1])
    if re.fullmatch(r"\d{4}-\d{2}", period):
        month = int(period[5:7])
        return f"{calendar.month_abbr[month]} {year}", date(year, month, calendar.monthrange(year, month)[1])
    return f"calendar year {year}", date(year, 12, 31)


def valid_metric(row: dict, *, evidence: bool = False) -> bool:
    """Validate both model rows and persisted rows, including finite values and metric units."""
    if not isinstance(row, dict):
        return False
    metric, value = row.get("metric"), row.get("value")
    if not isinstance(metric, str) or metric not in METRICS or row.get("submarket") not in SUBMARKETS:
        return False
    if type(value) not in (int, float) or abs(value) > 1e15 or not math.isfinite(value):
        return False
    try:
        period_info(row.get("period"))
    except (TypeError, ValueError):
        return False
    unit = "sq ft" if metric.endswith("_sqft") else "GBP" if metric == "investment_volume_gbp" else "GBP/sq ft/yr" if metric.endswith("_psf") else "%"
    if row.get("unit") != unit:
        return False
    if unit != "%" and value < 0:
        return False
    if unit == "%" and abs(value) > 10000:
        return False
    if metric in {"vacancy_rate", "grade_a_vacancy_rate", "refurb_share_of_starts", "pre_let_share_of_completions"} and not 0 <= value <= 100:
        return False
    for key in ("definition", "note", "url", "publisher", "report"):
        if row.get(key) is not None and (not isinstance(row[key], str) or len(row[key]) > 8000):
            return False
    if row.get("published"):
        try:
            if isinstance(row["published"], str) and re.fullmatch(r"\d{4}-\d{2}", row["published"]):
                period_info(row["published"])  # curated snapshots can have month precision
            else:
                date.fromisoformat(row["published"])
        except (TypeError, ValueError):
            return False
    return not evidence or isinstance(row.get("evidence"), str) and 8 <= len(row["evidence"]) <= 8000
