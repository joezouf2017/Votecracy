"""What happens when Redis goes away.

The policy under test, stated once:

  writes fail closed   — a vote is refused rather than counted unreliably
  reads fail over      — Postgres is the source of truth, Redis is a cache

The asymmetry is the point. Refusing a vote is annoying and recoverable;
accepting one without the atomic dedupe+tally gate would silently corrupt the
exact count that every other test in this suite is protecting.
"""

import logging
from datetime import date, datetime, timezone

import cache
import daily


def test_vote_is_refused_with_503_when_redis_is_down(client, kill_redis):
    question = client.get("/api/daily").json()
    kill_redis()

    r = client.post("/api/daily/vote", json={"choice": question["options"][0]})

    # 503 + Retry-After, not a bare 500 — this is a known, temporary condition
    # and the client should be told it's worth retrying.
    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"


def test_question_is_still_served_when_redis_is_down(client, kill_redis):
    kill_redis()

    r = client.get("/api/daily")

    assert r.status_code == 200
    body = r.json()
    assert body["options"]
    assert body["already_voted"] is False
    assert "outcome" not in body  # rule #1 holds in the degraded path too


def test_already_voted_survives_a_redis_outage(client, kill_redis):
    """A player who voted must not be asked to vote again just because the
    cache went away — the durable log still knows."""
    question = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": question["options"][0]})

    kill_redis()

    assert client.get("/api/daily").json()["already_voted"] is True


def test_reveal_still_works_when_redis_is_down(client, kill_redis):
    question = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": question["options"][1]})

    kill_redis()

    r = client.get("/api/daily/results")
    assert r.status_code == 200
    assert r.json()["your_choice"] == question["options"][1]


def vote_then_close_the_day(client, monkeypatch) -> dict:
    """Cast one vote while the day is open, then move the clock past midnight."""
    day = date(2026, 3, 14)
    monkeypatch.setattr(daily, "today", lambda: day)
    monkeypatch.setattr(daily, "now", lambda: datetime(2026, 3, 14, 12, tzinfo=timezone.utc))

    question = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": question["options"][0]})

    monkeypatch.setattr(daily, "now", lambda: datetime(2026, 3, 15, 0, 1, tzinfo=timezone.utc))
    return question


def test_tally_ignores_a_stale_cache_and_reads_the_durable_log(
    client, fake_redis, monkeypatch
):
    """The failure this whole change is about.

    A Redis that came back empty — or wrong — doesn't raise; it answers
    plausibly. So the tally is read from Postgres and the cache is corrected,
    rather than the cache being trusted because it didn't complain.
    """
    question = vote_then_close_the_day(client, monkeypatch)
    choice = question["options"][0]

    # Pretend the cache drifted: a restart, a partial rebuild, anything.
    fake_redis.hset(cache.tally_key(question["id"]), mapping={choice: 999})

    body = client.get("/api/daily/results").json()

    assert body["tally"] == {choice: 1}
    assert body["total_votes"] == 1
    # ...and the read repaired the cache on its way through.
    assert fake_redis.hgetall(cache.tally_key(question["id"])) == {choice: "1"}


def test_already_voted_survives_a_redis_flush(client, fake_redis):
    """Redis alive but empty is the dangerous case: nothing errors, the cache
    just quietly forgets who voted. The durable log has to be consulted before
    concluding a player hasn't voted."""
    question = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": question["options"][1]})

    fake_redis.flushall()

    assert client.get("/api/daily").json()["already_voted"] is True
    assert client.get("/api/daily/results").json()["your_choice"] == question["options"][1]


def test_tally_falls_back_to_the_cache_when_postgres_is_down(
    client, kill_postgres, monkeypatch
):
    """Postgres is the source of truth, but Redis still holds the same numbers —
    an outage there degrades to the cache rather than to nothing."""
    question = vote_then_close_the_day(client, monkeypatch)

    kill_postgres()

    body = client.get("/api/daily/results").json()
    assert body["tally_available"] is True
    assert body["tally"] == {question["options"][0]: 1}


def test_divergence_between_the_stores_is_logged(sqlite_db, caplog):
    """When the durable log rejects a vote Redis already counted, the two
    stores disagree. That log line is the only signal it happened."""
    daily.persist_vote("q1", "voter-a", "Support")

    with caplog.at_level(logging.ERROR):
        daily.persist_vote("q1", "voter-a", "Support")

    assert "disagree" in caplog.text
    assert "voter-a" in caplog.text


def test_no_reveal_when_both_stores_are_down(client, kill_redis, kill_postgres):
    """With nothing left to prove the player voted, rule #1 wins: no reveal."""
    question = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": question["options"][0]})

    kill_redis()
    kill_postgres()

    assert client.get("/api/daily/results").status_code == 403
