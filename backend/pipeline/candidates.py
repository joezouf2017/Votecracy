"""Candidate persistence, and the column a corpus re-run must never touch.

`voteview.candidates()` rebuilds 10,593 ranked candidates from the bulk corpus
in about 0.6 seconds, so the candidates are not worth storing for their own
sake. **What has no other home is the record that a human looked at one**, and
that is the only reason this module exists.

Which makes the whole design one property: re-reading the corpus refreshes every
derived column and leaves `status`, `reviewed_at` and `review_note` exactly as
they were. A re-run that reset a rejection to `pending` would undo human work
silently and look like nothing had happened — the corpus does not change, so
nobody would think to check.

That property is enforced by `_REFRESHED` rather than by remembering: it lists
the columns an update may write, and the review columns are not in it. Adding a
column means deciding which side it belongs on, which is the decision worth
forcing.

The upsert is written as a read, a split and two statements rather than as
`ON CONFLICT DO UPDATE`. Dialect-specific insert constructs would work on
Postgres and need a second spelling for the SQLite the tests run on, and this
way the invariant is visible in Python instead of inside a dialect's compiler.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, select, update

from shared.db import corpus
from shared.db import engine as db_engine

log = logging.getLogger(__name__)

# Columns a corpus re-run is allowed to write. `status`, `reviewed_at` and
# `review_note` are deliberately absent and must stay absent.
_REFRESHED = (
    "chamber",
    "vote_date",
    "vote_type",
    "subject",
    "description",
    "yea",
    "nay",
    "attention_percentile",
    "closeness",
    "coalition_break",
    "gaps",
)

_REVIEW_ONLY = ("status", "reviewed_at", "review_note")


class UnknownStatus(ValueError):
    """A status outside the vocabulary. Rejected rather than stored, because a
    typo'd status is a candidate that silently drops out of every queue."""


@dataclass(frozen=True)
class Counts:
    inserted: int
    refreshed: int


def _row(candidate) -> dict:
    """One candidate flattened to the derived columns, review state excluded."""
    signals = candidate.signals
    return {
        "congress": candidate.congress,
        "bill_number": candidate.bill_number,
        "chamber": candidate.chamber,
        "vote_date": candidate.vote_date,
        "vote_type": candidate.vote_type,
        "subject": candidate.subject,
        "description": candidate.description,
        "yea": candidate.yea,
        "nay": candidate.nay,
        "attention_percentile": signals.attention_percentile,
        "closeness": signals.closeness,
        "coalition_break": signals.coalition_break,
        "gaps": ",".join(candidate.gaps) if candidate.gaps else None,
    }


def upsert(candidates) -> Counts:
    """Write candidates, refreshing what the corpus owns and nothing else.

    Idempotent by construction: running it twice over an unchanged corpus
    inserts nothing and rewrites the same derived values.
    """
    now = datetime.now(UTC)
    rows = [_row(c) for c in candidates]
    engine = db_engine.get_engine()

    with engine.begin() as conn:
        existing = {
            (r.congress, r.bill_number): r.id
            for r in conn.execute(
                select(
                    corpus.candidates.c.id,
                    corpus.candidates.c.congress,
                    corpus.candidates.c.bill_number,
                )
            )
        }
        fresh = [r for r in rows if (r["congress"], r["bill_number"]) not in existing]
        seen = [r for r in rows if (r["congress"], r["bill_number"]) in existing]

        if fresh:
            conn.execute(
                corpus.candidates.insert(),
                [{**r, "first_seen_at": now, "refreshed_at": now} for r in fresh],
            )
        for r in seen:
            conn.execute(
                update(corpus.candidates)
                .where(
                    and_(
                        corpus.candidates.c.congress == r["congress"],
                        corpus.candidates.c.bill_number == r["bill_number"],
                    )
                )
                # Only the corpus-owned columns. Naming them explicitly is what
                # keeps a re-run from touching review state; `**r` would sweep
                # in whatever `_row` happens to return next year.
                .values(
                    {k: r[k] for k in _REFRESHED} | {"refreshed_at": now},
                )
            )

    log.info(
        "candidates: %d inserted, %d refreshed, review state untouched",
        len(fresh),
        len(seen),
    )
    return Counts(inserted=len(fresh), refreshed=len(seen))


def queue(*, limit: int = 50, status: str = "pending"):
    """The highest-ranked candidates nobody has ruled on yet.

    **The ORDER BY is `voteview.rank`'s, spelled again in SQL**, and the
    tie-break is not decoration. `attention_percentile` saturates: 1.000 is a
    large tie, so ordering on it alone leaves the top of the queue in whatever
    sequence the planner returns, and it can differ between runs. `rank()` says
    exactly this — "a review queue that reshuffles between runs cannot be
    worked through" — and the first version of this function reshuffled.

    Reviewing is otherwise plain SQL, matching how `daily_questions` is
    handled; there is no admin UI and does not need to be one.
    """
    if status not in corpus.CANDIDATE_STATUSES:
        raise UnknownStatus(
            f"{status!r} is not one of {list(corpus.CANDIDATE_STATUSES)}"
        )
    stmt = (
        select(corpus.candidates)
        .where(corpus.candidates.c.status == status)
        .order_by(
            corpus.candidates.c.attention_percentile.desc(),
            corpus.candidates.c.vote_date,
            corpus.candidates.c.bill_number,
        )
        .limit(limit)
    )
    with db_engine.get_engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt)]


def review(
    congress: int, bill_number: str, status: str, note: str | None = None
) -> int:
    """Record a human decision. Returns rows affected, so 0 means "no such
    candidate" rather than a silent no-op.

    Validated against the vocabulary because a typo'd status is worse than an
    error: the candidate stays out of the pending queue *and* out of the
    approved one, so it simply disappears.
    """
    if status not in corpus.CANDIDATE_STATUSES:
        raise UnknownStatus(
            f"{status!r} is not one of {list(corpus.CANDIDATE_STATUSES)}"
        )
    stmt = (
        update(corpus.candidates)
        .where(
            and_(
                corpus.candidates.c.congress == congress,
                corpus.candidates.c.bill_number == bill_number,
            )
        )
        .values(status=status, review_note=note, reviewed_at=datetime.now(UTC))
    )
    with db_engine.get_engine().begin() as conn:
        return conn.execute(stmt).rowcount
