"""Skill registry: adding a capability means writing a module with a SKILL and listing it here."""

from . import broker_reports, macro, news, weekly_brief

SKILLS = {s.name: s for s in [
    news.SKILL,
    broker_reports.SKILL,
    broker_reports.REFRESH_SKILL,
    macro.SKILL,
    weekly_brief.SKILL,
]}
