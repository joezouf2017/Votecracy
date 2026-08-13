import json
import random
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import QuestionSummary, RevealData, VoteRequest

app = FastAPI(title="Votecracy API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_data_path = Path(__file__).parent / "data" / "questions.json"
with open(_data_path, encoding="utf-8") as f:
    _questions: list[dict] = json.load(f)

_by_id: dict[str, dict] = {q["id"]: q for q in _questions}


def _public(q: dict) -> dict:
    """Strip the reveal field so it's never sent before a vote."""
    return {k: v for k, v in q.items() if k != "reveal"}


@app.get("/api/questions", response_model=list[QuestionSummary])
def list_questions():
    return [_public(q) for q in _questions]


@app.get("/api/questions/random", response_model=QuestionSummary)
def random_question():
    return _public(random.choice(_questions))


@app.post("/api/questions/{question_id}/vote", response_model=RevealData)
def vote(question_id: str, body: VoteRequest):
    q = _by_id.get(question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    if body.choice not in q["options"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid choice. Valid options: {q['options']}",
        )
    return RevealData(
        your_choice=body.choice,
        category=q["category"],
        era=q["era"],
        **q["reveal"],
    )


@app.get("/health")
def health():
    return {"status": "ok", "questions_loaded": len(_questions)}
