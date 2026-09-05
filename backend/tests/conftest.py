"""Test wiring for the distributed vote path.

Redis and Postgres are both swapped for in-process stand-ins so the whole
suite runs with `pytest` and no containers:

- fakeredis runs the *real* Lua script, so the atomicity the vote path depends
  on is genuinely exercised rather than mocked away.
- SQLite backs the durable log. The schema is portable SQLAlchemy Core, and
  the thing under test is the UNIQUE(question_id, voter_id) constraint, which
  behaves the same on both engines.
"""

from datetime import UTC, date, datetime

import fakeredis
import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from game import cache, daily
from game.main import app
from shared import db


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
    db.create_all_for_tests(engine)
    yield engine
    engine.dispose()


# A fixed Wednesday. The daily question is `rotation[day.toordinal() % 8]`, so
# without pinning the date the suite exercises a different question every day
# it runs — benign today only because all eight questions happen to have the
# same shape.
FROZEN_DAY = date(2026, 3, 14)
FROZEN_NOW = datetime(2026, 3, 14, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Stop the clock at midday on FROZEN_DAY.

    Both time reads are pinned, not just the date: `today()` decides which
    question is live, `now()` decides whether the tally has unlocked. Leaving
    `now()` real would put every test past the unlock boundary of a fixed past
    day and quietly reveal the tally everywhere.

    Tests about the day boundary re-patch `daily.now` themselves.
    """
    monkeypatch.setattr(daily, "today", lambda: FROZEN_DAY)
    monkeypatch.setattr(daily, "now", lambda: FROZEN_NOW)


@pytest.fixture
def client():
    """A fresh client per test — its cookie jar is one anonymous player."""
    with TestClient(app) as c:
        yield c


class _DeadRedis:
    """Every call raises, the way a client behaves when Redis is unreachable."""

    def __getattr__(self, name):
        def boom(*args, **kwargs):
            raise redis.ConnectionError(
                "Error 111 connecting to redis:6379. Connection refused."
            )

        return boom


@pytest.fixture
def kill_redis(monkeypatch):
    """Take Redis away — returns a callable so a test can do it mid-flow.

    Some degradation paths only matter *after* a vote has been cast, so this
    has to be triggerable partway through a test rather than only at setup.
    """

    def _kill():
        monkeypatch.setattr(cache, "get_redis", lambda: _DeadRedis())
        cache._cast_vote_script.cache_clear()

    return _kill


@pytest.fixture
def kill_postgres(monkeypatch):
    """Take Postgres away too, for the both-stores-down case."""

    def _kill():
        def boom():
            raise OperationalError("connection failed", None, Exception("down"))

        monkeypatch.setattr(db, "get_engine", boom)

    return _kill
