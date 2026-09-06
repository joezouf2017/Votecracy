"""Candidate persistence — one invariant, tested from several directions.

`voteview.candidates()` rebuilds every candidate from the bulk corpus in 0.6
seconds, so nothing here is protecting the candidates. It is protecting the
record that a human looked at one, which exists nowhere else and cannot be
recomputed.

The failure being prevented is silent: a re-run that reset a rejection to
`pending` produces no error, and since the corpus has not changed there is
nothing to make anyone suspicious. So the tests are about what an update does
*not* touch, which is harder to assert than what it does.
"""

from datetime import date

import pytest
from sqlalchemy import func, select

from pipeline import candidates as cands
from pipeline.voteview import Candidate, Signals
from shared.db import corpus


def _candidate(bill="HR6675", congress=89, **kw):
    base = dict(
        bill_number=bill,
        congress=congress,
        chamber="House",
        vote_date=date(1965, 4, 8),
        yea=313,
        nay=115,
        vote_type="congressional_passage",
        subject="medical",
        description="TO PASS H.R. 6675.",
        signals=Signals(
            attention=400,
            attention_percentile=0.97,
            closeness=0.36,
            coalition_break=0.5,
            turnout=428,
        ),
        gaps=(),
    )
    return Candidate(**(base | kw))


def _row(engine, bill="HR6675", congress=89):
    with engine.connect() as conn:
        return (
            conn.execute(
                select(corpus.candidates).where(
                    corpus.candidates.c.bill_number == bill,
                    corpus.candidates.c.congress == congress,
                )
            )
            .one()
            ._mapping
        )


# --- the invariant ------------------------------------------------------------


def test_a_rerun_does_not_reset_review_state(sqlite_db):
    """The single reason this table exists.

    Reject a candidate, re-read the corpus, and the rejection must survive. If
    it does not, a human's work is destroyed by an operation nobody thinks of
    as destructive.
    """
    cands.upsert([_candidate()])
    cands.review(89, "HR6675", "rejected", note="margin gives it away")

    cands.upsert([_candidate()])

    row = _row(sqlite_db)
    assert row["status"] == "rejected"
    assert row["review_note"] == "margin gives it away"
    assert row["reviewed_at"] is not None


def test_a_rerun_does_refresh_what_the_corpus_owns(sqlite_db):
    """The other half. Leaving review state alone must not mean freezing the
    row — a corrected description or a changed ranking has to land."""
    cands.upsert([_candidate()])
    cands.review(89, "HR6675", "approved")

    changed = _candidate(
        description="TO PASS H.R. 6675, AS AMENDED.",
        signals=Signals(
            attention=400,
            attention_percentile=0.42,
            closeness=0.36,
            coalition_break=0.5,
            turnout=428,
        ),
    )
    cands.upsert([changed])

    row = _row(sqlite_db)
    assert row["description"] == "TO PASS H.R. 6675, AS AMENDED."
    assert row["attention_percentile"] == pytest.approx(0.42)
    assert row["status"] == "approved"


def test_the_refresh_list_and_the_review_list_do_not_overlap(sqlite_db):
    """Structural, so that adding a column forces the decision rather than
    defaulting to whichever list it was pasted near."""
    assert not set(cands._REFRESHED) & set(cands._REVIEW_ONLY)
    stored = {c.name for c in corpus.candidates.columns}
    bookkeeping = {"id", "congress", "bill_number", "first_seen_at", "refreshed_at"}
    assert set(cands._REFRESHED) | set(cands._REVIEW_ONLY) | bookkeeping == stored


# --- idempotence --------------------------------------------------------------


def test_running_twice_inserts_once(sqlite_db):
    first = cands.upsert([_candidate(), _candidate("HR17255", 91)])
    second = cands.upsert([_candidate(), _candidate("HR17255", 91)])
    assert (first.inserted, first.refreshed) == (2, 0)
    assert (second.inserted, second.refreshed) == (0, 2)
    with sqlite_db.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(corpus.candidates)
            ).scalar_one()
            == 2
        )


