"""Rule #1's last line: what the reveal says that the sources never did.

Rule #1 is enforced structurally first — the generator only ever sees framing
chunks, so it cannot leak an outcome it was never shown. This is the check that
runs afterwards anyway, because "the generator could not have known" is an
argument about the pipeline, and an argument is not a test.

The forbidden set is a set difference, not a judgement, and both halves are
already in the database:

    forbidden = tokens(reveal) - tokens(pre-vote material)

A word in the reveal that also appears in the pre-vote record is not a spoiler:
"medicare", "hospital" and "insurance" are all over the 1965 Congressional
Record. Only what is *unique* to the reveal can give the outcome away. Measured
on the Medicare question, 28 distinct reveal words reduce to 4.

**Numbers are the half that matters.** The same measurement leaves 307 and 116
— the House vote — along with 19,000,000 first-year enrolment and 67,000,000
covered today. A word list can be argued with; a margin appearing in a pre-vote
prompt cannot.
"""

import re

from grounding import numbers_in

# Four or more letters: shorter tokens are almost all function words, and they
# generate noise without ever being the thing that gives an outcome away.
_WORD = re.compile(r"[a-z]{4,}")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def forbidden(reveal_text: str, pre_vote_text: str) -> tuple[set[str], set[float]]:
    """What may never appear before a player has voted, for this question."""
    return (
        _words(reveal_text) - _words(pre_vote_text),
        numbers_in(reveal_text) - numbers_in(pre_vote_text),
    )


def leaks(candidate: str, reveal_text: str, pre_vote_text: str):
    """Anything in `candidate` that only the reveal could have supplied.

    Returns `(words, numbers)`, both empty when the text is clean. Returning
    what leaked rather than a bare boolean is deliberate: a rejected generation
    has to be diagnosable, and "it leaked 307" points straight at the sentence.
    """
    bad_words, bad_numbers = forbidden(reveal_text, pre_vote_text)
    return (_words(candidate) & bad_words, numbers_in(candidate) & bad_numbers)
