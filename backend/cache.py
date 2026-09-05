"""Redis layer for the live vote path.

Everything on the vote hot path lives here. The design rule for Phase 2:
a single player's vote must either be fully counted or not counted at all,
even under concurrency. Two things have to happen together —

  1. mark this voter as having voted on this question (duplicate prevention)
  2. increment the tally for their chosen option

Doing those as two separate round trips leaves a window where the process can
die between them, marking the voter as done without counting their vote. So
they run as one Lua script, which Redis executes atomically.
"""

import os
from functools import lru_cache

import redis

# KEYS[1] = voted:{question_id}:{voter_id}   ARGV[1] = choice
# KEYS[2] = tally:{question_id}              ARGV[2] = ttl seconds for the marker
#
# SET ... NX returns nil (falsy in Lua) when the key already exists, which is
# exactly the "this voter already voted" case. Returning -1 lets the caller
# distinguish a duplicate from a genuine tally of 0.
_CAST_VOTE_LUA = """
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
    return redis.call('HINCRBY', KEYS[2], ARGV[1], 1)
else
    return -1
end
"""

# Voter markers expire after 30 days. Long enough that nobody can re-vote on a
# daily question, short enough that Redis doesn't accumulate keys forever —
# Postgres holds the permanent record, Redis is just the fast path.
VOTER_MARKER_TTL_SECONDS = 60 * 60 * 24 * 30

DUPLICATE_VOTE = -1

# Re-exported so callers can handle "Redis is gone" without importing redis
# themselves — the degradation policy lives in daily.py, the dependency here.
CacheUnavailable = redis.RedisError


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    """Lazily build the client so importing this module doesn't need a server."""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def voter_key(question_id: str, voter_id: str) -> str:
    return f"voted:{question_id}:{voter_id}"


def tally_key(question_id: str) -> str:
    return f"tally:{question_id}"


@lru_cache(maxsize=4)
def _cast_vote_script(client: redis.Redis):
    """Registered once per client so the hot path sends EVALSHA, not the script body."""
    return client.register_script(_CAST_VOTE_LUA)


def cast_vote(question_id: str, voter_id: str, choice: str) -> int:
    """Atomically dedupe + tally.

    Returns the new count for `choice`, or DUPLICATE_VOTE if this voter has
    already voted on this question.
    """
    client = get_redis()
    script = _cast_vote_script(client)
    return int(
        script(
            keys=[voter_key(question_id, voter_id), tally_key(question_id)],
            args=[choice, VOTER_MARKER_TTL_SECONDS],
        )
    )


def previous_choice(question_id: str, voter_id: str) -> str | None:
    """What this voter picked, or None if they haven't voted on this question.

    The marker key stores the choice itself, so this doubles as the
    "has this voter voted?" gate for the results endpoint.
    """
    return get_redis().get(voter_key(question_id, voter_id))


def get_tally(question_id: str) -> dict[str, int]:
    raw = get_redis().hgetall(tally_key(question_id))
    return {choice: int(count) for choice, count in raw.items()}
