"""The curated content store.

Still a static JSON file in Phase 2 — deliberately. Phase 2 is about making the
*vote* path distributed; moving questions into Postgres is Phase 3 work, when
the offline content pipeline actually starts generating them. Keeping it static
here means a load-test failure can only point at the vote path.

Non-negotiable rule #1 lives here: `public_view` is the only shape a question
is ever allowed to take before a player has voted.
"""

import json
from datetime import date
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "data" / "questions.json"

# What kind of decision the question is about. `select_sources` routes on this:
# Voteview covers congressional_passage and nothing else (it is a congressional
# dataset, so a constitutional ratification isn't in it at all), Hansard covers
# parliamentary_division, and an agency_rule lives in the agency's own record.
VOTE_TYPES = frozenset(
    {
        "congressional_passage",
        "constitutional_ratification",
        "parliamentary_division",
        "agency_rule",
    }
)

# The only keys a player may see before voting. A whitelist, not a blacklist:
# the previous version stripped `reveal` and passed everything else through,
# which fails open the moment a field is added — as the three pipeline fields
# below just demonstrated. Rule #1 is enforced structurally everywhere else in
# this codebase; a list of things to remember to hide is the weaker kind.
_PUBLIC_FIELDS = frozenset({"id", "category", "era", "prompt", "options"})

# What a `reveal` block must carry. These are the names `game.schemas.RevealData`
# requires, and `main.vote` reaches them by splatting `**q["reveal"]` straight
# into it — so a question missing one produces a 500 on the vote endpoint, for
# that question only, with nothing wrong at startup.
#
# The list cannot be imported from `RevealData`: `shared/` may not import
# `game/` (test_layering enforces it, and the pipeline has to validate content
# without the web layer present). `test_questions.py` ties the two together
# instead, so the duplication is checked rather than merely intended.
REVEAL_FIELDS = ("actual_vote", "outcome", "source")


def _validate(q: dict) -> None:
    """Reject a malformed question at import time.

    This module reads the file on import, so a bad entry takes the process
    down at startup rather than surfacing halfway through a retrieval run.
    `decision_date` in particular is a safety boundary — the pre-vote index is
    everything published before it — so a missing or unparseable one must not
    be something the pipeline can discover later.
    """
    qid = q.get("id", "<no id>")
    for field in ("jurisdiction", "vote_type", "decision_date"):
        if not q.get(field):
            raise ValueError(f"question {qid!r} is missing {field!r}")
    if q["vote_type"] not in VOTE_TYPES:
        raise ValueError(
            f"question {qid!r} has unknown vote_type {q['vote_type']!r}; "
            f"expected one of {sorted(VOTE_TYPES)}"
        )
    try:
        date.fromisoformat(q["decision_date"])
    except ValueError as exc:
        raise ValueError(
            f"question {qid!r} has an unparseable decision_date "
            f"{q['decision_date']!r}: {exc}"
        ) from exc

    # The reveal is the one block a player-facing endpoint dereferences by
    # name. Validating it here is what keeps a malformed question a startup
    # failure rather than a 500 on the vote that surfaces it.
    reveal = q.get("reveal")
    if not isinstance(reveal, dict):
        raise ValueError(f"question {qid!r} is missing its 'reveal' block")
    for field in REVEAL_FIELDS:
        if not isinstance(reveal.get(field), str) or not reveal[field].strip():
            raise ValueError(f"question {qid!r} has a missing or empty reveal.{field}")

    # `retrieval` is what `formulate_query` addresses a source with. Only
    # search_terms is required: `bill_number` and `congress` are congressional
    # concepts that a UK bill and an agency rule genuinely do not have, and
    # inventing a value for them would be worse than leaving them null.
    retrieval = q.get("retrieval")
    if not isinstance(retrieval, dict):
        raise ValueError(f"question {qid!r} is missing its 'retrieval' block")
    terms = retrieval.get("search_terms")
    if (
        not isinstance(terms, list)
        or not terms
        or not all(isinstance(t, str) and t for t in terms)
    ):
        raise ValueError(f"question {qid!r} needs a non-empty list of search_terms")
    if retrieval.get("congress") is not None and not isinstance(
        retrieval["congress"], int
    ):
        raise ValueError(f"question {qid!r} has a non-integer congress")


with open(_DATA_PATH, encoding="utf-8") as f:
    _questions: list[dict] = json.load(f)

for _q in _questions:
    _validate(_q)

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


def decision_date(question_id: str) -> date | None:
    """The boundary the retrieval scope is cut on.

    Pre-vote material is everything published strictly before this date; the
    outcome is everything from it onwards. See "Retrieval scope" in CLAUDE.md
    for why the rule is a date rather than a source type, and
    docs/content-audit.md for where each of these dates came from.
    """
    q = _by_id.get(question_id)
    return date.fromisoformat(q["decision_date"]) if q else None


def public_view(q: dict) -> dict:
    """The only shape a question may take before its player has voted.

    Drops the reveal *and* the pipeline metadata — `decision_date`,
    `vote_type` and `jurisdiction` describe how content is built, and have no
    business in front of a player. The API is already narrowed by the
    `QuestionSummary` response model, but the Phase 3 chatbot won't go through
    that; it will go through here.
    """
    return {k: v for k, v in q.items() if k in _PUBLIC_FIELDS}
