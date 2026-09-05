"""What happens when Redis goes away.

The policy under test, stated once:

  writes fail closed   — a vote is refused rather than counted unreliably
  reads fail over      — Postgres is the source of truth, Redis is a cache

The asymmetry is the point. Refusing a vote is annoying and recoverable;
accepting one without the atomic dedupe+tally gate would silently corrupt the
exact count that every other test in this suite is protecting.
"""

from datetime import date, timedelta

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


def test_tally_is_rebuilt_from_postgres_when_redis_is_down(client, kill_redis, monkeypatch):
    """The tally is a cache of `SELECT choice, count(*) ... GROUP BY choice`,
    so it can always be recovered from the durable log."""
    monkeypatch.setattr(daily, "today", lambda: date.today() - timedelta(days=2))

    question = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": question["options"][0]})

    kill_redis()

    body = client.get("/api/daily/results").json()
    assert body["tally_available"] is True
    assert body["tally"] == {question["options"][0]: 1}
    assert body["total_votes"] == 1


def test_no_reveal_when_both_stores_are_down(client, kill_redis, kill_postgres):
    """With nothing left to prove the player voted, rule #1 wins: no reveal."""
    question = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": question["options"][0]})

    kill_redis()
    kill_postgres()

    assert client.get("/api/daily/results").status_code == 403
