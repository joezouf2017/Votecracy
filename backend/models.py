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
