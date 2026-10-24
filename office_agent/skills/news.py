"""market_news skill: collect London office news from free RSS feeds, de-duplicate, and tag each item
with topics, submarkets and an impact level so the business team sees what matters first."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from .. import config, runtime, store, web
from .base import Skill

GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}+when:{days}d&hl=en-GB&gl=GB&ceid=GB:en"
GOOGLE_QUERIES = [
    '"London office" letting OR "pre-let" OR lease',
    '"London office" market rents OR vacancy OR take-up',
    '"City of London" office',
    '"Canary Wharf" office',
    '"West End" office London',
    'London office development OR refurbishment OR scheme',
    'London office acquisition OR sale OR investment',
    'London office EPC OR ESG OR retrofit OR "net zero"',
]
DIRECT_FEEDS = {
    "Bisnow London": "https://www.bisnow.com/rss/london",
    "Property Week Offices": "https://www.propertyweek.com/markets/offices/feed",
    "City AM": "https://www.cityam.com/feed/",
}

TOPIC_RULES = {
    "leasing": r"\blet(s|ting)?\b|pre-let|leas(e|ing)|take-up|signs|\bhq\b|headquarters|relocat|moves? to|occupier",
    "rents": r"\brents?\b|rental",
    "vacancy_supply": r"vacan|availability|supply|empty|shortage",
    "development": r"develop|scheme|planning|refurb|construction|contractor|completes|completion|tower|crane|"
                   r"retrofit|convert|conversion",
    "investment": r"acqui|\bbuys?\b|\bsells?\b|\bsale\b|\bsold\b|invest|fund|yield|portfolio|deal",
    "distress": r"administration|default|receiver|distress|write-?down|refinanc|loan|valuation (fall|drop)|lender",
    "macro_policy": r"interest rate|bank of england|inflation|budget|business rates|chancellor|healey|\btax\b|"
                    r"gdp|economy",
    "esg": r"\bepc\b|\besg\b|net zero|sustainab|green|carbon|mees",
    "hybrid_work": r"hybrid|occupancy|return to (the )?office|work from home|\bwfh\b|four-day|office attendance",
}
SUBMARKET_RULES = {
    "City": r"city of london|square mile|broadgate|spitalfields|liverpool street|bishopsgate|leadenhall|aldgate|"
            r"moorgate|bank junction|cheapside|fenchurch|\bthe city\b",
    "West End": r"west end|mayfair|st james|soho|fitzrovia|marylebone|victoria|grosvenor|piccadilly|covent garden|"
                r"paddington|knightsbridge|belgravia",
    "Canary Wharf": r"canary wharf|docklands|isle of dogs",
    "Midtown": r"midtown|holborn|farringdon|clerkenwell|chancery lane",
    "King's Cross": r"king'?s cross|euston",
    "Southbank": r"southbank|south bank|london bridge|bankside|waterloo",
    "Fringe / Tech Belt": r"shoreditch|old street|tech belt|whitechapel|white city|battersea|stratford",
}
OFFICE_PATTERN = re.compile(r"office|workspace|workplace|headquarters|\bhq\b|pre-let|letting|commercial property", re.I)
LONDON_PATTERN = re.compile(r"london|" + "|".join(SUBMARKET_RULES.values()), re.I)
SIZE_PATTERN = re.compile(r"([\d,.]+)\s*(k|m)?\s*(sq\.? ?ft|sqft|square feet)", re.I)
MONEY_PATTERN = re.compile(r"£\s?([\d,.]+)\s*(m|bn|million|billion)", re.I)


def _size_sqft(text: str) -> float:
    m = SIZE_PATTERN.search(text)
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    return n * {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)


def _money_gbp(text: str) -> float:
    m = MONEY_PATTERN.search(text)
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    return n * (1e9 if m.group(2).lower().startswith("b") else 1e6)


def tag(title: str) -> dict:
    t = title.lower()
    topics = [k for k, rx in TOPIC_RULES.items() if re.search(rx, t)]
    submarkets = [k for k, rx in SUBMARKET_RULES.items() if re.search(rx, t)]
    big_deal = _size_sqft(title) >= 100_000 or _money_gbp(title) >= 100e6
    if big_deal or {"distress", "macro_policy"} & set(topics):
        impact = "high"
    elif {"leasing", "investment", "development", "rents", "vacancy_supply"} & set(topics):
        impact = "medium"
    else:
        impact = "low"
    return {"topics": topics or ["general"], "submarkets": submarkets, "impact": impact}


def _parse_feed(xml_text: str, default_source: str) -> list[dict]:
    lowered = xml_text.lower() if isinstance(xml_text, str) else xml_text.decode("utf-8", errors="ignore").lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ET.ParseError("DTD/entity declarations are not accepted in news feeds.")
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    items = []
    for it in list(root.iter("item"))[:500]:
        title = (it.findtext("title") or "").strip()[:1000]
        source = (it.findtext("source") or default_source)[:200]
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = None
        items.append({"title": title, "url": (it.findtext("link") or "").strip()[:2048], "source": source,
                      "published": published.isoformat() if published else None})
    return items


def fetch_live(days: int) -> list[dict]:
    sources = {GOOGLE_NEWS.format(q=quote_plus(q), days=days): "Google News" for q in GOOGLE_QUERIES}
    sources.update({url: name for name, url in DIRECT_FEEDS.items()})
    raw = []
    for url, body in web.get_many(list(sources)).items():
        if isinstance(body, Exception):
            continue  # one dead feed shouldn't sink the rest
        try:
            raw += _parse_feed(body, sources[url])
        except ET.ParseError:
            continue
    return raw


def _normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())[:80]


def clean(raw: list[dict], days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen, out = set(), []
    for it in sorted(raw, key=lambda x: x["published"] or "", reverse=True):
        title = it["title"]
        if not (OFFICE_PATTERN.search(title) and LONDON_PATTERN.search(title)):
            continue
        if it["published"] and datetime.fromisoformat(it["published"]) < cutoff:
            continue
        key = _normalise_title(title)
        if key in seen:
            continue
        seen.add(key)
        out.append({**it, **tag(title)})
    return out


# Capitalised words that appear in many unrelated headlines and so say nothing about which event it is.
GENERIC_WORDS = set("""a an and as at by for from in into of on or the to with is are was be will would could
should its it this that new news report reports plans plan deal deals lease leases letting lettings office offices
london city west end uk british firm firms property properties market markets commercial real estate space
building buildings tower towers hq headquarters move moves signs nears agrees secures sells buys sale says
warn warns warned after over up down more than first latest q1 q2 q3 q4 sq ft million billion""".split())


def _anchors(title: str) -> set[str]:
    """Names and amounts that identify an event: proper nouns (Healey, Stripe, Blackstone) and figures (£1bn)."""
    title = title.replace("’", "'")
    # Adjacent capitalised words form one name ("Covent Garden", "Tower Hamlets"), so two stories that merely
    # share a place don't look like two shared anchors.
    names, run = set(), []
    for token in re.findall(r"[\w'&-]+", title) + [""]:
        word = re.sub(r"'s?$", "", token.lower())
        if token[:1].isupper() and word not in GENERIC_WORDS:
            run.append(word)
            continue
        if run:
            names.add(" ".join(run))
        run = []
    # Specific counts ("180 homes") identify a story too; years don't.
    counts = {n for n in re.findall(r"\b\d{2,}\b", title) if not re.fullmatch(r"(19|20)\d\d", n)}
    return names | counts | _amounts(title)


def _amounts(title: str) -> set[str]:
    found = {m.group(0).replace(" ", "").lower() for m in MONEY_PATTERN.finditer(title)}
    return found | {m.group(0).split()[0].replace(",", "") for m in SIZE_PATTERN.finditer(title)}


def _names_hit(anchors: set, shared_words: set) -> int:
    return sum(1 for p in anchors if set(p.split()) & shared_words)


def _same_event(a: dict, b: dict, anchors_a: set, anchors_b: set) -> bool:
    shared = anchors_a & anchors_b
    if len(shared) >= 2:
        return True
    # Title Case headlines glue names together ("Fast-Growing Fintech Stripe"), so also compare word by word:
    # same event if the shared words belong to two different names in both titles (Stripe + Blackstone),
    # but not if they are one name split in two (Covent + Garden).
    shared_words = {w for p in anchors_a for w in p.split()} & {w for p in anchors_b for w in p.split()}
    if _names_hit(anchors_a, shared_words) >= 2 and _names_hit(anchors_b, shared_words) >= 2:
        return True
    # One shared amount (e.g. "£1bn") is enough when the stories ran within a few days of each other.
    if shared & _amounts(a["title"]) and a["published"] and b["published"]:
        gap = abs(datetime.fromisoformat(a["published"]) - datetime.fromisoformat(b["published"]))
        return gap <= timedelta(days=3)
    return False


def group_events(items: list[dict]) -> list[dict]:
    """Merge headlines about the same event so one story reported by five outlets counts once. Returns one
    item per event, carrying how many outlets covered it."""
    anchors = [_anchors(i["title"]) for i in items]
    parent = list(range(len(items)))

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if _same_event(items[a], items[b], anchors[a], anchors[b]):
                parent[root(b)] = root(a)
    groups: dict[int, list[dict]] = {}
    for idx, item in enumerate(items):
        groups.setdefault(root(idx), []).append(item)
    rank = {"high": 0, "medium": 1, "low": 2}
    events = []
    for members in groups.values():
        lead = min(members, key=lambda i: (rank[i["impact"]], i["published"] or ""))  # earliest strongest
        topics = {t for m in members for t in m["topics"]}
        events.append({**lead,
                       "impact": min((m["impact"] for m in members), key=rank.get),
                       "topics": sorted(topics - {"general"}) or ["general"],
                       "submarkets": sorted({s for m in members for s in m["submarkets"]}),
                       "outlets": len({m["source"] for m in members}),
                       "other_headlines": [f'{m["title"]} ({m["source"]})' for m in members if m is not lead][:4]})
    return events


def score_relevance(items: list[dict]) -> list[dict]:
    """Attach Jev's probability that each headline is office-market news (None if Jev is off or failed)."""
    from ..checks import review

    if not review.enabled():
        return items
    scores = review.news_relevance([i["title"] for i in items])
    return [{**i, "relevance": scores.get(i["title"])} for i in items]


