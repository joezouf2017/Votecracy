"""The durable vote log — the live game path's half of the schema.

Redis is the fast path; this is the source of truth. Writes happen *off* the
request path (FastAPI BackgroundTasks) so a slow database never adds latency
to a player's vote.

The UNIQUE(question_id, voter_id) constraint is the backstop for duplicate
prevention. Redis already rejects duplicates atomically, but if Redis is ever
flushed or fails over, the constraint means a replayed vote still can't
produce two rows. Belt and braces on the one invariant that matters most.
"""

from datetime import UTC, date, datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError

# The engine is reached through the module, not imported as a bare name.
# `from ... import get_engine` binds at import time, so the test suite's
# monkeypatch of the engine would miss this module entirely and every test
# would quietly run against the real Postgres. That is not hypothetical: the
# package split did exactly that, and five tests started reading production
# vote counts instead of their own fixtures.
from shared.db import engine as db_engine
from shared.db.engine import metadata

votes = Table(
    "votes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("question_id", String(128), nullable=False, index=True),
    Column("voter_id", String(64), nullable=False),
    Column("choice", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("question_id", "voter_id", name="uq_votes_question_voter"),
)

# Which question is live on a given day. A row here is how an admin overrides
# the default rotation — insert (day, question_id) and that day is pinned.
daily_questions = Table(
    "daily_questions",
    metadata,
    Column("day", Date, primary_key=True),
    Column("question_id", String(128), nullable=False),
)


def record_vote(question_id: str, voter_id: str, choice: str) -> bool:
    """Append a vote to the durable log. Returns False if it was a duplicate.

    Runs as a background task, so it must never raise into the request — a
    duplicate here means Redis and Postgres disagreed, which is worth knowing
    about but not worth failing a player's already-counted vote over.
    """
    stmt = votes.insert().values(
        question_id=question_id,
        voter_id=voter_id,
        choice=choice,
        created_at=datetime.now(UTC),
    )
    try:
        with db_engine.get_engine().begin() as conn:
            conn.execute(stmt)
        return True
    except IntegrityError:
        return False


def count_votes(question_id: str) -> int:
    """Durable vote count — the number the Redis tally is checked against."""
    stmt = (
        select(func.count())
        .select_from(votes)
        .where(votes.c.question_id == question_id)
    )
    with db_engine.get_engine().connect() as conn:
        return conn.execute(stmt).scalar_one()


def get_voter_choice(question_id: str, voter_id: str) -> str | None:
    """What this voter picked, straight from the durable log.

    The slow-but-authoritative answer to the question Redis normally answers
    in one hop. Only used when Redis is unavailable.
    """
    stmt = select(votes.c.choice).where(
        votes.c.question_id == question_id, votes.c.voter_id == voter_id
    )
    with db_engine.get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    return row[0] if row else None


def tally(question_id: str) -> dict[str, int]:
    """Rebuild the tally from the durable log.

    Redis holds the same numbers and answers in O(1), but this is where they
    can always be recovered from — the tally is a cache of this query, not a
    separate source of truth.
    """
    stmt = (
        select(votes.c.choice, func.count())
        .where(votes.c.question_id == question_id)
        .group_by(votes.c.choice)
    )
    with db_engine.get_engine().connect() as conn:
        return {choice: count for choice, count in conn.execute(stmt)}


def get_daily_question_id(day: date) -> str | None:
    stmt = select(daily_questions.c.question_id).where(daily_questions.c.day == day)
    with db_engine.get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    return row[0] if row else None


def set_daily_question_id(day: date, question_id: str) -> None:
    with db_engine.get_engine().begin() as conn:
        existing = conn.execute(
            select(daily_questions.c.day).where(daily_questions.c.day == day)
        ).first()
        if existing:
            conn.execute(
                daily_questions.update()
                .where(daily_questions.c.day == day)
                .values(question_id=question_id)
            )
        else:
            conn.execute(
                daily_questions.insert().values(day=day, question_id=question_id)
            )
