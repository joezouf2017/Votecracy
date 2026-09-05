from datetime import date, datetime

from pydantic import BaseModel


class VoteRequest(BaseModel):
    choice: str


class QuestionSummary(BaseModel):
    id: str
    category: str
    era: str
    prompt: str
    options: list[str]


class RevealData(BaseModel):
    your_choice: str
    actual_vote: str
    outcome: str
    source: str
    category: str
    era: str


class DailyQuestion(QuestionSummary):
    """Today's question. Carries no reveal data and no tally — rule #1."""

    day: date
    already_voted: bool


class DailyResults(BaseModel):
    """Everything a player is allowed to see once they've voted.

    The historical reveal unlocks the moment they vote. The community tally
    unlocks when the day closes, so nobody can be nudged by a running count
    while the vote is still open.
    """

    question_id: str
    day: date
    your_choice: str
    actual_vote: str
    outcome: str
    source: str
    tally_available: bool
    tally_available_at: datetime
    tally: dict[str, int] | None = None
    total_votes: int | None = None