def drop_noise(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Keyword matching lets through headlines that merely mention an office (an embassy event, a gas leak);
    drop those Jev judges unrelated. Items without a score are kept."""
    from ..checks import review

    noise = [i for i in items if i.get("relevance") is not None and i["relevance"] < review.RELEVANT_MIN]
    return [i for i in items if i not in noise], [i["title"] for i in noise]


def load_snapshot() -> list[dict]:
    path = config.SNAPSHOT_DIR / "news.json"
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    return [{**i, **tag(i["title"])} for i in items]  # re-tag so rule changes apply to stored items too


def get_market_news(topic: str | None = None, submarket: str | None = None, days: int = 14,
                    limit: int = 15) -> dict:
    provided = runtime.source_data("news")
    if provided is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        items = [r for r in provided["items"] if (not topic or topic in r["topics"])
                 and (not submarket or submarket in r["submarkets"])
                 and r.get("published") and datetime.fromisoformat(r["published"]) >= cutoff]
        return {**provided, "items":items[:limit], "events_matching":len(items)}
    origin, warning = "live", None
    if config.offline():
        items, origin = load_snapshot(), "snapshot"
    else:
        try:
            items = score_relevance(clean(fetch_live(days), days))
            if not items:
                raise RuntimeError("no items")
            with store.connect() as conn:
                store.save_news(conn, items, origin="live")
        except runtime.DeadlineExceeded:
            raise
        except Exception as exc:
            if runtime.live_only():
                return {"origin":"unavailable", "items":[], "events_matching":0, "topic_counts":{},
                        "warning":f"Live news retrieval failed ({type(exc).__name__}). No replacement data was used."}
            items, origin = load_snapshot(), "snapshot"
            warning = f"Live news fetch failed ({type(exc).__name__}); showing bundled snapshot."
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items = [i for i in items if i.get("published") and datetime.fromisoformat(i["published"]) >= cutoff][:500]
    headlines = len(items)
    items, noise = drop_noise(items)
    items = group_events(items)
    if topic:
        items = [i for i in items if topic in i["topics"]]
    if submarket:
        items = [i for i in items if submarket.lower() in (s.lower() for s in i["submarkets"])]
    rank = {"high": 0, "medium": 1, "low": 2}
    # Within an impact level, stories carried by more outlets first, then the newest.
    items = sorted(items, key=lambda i: (rank[i["impact"]], -i["outlets"],
                                         -(datetime.fromisoformat(i["published"]).timestamp()
                                           if i["published"] else 0)))
    counts = {}
    for i in items:
        for t in i["topics"]:
            counts[t] = counts.get(t, 0) + 1
    out = {"origin": origin, "headlines_collected": headlines, "dropped_as_unrelated": noise,
           "events_matching": len(items), "topic_counts": counts, "items": items[:limit]}
    if warning:
        out["warning"] = warning
    return out


SKILL = Skill(
    name="get_market_news",
    description=(
        "Recent London office market news (Google News, Bisnow, Property Week, City AM), grouped into events "
        "(one item per story, with how many outlets covered it and their other headlines) and tagged by topic, "
        "submarket and impact (high = deals >=100k sq ft or >=GBP100m, distress, policy/rates). Call once without "
        "a topic for an overview; filter only for a focused question. "
        "Use for 'what's happening', deals, lettings, pre-lets, acquisitions, distress, breaking events, or "
        "news about a specific submarket. Headlines only: say so, and don't infer details not in the title."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "enum": list(TOPIC_RULES), "description": "Filter by topic."},
            "submarket": {"type": "string", "enum": list(SUBMARKET_RULES), "description": "Filter by submarket."},
            "days": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Look-back window (default 14)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 40, "description": "Max items (default 15)."},
        },
        "additionalProperties": False,
    },
    run=get_market_news,
)
