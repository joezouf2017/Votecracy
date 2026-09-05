"""Rule #2's enforcement, tested against the ways a generator actually lies.

Solitary unit tests — `grounding` takes text and returns a verdict, with no
model, no network and no database in the path. That is the property under test
as much as any assertion here: the moment this needs an LLM to decide whether
a claim is grounded, it has stopped being a check.
"""

import pytest

from pipeline.grounding import (
    MAX_SPAN_CHARS,
    Claim,
    numbers_in,
    unsupported_numbers,
    verify,
)

# A real sentence from the 1965 Congressional Record volume, lightly trimmed.
DOC = (
    "Mr. Speaker, the committee bill provides hospital insurance for some "
    "19 million Americans over the age of 65, and the cost is estimated at "
    "$3,200,000,000 in the first full year of operation. The gentleman from "
    "Louisiana has raised nineteen separate objections to the financing."
)


def span_of(needle: str) -> tuple[int, int]:
    i = DOC.index(needle)
    return (i, i + len(needle))


# --- numbers_in ---------------------------------------------------------------
#
# "19 million", "19,000,000" and "nineteen million" are one assertion. A check
# that only understood one of them would pass a claim the source contradicts.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("19 million Americans", {19_000_000.0}),
        ("19,000,000 Americans", {19_000_000.0}),
        ("nineteen million Americans", {19_000_000.0}),
        ("$3,200,000,000", {3_200_000_000.0}),
        ("3.2 billion", {3_200_000_000.0}),
        ("covers 67 million people", {67_000_000.0}),
        ("passed 307 to 116", {307.0, 116.0}),
        ("no numbers at all here", set()),
    ],
)
def test_numbers_are_read_however_they_are_written(text, expected):
    assert numbers_in(text) == expected


def test_a_bare_year_is_read_as_a_number():
    """Not a bug to fix here. A generator writing "in 1965" is making a
    checkable claim, and treating it as one is the safer default."""
    assert 1965.0 in numbers_in("enacted in 1965")


# --- verify -------------------------------------------------------------------


def test_a_claim_whose_number_is_in_the_span_passes():
    claim = Claim(
        text="Around 19 million Americans over 65 would be covered.",
        document_id=1,
        char_span=span_of("19 million Americans over the age of 65"),
        value=19_000_000,
    )
    assert verify(claim, DOC)


def test_a_claim_whose_number_is_absent_from_the_span_fails():
    """The case this module exists for: a fabricated figure attached to a real
    citation, which is more dangerous than no citation because it looks
    checked."""
    claim = Claim(
        text="Around 40 million Americans would be covered.",
        document_id=1,
        char_span=span_of("19 million Americans over the age of 65"),
        value=40_000_000,
    )
    v = verify(claim, DOC)
    assert not v
    assert "40000000 does not appear" in v.reason.replace(",", "")


def test_the_span_may_be_written_differently_from_the_claim():
    """The source says "$3,200,000,000"; the reveal would say "$3.2 billion"."""
    claim = Claim(
        text="The first full year was projected to cost $3.2 billion.",
        document_id=1,
        char_span=span_of("$3,200,000,000 in the first full year"),
        value=3_200_000_000,
    )
    assert verify(claim, DOC)


def test_a_word_number_in_the_source_matches_a_digit_claim():
    claim = Claim(
        text="Nineteen objections were raised.",
        document_id=1,
        char_span=span_of("nineteen separate objections"),
        value=19,
    )
    assert verify(claim, DOC)


@pytest.mark.parametrize(
    "span",
    [(-1, 20), (0, len(DOC) + 1), (50, 50), (60, 20)],
    ids=["negative", "past the end", "empty", "reversed"],
)
def test_an_impossible_span_fails(span):
    """A model that invents an offset has invented the citation."""
    claim = Claim("anything", 1, span, value=19_000_000)
    v = verify(claim, DOC)
    assert not v
    assert "not inside document" in v.reason


def test_citing_the_whole_document_fails_even_when_the_number_is_in_it():
    """Otherwise the cheapest way to pass is to cite everything. A citation is
    a sentence or two; a chapter proves nothing about which sentence."""
    long_doc = DOC + " filler." * 200
    claim = Claim("19 million", 1, (0, len(long_doc)), value=19_000_000)
    v = verify(claim, long_doc)
    assert not v
    assert "haystack" in v.reason
    assert 19_000_000.0 in numbers_in(long_doc)  # the number really is in there


def test_a_span_at_exactly_the_limit_is_allowed():
    doc = "x" * (MAX_SPAN_CHARS - 3) + " 42"
    assert verify(Claim("42", 1, (0, len(doc)), value=42), doc)


def test_a_claim_with_no_number_is_not_rejected_but_is_not_evidence_either():
    """A span cannot prove an opinion. "Most economists consider it one of the
    best investments ever made" has nothing to check — several of the existing
    eight reveals lean on that shape, and it is the shape a model produces most
    readily. Catching it is human review's job, not this function's."""
    claim = Claim("Opinions differed sharply.", 1, span_of("Mr. Speaker"), value=None)
    assert verify(claim, DOC)


# --- unsupported_numbers ------------------------------------------------------
#
# Per-claim checks prove the citations that exist are honest. This proves none
# are missing.


def test_a_number_with_no_claim_behind_it_is_reported():
    text = "It covered 19 million people and cost $3.2 billion."
    claims = [Claim("19 million", 1, span_of("19 million"), value=19_000_000)]
    assert unsupported_numbers(text, claims) == {3_200_000_000.0}


def test_prose_whose_every_number_is_cited_reports_nothing():
    text = "It covered 19 million people."
    claims = [Claim(text, 1, span_of("19 million"), value=19_000_000)]
    assert unsupported_numbers(text, claims) == set()


def test_prose_with_no_numbers_reports_nothing():
    assert unsupported_numbers("It was widely debated.", []) == set()
