"""Daily vote mode — the distributed path.

One question per UTC day, hundreds of players voting at once. The shape of a
vote request:

    atomic dedupe + tally in Redis  ->  respond  ->  durable write to Postgres

The Postgres write is a BackgroundTask on purpose: it happens after the
response is sent, so database latency never shows up in the player's vote.
Redis is what makes the count correct under concurrency; Postgres is what
makes it survive a restart.

Degradation policy when Redis is unavailable:

- Writes fail closed. `POST /vote` returns 503, not a partial write. Without
  the atomic gate there's no way to accept a vote and still promise it was
  counted exactly once, and that promise is the whole point of the phase.
- Reads fail over to Postgres. Serving a question, remembering that a player
  already voted, and rebuilding the tally all have slower authoritative
  answers in the durable log, so a cache outage degrades latency, not truth.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from game import cache
from game.identity import get_voter_id
from game.models import DailyQuestion, DailyResults, VoteRequest
from shared import content, db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/daily", tags=["daily"])


def now() -> datetime:
    """The clock, in one place. Every time-dependent branch reads it, so tests
    can move time instead of waiting for it."""
    return datetime.now(UTC)


def today() -> date:
    """UTC, not server-local — the day boundary has to be the same for everyone."""
    return now().date()


def question_for_day(day: date) -> dict:
    """The question live on `day`.

    An admin pins a day by inserting a row in `daily_questions`. With no row,
    fall back to a deterministic rotation over the curated set so the game
    always has something to show — including before any admin tooling exists.
    """
    question_id = None
    try:
        question_id = db.get_daily_question_id(day)
    except SQLAlchemyError:
        # Serving yesterday's rotation beats serving a 500. The admin override
        # is a nice-to-have; having a question at all is not.
        log.warning(
            "daily_questions lookup failed, falling back to rotation", exc_info=True
        )

    question = content.get_question(question_id) if question_id else None
    if question is None:
        rotation = content.rotation()
        question = content.get_question(rotation[day.toordinal() % len(rotation)])
    return question


def tally_available_at(day: date) -> datetime:
    """Midnight UTC at the end of `day` — when the community count unlocks."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)


def tally_is_unlocked(day: date, at: datetime) -> bool:
    """Whether `day`'s community split may be shown at time `at`.

    Pulled out of `_results` so the boundary can be tested directly. An
    off-by-one here shows the tally an hour early — which is a spoiler, and
    spoilers are the one thing this game can't get wrong.
    """
    return at >= tally_available_at(day)


def voter_choice(question_id: str, voter_id: str) -> str | None:
    """What this voter picked — Redis first, Postgres as the fallback.

    Redis answers this in one hop and is the normal path. Two ways it can stop
    being trustworthy, and both fall through to the durable log:

    - unreachable — raises, obvious
    - alive but empty after a restart — returns None, *not* obvious. An empty
      cache can't distinguish "never voted" from "marker lost", so a bare None
      isn't enough to conclude the player hasn't voted.

    That's why a None is retried against Postgres and not just an exception.
    The cost is one indexed lookup per page load for players who genuinely
    haven't voted yet; `GET /api/daily` is once per player per day, so this
    isn't the hot path — casting the vote is, and that still never touches
    Postgres synchronously.
    """
    try:
        cached = cache.previous_choice(question_id, voter_id)
        if cached is not None:
            return cached
    except cache.CacheUnavailable:
        log.warning(
            "redis unavailable, reading voter choice from postgres", exc_info=True
        )

    try:
        return db.get_voter_choice(question_id, voter_id)
    except SQLAlchemyError:
        # Both stores down. Claiming "not voted" is the safe answer: it shows
        # the vote screen rather than a reveal, so rule #1 still holds.
        log.error("both redis and postgres unavailable for voter lookup", exc_info=True)
        return None


