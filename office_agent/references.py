"""Source catalog shared by terminal output and exported briefs; never fetches links."""

import ipaddress
import re
from urllib.parse import urlsplit

from .security import safe_text


def display_url(value) -> str | None:
    if not isinstance(value, str) or re.search(r'[\s<>"\\]', value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            return None
        try:
            if not ipaddress.ip_address(parts.hostname).is_global:
                return None
        except ValueError:
            if "." not in parts.hostname or parts.hostname.endswith((".local", ".localhost", ".internal")):
                return None
    except ValueError:
        return None
    return safe_text(value)


def key(row: dict) -> tuple:
    return (row.get("publisher") or row.get("source") or "Unknown source",
            row.get("period_label") or row.get("period") or row.get("as_of") or row.get("published") or "Period not stated",
            display_url(row.get("url")))


def catalog(data) -> list[dict]:
    found = {}
    def walk(node, inherited_origin=None):
        if isinstance(node, dict):
            origin = node.get("origin") or inherited_origin
            if (node.get("publisher") or node.get("source")) and "url" in node:
                publisher, period, url = key(node)
                record = found.setdefault((publisher, period, url), {
                    "publisher":publisher, "period":period, "url":url,
                    "origins":set(), "warnings":set(), "metrics":set(),
                })
                record["origins"].add(origin or "not stated")
                if node.get("stale"):
                    record["warnings"].add("stale")
                note = node.get("note") or ""
                if "table row" in note or "Review flag" in note:
                    record["warnings"].add("lower confidence")
                if not url:
                    record["warnings"].add("source link missing or unsafe")
                if node.get("metric") or node.get("indicator"):
                    record["metrics"].add(node.get("metric") or node["indicator"])
            for value in node.values():
                walk(value, origin)
        elif isinstance(node, list):
            for value in node:
                walk(value, inherited_origin)
    walk(data)
    rows = []
    for index, (_, row) in enumerate(sorted(found.items(), key=lambda pair: (pair[0][0], pair[0][1], pair[0][2] or "")), 1):
        rows.append({**row, "id":f"S{index}", "origins":sorted(row["origins"]),
                     "warnings":sorted(row["warnings"]), "metrics":sorted(row["metrics"])})
    return rows


def lines(data) -> list[str]:
    output = []
    for row in catalog(data):
        flags = "; " + ", ".join(row["warnings"]) if row["warnings"] else ""
        output.append(safe_text(f"[{row['id']}] {row['publisher']} | {row['period']} | {', '.join(row['origins'])}{flags}"))
        output.append(f"    {row['url'] or '(no safe source link available)'}")
    return output
