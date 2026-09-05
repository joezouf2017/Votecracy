"""Test wiring for the distributed vote path.

Redis and Postgres are both swapped for in-process stand-ins so the whole
suite runs with `pytest` and no containers:

- fakeredis runs the *real* Lua script, so the atomicity the vote path depends
  on is genuinely exercised rather than mocked away.
- SQLite backs the durable log. The schema is portable SQLAlchemy Core, and
  the thing under test is the UNIQUE(question_id, voter_id) constraint, which
  behaves the same on both engines.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import cache
import db
from main import app


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(cache, "get_redis", lambda: client)
    cache._cast_vote_script.cache_clear()
    yield client
    cache._cast_vote_script.cache_clear()


@pytest.fixture(autouse=True)
def sqlite_db(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'votecracy-test.db'}", future=True)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    db.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client():
    """A fresh client per test — its cookie jar is one anonymous player."""
    with TestClient(app) as c:
        yield c
