"""Postgres layer — the durable record of every vote.

Redis is the fast path; this is the source of truth. Writes happen *off* the
request path (FastAPI BackgroundTasks) so a slow database never adds latency
to a player's vote.

The UNIQUE(question_id, voter_id) constraint is the backstop for duplicate
prevention. Redis already rejects duplicates atomically, but if Redis is ever
flushed or fails over, the constraint means a replayed vote still can't
produce two rows. Belt and braces on the one invariant that matters most.

SQLAlchemy Core (not the ORM) on purpose: the schema is two tables and the
queries are trivial, so an ORM layer would only obscure what SQL actually runs.
"""

import os
from datetime import date, datetime, timezone
from functools import lru_cache

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError

metadata = MetaData()

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


@lru_cache(maxsize=1)
def get_engine():
    """Lazily build the engine so importing this module doesn't need a server."""
    url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://votecracy:votecracy@localhost:5432/votecracy"
    )
    # SQLAlchemy needs the driver spelled out; compose supplies a bare
    # postgresql:// URL, so normalise it rather than duplicating config.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True, future=True)


def init_db() -> None:
    metadata.create_all(get_engine())


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
        created_at=datetime.now(timezone.utc),
    )
    try:
        with get_engine().begin() as conn:
            conn.execute(stmt)
        return True
    except IntegrityError:
        return False


def count_votes(question_id: str) -> int:
    """Durable vote count — the number the Redis tally is checked against."""
    stmt = select(func.count()).select_from(votes).where(votes.c.question_id == question_id)
    with get_engine().connect() as conn:
        return conn.execute(stmt).scalar_one()


def get_voter_choice(question_id: str, voter_id: str) -> str | None:
    """What this voter picked, straight from the durable log.

    The slow-but-authoritative answer to the question Redis normally answers
    in one hop. Only used when Redis is unavailable.
    """
    stmt = select(votes.c.choice).where(
        votes.c.question_id == question_id, votes.c.voter_id == voter_id
    )
    with get_engine().connect() as conn:
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
    with get_engine().connect() as conn:
        return {choice: count for choice, count in conn.execute(stmt)}


def get_daily_question_id(day: date) -> str | None:
    stmt = select(daily_questions.c.question_id).where(daily_questions.c.day == day)
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    return row[0] if row else None


def set_daily_question_id(day: date, question_id: str) -> None:
    with get_engine().begin() as conn:
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
            conn.execute(daily_questions.insert().values(day=day, question_id=question_id))
