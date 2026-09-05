"""Daily mode: the vote → reveal flow under a shared, concurrent tally."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import uuid4

import pytest

import cache
import content
import daily
import db


# --- rule #1: nothing reveals before the vote ---------------------------------


def test_daily_question_never_includes_reveal(client):
    body = client.get("/api/daily").json()
    assert "reveal" not in body
    assert "actual_vote" not in body
    assert "outcome" not in body
    assert body["already_voted"] is False


def test_results_forbidden_before_voting(client):
    r = client.get("/api/daily/results")
    assert r.status_code == 403


def test_daily_question_hides_the_tally(client):
    """Even the running count is withheld while the vote is open."""
    q = client.get("/api/daily").json()
    r = client.post("/api/daily/vote", json={"choice": q["options"][0]})
    assert r.status_code == 200
    assert r.json()["tally_available"] is False
    assert r.json()["tally"] is None


# --- the vote itself ----------------------------------------------------------


def test_vote_returns_reveal_and_echoes_choice(client):
    q = client.get("/api/daily").json()
    r = client.post("/api/daily/vote", json={"choice": q["options"][1]})
    assert r.status_code == 200
    body = r.json()
    assert body["your_choice"] == q["options"][1]
    assert body["question_id"] == q["id"]
    assert body["actual_vote"] and body["outcome"] and body["source"]


def test_vote_rejects_invalid_choice(client):
    r = client.post("/api/daily/vote", json={"choice": "NotAnOption"})
    assert r.status_code == 400


def test_second_vote_from_same_player_is_rejected(client):
    q = client.get("/api/daily").json()
    assert client.post("/api/daily/vote", json={"choice": q["options"][0]}).status_code == 200

    second = client.post("/api/daily/vote", json={"choice": q["options"][1]})
    assert second.status_code == 409

    # ...and the rejected vote left the tally untouched.
    assert cache.get_tally(q["id"]) == {q["options"][0]: 1}


def test_results_available_after_voting(client):
    q = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": q["options"][0]})

    r = client.get("/api/daily/results")
    assert r.status_code == 200
    assert r.json()["your_choice"] == q["options"][0]

    assert client.get("/api/daily").json()["already_voted"] is True


def test_tally_unlocks_once_the_day_closes(client, monkeypatch):
    """A closed day shows the community split; an open one doesn't."""
    closed_day = date.today() - timedelta(days=2)
    monkeypatch.setattr(daily, "today", lambda: closed_day)

    q = client.get("/api/daily").json()
    body = client.post("/api/daily/vote", json={"choice": q["options"][0]}).json()

    assert body["tally_available"] is True
    assert body["tally"] == {q["options"][0]: 1}
    assert body["total_votes"] == 1


# --- durability ---------------------------------------------------------------


def test_vote_is_written_to_the_durable_log(client):
    q = client.get("/api/daily").json()
    client.post("/api/daily/vote", json={"choice": q["options"][0]})
    assert db.count_votes(q["id"]) == 1


def test_durable_log_rejects_a_replayed_vote(sqlite_db):
    """The Postgres constraint holds even if Redis never saw the duplicate."""
    assert db.record_vote("q1", "voter-a", "Support") is True
    assert db.record_vote("q1", "voter-a", "Oppose") is False
    assert db.count_votes("q1") == 1


# --- concurrency: the invariant this whole phase exists for -------------------


@pytest.mark.parametrize("voters", [200])
def test_concurrent_distinct_voters_are_all_counted_exactly_once(voters):
    """Total tally must equal the number of voters — no lost or double counts."""
    voter_ids = [uuid4().hex for _ in range(voters)]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda v: cache.cast_vote("q-load", v, "Support"), voter_ids))

    assert cache.DUPLICATE_VOTE not in results
    # Every call got a distinct sequence number, which is only true if no two
    # increments collided.
    assert sorted(results) == list(range(1, voters + 1))
    assert cache.get_tally("q-load") == {"Support": voters}


def test_concurrent_duplicate_votes_let_exactly_one_through():
    """Same player, 50 simultaneous requests — the classic double-submit race."""
    voter_id = uuid4().hex

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(lambda _: cache.cast_vote("q-dupe", voter_id, "Support"), range(50))
        )

    assert results.count(cache.DUPLICATE_VOTE) == 49
    assert cache.get_tally("q-dupe") == {"Support": 1}


# --- which question is live ---------------------------------------------------


def test_rotation_is_deterministic_for_a_given_day():
    day = date(2026, 3, 14)
    assert daily.question_for_day(day)["id"] == daily.question_for_day(day)["id"]


def test_admin_override_pins_the_days_question(sqlite_db):
    day = date(2026, 3, 14)
    pinned = content.rotation()[-1]
    default = daily.question_for_day(day)["id"]

    db.set_daily_question_id(day, pinned)
    assert daily.question_for_day(day)["id"] == pinned
    assert pinned != default or len(content.rotation()) == 1
