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
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

import cache
import content
import db
from identity import get_voter_id
from models import DailyQuestion, DailyResults, VoteRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/daily", tags=["daily"])


def today() -> date:
    """UTC, not server-local — the day boundary has to be the same for everyone."""
    return datetime.now(timezone.utc).date()


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
        log.warning("daily_questions lookup failed, falling back to rotation", exc_info=True)

    question = content.get_question(question_id) if question_id else None
    if question is None:
        rotation = content.rotation()
        question = content.get_question(rotation[day.toordinal() % len(rotation)])
    return question


def tally_available_at(day: date) -> datetime:
    """Midnight UTC at the end of `day` — when the community count unlocks."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc)


def voter_choice(question_id: str, voter_id: str) -> str | None:
    """What this voter picked — Redis first, Postgres as the fallback.

    Redis answers this in one hop and is the normal path. When it's gone we
    fall back to the durable log rather than telling a player who already
    voted that they haven't: Postgres is the source of truth, Redis is the
    accelerator in front of it.
    """
    try:
        return cache.previous_choice(question_id, voter_id)
    except cache.CacheUnavailable:
        log.warning("redis unavailable, reading voter choice from postgres", exc_info=True)

    try:
        return db.get_voter_choice(question_id, voter_id)
    except SQLAlchemyError:
        # Both stores down. Claiming "not voted" is the safe answer: it shows
        # the vote screen rather than a reveal, so rule #1 still holds.
        log.error("both redis and postgres unavailable for voter lookup", exc_info=True)
        return None


def community_tally(question_id: str) -> dict[str, int] | None:
    """The vote split — Redis first, rebuilt from the durable log if it's gone."""
    try:
        return cache.get_tally(question_id)
    except cache.CacheUnavailable:
        log.warning("redis unavailable, rebuilding tally from postgres", exc_info=True)

    try:
        return db.tally(question_id)
    except SQLAlchemyError:
        log.error("both redis and postgres unavailable for tally", exc_info=True)
        return None


def _results(question: dict, day: date, your_choice: str) -> DailyResults:
    unlocks_at = tally_available_at(day)
    available = datetime.now(timezone.utc) >= unlocks_at

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
    except cache.CacheUnavailable:
        # Fail closed. Without the atomic dedupe+tally gate there's no way to
        # accept a vote and still guarantee it's counted exactly once, and an
        # exact count is the one thing this whole design exists to protect.
        # Refusing the vote is recoverable; a silently wrong tally is not.
        log.error("redis unavailable, refusing vote", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Voting is temporarily unavailable. Please try again in a moment.",
            headers={"Retry-After": "5"},
        )

    if count == cache.DUPLICATE_VOTE:
        raise HTTPException(
            status_code=409,
            detail="You've already voted on today's question.",
        )

    # Counted in Redis, so the player's vote is safe. Persist after responding.
    background.add_task(db.record_vote, question["id"], voter_id, body.choice)

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
