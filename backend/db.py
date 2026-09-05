"""Postgres layer — the durable record of every vote.

Redis is the fast path; this is the source of truth. Writes happen *off* the
request path (FastAPI BackgroundTasks) so a slow database never adds latency
to a player's vote.

The UNIQUE(question_id, voter_id) constraint is the backstop for duplicate
prevention. Redis already rejects duplicates atomically, but if Redis is ever
flushed or fails over, the constraint means a replayed vote still can't
produce two rows. Belt and braces on the one invariant that matters most.

SQLAlchemy Core (not the ORM) on purpose: the queries are trivial, so an ORM
layer would only obscure what SQL actually runs.

Phase 3 adds the content pipeline's source tables further down. They share this
metadata (and so the Alembic chain) but nothing on the vote path reads them.
"""

from datetime import UTC, date, datetime
from functools import lru_cache

from settings import get_settings
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import TypeDecorator

metadata = MetaData()

# Gemini text-embedding-004. Changing this invalidates every stored vector, so
# `chunk_embeddings.model` records which model produced each one.
EMBEDDING_DIM = 768


class Vector(TypeDecorator):
    """pgvector's `vector(n)` on Postgres, opaque bytes anywhere else.

    The tests build the schema from this metadata against SQLite, where there
    is no vector type and `pgvector.sqlalchemy.Vector` fails to compile — which
    would take down the whole suite, not just the parts that touch embeddings.
    Nothing in the test suite reads or writes a vector, so the fallback only
    has to be creatable.
    """

    impl = LargeBinary
    cache_ok = True

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector as PGVector

            return dialect.type_descriptor(PGVector(self.dim))
        return dialect.type_descriptor(LargeBinary())


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


# --- Phase 3: the content pipeline's source store ---------------------------
#
# Three tables for the three storage layers, ordered by how expensive they are
# to lose:
#
#   source_documents   only rebuildable by going back to the network — durable
#   source_chunks      rebuildable from documents, locally, for free
#   chunk_embeddings   rebuildable from chunks, but costs embedding API calls
#
# Embeddings are a separate table rather than a column on chunks so that
# "drop the vector index and rebuild it" is an operation you can actually
# perform. If dropping the vectors meant re-fetching from the network, the
# layering would be wrong.

source_documents = Table(
    "source_documents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("question_id", String(128), nullable=False, index=True),
    # voteview | govinfo | loc | fred | pubmed — see `select_sources`.
    Column("source_key", String(64), nullable=False),
    # Whatever identifies this document to that source: LCCN + page, a GovInfo
    # package id, a PMID. Paired with source_key it's the natural key.
    Column("external_id", String(256), nullable=False),
    Column("url", Text, nullable=False),
    Column("title", Text),
    # NOT NULL on purpose. The pre/post-vote boundary is `published_date <
    # decision_date`, so a document with no date can't be placed on either
    # side of it — and a nullable column invites a query that treats NULL as
    # "before", which is the failure that leaks an outcome. Making it required
    # pushes the problem to where it belongs: if the fetcher can't establish a
    # date, it must not store the document. Every whitelisted source carries
    # one, so this costs nothing today.
    Column("published_date", Date, nullable=False),
    # Recorded because the spike found loc.gov's old OCR path answers a
    # Cloudflare challenge *page* rather than an HTTP error — a naive client
    # stores the HTML and calls it source text. Validate before writing.
    Column("content_type", String(128), nullable=False),
    Column("sha256", String(64), nullable=False),  # content-addressed cache key
    Column("text", Text, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "source_key", "external_id", name="uq_source_documents_source_external"
    ),
    # Not useful on its own — it exists so source_chunks can point a composite
    # foreign key at these three columns. See the note there.
    UniqueConstraint(
        "id", "question_id", "published_date", name="uq_source_documents_scope_key"
    ),
)

# What a chunk's `role` may be. Values are ordered from safest to most
# dangerous, which is also roughly how they're used.
CHUNK_ROLES = ("framing", "vote_record", "outcome")

