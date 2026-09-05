"""Voteview as a question generator, not only as a `vote_record` source.

Every other module in the pipeline takes a question as input and goes looking
for material about it. This one runs the other way: it reads a corpus and
produces *candidates* — historical decisions that could become questions.

That inversion is possible because Voteview is a corpus, not a query API. One
29 MB CSV holds all 113,524 congressional roll calls back to 1789, so it can
be retrieved with no question in hand. Each row carries the entire structured
half of a question — date, chamber, bill number, margin — derived rather than
invented. The prose half (`prompt`, `options`, `reveal.outcome`) still needs
per-question retrieval; a candidate is not playable, it is the skeleton one is
built on.

The download uses `urllib` rather than an HTTP client dependency on purpose:
it is a single bulk file with no pagination, no rate limit and no auth, so it
needs none of the retry and backoff machinery the per-question fetch layer
does — and httpx is a dev-only dependency, so reaching for it here would break
the container while every test still passed.
"""

import csv
import logging
import re
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import sources

log = logging.getLogger(__name__)

CORPUS_URL = "https://voteview.com/static/data/out/rollcalls/HSall_rollcalls.csv"
USER_AGENT = "votecracy/0.1 (educational project; contact via repository)"

# Identifying which roll call is *the* vote is the hard part, and the spike
# concluded it was impossible before 1990 because `vote_question` is 0-1%
# filled in that era. That reading was too pessimistic: `dtl_desc` is 100%
# filled before 1990 (and only 27% after), and it states the motion outright.
# The two fields are near-complements, so between them the eras are covered.
#
# Measured over all 29,457 measures that carry a bill number, this identifies
# a passage vote for 43% of 1950-1989 measures against 39% of post-1990 ones —
# the historical era is no worse served than the modern one, which matters
# because historical distance is most of the game's appeal.
#
# The residual 60% is mostly genuine: plenty of measures were passed on a voice
# vote and only their procedural motions were recorded by roll call.
_PASSAGE_PREFIXES = (
    "TO PASS ",
    # A joint resolution proposing a constitutional amendment is adopted, not
    # passed. Without these the 16th and 18th Amendments look like measures
    # with no final vote at all.
    "TO ADOPT ",
    "ON ADOPTION OF ",
)
_PASSAGE_QUESTIONS = frozenset(
    {
        "On Passage",
        "On Passage of the Bill",
        "On Joint Resolution",
        "On Agreeing to the Resolution",
    }
)


# The motion has to be *about* the measure the row is filed under, and the bill
# number has to be the motion's object rather than merely present in it.
#
# Roll call 267 of 1970 is filed under H.R. 17255 and reads "TO ADOPT H.RES.
# 1069, THE RULE UNDER WHICH THE HOUSE CONSIDERS H.R. 17255" — adopting the
# *rule*, a procedural vote. Matching on the verb alone accepted it and picked
# 336-40 as the Clean Air Act's decisive vote instead of the 375-1 passage two
# roll calls later. Loosening to "is H.R. 17255 mentioned anywhere" accepted it
# too, because it is. Only the object test rejects it.
#
# Both sides go through `normalize_bill_number` because the corpus spells its
# own numbers inconsistently: the 16th Amendment is filed as SJR40 and
# described as "S. J. RES. 40". That is spike finding 5 again, this time
# *within* a single file rather than between two sources.
def _is_object_of(dtl_desc: str, verb: str, bill_number: str) -> bool:
    remainder = dtl_desc[len(verb) :].strip()
    if not remainder:
        return False
    compact = sources.normalize_bill_number(remainder)
    bill = sources.normalize_bill_number(bill_number)
    if not compact.startswith(bill):
        return False
    # Stops HR172 matching the start of HR17255.
    tail = compact[len(bill) :]
    return not tail[:1].isdigit()


# Joint resolutions propose constitutional amendments. Voteview records
# Congress adopting the proposal; it is a congressional dataset and holds
# nothing about the state ratification that actually decides the question.
_JOINT_RESOLUTION = re.compile(r"^(S|H)JR\d")

# Some eras store dtl_desc as a CQ-style header — "HR 10660.  HIGHWAY
# CONSTRUCTION ACT.  AMEND AND SUPPLEMENT..." — whose middle field is the
# measure's subject.
#
# Sometimes that reads as a popular name ("HIGHWAY CONSTRUCTION ACT") and
# sometimes as a description of the action ("AMEND THE SELECTIVE TRAINING AND
# SERVICE ACT OF 1940"), so it is a `subject`, not a popular name. Calling it
# one would overstate it: the significance signal candidate ranking needs is
# still the Law Revision Counsel's popular-name table, which is a separate
# download. What this is genuinely good for is seeding `search_terms`, which
# the other sources require and which Voteview otherwise cannot supply before
# 1990.
_CQ_HEADER = re.compile(r"^[A-Z][A-Z.\s]*\d+\.\s+([A-Z][A-Z0-9 ,'&/-]{6,70}?)\.")