def community_tally(question_id: str) -> dict[str, int] | None:
    """The vote split — read from the durable log, then cached in Redis.

    Postgres first, deliberately, even though Redis holds the same numbers and
    answers faster. A Redis that came back empty doesn't error; it answers
    *plausibly wrong*, and a tally that silently undercounts is worse than a
    slow one. This number is only read once the day has closed, so one GROUP BY
    over an indexed column is a fine price for it always being right.

    Redis stays in the picture as the cache: the answer is written back so
    repeat views of the same closed day don't re-run the query, and it's still
    the fallback if Postgres is the store that's unreachable.
    """
    try:
        tally = db.tally(question_id)
    except SQLAlchemyError:
        log.warning(
            "postgres unavailable, serving tally from the redis cache", exc_info=True
        )
        try:
            return cache.get_tally(question_id)
        except cache.CacheUnavailable:
            log.error("both redis and postgres unavailable for tally", exc_info=True)
            return None

    try:
        cache.store_tally(question_id, tally)
    except cache.CacheUnavailable:
        log.warning("could not refresh the cached tally", exc_info=True)

    return tally


def persist_vote(question_id: str, voter_id: str, choice: str) -> None:
    """Background task: append the vote to the durable log.

    Failures are swallowed — the vote is already counted and the response is
    already sent, so there is nothing left to raise into. But they must not be
    swallowed *silently*: the durable log rejecting a duplicate means Redis and
    Postgres disagree about who has voted, which is the signature of the cache
    having lost its voter markers. This log line is the only place that shows up.
    """
    try:
        if not db.record_vote(question_id, voter_id, choice):
            log.error(
                "durable log rejected a duplicate for voter %s on %s — redis and "
                "postgres disagree; the cache may have lost its voter markers",
                voter_id,
                question_id,
            )
    except SQLAlchemyError:
        log.error(
            "durable write failed for voter %s on %s; vote counted in redis only",
            voter_id,
            question_id,
            exc_info=True,
        )


def _results(question: dict, day: date, your_choice: str) -> DailyResults:
    unlocks_at = tally_available_at(day)
    available = tally_is_unlocked(day, now())

    tally = community_tally(question["id"]) if available else None
    # Don't claim the tally is available while handing back nothing.
    available = available and tally is not None
    return DailyResults(
        question_id=question["id"],
        day=day,
        your_choice=your_choice,
        tally_available=available,
        tally_available_at=unlocks_at,
        tally=tally,
        total_votes=sum(tally.values()) if tally is not None else None,
        **question["reveal"],
    )


@router.get("", response_model=DailyQuestion)
def get_daily(voter_id: str = Depends(get_voter_id)):
    day = today()
    question = question_for_day(day)
    return DailyQuestion(
        **content.public_view(question),
        day=day,
        already_voted=voter_choice(question["id"], voter_id) is not None,
    )


@router.post("/vote", response_model=DailyResults)
def vote_daily(
    body: VoteRequest,
    background: BackgroundTasks,
    voter_id: str = Depends(get_voter_id),
):
    day = today()
    question = question_for_day(day)

    if body.choice not in question["options"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid choice. Valid options: {question['options']}",
        )

    try:
        count = cache.cast_vote(question["id"], voter_id, body.choice)
    except cache.CacheUnavailable as unavailable:
        # Fail closed. Without the atomic dedupe+tally gate there's no way to
        # accept a vote and still guarantee it's counted exactly once, and an
        # exact count is the one thing this whole design exists to protect.
        # Refusing the vote is recoverable; a silently wrong tally is not.
        log.error("redis unavailable, refusing vote", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Voting is temporarily unavailable. Please try again in a moment.",
            headers={"Retry-After": "5"},
        ) from unavailable

    if count == cache.DUPLICATE_VOTE:
        raise HTTPException(
            status_code=409,
            detail="You've already voted on today's question.",
        )

    # Counted in Redis, so the player's vote is safe. Persist after responding.
    background.add_task(persist_vote, question["id"], voter_id, body.choice)

    return _results(question, day, body.choice)


@router.get("/results", response_model=DailyResults)
def daily_results(voter_id: str = Depends(get_voter_id)):
    day = today()
    question = question_for_day(day)

    your_choice = voter_choice(question["id"], voter_id)
    if your_choice is None:
        # Rule #1, enforced server-side: no reveal without a vote.
        raise HTTPException(
            status_code=403,
            detail="Vote on today's question before viewing the results.",
        )

    return _results(question, day, your_choice)
