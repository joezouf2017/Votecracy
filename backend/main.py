import logging
import random

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import content
from daily import router as daily_router
from logging_config import configure_logging
from models import QuestionSummary, RevealData, VoteRequest
from settings import get_settings

# Before anything else logs. uvicorn leaves the root logger unconfigured, so
# without this the application's records reach stderr through
# logging.lastResort: no level, no timestamp, and nothing below WARNING at all.
configure_logging()

log = logging.getLogger(__name__)


# No schema creation here on purpose. Alembic owns the schema, and the
# container runs `alembic upgrade head` before uvicorn starts. An app that also
# creates tables would race the migration and win, leaving Alembic to fail on
# a table it thinks it still has to create.
app = FastAPI(title="Votecracy API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    # Daily mode identifies voters with an httpOnly cookie, which the browser
    # only sends cross-origin when credentials are allowed on both ends.
    allow_credentials=True,
)

app.include_router(daily_router)


@app.get("/api/questions", response_model=list[QuestionSummary])
def list_questions():
    return [content.public_view(q) for q in content.all_questions()]


@app.get("/api/questions/random", response_model=QuestionSummary)
def random_question():
    return content.public_view(random.choice(content.all_questions()))


@app.post("/api/questions/{question_id}/vote", response_model=RevealData)
def vote(question_id: str, body: VoteRequest):
    q = content.get_question(question_id)
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
    return {"status": "ok", "questions_loaded": len(content.all_questions())}
