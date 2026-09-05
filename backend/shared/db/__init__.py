"""One schema, two systems, and the import that keeps them together.

`shared.db.votes` is the live game path's half; `shared.db.corpus` is the
content pipeline's. Splitting them means each system's tables live next to the
queries that use them, and a reader can see which half they are in.

**Both submodules are imported here on purpose, and removing either is a
schema-destroying bug.** SQLAlchemy registers a `Table` with the metadata when
its module is imported. Alembic compares that metadata against the live
database, so if only half the tables have been imported when `alembic
revision --autogenerate` runs, the other half look like tables that exist in
the database and should not — and it writes `op.drop_table` for every one of
them. The failure is silent at import time and catastrophic at migration time,
which is why `test_layering.py` asserts the full set is present after a bare
`from shared import db`.
"""

# Imported for their side effect: registering tables on `metadata`. Not
# re-exported — callers say which half they mean.
from shared.db import corpus, engine, votes  # noqa: F401  (see the module docstring)
from shared.db.engine import EMBEDDING_DIM, Vector, create_all_for_tests, metadata

# `get_engine` is deliberately not re-exported. `from ... import get_engine`
# takes a snapshot of the function, so a test that replaces the engine would
# not reach a caller holding that snapshot — which is exactly how five tests
# silently started reading the real Postgres during the package split. Reach
# it as `engine.get_engine()`; `test_layering` enforces this.
__all__ = [
    "EMBEDDING_DIM",
    "Vector",
    "corpus",
    "create_all_for_tests",
    "engine",
    "metadata",
    "votes",
]
