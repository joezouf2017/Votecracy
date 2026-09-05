"""Candidate generation — solitary unit tests, same layer as test_sources.py.

No network and no 29 MB fixture: `classify` and `candidates` are pure functions
over dict rows, so the rows that matter are written out here. Every one below
is copied from the real corpus, because the bugs this module had were all
bugs about what the data actually looks like, not about the logic on top.
"""

from datetime import date

import pytest

import voteview


def row(**kw):
    base = {
        "congress": "91",
        "chamber": "House",
        "rollnumber": "268",
        "date": "1970-06-10",
        "yea_count": "375",
        "nay_count": "1",
        "bill_number": "HR17255",
        "vote_result": "",
        "vote_desc": "",
        "vote_question": "",
        "dtl_desc": "TO PASS H.R. 17255.",
    }
    return {**base, **kw}


# --- classify -----------------------------------------------------------------


def test_a_passage_motion_is_a_passage_vote():
    assert voteview.classify(row()) == "passage"


def test_the_rule_under_which_the_house_considers_a_bill_is_not_its_passage():
    """The bug that made this module's object test necessary.

    Roll call 267 of 1970 is filed under H.R. 17255 and reads "TO ADOPT H.RES.
    1069, THE RULE UNDER WHICH THE HOUSE CONSIDERS H.R. 17255". Matching the
    verb alone accepted it and reported 336-40 as the Clean Air Act's decisive
    vote instead of the 375-1 two roll calls later — the wrong date and the
    wrong margin, presented as fact. Checking "is H.R. 17255 mentioned" accepts
    it too, because it is. Only requiring the bill to be the motion's *object*
    rejects it.
    """
    assert (
        voteview.classify(
            row(
                rollnumber="267",
                yea_count="336",
                nay_count="40",
                dtl_desc=(
                    "TO ADOPT H.RES. 1069, THE RULE UNDER WHICH THE HOUSE "
                    "CONSIDERS H.R. 17255, THE CLEAN AIR ACT."
                ),
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "dtl_desc",
    [
        "TO RECOMMIT H.R. 17255, WITH INSTRUCTIONS.",
        "TO AMEND H.R. 17255 BY PROVIDING FOR CONGRESSIONAL REVIEW.",
        "TO TABLE THE MOTION TO RECONSIDER H.R. 17255.",
        "TO AGREE TO CONFERENCE REPORT ON H.R. 17255.",
    ],
)
def test_procedural_motions_are_not_passage(dtl_desc):
    assert voteview.classify(row(dtl_desc=dtl_desc)) is None


def test_a_joint_resolution_is_adopted_rather_than_passed():
    """Without the ADOPT verbs the 16th and 18th Amendments look like measures
    that never got a final vote at all."""
    assert (
        voteview.classify(
            row(
                congress="61",
                bill_number="SJR40",
                dtl_desc=(
                    "TO ADOPT S. J. RES. 40, AMENDING THE CONSTITUTION "
                    "OF THE UNITED STATES."
                ),
            )
        )
        == "passage"
    )


def test_the_corpus_spelling_of_its_own_bill_numbers_is_reconciled():
    """Filed as SJR40, described as "S. J. RES. 40". Spike finding 5 again,
    this time inside a single file rather than between two sources."""
    assert voteview._is_object_of(
        "TO ADOPT S. J. RES. 40, AMENDING THE CONSTITUTION.", "TO ADOPT ", "SJR40"
    )


def test_a_shorter_bill_number_does_not_match_the_start_of_a_longer_one():
    assert not voteview._is_object_of("TO PASS H.R. 17255.", "TO PASS ", "HR172")


def test_the_modern_era_is_classified_from_vote_question():
    """dtl_desc is only 27% filled after 1990; vote_question is 100%."""
    assert (
        voteview.classify(
            row(congress="111", dtl_desc="", vote_question="On Passage of the Bill")
        )
        == "passage"
    )


def test_a_row_with_no_bill_number_cannot_be_a_candidate():
    """Nothing addresses the other sources without one."""
    assert voteview.classify(row(bill_number="")) is None


# --- candidates ---------------------------------------------------------------


def test_the_earliest_passage_vote_is_the_decision():
    """Step 2's rule: the boundary is the first point the outcome became
    public. A newspaper printed between the two chambers already reports one."""
    cands = voteview.candidates(
        [
            row(
                chamber="Senate",
                rollnumber="543",
                date="1970-09-22",
                yea_count="73",
                nay_count="0",
            ),
            row(),  # 1970-06-10, the House
        ]
    )
    assert len(cands) == 1
    assert cands[0].decision_date == date(1970, 6, 10)
    assert (cands[0].yea, cands[0].nay) == (375, 1)


def test_a_same_day_tie_is_broken_deterministically():
    """Otherwise which roll call becomes the decision depends on the order rows
    happen to sit in a 113,524-line file."""
    a = row(rollnumber="268", yea_count="375", nay_count="1")
    b = row(rollnumber="269", yea_count="300", nay_count="80")
    assert (
        voteview.candidates([a, b])[0].yea == voteview.candidates([b, a])[0].yea == 375
    )


def test_an_amendment_reports_no_decision_date_and_says_why():
    """Voteview records Congress *proposing* an amendment. The question turns
    on state ratification years later, which a congressional dataset does not
    hold at any date — so returning the congressional date would be a wrong
    answer that looks right."""
    [c] = voteview.candidates(
        [
            row(
                congress="61",
                bill_number="SJR40",
                dtl_desc="TO ADOPT S. J. RES. 40, AMENDING THE CONSTITUTION.",
            )
        ]
    )
    assert c.vote_type == "constitutional_ratification"
    assert c.decision_date is None
    assert c.gaps and "ratification" in c.gaps[0]


def test_ordinary_legislation_carries_its_decision_date_and_no_gaps():
    [c] = voteview.candidates([row()])
    assert c.vote_type == "congressional_passage"
    assert c.decision_date == date(1970, 6, 10)
    assert c.gaps == ()


def test_measures_are_grouped_by_congress_and_bill():
    """The same bill number is reused every congress."""
    cands = voteview.candidates(
        [row(congress="91"), row(congress="92", date="1972-06-10")]
    )
    assert len(cands) == 2


# --- the prompt generator's whitelist -----------------------------------------


def test_the_prompt_generator_is_never_shown_the_margin():
    """Rule #1 inside the pipeline. The spike requires `prompt` to be generated
    from framing material only — a model that has not seen the result cannot
    leak it. Handing the generator a whole candidate would put "375-1" in its
    context, where no player-facing test would ever catch it."""
    [c] = voteview.candidates([row()])
    shown = voteview.for_prompt_generation(c)
    assert "yea" not in shown
    assert "nay" not in shown
    assert 375 not in shown.values()


def test_the_prompt_generator_whitelist_drops_fields_it_has_never_heard_of():
    """A whitelist for the same reason `content.public_view` is one: a
    blacklist leaks the next field someone adds to Candidate."""
    [c] = voteview.candidates([row()])
    assert set(voteview.for_prompt_generation(c)) <= set(voteview._GENERATOR_FIELDS)


def test_the_prompt_generator_still_gets_what_it_needs_to_write_a_prompt():
    [c] = voteview.candidates([row()])
    shown = voteview.for_prompt_generation(c)
    assert shown["bill_number"] == "HR17255"
    assert shown["vote_date"] == date(1970, 6, 10)


# --- subject ------------------------------------------------------------------


def test_subject_reads_the_cq_style_header():
    assert (
        voteview.subject("HR 10660.  HIGHWAY CONSTRUCTION ACT.  AMEND AND SUPPLEMENT")
        == "Highway Construction Act"
    )


def test_subject_is_absent_from_the_verb_first_format():
    assert voteview.subject("TO PASS H.R. 17255.") is None


# --- signals ------------------------------------------------------------------
#
# Deliberately separate numbers rather than one score. Validated against the
# hand-written questions, where a combined score would have been wrong: Clean
# Air is 375-1 and perfectly predicted, ACA is 60-39 and perfectly predicted,
# Medicare is contested on both axes, and all three are good questions.


def test_closeness_is_zero_for_a_unanimous_vote():
    [c] = voteview.candidates([row(yea_count="100", nay_count="0")])
    assert c.signals.closeness == 0.0


def test_closeness_is_a_half_for_a_dead_even_vote():
    [c] = voteview.candidates([row(yea_count="50", nay_count="50")])
    assert c.signals.closeness == 0.5


def test_a_perfectly_predicted_vote_breaks_no_coalitions():
    """log-likelihood 0 means the spatial model called every vote, so nobody
    crossed their usual position — the signature of a party-line vote whether
    it was 375-1 or 60-39."""
    [c] = voteview.candidates([row(nominate_log_likelihood="0")])
    assert c.signals.coalition_break == pytest.approx(0.0)


def test_coalition_break_is_normalised_by_the_number_voting():
    """Otherwise a House vote always looks messier than a Senate one, and 1850
    always looks messier than 2010, purely from chamber size."""
    house = voteview.candidates(
        [row(yea_count="200", nay_count="200", nominate_log_likelihood="-400")]
    )[0]
    senate = voteview.candidates(
        [row(yea_count="50", nay_count="50", nominate_log_likelihood="-100")]
    )[0]
    assert house.signals.coalition_break == pytest.approx(
        senate.signals.coalition_break
    )


def test_attention_counts_every_roll_call_on_the_measure_not_only_passage():
    """Congress voting on something nine times is Congress struggling with it,
    and most of those nine are amendments and motions, not the passage."""
    [c] = voteview.candidates(
        [
            row(),
            row(rollnumber="200", dtl_desc="TO AMEND H.R. 17255 BY STRIKING TITLE II."),
            row(rollnumber="201", dtl_desc="TO RECOMMIT H.R. 17255."),
        ]
    )
    assert c.signals.attention == 3


def test_a_measure_with_no_passage_vote_produces_no_candidate():
    assert voteview.candidates([row(dtl_desc="TO RECOMMIT H.R. 17255.")]) == []


# --- ranking ------------------------------------------------------------------


def test_attention_is_ranked_within_its_own_congress():
    """Raw counts are not comparable across eras — legislative practice
    changed, and ranking on them globally over-selected the 19th century by
    1.55x in the top 1,000. That is the set-level skew the Phase 3 balance
    audit exists to catch, showing up inside the ranking itself."""
    old = [
        row(
            congress="20",
            bill_number="HR1",
            date="1828-01-01",
            dtl_desc="TO PASS H.R. 1.",
        )
    ] + [
        row(
            congress="20",
            bill_number="HR1",
            date="1828-01-02",
            rollnumber=str(i),
            dtl_desc="TO AMEND H.R. 1.",
        )
        for i in range(30)
    ]
    modern = [
        row(
            congress="111",
            bill_number="HR9",
            date="2010-01-01",
            dtl_desc="TO PASS H.R. 9.",
        )
    ]
    ranked = voteview.rank(voteview.candidates(old + modern))
    # Each measure is alone in its congress, so both sit at the top of it.
    assert {c.signals.attention_percentile for c in ranked} == {1.0}
    assert ranked[0].signals.attention == 31


def test_ranking_is_reproducible():
    """A review queue that reshuffles between runs cannot be worked through."""
    cands = voteview.candidates(
        [
            row(),
            row(
                congress="92",
                bill_number="HR9",
                date="1972-01-01",
                dtl_desc="TO PASS H.R. 9.",
            ),
        ]
    )
    assert len(cands) == 2
    assert [c.bill_number for c in voteview.rank(cands)] == [
        c.bill_number for c in voteview.rank(list(reversed(cands)))
    ]


def test_the_prompt_generator_is_not_shown_the_signals_either():
    """closeness is computed from yea/nay, so it leaks the margin by another
    route: 0.003 says "near-unanimous" as plainly as 375-1 does. The whitelist
    excludes it without needing to know that, which is the point of a
    whitelist."""
    [c] = voteview.candidates([row()])
    assert "signals" not in voteview.for_prompt_generation(c)