source_chunks = Table(
    "source_chunks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("document_id", Integer, nullable=False),
    # question_id and published_date are copied down from the parent document
    # so that the pre-vote filter is one indexed predicate on the table the
    # retriever actually queries. Left on source_documents, every retrieval
    # would need a join — and the failure mode of forgetting that join is
    # silently returning outcome material, which is rule #1.
    #
    # The composite FK below is what makes the copy safe: the three columns
    # are a foreign key into (id, question_id, published_date) on the parent,
    # so a chunk whose scope disagrees with its document cannot be written at
    # all. That's a constraint, not a convention backed by a test.
    Column("question_id", String(128), nullable=False),
    Column("published_date", Date, nullable=False),
    # framing | vote_record | outcome. Date alone can't separate an amendment's
    # *description* (pre-vote — knowing what else was on the table doesn't
    # reveal whether the bill passed) from its *vote counts* (post-vote —
    # margins leak the outcome), because both sit in the same Congressional
    # Record document on the same day.
    #
    # This is a second filter, never an alternative one. Pre-vote scope is
    # `published_date < decision_date AND role != 'outcome'` — a conjunction,
    # so role can only ever remove material from the date window. It cannot
    # admit anything published after the decision.
    Column("role", String(32), nullable=False),
    Column("ordinal", Integer, nullable=False),  # position within the document
    # Offsets into source_documents.text. Not optional: rule #2 is enforced by
    # code checking that a claim's (document_id, char_span) exists and that the
    # span text contains the cited value. Without offsets that check would have
    # to be an LLM judging an LLM, which is the thing rule #2 exists to prevent.
    Column("char_start", Integer, nullable=False),
    Column("char_end", Integer, nullable=False),
    Column("text", Text, nullable=False),
    ForeignKeyConstraint(
        ["document_id", "question_id", "published_date"],
        [
            "source_documents.id",
            "source_documents.question_id",
            "source_documents.published_date",
        ],
        name="fk_source_chunks_document_scope",
        ondelete="CASCADE",
        # A corrected publication date on the document propagates to its
        # chunks rather than leaving them behind on the old boundary.
        onupdate="CASCADE",
    ),
    UniqueConstraint(
        "document_id", "ordinal", name="uq_source_chunks_document_ordinal"
    ),
    Index("ix_source_chunks_question_published", "question_id", "published_date"),
)

chunk_embeddings = Table(
    "chunk_embeddings",
    metadata,
    Column(
        "chunk_id",
        Integer,
        ForeignKey("source_chunks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # Part of the primary key so vectors from two models can coexist during a
    # re-embed, and so "which model produced this" can't be lost. Swapping the
    # embedding model without re-embedding doesn't error — queries keep working
    # and just return worse matches — so it has to be impossible to forget.
    Column("model", String(128), primary_key=True),
    Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# No ANN index (HNSW/IVFFlat) yet, deliberately. At a few thousand chunks an
# exact scan is both faster and exactly correct, and IVFFlat built on an empty
# table is actively harmful — it needs rows to pick its centroids. Add one in
# a migration of its own once there's enough data to measure recall against.


@lru_cache(maxsize=1)
def get_engine():
    """Lazily build the engine so importing this module doesn't need a server."""
    url = get_settings().database_url
    # SQLAlchemy needs the driver spelled out; compose supplies a bare
    # postgresql:// URL, so normalise it rather than duplicating config.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True, future=True)


def create_all_for_tests(engine) -> None:
    """Build the schema straight from the metadata, for tests only.

    Production schema is owned by Alembic (`alembic upgrade head`, run by the
    container before uvicorn starts). Tests deliberately skip the migration
    chain — they want a schema in one step against a throwaway SQLite file, not
    a replay of every historical migration.

    The cost of that shortcut is that the two can drift: a model change without
    a matching migration passes the tests and breaks deployment. `alembic check`
    against a live database is what catches it; run it before deploying.
    """
    metadata.create_all(engine)


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
        with get_engine().begin() as conn:
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
            conn.execute(
                daily_questions.insert().values(day=day, question_id=question_id)
            )
