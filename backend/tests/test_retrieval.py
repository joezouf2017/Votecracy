"""The scope predicate — rule #1's only implementation.

These are sociable tests: they build a corpus in SQLite through the same
schema production uses, because the thing under test *is* a WHERE clause and a
solitary test of it could only assert that the code says what it says.

The corpus below is deliberately the awkward one. One document straddles the
boundary in role rather than in date, one is published exactly on the decision
date, and one is genuinely after it. Between them they cover every way a chunk
can end up on the wrong side.
"""

from datetime import UTC, date, datetime

import pytest

from pipeline import grounding, ingest, retrieval
from shared.db import corpus

QID = "us-medicare-1965"
DECISION = date(1965, 4, 8)  # from questions.json, via content.decision_date

BEFORE = date(1965, 4, 6)
AFTER = date(1965, 5, 1)


def _document(conn, *, doc_id, published_date, text, question_id=QID):
    conn.execute(
        corpus.source_documents.insert(),
        {
            "id": doc_id,
            "question_id": question_id,
            "source_key": "govinfo:crecb",
            "external_id": f"test-{doc_id}",
            "url": "https://example.invalid/",
            "title": None,
            "published_date": published_date,
            "content_type": "text/plain",
            "sha256": "0" * 64,
            "text": text,
            "fetched_at": datetime.now(UTC),
        },
    )


def _chunk(
    conn, *, doc_id, ordinal, role, published_date, start, end, text, question_id=QID
):
    conn.execute(
        corpus.source_chunks.insert(),
        {
            "document_id": doc_id,
            "question_id": question_id,
            "published_date": published_date,
            "role": role,
            "ordinal": ordinal,
            "char_start": start,
            "char_end": end,
            "text": text,
        },
    )


# Document 1's text holds both halves of the case `role` exists for: an
# amendment's description, which is legitimate pre-vote material, and its vote
# counts, which are not — same page, same day, both published before the
# decision. The date filter cannot separate them.
DOC1_TEXT = (
    "The amendment would extend hospital insurance. "
    "The amendment failed, 128 to 291."
)
DESCRIPTION = (0, 46)
COUNTS = (47, 80)


@pytest.fixture
def corpus_rows(sqlite_db):
    with sqlite_db.begin() as conn:
        _document(conn, doc_id=1, published_date=BEFORE, text=DOC1_TEXT)
        _chunk(
            conn,
            doc_id=1,
            ordinal=0,
            role="framing",
            published_date=BEFORE,
            start=DESCRIPTION[0],
            end=DESCRIPTION[1],
            text=DOC1_TEXT[DESCRIPTION[0] : DESCRIPTION[1]],
        )
        _chunk(
            conn,
            doc_id=1,
            ordinal=1,
            role="vote_record",
            published_date=BEFORE,
            start=COUNTS[0],
            end=COUNTS[1],
            text=DOC1_TEXT[COUNTS[0] : COUNTS[1]],
        )

        # Published *on* the decision date: post-vote by one day.
        _document(conn, doc_id=2, published_date=DECISION, text="Passed, 313 to 115.")
        _chunk(
            conn,
            doc_id=2,
            ordinal=0,
            role="vote_record",
            published_date=DECISION,
            start=0,
            end=19,
            text="Passed, 313 to 115.",
        )

        _document(conn, doc_id=3, published_date=AFTER, text="19 million enrolled.")
        _chunk(
            conn,
            doc_id=3,
            ordinal=0,
            role="outcome",
            published_date=AFTER,
            start=0,
            end=20,
            text="19 million enrolled.",
        )

        # A different question's framing, published in the same window. Scope
        # is per question; without the question_id predicate this would appear
        # in every question's pre-vote material.
        _document(
            conn,
            doc_id=4,
            published_date=BEFORE,
            text="Clean air standards.",
            question_id="us-clean-air-act-1970",
        )
        _chunk(
            conn,
            doc_id=4,
            ordinal=0,
            role="framing",
            published_date=BEFORE,
            start=0,
            end=20,
            text="Clean air standards.",
            question_id="us-clean-air-act-1970",
        )
    return sqlite_db


# --- the predicate ------------------------------------------------------------


def test_pre_vote_admits_only_framing_published_before_the_decision(corpus_rows):
    got = retrieval.chunks(QID, retrieval.Scope.PRE_VOTE)
    assert [c.text for c in got] == [DOC1_TEXT[DESCRIPTION[0] : DESCRIPTION[1]]]


