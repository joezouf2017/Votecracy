"""Source routing — solitary unit tests, same layer as test_pure.py.

No HTTP, no Redis, no Postgres, no network. `sources` is pure by construction
and this is the level its failures live at.

The centrepiece is the full question x need matrix below. It is written out
rather than computed because it *is* the specification: add a source, widen a
coverage window, and the matrix says precisely which questions changed. A test
that recomputed the answer from the whitelist would agree with any bug.
"""

import contextlib
from datetime import date

import pytest

import content
import db
import sources

# Every question x need cell. A tuple of source keys, or RAISE.
#
# The eight RAISE cells are the point of Step 3. They are not gaps in the code:
# Voteview is a congressional dataset and holds no state ratification votes,
# nothing in the whitelist covers the UK, and there is no FCC source at all.
# Step 3's job is to make that fail loudly instead of returning a plausible
# wrong answer.
RAISE = "RAISE"

_CRECB = "govinfo:crecb"
_CREC = "govinfo:crec"
_STATUTE = "govinfo:statute"
_LOC = "loc:chronicling-america"
_VOTEVIEW = "voteview"

MATRIX = {
    ("us-medicare-1965", "framing"): (_CRECB, _STATUTE, _LOC),
    ("us-medicare-1965", "vote_record"): (_VOTEVIEW, _CRECB),
    ("us-medicare-1965", "outcome"): (_CRECB, _CREC),
    ("us-prohibition-1919", "framing"): (_CRECB, _STATUTE, _LOC),
    # Voteview has the congressional votes on S.J.Res. 17 but not the state
    # ratification the reveal cites, and neither does the Congressional Record.
    ("us-prohibition-1919", "vote_record"): RAISE,
    ("us-prohibition-1919", "outcome"): (_CRECB, _CREC, _LOC),
    ("us-interstate-highway-1956", "framing"): (_CRECB, _STATUTE, _LOC),
    ("us-interstate-highway-1956", "vote_record"): (_VOTEVIEW, _CRECB),
    ("us-interstate-highway-1956", "outcome"): (_CRECB, _CREC, _LOC),
    # Chronicling America stops in 1963, so it can serve the 1960-63 slice of
    # this question's framing window but none of its outcome.
    ("us-clean-air-act-1970", "framing"): (_CRECB, _STATUTE, _LOC),
    ("us-clean-air-act-1970", "vote_record"): (_VOTEVIEW, _CRECB),
    ("us-clean-air-act-1970", "outcome"): (_CRECB, _CREC),
    # An agency rule. Nothing in the whitelist covers the FCC, and the one
    # source not restricted by vote_type ends 52 years before the decision.
    ("us-net-neutrality-2015", "framing"): RAISE,
    ("us-net-neutrality-2015", "vote_record"): RAISE,
    ("us-net-neutrality-2015", "outcome"): RAISE,
    ("us-affordable-care-act-2010", "framing"): (_CRECB, _CREC, _STATUTE),
    ("us-affordable-care-act-2010", "vote_record"): (_VOTEVIEW, _CRECB, _CREC),
    ("us-affordable-care-act-2010", "outcome"): (_CRECB, _CREC),
    ("us-income-tax-1913", "framing"): (_CRECB, _STATUTE, _LOC),
    ("us-income-tax-1913", "vote_record"): RAISE,
    ("us-income-tax-1913", "outcome"): (_CRECB, _CREC, _LOC),
    # No UK source of any kind yet — Hansard isn't wired up.
    ("uk-national-health-service-1946", "framing"): RAISE,
    ("uk-national-health-service-1946", "vote_record"): RAISE,
    ("uk-national-health-service-1946", "outcome"): RAISE,
}


@pytest.mark.parametrize(
    ("cell", "expected"), sorted(MATRIX.items()), ids=lambda v: str(v)
)
def test_source_routing_matrix(cell, expected):
    question_id, need = cell
    question = content.get_question(question_id)
    if expected is RAISE:
        with pytest.raises(sources.NoSourceAvailable):
            sources.select_sources(question, need)
    else:
        assert tuple(s.key for s in sources.select_sources(question, need)) == expected


def test_matrix_covers_every_question_and_need():
    """Otherwise a new question silently has no routing spec at all."""
    expected = {(q["id"], n) for q in content.all_questions() for n in db.CHUNK_ROLES}
    assert set(MATRIX) == expected


# --- the never-empty guarantee ------------------------------------------------


@pytest.mark.parametrize("question", content.all_questions(), ids=lambda q: q["id"])
@pytest.mark.parametrize("need", db.CHUNK_ROLES)
def test_select_sources_is_never_empty(question, need):
    """Two outcomes only: a non-empty tuple, or NoSourceAvailable.

    An empty tuple is the dangerous third one — a caller reads it as "nothing
    matched this run" and carries on building a question with no grounding.
    """
    with contextlib.suppress(sources.NoSourceAvailable):
        assert sources.select_sources(question, need)


