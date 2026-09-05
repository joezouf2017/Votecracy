"""The curated content store.

Still a static JSON file in Phase 2 — deliberately. Phase 2 is about making the
*vote* path distributed; moving questions into Postgres is Phase 3 work, when
the offline content pipeline actually starts generating them. Keeping it static
here means a load-test failure can only point at the vote path.

Non-negotiable rule #1 lives here: `public_view` is the only shape a question
is ever allowed to take before a player has voted.
"""

import json
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "data" / "questions.json"

with open(_DATA_PATH, encoding="utf-8") as f:
    _questions: list[dict] = json.load(f)

_by_id: dict[str, dict] = {q["id"]: q for q in _questions}

# Stable order for the daily rotation — JSON file order shouldn't silently
# change which question a given day maps to.
_rotation: list[str] = sorted(_by_id)


def all_questions() -> list[dict]:
    return _questions


def get_question(question_id: str) -> dict | None:
    return _by_id.get(question_id)


def rotation() -> list[str]:
    return _rotation


def public_view(q: dict) -> dict:
    """Strip the reveal field so it's never sent before a vote."""
    return {k: v for k, v in q.items() if k != "reveal"}
