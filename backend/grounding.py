"""Rule #2, enforced by code: a claim is only allowed if the source says it.

The generator emits each factual sentence with a citation — which document, and
which character span inside it. This module checks the span actually supports
the claim. Nothing here calls a model, and that is the entire point: using an
LLM to decide whether an LLM hallucinated reintroduces exactly what rule #2
exists to prevent.

Three things get checked, and each has a failure it is there to catch:

- **The span is in bounds.** A model that invents an offset is inventing a
  citation, which is worse than not citing at all — it looks verified.
- **The span is tight.** Citing the whole document would "contain" any number
  in it. A citation is a sentence or two; a chapter is not evidence.
- **The number is in the span.** The check that does the real work, because
  numbers are the claims that matter and the ones a model most readily
  fabricates. "19 million", "19,000,000" and "nineteen million" are the same
  assertion and all three have to match.

What this deliberately cannot check is a sentence with no number in it. "Most
economists consider it one of the best infrastructure investments ever made"
has no value to verify and no span that could support it. Such sentences must
be attributed to a named speaker or cut — see `unsupported_numbers`, which is
the half of the job that catches a generator quietly dropping citations.
"""

import re
from dataclasses import dataclass

# A citation longer than this is not evidence, it is a haystack. Two or three
# sentences of Congressional Record run to roughly this length.
MAX_SPAN_CHARS = 600

_SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

# 19,000,000 | 19 million | nineteen million | 19.4 million | 67
_NUMBER = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(hundred|thousand|million|billion|trillion)?\b"
    r"|\b("
    + "|".join(_WORD_NUMBERS)
    + r")(?:[ -](hundred|thousand|million|billion|trillion))?\b",
    re.IGNORECASE,
)


def numbers_in(text: str) -> set[float]:
    """Every quantity the text asserts, normalised to a plain number.

    Written as a set because the question is only ever "is this value present",
    never "where" or "how many times".
    """
    found: set[float] = set()
    for m in _NUMBER.finditer(text):
        digits, digit_scale, word, word_scale = m.groups()
        if digits is not None:
            value = float(digits.replace(",", ""))
            scale = digit_scale
        else:
            value = float(_WORD_NUMBERS[word.lower()])
            scale = word_scale
        if scale:
            value *= _SCALES[scale.lower()]
        found.add(value)
    return found


def _readable(value: float) -> str:
    """`40,000,000`, not `4e+07`.

    These messages are read by whoever is reviewing a rejected claim, and
    scientific notation makes them do a conversion in their head before they
    can tell whether the number is even the one they were expecting.
    """
    return f"{value:,.0f}" if value == int(value) else f"{value:,}"


@dataclass(frozen=True)
class Claim:
    """One factual sentence and the span the generator says supports it."""

    text: str
    document_id: int
    char_span: tuple[int, int]
    value: float | None = None


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def verify(claim: Claim, document_text: str) -> Verdict:
    """Does the cited span support the claim? No model involved."""
    start, end = claim.char_span
    if start < 0 or end > len(document_text) or start >= end:
        return Verdict(
            False,
            f"span {claim.char_span} is not inside document {claim.document_id} "
            f"(0..{len(document_text)})",
        )
    if end - start > MAX_SPAN_CHARS:
        return Verdict(
            False,
            f"span is {end - start} chars; a citation over {MAX_SPAN_CHARS} is a "
            "haystack, not evidence",
        )

    span = document_text[start:end]
    if claim.value is None:
        # Nothing quantitative to check. Left to `unsupported_numbers` and to
        # human review — a span cannot prove an opinion.
        return Verdict(True)

    if claim.value not in numbers_in(span):
        return Verdict(
            False,
            f"value {_readable(claim.value)} does not appear in the cited span: "
            f"{span[:120]!r}",
        )
    return Verdict(True)


def unsupported_numbers(text: str, claims: list[Claim]) -> set[float]:
    """Numbers asserted in the prose that no claim vouches for.

    The other half of rule #2. Per-claim verification proves the citations that
    exist are honest; this proves none are missing. Without it a generator can
    cite one number correctly and invent the rest of the paragraph.
    """
    return numbers_in(text) - {c.value for c in claims if c.value is not None}
