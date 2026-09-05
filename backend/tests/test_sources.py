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

from pipeline import sources
from shared import content
from shared.db import corpus

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
    expected = {
        (q["id"], n) for q in content.all_questions() for n in corpus.CHUNK_ROLES
    }
    assert set(MATRIX) == expected


# --- the never-empty guarantee ------------------------------------------------


@pytest.mark.parametrize("question", content.all_questions(), ids=lambda q: q["id"])
@pytest.mark.parametrize("need", corpus.CHUNK_ROLES)
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
        assert need in corpus.CHUNK_ROLES
        assert vote_type in content.VOTE_TYPES


@pytest.mark.parametrize("source", sources.WHITELIST, ids=lambda s: s.key)
def test_coverage_windows_are_ordered(source):
    assert source.coverage_end is None or source.coverage_start < source.coverage_end


def test_source_keys_are_unique():
    keys = [s.key for s in sources.WHITELIST]
    assert len(keys) == len(set(keys))


# --- normalize_bill_number ----------------------------------------------------
#
# Spike finding 5. Getting this wrong returns zero rows, which is
# indistinguishable from the measure not being in the dataset.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("S.J.Res. 17", "SJR17"),
        ("SJRES17", "SJR17"),
        ("sjres 17", "SJR17"),
        ("SJR17", "SJR17"),  # idempotent
        ("H.J.Res. 1", "HJR1"),
        ("HJRES1", "HJR1"),
        ("H.R. 6675", "HR6675"),
        ("hr 6675", "HR6675"),
        ("HR6675", "HR6675"),
        ("  H.R.  10660  ", "HR10660"),
        ("S. 4358", "S4358"),
        ("H.Res. 5", "HRES5"),  # a House resolution is not a joint resolution
    ],
)
def test_normalize_bill_number(raw, expected):
    assert sources.normalize_bill_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_normalize_bill_number_rejects_nothing(raw):
    with pytest.raises(ValueError, match="empty"):
        sources.normalize_bill_number(raw)


def test_normalization_is_idempotent_over_the_real_dataset():
    for q in content.all_questions():
        raw = q["retrieval"]["bill_number"]
        if raw:
            once = sources.normalize_bill_number(raw)
            assert sources.normalize_bill_number(once) == once


# --- formulate_query ----------------------------------------------------------


def _framing_queries():
    """Every (question, source) pair that framing retrieval would actually hit."""
    for q in content.all_questions():
        with contextlib.suppress(sources.NoSourceAvailable):
            for source in sources.select_sources(q, "framing"):
                yield q, source


@pytest.mark.parametrize(
    ("question", "source"),
    list(_framing_queries()),
    ids=lambda v: v["id"] if isinstance(v, dict) else v.key,
)
def test_framing_queries_never_ask_for_material_from_the_decision_onwards(
    question, source
):
    """Rule #1, enforced in the request rather than on the way back.

    A framing fetch that asked for the decision date would pull the result into
    the cache even if the chunk filter later hid it — and the fetch cache is
    where a mistake persists."""
    query = sources.formulate_query(question, source, "framing")
    ceiling = query.get("published_to")
    if ceiling is None:  # loc.gov takes a `dates=FROM/TO` range instead
        ceiling = date.fromisoformat(query["dates"].split("/")[1])
    assert ceiling < date.fromisoformat(question["decision_date"])


def test_query_dates_are_clamped_to_what_the_source_holds():
    """Chronicling America ends in 1963. A Clean Air Act framing window runs to
    1970, so an unclamped query would ask for seven years the source cannot
    have and read the empty result as silence on the subject."""
    question = content.get_question("us-clean-air-act-1970")
    loc = next(s for s in sources.WHITELIST if s.key == "loc:chronicling-america")
    query = sources.formulate_query(question, loc, "framing")
    assert query["dates"] == "1960-06-10/1963-12-31"


def test_govinfo_query_carries_the_collection_not_the_site():
    question = content.get_question("us-medicare-1965")
    crecb = next(s for s in sources.WHITELIST if s.key == "govinfo:crecb")
    query = sources.formulate_query(question, crecb, "framing")
    assert query["collection"] == "CRECB"
    assert query["published_from"] == date(1955, 4, 8)
    assert query["published_to"] == date(1965, 4, 7)  # the day before the decision
    assert '"Medicare"' in query["query"]


def test_voteview_query_is_keyed_by_measure_and_normalised():
    question = content.get_question("us-medicare-1965")
    voteview = next(s for s in sources.WHITELIST if s.key == "voteview")
    assert sources.formulate_query(question, voteview, "vote_record") == {
        "congress": 89,
        "bill_number": "HR6675",
    }


def test_voteview_query_refuses_a_question_with_no_bill_number():
    """Better than silently querying for nothing, which returns zero roll calls
    and looks exactly like a measure Voteview does not cover."""
    question = content.get_question("uk-national-health-service-1946")
    voteview = next(s for s in sources.WHITELIST if s.key == "voteview")
    with pytest.raises(ValueError, match="no bill_number/congress"):
        sources.formulate_query(question, voteview, "vote_record")


def test_outcome_queries_start_at_the_decision_and_are_open_ended():
    question = content.get_question("us-medicare-1965")
    crec = next(s for s in sources.WHITELIST if s.key == "govinfo:crec")
    query = sources.formulate_query(question, crec, "outcome")
    assert query["published_from"] == date(1994, 1, 1)  # clamped to coverage
    assert query["published_to"] is None