@dataclass(frozen=True)
class Candidate:
    """A decision that could become a question. Not playable on its own."""

    bill_number: str
    congress: int
    chamber: str
    vote_date: date
    yea: int
    nay: int
    vote_type: str
    subject: str | None
    description: str
    # What a human still has to supply before this can enter the bank. Empty
    # means the structured half is complete. Never silently defaulted — the
    # same reason `select_sources` raises rather than falling back.
    gaps: tuple[str, ...] = ()

    @property
    def decision_date(self) -> date | None:
        """The retrieval boundary, where this corpus can establish it.

        For ordinary legislation the first passage vote *is* the earliest point
        the outcome became public. For a proposed constitutional amendment it
        is not: the question turns on ratification by the states, which happens
        years later and is not in a congressional dataset at any date.
        """
        return (
            None if self.vote_type == "constitutional_ratification" else self.vote_date
        )


def classify(row: dict) -> str | None:
    """`"passage"` if this roll call decided the measure, else None."""
    if row.get("vote_question", "").strip() in _PASSAGE_QUESTIONS:
        return "passage"
    bill = row.get("bill_number", "").strip()
    dtl = row.get("dtl_desc", "").upper().strip()
    if not bill:
        return None
    for verb in _PASSAGE_PREFIXES:
        if dtl.startswith(verb) and _is_object_of(dtl, verb, bill):
            return "passage"
    return None


def subject(dtl_desc: str) -> str | None:
    """The measure's subject line, where the row's format carries one."""
    match = _CQ_HEADER.match(dtl_desc.strip())
    if not match:
        return None
    text = match.group(1).strip().title()
    return text if len(text.split()) >= 2 else None


def _measure_key(row: dict) -> tuple[str, str] | None:
    bill = row.get("bill_number", "").strip()
    return (row["congress"], bill) if bill else None


def candidates(rows) -> list[Candidate]:
    """Group roll calls by measure and emit one candidate per measure.

    The chosen vote is the *earliest* passage vote, which follows the rule
    already settled in Step 2: the boundary is the first point the outcome
    became public. If the House passes in April and the Senate in July, a
    newspaper printed in May already reports a result.
    """
    by_measure: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = _measure_key(row)
        if key and classify(row) == "passage":
            by_measure.setdefault(key, []).append(row)

    out = []
    for (congress, bill), group in by_measure.items():
        # (date, rollnumber) so ties inside a single day are deterministic
        # rather than decided by the order rows happen to sit in the file.
        first = min(group, key=lambda r: (r["date"], int(r["rollnumber"])))
        is_amendment = bool(_JOINT_RESOLUTION.match(bill))
        out.append(
            Candidate(
                bill_number=bill,
                congress=int(congress),
                chamber=first["chamber"],
                vote_date=date.fromisoformat(first["date"]),
                yea=int(first["yea_count"]),
                nay=int(first["nay_count"]),
                vote_type=(
                    "constitutional_ratification"
                    if is_amendment
                    else "congressional_passage"
                ),
                subject=subject(first["dtl_desc"]),
                description=first["dtl_desc"].strip(),
                gaps=(
                    (
                        "decision_date: this is Congress proposing the amendment. The "
                        "question turns on state ratification, which Voteview does not "
                        "cover at any date.",
                    )
                    if is_amendment
                    else ()
                ),
            )
        )
    return sorted(out, key=lambda c: (c.vote_date, c.bill_number))


# The fields a prompt generator is allowed to see.
#
# Structurally identical to `content.public_view`, and for the same reason: a
# candidate carries `yea`/`nay`, and the spike is explicit that `prompt` must
# be generated from framing material only, because a model that has not been
# shown the result cannot leak it. Handing the generator the whole row would
# put the margin in its context — rule #1 broken inside the pipeline, where no
# player-facing test would ever see it.
#
# A whitelist rather than a blacklist, again for the same reason: a blacklist
# starts leaking the next field someone adds.
_GENERATOR_FIELDS = ("bill_number", "congress", "vote_date", "vote_type", "subject")


def for_prompt_generation(candidate: Candidate) -> dict:
    """The only shape of a candidate a prompt generator may be shown."""
    return {field: getattr(candidate, field) for field in _GENERATOR_FIELDS}


def download_corpus(path: Path) -> Path:
    """Fetch the roll call corpus if it isn't already cached locally."""
    if path.exists():
        log.info("voteview corpus already cached at %s", path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading voteview corpus from %s", CORPUS_URL)
    request = urllib.request.Request(CORPUS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response, open(path, "wb") as f:  # noqa: S310
        f.write(response.read())
    log.info("voteview corpus cached at %s (%d bytes)", path, path.stat().st_size)
    return path


def load_corpus(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
