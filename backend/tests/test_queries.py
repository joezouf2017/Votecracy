"""Addressing a source — the other half of what `test_sources` covers.

`test_sources` asks which source can serve a need; this asks what request to
send it. Same solitary layer: no HTTP, no network, no database.
"""

import contextlib
from datetime import date

import pytest

from pipeline import queries, sources
from shared import content

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
    assert queries.normalize_bill_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_normalize_bill_number_rejects_nothing(raw):
    with pytest.raises(ValueError, match="empty"):
        queries.normalize_bill_number(raw)


def test_normalization_is_idempotent_over_the_real_dataset():
    for q in content.all_questions():
        raw = q["retrieval"]["bill_number"]
        if raw:
            once = queries.normalize_bill_number(raw)
            assert queries.normalize_bill_number(once) == once


# --- formulate_query ----------------------------------------------------------


def _framing_queries():
    """Every (question, source) pair that framing retrieval would actually hit."""
    for q in content.all_questions():
        with contextlib.suppress(sources.NoSourceAvailable):
            for source in sources.select_sources(q, "framing"):
                yield q, source


def _dates_in(value):
    """Every date a query carries, whatever key or format it hides under.

    Sources spell their bounds differently — `published_to`, `dates=FROM/TO`,
    `to` — and will keep doing so. Walking the structure means a source added
    later is covered by the rule below without anyone remembering to extend it.
    """
    if isinstance(value, date):
        yield value
    elif isinstance(value, str):
        for part in value.split("/"):
            with contextlib.suppress(ValueError):
                yield date.fromisoformat(part.strip())
    elif isinstance(value, dict):
        for v in value.values():
            yield from _dates_in(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _dates_in(v)


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
    where a mistake persists.

    Every date anywhere in the query is checked, rather than one key per source
    shape. The earlier version knew about `published_to` and loc.gov's
    `dates=FROM/TO` and broke the moment Hansard arrived with a third spelling —
    which is the point: a rule that has to be taught each new source is a rule
    that a new source can be added without.
    """
    query = queries.formulate_query(question, source, "framing")
    decision = date.fromisoformat(question["decision_date"])

    dates = list(_dates_in(query))
    assert dates, f"{source.key} query carries no date bound at all: {query}"
    for found in dates:
        assert found < decision, f"{source.key} asks for {found}, on or past {decision}"


def test_query_dates_are_clamped_to_what_the_source_holds():
    """Chronicling America ends in 1963. A Clean Air Act framing window runs to
    1970, so an unclamped query would ask for seven years the source cannot
    have and read the empty result as silence on the subject."""
    question = content.get_question("us-clean-air-act-1970")
    loc = next(s for s in sources.WHITELIST if s.key == "loc:chronicling-america")
    query = queries.formulate_query(question, loc, "framing")
    assert query["dates"] == "1960-06-10/1963-12-31"


def test_govinfo_query_carries_the_collection_not_the_site():
    question = content.get_question("us-medicare-1965")
    crecb = next(s for s in sources.WHITELIST if s.key == "govinfo:crecb")
    query = queries.formulate_query(question, crecb, "framing")
    assert query["collection"] == "CRECB"
    assert query["published_from"] == date(1955, 4, 8)
    assert query["published_to"] == date(1965, 4, 7)  # the day before the decision
    assert '"Medicare"' in query["query"]


def test_voteview_query_is_keyed_by_measure_and_normalised():
    question = content.get_question("us-medicare-1965")
    voteview = next(s for s in sources.WHITELIST if s.key == "voteview")
    assert queries.formulate_query(question, voteview, "vote_record") == {
        "congress": 89,
        "bill_number": "HR6675",
    }


def test_voteview_query_refuses_a_question_with_no_bill_number():
    """Better than silently querying for nothing, which returns zero roll calls
    and looks exactly like a measure Voteview does not cover."""
    question = content.get_question("uk-national-health-service-1946")
    voteview = next(s for s in sources.WHITELIST if s.key == "voteview")
    with pytest.raises(ValueError, match="no bill_number/congress"):
        queries.formulate_query(question, voteview, "vote_record")


def test_outcome_queries_start_at_the_decision_and_are_open_ended():
    question = content.get_question("us-medicare-1965")
    crec = next(s for s in sources.WHITELIST if s.key == "govinfo:crec")
    query = queries.formulate_query(question, crec, "outcome")
    assert query["published_from"] == date(1994, 1, 1)  # clamped to coverage
    assert query["published_to"] is None
