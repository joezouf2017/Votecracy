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

from functools import lru_cache

from sqlalchemy import (
    LargeBinary,
    MetaData,
    create_engine,
)
from sqlalchemy.types import TypeDecorator

from shared.settings import get_settings

metadata = MetaData()

# Deliberately 768, not the model's default. gemini-embedding-001 returns 3072
# dimensions; asking for 768 yields the first 768 components of that vector,
# which is a supported truncation and a quarter of the storage — measured at
# 500 questions, 472 MB against roughly 1.9 GB.
#
# Retrieval here is always scoped to a single question, a few hundred chunks
# rather than the whole table, so the ranking problem is easy enough that the
# extra dimensions buy very little.
#
# Changing this invalidates every stored vector, which is why
# `chunk_embeddings.model` records what produced each one. See embedding.py for
# why the truncated vectors have to be renormalised before they are stored.
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
