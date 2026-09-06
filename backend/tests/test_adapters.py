"""The five source adapters — the parts that decide dates and identity.

No network. What is worth testing here is not the HTTP (that is `test_fetch`)
but the three things each adapter decides on its own, each of which fails
quietly when wrong:

- **`published_date`**, which places a document on one side of rule #1's
  boundary. Two adapters read it from an exact address, two derive it from a
  span, and the difference is the whole reason `volumes.py` is long.
- **`role`**, which must be *derived* from that date rather than declared, so a
  document cannot be labelled framing by hand.
- **`external_id`**, which is the provenance and the uniqueness key. A wrong one
  either collides or silently duplicates.
"""

from datetime import date

import pytest

from pipeline import govinfo, hansard, newspapers, volumes
from shared import content

# --- published_date: exact where it can be, conservative where it cannot ------


def test_a_bound_volume_is_dated_by_its_last_day_never_its_first():
    """The Clean Air decision volume opens 4 June against a 10 June decision.
    Dating it 4 June would satisfy `published_date < decision_date` for the roll
    call inside it, which is a rule #1 leak; dating it 12 June loses six days of
    debate instead. Losing is the direction to fail in."""
    v = next(x for x in volumes.VOLUMES if x.identifier.endswith("june-4-12-1970_116"))
    assert v.starts == date(1970, 6, 4)
    assert v.published_date == v.ends == date(1970, 6, 12)


def test_a_hansard_sitting_is_dated_by_the_sitting():
    """One debate, one named day — so no conservative rounding is needed and
    nothing is lost to the safe direction."""
    s = hansard.SITTINGS[0]
    assert s.day == date(1944, 3, 16)
    assert "1944/mar/16" in s.path


def test_a_govinfo_granule_reads_its_date_off_the_package_id():
    g = govinfo.Granule("q", "CREC-2009-11-19", "CREC-2009-11-19-pt1-PgS11582", "t")
    assert g.day == date(2009, 11, 19)


def test_a_granule_with_no_date_in_its_package_id_raises():
    """Rather than defaulting. `source_documents.published_date` is NOT NULL
    precisely so an undateable document cannot be filed on either side."""
    with pytest.raises(govinfo.GovInfoError, match="no date in package id"):
        _ = govinfo.Granule("q", "NOT-A-PACKAGE", "g", "t").day


# --- role is derived, never declared ------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(1946, 5, 1), "framing"),  # day before
        (date(1946, 5, 2), "vote_record"),  # the decision itself
        (date(1946, 5, 3), "vote_record"),
    ],
)
def test_hansard_role_turns_over_on_the_decision_date(day, expected):
    s = hansard.Sitting("q", "commons", day, "slug", "t")
    assert hansard.role_for(s, date(1946, 5, 2)) == expected


def test_every_volume_labelled_framing_really_does_end_before_its_decision():
    """`role` is a property, not a field, so this cannot drift — but the table
    it reads from is hand-written and can."""
    for v in volumes.VOLUMES:
        decision = content.decision_date(v.question_id)
        if v.role == "framing":
            assert v.ends < decision, f"{v.identifier} is framing but ends {v.ends}"
        else:
            assert v.ends >= decision


def test_every_hansard_sitting_agrees_with_its_questions_decision():
    for s in hansard.SITTINGS:
        decision = content.decision_date(s.question_id)
        assert (hansard.role_for(s, decision) == "framing") == (s.day < decision)


# --- external_id is the source's own identifier -------------------------------


def test_the_1909_volume_does_not_double_its_prefix():
    """Its archive.org id already begins with the short form. Stripping only the
    long form produced `sim_congressional-record_sim_congressional-record_...` —
    unique, so nothing failed, and wrong in the column that is the provenance."""
    v = next(x for x in volumes.VOLUMES if "1909" in x.identifier)
    assert v.external_id_prefix == "sim_congressional-record_june-17-july-13-1909_44"
    assert v.external_id_prefix.count("sim_congressional-record") == 1


def test_hansard_external_id_round_trips_to_a_url():
    s = hansard.SITTINGS[2]
    assert s.url.endswith(s.external_id)


def test_govinfo_external_id_names_both_package_and_granule():
    g = govinfo.Granule("q", "FR-2015-04-13", "2015-07841", "t")
    assert g.external_id == "FR-2015-04-13/2015-07841"


# --- newspapers: the two ways a result is refused -----------------------------


def test_a_newspaper_result_with_no_usable_date_is_dropped():
    assert newspapers._page_from({"description": ["text"], "date": "n.d."}, "q") is None


def test_a_newspaper_result_with_no_ocr_is_dropped():
    """A catalogue entry is not a source."""
    assert newspapers._page_from({"date": "1962-08-02", "description": []}, "q") is None


def test_a_usable_newspaper_result_keeps_its_image_url():
    """The IIIF URL is the whole reason this source is worth more than its text
    — a headline crop costs no derivative storage."""
    page = newspapers._page_from(
        {
            "date": "1962-08-02",
            "description": ["MEDICARE BILL DEFEATED"],
            "id": "https://www.loc.gov/item/x/",
            "title": "Arizona sun",
            "image_url": [
                "https://tile.loc.gov/image-services/iiif/x/full/pct:50/0/default.jpg"
            ],
        },
        "q",
    )
    assert page.day == date(1962, 8, 2)
    assert page.image_url.startswith("https://tile.loc.gov/image-services/iiif/")


def test_a_page_past_the_public_domain_cutoff_is_refused(sqlite_db):
    """Chronicling America stops at 1963 and that is a rights boundary, not a
    coverage one."""
    page = newspapers.Page("q", "id", "t", date(1964, 1, 1), "text", None)
    with pytest.raises(newspapers.NewspaperError, match="public-domain cutoff"):
        newspapers.ingest_page(page, date(1970, 6, 10))