def test_first_seen_at_survives_a_refresh(sqlite_db):
    """When a candidate was first surfaced is provenance, not a derived value —
    a re-run must not make everything look newly discovered."""
    cands.upsert([_candidate()])
    first_seen = _row(sqlite_db)["first_seen_at"]
    cands.upsert([_candidate(description="different")])
    assert _row(sqlite_db)["first_seen_at"] == first_seen


def test_a_new_candidate_starts_pending(sqlite_db):
    cands.upsert([_candidate()])
    assert _row(sqlite_db)["status"] == "pending"


# --- the review vocabulary ----------------------------------------------------


@pytest.mark.parametrize("bad", ["aproved", "APPROVED", "", "deleted"])
def test_an_unknown_status_is_refused(sqlite_db, bad):
    """A typo'd status is worse than an error: the candidate leaves the pending
    queue without joining the approved one, so it simply vanishes."""
    cands.upsert([_candidate()])
    with pytest.raises(cands.UnknownStatus):
        cands.review(89, "HR6675", bad)
    with pytest.raises(cands.UnknownStatus):
        cands.queue(status=bad)


def test_reviewing_a_missing_candidate_reports_zero_rather_than_passing(sqlite_db):
    assert cands.review(1, "HR9999", "approved") == 0


# --- the review queue ---------------------------------------------------------


def test_the_queue_is_ranked_and_excludes_what_has_been_ruled_on(sqlite_db):
    high = _candidate("HR1", 90, signals=Signals(400, 0.99, 0.3, 0.5, 428))
    mid = _candidate("HR2", 90, signals=Signals(300, 0.60, 0.3, 0.5, 428))
    low = _candidate("HR3", 90, signals=Signals(200, 0.10, 0.3, 0.5, 428))
    cands.upsert([mid, low, high])

    assert [c["bill_number"] for c in cands.queue()] == ["HR1", "HR2", "HR3"]

    cands.review(90, "HR1", "approved")
    assert [c["bill_number"] for c in cands.queue()] == ["HR2", "HR3"]
    assert [c["bill_number"] for c in cands.queue(status="approved")] == ["HR1"]


def test_the_queue_respects_its_limit(sqlite_db):
    cands.upsert([_candidate(f"HR{i}", 90) for i in range(5)])
    assert len(cands.queue(limit=2)) == 2


# --- the margin is stored, and kept away from the generator -------------------


def test_the_margin_is_stored_because_review_needs_it(sqlite_db):
    cands.upsert([_candidate()])
    row = _row(sqlite_db)
    assert (row["yea"], row["nay"]) == (313, 115)


def test_the_prompt_projection_still_drops_the_margin():
    """The candidate row carries the outcome and the generator must not see it.
    Same shape as `content.public_view`, different consumer — pinned here
    because the table is now where that margin lives."""
    from pipeline import voteview

    projected = voteview.for_prompt_generation(_candidate())
    assert "yea" not in projected and "nay" not in projected
    assert "313" not in str(projected) and "115" not in str(projected)


def test_the_queue_orders_exactly_as_rank_does(sqlite_db):
    """`attention_percentile` saturates -- 1.000 is a large tie in the real
    corpus -- so ordering on it alone leaves the top of the queue in whatever
    sequence the planner returns. `voteview.rank` breaks ties on date then bill
    number for precisely this reason, and the queue has to spell the same order
    in SQL or a reviewer cannot work through it."""
    from pipeline import voteview

    tied = [
        _candidate(
            "HR30",
            90,
            vote_date=date(1970, 3, 1),
            signals=Signals(9, 1.0, 0.3, 0.5, 400),
        ),
        _candidate(
            "HR10",
            90,
            vote_date=date(1970, 1, 1),
            signals=Signals(9, 1.0, 0.3, 0.5, 400),
        ),
        _candidate(
            "HR20",
            90,
            vote_date=date(1970, 1, 1),
            signals=Signals(9, 1.0, 0.3, 0.5, 400),
        ),
        _candidate(
            "HR40",
            90,
            vote_date=date(1969, 1, 1),
            signals=Signals(9, 0.5, 0.3, 0.5, 400),
        ),
    ]
    cands.upsert(tied)
    assert [c["bill_number"] for c in cands.queue()] == [
        c.bill_number for c in voteview.rank(tied)
    ]
