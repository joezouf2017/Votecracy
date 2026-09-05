"""Rule #1's post-hoc check. Structural prevention comes first; this is the test
that the argument for it actually held.
"""

from spoilers import forbidden, leaks

REVEAL = (
    "Passed 307-116 in the House. "
    "Medicare enrolled 19 million Americans in its first year."
)
PRE_VOTE = (
    "Mr. Speaker, the committee bill provides hospital insurance for the aged. "
    "Medicare would be financed through the social security payroll tax. "
    "The House will consider the measure this week."
)


def test_a_word_shared_with_the_sources_is_not_forbidden():
    """ "Medicare" and "House" are all over the pre-vote record. Treating every
    reveal word as a spoiler would reject every legitimate prompt."""
    words, _ = forbidden(REVEAL, PRE_VOTE)
    assert "medicare" not in words
    assert "house" not in words


def test_a_word_unique_to_the_reveal_is_forbidden():
    words, _ = forbidden(REVEAL, PRE_VOTE)
    assert "enrolled" in words


def test_the_margin_is_forbidden():
    """The half that matters. A word list can be argued with; a vote count
    appearing in a pre-vote prompt cannot."""
    _, numbers = forbidden(REVEAL, PRE_VOTE)
    assert {307.0, 116.0, 19_000_000.0} <= numbers


def test_a_clean_prompt_leaks_nothing():
    clean = (
        "It's 1965. Congress is debating hospital insurance for the aged. "
        "Do you vote for it?"
    )
    assert leaks(clean, REVEAL, PRE_VOTE) == (set(), set())


def test_a_prompt_carrying_the_margin_is_caught():
    assert leaks("The bill passed 307 to 116.", REVEAL, PRE_VOTE)[1] == {307.0, 116.0}


def test_a_number_written_differently_is_still_caught():
    """ "19 million" and "19,000,000" are one assertion, so a generator cannot
    evade the check by changing format."""
    assert leaks("It covered 19,000,000 people.", REVEAL, PRE_VOTE)[1] == {19_000_000.0}


def test_short_words_are_ignored():
    """Tokens under four letters are function words and produce noise without
    ever being what gives an outcome away."""
    words, _ = forbidden("its the and", PRE_VOTE)
    assert words == set()
