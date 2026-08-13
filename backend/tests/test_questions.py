import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_list_questions_returns_at_least_five():
    r = client.get("/api/questions")
    assert r.status_code == 200
    assert len(r.json()) >= 5


def test_list_questions_never_includes_reveal():
    questions = client.get("/api/questions").json()
    for q in questions:
        assert "reveal" not in q
        assert "id" in q
        assert "prompt" in q
        assert "options" in q


def test_random_question_never_includes_reveal():
    r = client.get("/api/questions/random")
    assert r.status_code == 200
    assert "reveal" not in r.json()


def test_vote_returns_reveal_after_valid_choice():
    questions = client.get("/api/questions").json()
    q = questions[0]
    r = client.post(f"/api/questions/{q['id']}/vote", json={"choice": q["options"][0]})
    assert r.status_code == 200
    reveal = r.json()
    assert reveal["your_choice"] == q["options"][0]
    assert "actual_vote" in reveal
    assert "outcome" in reveal
    assert "source" in reveal


def test_vote_rejects_invalid_choice():
    questions = client.get("/api/questions").json()
    q = questions[0]
    r = client.post(f"/api/questions/{q['id']}/vote", json={"choice": "NotAnOption"})
    assert r.status_code == 400


def test_vote_rejects_unknown_question():
    r = client.post("/api/questions/does-not-exist/vote", json={"choice": "Support"})
    assert r.status_code == 404


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["questions_loaded"] >= 5