def test_pre_vote_excludes_a_margin_published_before_the_decision(corpus_rows):
    """The case `role` exists for, and the one a date filter alone gets wrong.

    A rejected amendment's vote counts are `vote_record` and are published
    *before* the decision. `published_date < decision_date` admits them, and so
    would `role != 'outcome'`. Only the framing whitelist keeps "failed, 128 to
    291" away from a player who has not voted.
    """
    text = retrieval.scope_text(QID, retrieval.Scope.PRE_VOTE)
    assert "128 to 291" not in text
    assert "hospital insurance" in text


def test_a_chunk_published_on_the_decision_date_is_post_vote(corpus_rows):
    """`<` and `>=`, not `<=` and `>`. The vote happens on the decision date,
    so that day's record is the outcome, not the framing."""
    pre = {c.document_id for c in retrieval.chunks(QID, retrieval.Scope.PRE_VOTE)}
    post = {c.document_id for c in retrieval.chunks(QID, retrieval.Scope.POST_VOTE)}
    assert 2 not in pre
    assert 2 in post


def test_post_vote_does_not_filter_on_role(corpus_rows):
    """Asymmetric on purpose: after voting there is nothing left to protect."""
    roles = {c.role for c in retrieval.chunks(QID, retrieval.Scope.POST_VOTE)}
    assert roles == {"vote_record", "outcome"}


def test_scope_is_per_question(corpus_rows):
    text = retrieval.scope_text(QID, retrieval.Scope.PRE_VOTE)
    assert "Clean air" not in text


def test_chunks_are_ordered_by_document_then_position(corpus_rows):
    got = retrieval.chunks(QID, retrieval.Scope.POST_VOTE)
    assert [(c.document_id, c.char_start) for c in got] == sorted(
        (c.document_id, c.char_start) for c in got
    )


def test_limit_is_applied(corpus_rows):
    assert len(retrieval.chunks(QID, retrieval.Scope.POST_VOTE, limit=1)) == 1


# --- failing closed -----------------------------------------------------------


def test_an_unknown_question_raises_rather_than_returning_nothing(corpus_rows):
    """ "No such question" and "no material yet" are the same empty list, and
    only one of them is safe to carry on from."""
    with pytest.raises(retrieval.UnknownQuestion):
        retrieval.chunks("does-not-exist", retrieval.Scope.PRE_VOTE)


@pytest.mark.parametrize(
    "call",
    [
        lambda: retrieval.chunks(QID, "pre_vote"),
        lambda: retrieval.scope_text(QID, "pre_vote"),
        lambda: retrieval.document_text(1, question_id=QID, scope="pre_vote"),
        lambda: retrieval.nearest(
            QID, "pre_vote", [0.0] * 768, model="gemini-embedding-001"
        ),
    ],
    ids=["chunks", "scope_text", "document_text", "nearest"],
)
def test_no_entry_point_accepts_a_scope_as_a_string(call, corpus_rows):
    """Every public function routes through the same two branches.

    A string that happened to be handled by one of them would be a second
    implementation of the boundary, which is the thing this module exists to
    prevent.
    """
    with pytest.raises(TypeError, match="must be a Scope"):
        call()


# --- documents ----------------------------------------------------------------


def test_document_text_is_scoped(corpus_rows):
    assert retrieval.document_text(3, question_id=QID, scope=retrieval.Scope.POST_VOTE)
    assert (
        retrieval.document_text(3, question_id=QID, scope=retrieval.Scope.PRE_VOTE)
        is None
    )


def test_a_pre_vote_document_still_contains_its_vote_record_spans(corpus_rows):
    """Not a bug — the reason `scope_text` builds from chunks and not documents.

    `role` lives on the chunk, so a document that straddles it is visible whole
    to `document_text`. That is correct for checking a citation against its
    source and wrong for assembling material, and the two have separate
    functions so the distinction cannot be made by accident.
    """
    text = retrieval.document_text(1, question_id=QID, scope=retrieval.Scope.PRE_VOTE)
    assert "128 to 291" in text
    assert "128 to 291" not in retrieval.scope_text(QID, retrieval.Scope.PRE_VOTE)


# --- verify -------------------------------------------------------------------


