"""Check an answer's figures against the skill results it was based on.

The model is told to use only figures from skill results; this is the check that it did. Every number in the
answer is looked up in the data the skills returned. Numbers that can't be found are either the model's own
arithmetic (a difference, a sum) or a slip, and are listed under the answer so the reader knows to check them.
Numbers that are found but cited to a publisher that never gave them are listed too.
"""

import json
import re
import math
from ..domain import SUBMARKETS, period_info

FIGURE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"  # 3,100,000 / 3.1 / 86.26 - not the comma after "2026,"
NUMBER = re.compile(rf"(?<![A-Za-z\d.])([-+]?{FIGURE})\s*(%|bps|basis points|billion|bn|million|m\b|k\b|万|亿)?",
                    re.IGNORECASE)
SCALE = {"bn": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "万": 1e4, "亿": 1e8}  # 310万 = 3.1m


CITATION = re.compile(r"\[([^\[\]]{2,80})\]")
AREA_UNIT = r"(?:sq\.?\s*ft|sqft|square feet|平方英尺)"
MONTHS = {name.lower():i for i, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
METADATA_KEYS = {"period", "period_label", "published", "retrieved_at", "url", "id", "as_of", "previous_as_of",
                 "months_since_period_end", "headlines_collected", "events_matching", "outlets", "topic_counts",
                 "invalid_rows_skipped", "metrics_extracted", "flagged", "unreviewed", "proposed", "kept"}


def _source_numbers(texts: list[str]) -> list[float]:
    values = []
    def numbers(text):
        for raw, unit in NUMBER.findall(text):
            values.append(float(raw.replace(",", "")) * SCALE.get(unit.lower(), 1))

    def walk(node):
        if type(node) in (int, float) and math.isfinite(node):
            values.append(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in METADATA_KEYS:
                    continue
                if key in {"title", "evidence", "note"} and isinstance(value, str):
                    if "value" not in node and "latest" not in node:
                        numbers(value)
                elif isinstance(value, (dict, list)) or type(value) in (int, float):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for t in texts:
        try:
            walk(json.loads(t))
        except (json.JSONDecodeError, TypeError):
            numbers(t)
    return values


def _found(x: float, pool: list[float]) -> bool:
    return any(abs(x - v) <= max(0.006 * abs(v), 0.006) for v in pool)


def _is_claim(raw: str, unit: str) -> bool:
    """Counts, list numbers, days of the month and years carry no market claim."""
    x = float(raw.replace(",", ""))
    return bool(unit) or not (x < 32 and x == int(x) or re.fullmatch(r"(19|20)\d\d", raw))


def _candidates(raw: str, unit: str) -> set[float]:
    x = float(raw.replace(",", ""))
    return {x * SCALE.get(unit, 1)}


def _label(raw: str, unit: str) -> str:
    return f"{raw}{unit if unit in ('%', 'bn', 'm', 'k', '万', '亿') else ' ' + unit if unit else ''}"


def _publisher_index(texts: list[str]) -> dict[str, list[float]]:
    """Numbers each publisher or source actually gave, from every result record that names one
    (broker figures carry "publisher"; macro indicators and news items carry "source")."""
    index: dict[str, list[float]] = {}

    def walk(node):
        if isinstance(node, dict):
            name = node.get("publisher") or node.get("source")
            if isinstance(name, str) and name.strip():
                flat = json.dumps({k: v for k, v in node.items() if not isinstance(v, (dict, list))},
                                  ensure_ascii=False)
                index.setdefault(name.strip().lower(), []).extend(_source_numbers([flat]))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for t in texts:
        try:
            walk(json.loads(t))
        except (json.JSONDecodeError, TypeError):
            continue
    return index


def audit(answer: str, tool_texts: list[str]) -> dict:
    """Every figure in the answer, checked two ways:
    traced      the number appears somewhere in the data the skills returned;
    not_found   it doesn't (the model's own arithmetic, or a slip);
    wrong_source it appears in the data, but not from the publisher the answer cites right after it
                 (e.g. "7.0% [Avison Young]" when 7.0% is Savills' figure)."""
    pool = _source_numbers(tool_texts)
    index = _publisher_index(tool_texts)
    traced, missing, wrong, unverified = 0, [], [], []
    records = source_records(tool_texts)
    claims = [(m, (m.group(2) or "").lower()) for m in NUMBER.finditer(answer)]
    claims = [(m, u) for m, u in claims if _is_claim(m.group(1), u)
              or re.search(r"(?:GBP|USD|£|\$)\s*$", answer[max(0, m.start() - 8):m.start()], re.I)
              or re.match(rf"\s*{AREA_UNIT}", answer[m.end():], re.I)]
    for i, (m, unit) in enumerate(claims):
        raw, label = m.group(1), _label(m.group(1), unit)
        if not any(_found(c, pool) for c in _candidates(raw, unit)):
            if label not in missing:
                missing.append(label)
            continue
        traced += 1
        # The citation right after the figure, on the same line, with no other figure in between.
        tail = answer[m.end(): m.end() + 160].split("\n")[0]
        cite = CITATION.search(tail)
        next_claim = claims[i + 1][0].start() - m.end() if i + 1 < len(claims) else len(tail)
        shared = bool(cite and cite.start() > next_claim and _shared_citation(answer, claims, i, m.end() + cite.start()))
        if not cite or cite.start() > next_claim and not shared:
            unverified.append(f"{label}: missing adjacent citation")
            continue
        cited = [k for k in index if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", cite.group(1).lower())]
        if not cited:
            unverified.append(f"{label}: unknown cited publisher")
        elif not any(_found(c, index[k]) for k in cited for c in _candidates(raw, unit)):
            wrong.append(f"{label} [{cite.group(1)}]")
        else:
            matching = [r for r in records if r["publisher"].lower() in cited
                        and any(_found(c, [r["value"]]) for c in _candidates(raw, unit))]
            if matching:
                before = answer[max(0, m.start() - 120):m.start()]
                if problem := _context_problem(matching, cite.group(1), before, tail, unit):
                    unverified.append(f"{label}: {problem}")
    result = {"traced": traced, "not_found": missing, "wrong_source": wrong}
    if unverified:
        result["unverified"] = list(dict.fromkeys(unverified))
    return result


def _shared_citation(answer: str, claims: list, index: int, citation_start: int) -> bool:
    """Only a numeric list/range can share a trailing citation, never a new noun clause."""
    end = claims[index][0].end()
    for match, _ in claims[index + 1:]:
        if match.start() >= citation_start:
            break
        bridge = answer[end:match.start()].strip()
        if not re.fullmatch(r"(?:[-–—/,]|and|to|至|到|和|、|以及)?", bridge, re.I):
            return False
        end = match.end()
    return bool(re.fullmatch(rf"\s*(?:{AREA_UNIT})?\s*", answer[end:citation_start], re.I))


def _date_parts(text: str) -> dict:
    parts = {}
    if year := re.search(r"\b((?:19|20)\d{2})\b", text):
        parts["year"] = int(year.group())
    if iso := re.search(r"\b(?:19|20)\d{2}-(\d{2})(?:-(\d{2}))?\b", text):
        parts["month"] = int(iso[1])
        if iso[2]:
            parts["day"] = int(iso[2])
    elif month := re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b", text, re.I):
        parts["month"] = MONTHS[month[1].lower()]
        if day := re.search(r"\b([0-3]?\d)\s*$", text[:month.start()]):
            parts["day"] = int(day[1])
    if quarter := re.search(r"\bQ\s*([1-4])\b", text, re.I):
        parts["quarter"] = int(quarter[1])
    if half := re.search(r"\bH\s*([12])\b", text, re.I):
        parts["half"] = int(half[1])
    return parts


def _context_problem(records: list[dict], citation: str, before: str, tail: str, unit: str) -> str | None:
    """All constraints must hold on one record; do not combine fields from different facts."""
    cited_date = _date_parts(citation)
    family = _family(before)
    markets = [(hit.start(), market) for market in SUBMARKETS for hit in re.finditer(re.escape(market), before, re.I)]
    def date_matches(record):
        period = record.get("period") or record.get("as_of") or record.get("period_label")
        if not period:
            return True
        actual = _date_parts(period)
        return bool(cited_date) and all(actual.get(key) == value for key, value in cited_date.items())
    constraints = [
        ("cited period does not match or is missing", date_matches),
        ("metric context does not match", lambda r: not family or _family(r.get("metric", "")) == family),
        ("submarket context does not match", lambda r: not markets or not r.get("submarket") or r["submarket"].lower() == max(markets)[1].lower()),
        ("unit does not match", lambda r: unit != "%" or r.get("unit") in (None, "%")),
        ("currency does not match", lambda r: not re.search(r"GBP|£", before[-12:], re.I) or (r.get("unit") or "").startswith("GBP")),
        ("area unit does not match", lambda r: not re.match(rf"\s*{AREA_UNIT}", tail, re.I) or r.get("unit") in (None, "sq ft")),
    ]
    for reason, matches in constraints:
        records = [record for record in records if matches(record)]
        if not records:
            return reason
    return None


def _family(text: str) -> str | None:
    patterns = {"vacancy": r"vacan\w*|空置", "rent": r"rent\w*|租金", "take_up": r"take.?up|吸纳|成交量",
                "rate": r"bank.?rate|sonia|利率", "gdp": r"gdp|生产总值", "cpi": r"cpi|inflation|通胀"}
    hits = [(m.start(), family) for family, pattern in patterns.items() for m in re.finditer(pattern, text, re.I)]
    return max(hits)[1] if hits else None


def source_records(texts: list[str]) -> list[dict]:
    records = []
    def walk(node, path=()):
        if isinstance(node, dict):
            value = node.get("value", node.get("latest"))
            publisher = node.get("publisher", node.get("source"))
            if isinstance(publisher, str) and type(value) in (int, float):
                record = {**node, "publisher": publisher, "value": value,
                          "metric": node.get("metric") or node.get("indicator") or node.get("label") or " ".join(path)}
                if not record.get("submarket"):
                    record["submarket"] = next((part for part in path if part in SUBMARKETS), None)
                try:
                    record.setdefault("period_label", period_info(node.get("period"))[0])
                except (TypeError, ValueError):
                    pass
                records.append(record)
            for key, child in node.items():
                walk(child, (*path, key))
        elif isinstance(node, list):
            for child in node:
                walk(child, path)
    for text in texts:
        try:
            walk(json.loads(text))
        except (TypeError, ValueError):
            pass
    return records


def check(answer: str, tool_texts: list[str]) -> tuple[int, list[str]]:
    """Returns (figures traced to the data, figures not found)."""
    a = audit(answer, tool_texts)
    return a["traced"], a["not_found"]


def footer_from(a: dict) -> str:
    parts = [f"{a['traced']} numeric matches in the source data"]
    if a["not_found"]:
        parts.append(f"{len(a['not_found'])} not found as-is (verify before quoting): {', '.join(a['not_found'][:10])}")
    if a["wrong_source"]:
        parts.append(f"{len(a['wrong_source'])} may be cited to the wrong source: {'; '.join(a['wrong_source'][:6])}")
    if a.get("unverified"):
        parts.append("unverified: " + "; ".join(a["unverified"][:8]))
    parts.append("automated check only; full meaning and context still require review")
    return "\n\n_Check: " + "; ".join(parts) + "_"


def footer(answer: str, tool_texts: list[str]) -> str:
    return footer_from(audit(answer, tool_texts)) if tool_texts else ""
