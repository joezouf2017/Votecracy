"""Anonymous voter identity.

The whole identity layer for Phase 2 is one httpOnly cookie holding a random
UUID. No accounts, no login — a player should be able to vote in one tap.

What this does and doesn't buy us, stated plainly because it will come up:
it stops the accidental double-vote (refresh, back button, two tabs) and the
casual one, but a determined player can clear cookies or open a private window
and vote again. Real vote integrity needs real accounts; that's a later phase.
Putting the id in an httpOnly cookie rather than localStorage at least keeps
page scripts from reading or rewriting it, and leaves the seam where an
authenticated user id would slot in.
"""

import os
import re
from uuid import uuid4

from fastapi import Request, Response

COOKIE_NAME = "votecracy_voter"

# Chrome caps cookie lifetime at 400 days; asking for more just gets clamped.
COOKIE_MAX_AGE_SECONDS = 400 * 24 * 60 * 60

_VOTER_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")


def _valid(voter_id: str | None) -> bool:
    """Only accept ids we could have issued.

    This is load-bearing, not defensive noise: the voter id becomes part of a
    Redis key, so an unvalidated cookie lets anyone write arbitrary-length keys
    into Redis.
    """
    return bool(voter_id) and bool(_VOTER_ID_RE.match(voter_id))


def get_voter_id(request: Request, response: Response) -> str:
    """FastAPI dependency: return this player's id, issuing one if needed."""
    voter_id = request.cookies.get(COOKIE_NAME)
    if _valid(voter_id):
        return voter_id

    voter_id = uuid4().hex
    response.set_cookie(
        COOKIE_NAME,
        voter_id,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        # Off for local http dev; on everywhere else.
        secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
    )
    return voter_id
