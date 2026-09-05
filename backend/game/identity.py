"""Voter identity.

The whole identity layer for Phase 2 is one httpOnly cookie holding a random
UUID. No accounts, no login — a player should be able to vote in one tap.

What this does and doesn't buy us, stated plainly because it will come up:
it stops the accidental double-vote (refresh, back button, two tabs) and the
casual one, but a determined player can clear cookies or open a private window
and vote again. Real vote integrity needs real accounts; that's a later phase.
Putting the id in an httpOnly cookie rather than localStorage at least keeps
page scripts from reading or rewriting it.

Ids are prefixed by kind — `anon:8f3a...` today, `user:...` once accounts
exist. The prefix is here now, before there is any data to migrate, because
adding it later means rewriting `voter_id` on every historical vote and every
Redis key. It costs nothing today and buys three things: the validator can
accept both forms without ambiguity, Redis keys stay self-describing, and a
query can tell an anonymous vote from an authenticated one without a join.
"""

import re
from uuid import uuid4

from fastapi import Request, Response

from shared.settings import get_settings

COOKIE_NAME = "votecracy_voter"

# Chrome caps cookie lifetime at 400 days; asking for more just gets clamped.
COOKIE_MAX_AGE_SECONDS = 400 * 24 * 60 * 60

ANON_PREFIX = "anon:"

# kind -> what the part after the colon must look like. Accounts add
# `"user": <pattern>` here and nothing else in this module changes.
_ID_PATTERNS = {
    "anon": re.compile(r"\A[0-9a-f]{32}\Z"),
}


def _valid(voter_id: str | None) -> bool:
    """Only accept ids we could have issued.

    This is load-bearing, not defensive noise: the voter id becomes part of a
    Redis key, so an unvalidated cookie lets anyone write arbitrary-length keys
    into Redis.
    """
    kind, separator, rest = (voter_id or "").partition(":")
    pattern = _ID_PATTERNS.get(kind)
    return bool(separator and pattern and pattern.match(rest))


def get_voter_id(request: Request, response: Response) -> str:
    """FastAPI dependency: return this player's id, issuing one if needed."""
    voter_id = request.cookies.get(COOKIE_NAME)
    if _valid(voter_id):
        return voter_id

    voter_id = f"{ANON_PREFIX}{uuid4().hex}"
    response.set_cookie(
        COOKIE_NAME,
        voter_id,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        # Off for local http dev; on everywhere else.
        secure=get_settings().cookie_secure,
    )
    return voter_id
