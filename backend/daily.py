"""Daily vote mode — the distributed path.

One question per UTC day, hundreds of players voting at once. The shape of a
vote request:

    atomic dedupe + tally in Redis  ->  respond  ->  durable write to Postgres

The Postgres write is a BackgroundTask on purpose: it happens after the
response is sent, so database latency never shows up in the player's vote.
Redis is what makes the count correct under concurrency; Postgres is what
makes it survive a restart.
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


def _results(question: dict, day: date, your_choice: str) -> DailyResults:
    unlocks_at = tally_available_at(day)
    available = datetime.now(timezone.utc) >= unlocks_at

    tally = cache.get_tally(question["id"]) if available else None
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
        already_voted=cache.previous_choice(question["id"], voter_id) is not None,
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

    count = cache.cast_vote(question["id"], voter_id, body.choice)
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

    your_choice = cache.previous_choice(question["id"], voter_id)
    if your_choice is None:
        # Rule #1, enforced server-side: no reveal without a vote.
        raise HTTPException(
            status_code=403,
            detail="Vote on today's question before viewing the results.",
        )

    return _results(question, day, your_choice)
