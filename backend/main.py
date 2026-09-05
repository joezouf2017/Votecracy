import logging
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

import content
import db
from daily import router as daily_router
from models import QuestionSummary, RevealData, VoteRequest

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.init_db()
    except SQLAlchemyError:
        # Quick mode doesn't touch Postgres, so a missing database shouldn't
        # stop the app booting — daily mode will fail loudly on its own.
        log.warning("could not initialise database schema", exc_info=True)
    yield


app = FastAPI(title="Votecracy API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
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
