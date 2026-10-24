"""Jev (TypeSafe System One) client: a judgement model, not a chat model.

It answers structured questions about a piece of text and returns numbers, never prose:
  noul    probability (0-1) that a statement holds
  choice  one option out of several, with per-option probabilities and a confidence
  score   a point on a labelled scale
Several questions about the same text go in one request and are answered in parallel. English works best.
Docs: https://docs.typesafe.ai/api
"""

import os

import requests
from .. import runtime

URL = "https://api.typesafe.ai/v1/systemone"


class JevError(RuntimeError):
    pass


def available() -> bool:
    return bool(os.environ.get("JEV_API_KEY"))


def judge(state: str, questions: dict, timeout: int = 30) -> dict:
    """Ask `questions` about `state`; returns the answers dict keyed like the questions."""
    runtime.checkpoint()
    resp = requests.post(URL, timeout=runtime.timeout(timeout),
                         headers={"Authorization": f"Bearer {os.environ.get('JEV_API_KEY', '').strip()}"},
                         json={"state": state, "model": "jev-latest", "questions": questions})
    if resp.status_code == 429:
        raise JevError("Jev rate limit reached.")
    if resp.status_code != 200:
        raise JevError(f"Jev error {resp.status_code}.")
    runtime.checkpoint()
    return resp.json()["answers"]
