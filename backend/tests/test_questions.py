"""Quick mode (Phase 1) — the endpoint contract for the single-player loop.

These are endpoint tests, not unit tests: each one goes through routing,
response_model serialisation and the content store together. That's the right
level for what they assert — status codes and response shape — but it means a
failure here doesn't localise on its own. The pure pieces underneath
(`content.public_view`, the rotation ordering) have solitary tests in
test_pure.py; nothing here should be re-testing those.
"""

# The reveal fixture these tests vote on. Named rather than indexed, so
# reordering questions.json can't silently change what's under test.
SAMPLE_ID = "us-medicare-1965"


def sample(client) -> dict:
    questions = client.get("/api/questions").json()
    return next(q for q in questions if q["id"] == SAMPLE_ID)


def test_list_questions_returns_at_least_five(client):
    """Asserts the seed dataset, not the code — a failure here means
    questions.json shrank, not that anything broke."""
    r = client.get("/api/questions")
    assert r.status_code == 200
    assert len(r.json()) >= 5


def test_list_questions_never_includes_reveal(client):
    for q in client.get("/api/questions").json():
        assert "reveal" not in q
        assert "id" in q
        assert "prompt" in q
        assert "options" in q


def test_random_question_is_random_and_never_includes_reveal(client):
    """Draws repeatedly rather than once. A single draw checked one arbitrary
    question of eight and couldn't tell a working endpoint from one that always
    returns the same one."""
    seen = set()
    for _ in range(30):
        body = client.get("/api/questions/random").json()
        assert "reveal" not in body
        seen.add(body["id"])
    assert len(seen) > 1


def test_vote_returns_reveal_after_valid_choice(client):
    q = sample(client)
    r = client.post(f"/api/questions/{q['id']}/vote", json={"choice": q["options"][0]})
    assert r.status_code == 200
    reveal = r.json()
    assert reveal["your_choice"] == q["options"][0]
    assert "actual_vote" in reveal
    assert "outcome" in reveal
    assert "source" in reveal


def test_vote_rejects_invalid_choice(client):
    q = sample(client)
    r = client.post(f"/api/questions/{q['id']}/vote", json={"choice": "NotAnOption"})
    assert r.status_code == 400


def test_vote_rejects_unknown_question(client):
    r = client.post("/api/questions/does-not-exist/vote", json={"choice": "Support"})
    assert r.status_code == 404


def test_health(client):
    """Also a dataset assertion — see test_list_questions_returns_at_least_five."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["questions_loaded"] >= 5


# --- the two field lists that are written down twice --------------------------
#
# `shared/` may not import `game/`, so `content` cannot take these names from
# the response models it has to agree with. That makes them duplicated by
# necessity rather than by neglect — and duplication that nothing checks is
# just a comment. These two tests are the check.


def test_reveal_fields_match_what_the_vote_endpoint_splats():
    """`main.vote` builds RevealData with `**q["reveal"]` plus three names it
    supplies itself. Anything else RevealData requires has to come from the
    reveal block, so that is what `content` must validate — otherwise a
    question missing a field is a 500 on the vote that surfaces it, and only
    on that question."""
    from game.schemas import RevealData
    from shared import content

    supplied_by_the_endpoint = {"your_choice", "category", "era"}
    assert set(RevealData.model_fields) - supplied_by_the_endpoint == set(
        content.REVEAL_FIELDS
    )


def test_public_fields_match_the_pre_vote_response_model():
    """`public_view` is rule #1's whitelist and `QuestionSummary` is the API's.
    They are the same five names in two packages; the Phase 3 chatbot will go
    through the first and not the second, so a field added to one and not the
    other is either a leak or a missing field."""
    from game.schemas import QuestionSummary
    from shared import content

    assert set(QuestionSummary.model_fields) == content._PUBLIC_FIELDS
