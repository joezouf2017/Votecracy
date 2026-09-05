"""The voter cookie is a security boundary, not just a convenience.

Its value ends up inside a Redis key (`voted:{question}:{voter}`), so an
unvalidated cookie would let anyone write keys of their choosing — arbitrary
length, arbitrary content — into the store the vote path depends on. These
tests pin the validation that stops that.
"""

import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from game.identity import ANON_PREFIX, COOKIE_NAME
from game.main import app

VOTER_ID = re.compile(r"\Aanon:[0-9a-f]{32}\Z")

# Anything that isn't exactly 32 lowercase hex chars. Uppercase is in here on
# purpose: `uuid4().hex` is lowercase, so an uppercase id is not one we issued.
MALFORMED = [
    "not-hex-at-all",
    # The unprefixed form issued before ids carried a kind. Rejected on
    # purpose: an id whose kind cannot be read is one we cannot route.
    "0123456789abcdef0123456789abcdef",
    "user:0123456789abcdef0123456789abcdef",  # a kind we do not issue yet
    ANON_PREFIX + "A" * 32,
    ANON_PREFIX + "0" * 31,
    ANON_PREFIX + "0" * 33,
    "x" * 2000,
    "'; FLUSHALL; --",
]


def todays_options() -> list[str]:
    """Read-only peek at the live question. Its client is thrown away so its
    cookie jar can't leak into the request under test."""
    with TestClient(app) as reader:
        return reader.get("/api/daily").json()["options"]


def act_as(voter_id: str, method: str, path: str, **kwargs):
    """One request from a player presenting exactly `voter_id`, no jar."""
    with TestClient(app) as c:
        return getattr(c, method)(
            path, headers={"Cookie": f"{COOKIE_NAME}={voter_id}"}, **kwargs
        )


@pytest.mark.parametrize("malformed", MALFORMED)
def test_malformed_cookie_never_reaches_redis(fake_redis, malformed):
    choice = todays_options()[0]

    r = act_as(malformed, "post", "/api/daily/vote", json={"choice": choice})
    assert r.status_code == 200

    keys = fake_redis.keys("voted:*")
    assert len(keys) == 1
    # The vote was counted under a freshly issued id, not the supplied one.
    assert malformed not in keys[0]
    # voted:<question>:<kind>:<id> — the voter id is the last two segments
    assert VOTER_ID.match(keys[0].split(":", 2)[2])


@pytest.mark.parametrize("malformed", MALFORMED)
def test_malformed_cookie_is_replaced_with_a_valid_one(malformed):
    r = act_as(malformed, "get", "/api/daily")
    issued = r.cookies[COOKIE_NAME]
    assert VOTER_ID.match(issued)


def test_valid_cookie_is_used_as_given(fake_redis):
    voter_id = f"{ANON_PREFIX}{uuid4().hex}"
    choice = todays_options()[0]

    r = act_as(voter_id, "post", "/api/daily/vote", json={"choice": choice})
    assert r.status_code == 200
    assert fake_redis.exists(f"voted:{r.json()['question_id']}:{voter_id}")


def test_valid_cookie_is_not_reissued():
    """A returning player keeps their id — otherwise every visit is a new voter."""
    r = act_as(f"{ANON_PREFIX}{uuid4().hex}", "get", "/api/daily")
    assert COOKIE_NAME not in r.cookies


def test_issued_cookie_is_httponly():
    """Page scripts must not be able to read or rewrite the voter id."""
    with TestClient(app) as c:
        header = c.get("/api/daily").headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