def test_no_source_available_says_why_each_candidate_lost():
    """ "No UK source at all" and "no source for ratification votes" are
    different gaps. An exception that only says "none" hides the difference."""
    question = content.get_question("uk-national-health-service-1946")
    with pytest.raises(sources.NoSourceAvailable) as exc:
        sources.select_sources(question, "framing")
    assert set(exc.value.reasons) == {s.key for s in sources.WHITELIST}
    assert all("jurisdiction UK" in why for why in exc.value.reasons.values())
    assert "uk-national-health-service-1946" in str(exc.value)


def test_no_source_available_distinguishes_a_vote_type_gap_from_a_jurisdiction_one():
    question = content.get_question("us-prohibition-1919")
    with pytest.raises(sources.NoSourceAvailable) as exc:
        sources.select_sources(question, "vote_record")
    assert "constitutional_ratification" in exc.value.reasons["voteview"]
    assert "jurisdiction" not in exc.value.reasons["voteview"]


# --- the need windows ---------------------------------------------------------
#
# One day separates framing from vote_record, and that day is the decision.


def test_framing_window_stops_strictly_before_the_decision():
    assert sources.need_window("framing", date(1965, 4, 8))[1] == date(1965, 4, 8)


def test_vote_record_window_includes_the_decision_day():
    """The vote happens *on* the decision date. A window that stopped short of
    it would make the deciding roll call unretrievable."""
    assert sources.need_window("vote_record", date(1965, 4, 8))[1] == date(1965, 4, 9)


def test_outcome_window_starts_at_the_decision_and_never_ends():
    assert sources.need_window("outcome", date(1965, 4, 8)) == (date(1965, 4, 8), None)


def test_lookback_survives_a_leap_day():
    """29 February minus ten years is not a date. `date.replace` raises rather
    than rounding, so an unguarded lookback would crash on one question in
    1461 — the kind of bug that lands in production on a Tuesday."""
    assert sources.need_window("framing", date(2016, 2, 29))[0] == date(2006, 2, 28)


def test_unknown_need_is_rejected_by_both_entry_points():
    for call in (
        lambda: sources.need_window("spoiler", date(1965, 4, 8)),
        lambda: sources.select_sources(
            content.get_question("us-medicare-1965"), "spoiler"
        ),
    ):
        with pytest.raises(ValueError, match="unknown need"):
            call()


# --- coverage overlap ---------------------------------------------------------
#
# The single most off-by-one-able piece of logic in the module.

_ENDS_1963 = sources.Source(
    key="test:ends-1963",
    serves=sources._serves({"framing"}, {"congressional_passage"}),
    coverage_start=date(1900, 1, 1),
    coverage_end=date(1963, 12, 31),
    jurisdictions=None,
)


@pytest.mark.parametrize(
    ("window", "overlaps"),
    [
        ((date(1963, 12, 31), date(1970, 1, 1)), True),  # window opens on the last day
        ((date(1964, 1, 1), date(1970, 1, 1)), False),  # opens the day after
        ((date(1890, 1, 1), date(1900, 1, 1)), False),  # closes as coverage opens
        ((date(1890, 1, 1), date(1900, 1, 2)), True),  # closes one day later
        ((date(1970, 1, 1), None), False),  # open-ended, entirely after
        ((date(1950, 1, 1), None), True),  # open-ended, overlapping
    ],
)
def test_coverage_overlap_at_the_boundaries(window, overlaps):
    assert sources._overlaps(_ENDS_1963, window) is overlaps


def test_a_source_is_not_eligible_just_because_it_started_early_enough():
    """Chronicling America starts in 1777, so `coverage_start < decision_date`
    holds for every question ever. It ends in 1963, which is what actually
    decides whether it can supply anything."""
    loc = next(s for s in sources.WHITELIST if s.key == "loc:chronicling-america")
    window = sources.need_window("framing", date(2015, 2, 26))
    assert loc.coverage_start < date(2015, 2, 26)
    assert sources._overlaps(loc, window) is False


# --- the whitelist's own vocabulary -------------------------------------------


@pytest.mark.parametrize("source", sources.WHITELIST, ids=lambda s: s.key)
def test_whitelist_uses_the_shared_vocabularies(source):
    """`need` and a chunk's `role` have to be the same word list, and every
    vote_type has to be one questions.json can actually contain. Two copies of
    either would drift, and the drift would look like a routing gap."""
    for need, vote_type in source.serves:
        assert need in db.CHUNK_ROLES
        assert vote_type in content.VOTE_TYPES


@pytest.mark.parametrize("source", sources.WHITELIST, ids=lambda s: s.key)
def test_coverage_windows_are_ordered(source):
    assert source.coverage_end is None or source.coverage_start < source.coverage_end


def test_source_keys_are_unique():
    keys = [s.key for s in sources.WHITELIST]
    assert len(keys) == len(set(keys))