def test_verify_resolves_the_document_instead_of_trusting_the_caller(corpus_rows):
    claim = grounding.Claim(
        text="The amendment failed.",
        document_id=1,
        char_span=COUNTS,
        value=128,
    )
    assert retrieval.verify(claim, question_id=QID, scope=retrieval.Scope.PRE_VOTE)


def test_verify_fails_a_claim_citing_a_document_outside_the_scope(corpus_rows):
    """Before this, a pre-vote claim citing post-vote material was a rule #2
    pass: `grounding.verify` was handed the text and had no way to know where
    it came from."""
    claim = grounding.Claim(
        text="19 million enrolled.", document_id=3, char_span=(0, 20), value=19_000_000
    )
    assert retrieval.verify(claim, question_id=QID, scope=retrieval.Scope.POST_VOTE)
    verdict = retrieval.verify(claim, question_id=QID, scope=retrieval.Scope.PRE_VOTE)
    assert not verdict
    assert "not in pre_vote scope" in verdict.reason


def test_verify_on_a_nonexistent_document_does_not_raise(corpus_rows):
    verdict = retrieval.verify(
        grounding.Claim(text="x", document_id=9999, char_span=(0, 1)),
        question_id=QID,
        scope=retrieval.Scope.PRE_VOTE,
    )
    assert not verdict


# --- nearest ------------------------------------------------------------------
#
# The vector search itself needs pgvector and so is exercised against the real
# database, not here. What is worth pinning in a container-free test is the
# guard that stops a wrong-sized vector reaching it.


def test_nearest_rejects_a_vector_of_the_wrong_size(corpus_rows):
    """A truncated or full-length 3072 vector is a silent ranking bug on
    Postgres, not an error, so it is caught before the query is built."""
    with pytest.raises(ValueError, match="768-dimension"):
        retrieval.nearest(
            QID, retrieval.Scope.PRE_VOTE, [0.0] * 3072, model="gemini-embedding-001"
        )


# --- the citation unit is smaller than the retrieval unit ---------------------


def test_a_whole_chunk_is_too_large_to_be_a_citation():
    """Measured, and deliberate: a citation is evidence, a chunk is a lookup.

    `ingest.CHUNK_CHARS` is 1000 and `grounding.MAX_SPAN_CHARS` is 600, so a
    generator that cites the chunk it was handed fails the haystack check —
    measured on the Medicare corpus, 192 of 218 chunks. The numbers are right
    and the relationship is intentional; what it means is that the prompt has
    to ask for a span *within* the chunk. This test exists so that changing
    either constant surfaces that requirement rather than silently making
    whole-chunk citation legal.
    """
    assert grounding.MAX_SPAN_CHARS < ingest.CHUNK_CHARS


def test_adding_post_vote_material_never_changes_pre_vote_scope(corpus_rows, sqlite_db):
    """Widening the outcome window cannot leak, and this is why.

    The pre-vote predicate is a conjunction, so a document dated on or after the
    decision fails its first clause whatever its `role` says. That means the
    outcome corpus can be extended by decades — which it needs to be, since most
    questions' outcome material currently spans days — without any of it
    becoming reachable before a player votes.

    Pinned because the reasoning is easy to state and easy to lose. The obvious
    worry is "more material, more chance of a leak", and the answer is that the
    date clause makes the amount irrelevant.
    """
    before = [c.id for c in retrieval.chunks(QID, retrieval.Scope.PRE_VOTE)]

    with sqlite_db.begin() as conn:
        # Deliberately hostile: post-decision documents mislabelled `framing`,
        # containing the exact margin the reveal cites.
        for n, day in enumerate((date(1965, 4, 9), date(1975, 1, 1), AFTER), start=90):
            _document(
                conn,
                doc_id=n,
                published_date=day,
                text="Medicare passed 307 to 116 and now covers 67 million.",
            )
            _chunk(
                conn,
                doc_id=n,
                ordinal=0,
                role="framing",
                published_date=day,
                start=0,
                end=52,
                text="Medicare passed 307 to 116 and now covers 67 million.",
            )

    after = [c.id for c in retrieval.chunks(QID, retrieval.Scope.PRE_VOTE)]
    assert after == before, "post-decision material reached pre-vote scope"

    text = retrieval.scope_text(QID, retrieval.Scope.PRE_VOTE)
    assert "307" not in text and "67 million" not in text
    # And it is genuinely there, on the other side of the boundary.
    assert len(retrieval.chunks(QID, retrieval.Scope.POST_VOTE)) >= 3
