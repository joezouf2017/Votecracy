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
import math
import re
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from pipeline import fetch as http
from pipeline import queries

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
    compact = queries.normalize_bill_number(remainder)
    bill = queries.normalize_bill_number(bill_number)
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
class Signals:
    """Measurable properties of a candidate, for ranking and for auditing.

    Deliberately *not* combined into a single score. Checked against the eight
    hand-written questions, a combined score would have been actively wrong:

        Medicare 1965   313-115   closeness 0.269   coalition_break 0.246
        Clean Air 1970  375-1     closeness 0.003   coalition_break 0.000
        ACA 2009        60-39     closeness 0.394   coalition_break 0.002

    All three are good questions, and they have nothing in common on either
    axis. Clean Air was near-unanimous — which is exactly what makes it a good
    question, because a modern reader thinks the answer is obvious and 1970's
    industry did not. ACA was nearly a tie *and* perfectly predicted by
    ideology, the signature of a party-line vote. Any single score ranks Clean
    Air near the bottom.

    So `attention` ranks, and the other two describe *what kind* of question
    this is rather than how good it is. Keeping them separate is also what lets
    the Phase 3 set-balance audit see whether the bank over-selects one shape.
    """

    # How many roll calls the measure took in total. The significance proxy:
    # the median measure in the corpus takes one, while all six of the
    # hand-written questions with a bill number sit at the 86th-100th
    # percentile. Congress voting on something nine times is Congress
    # struggling with it.
    attention: int
    # `attention` ranked against the other measures of the *same congress*, 0..1.
    #
    # Raw counts are not comparable across eras: legislative practice changed,
    # and ranking on them globally over-selected the 19th century by 1.55x in
    # the top 1,000. That is precisely the set-level skew the Phase 3 balance
    # audit exists to catch, and it turned up inside the ranking itself.
    #
    # Normalising per congress makes the ranking era-neutral. How many
    # questions to draw from each era is then a separate and *visible*
    # decision, rather than something smuggled into a score.
    attention_percentile: float = 0.0
    # 0 = unanimous, 0.5 = dead even.
    closeness: float = 0.0
    # Share of the vote the DW-NOMINATE spatial model fails to predict, so
    # roughly "how many members voted against their usual position". Near zero
    # means the usual coalitions held, whether the vote was 375-1 or 60-39.
    coalition_break: float = 0.0
    # Members voting. Distinguishes a measure the chamber turned out for from
    # a thinly-attended routine one.
    turnout: int = 0


def _signals(passage: dict, measure_rows: list[dict]) -> Signals:
    yea, nay = int(passage["yea_count"]), int(passage["nay_count"])
    total = yea + nay
    if total == 0:
        return Signals(attention=len(measure_rows))
    log_likelihood = float(passage.get("nominate_log_likelihood") or 0.0)
    # exp(ll/n) is the geometric-mean probability the spatial model assigned to
    # the votes actually cast; 1 means it called every one. The normalisation
    # by n is what makes House and Senate, 1850 and 2010, comparable.
    return Signals(
        attention=len(measure_rows),
        closeness=min(yea, nay) / total,
        coalition_break=1.0 - math.exp(log_likelihood / total),
        turnout=total,
    )


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
    signals: Signals
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
    all_rows: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = _measure_key(row)
        if key:
            all_rows.setdefault(key, []).append(row)

    by_measure = {
        key: passage
        for key, group in all_rows.items()
        if (passage := [r for r in group if classify(r) == "passage"])
    }

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
                signals=_signals(first, all_rows[(congress, bill)]),
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
    return _with_percentiles(sorted(out, key=lambda c: (c.vote_date, c.bill_number)))


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


def _with_percentiles(cands: list[Candidate]) -> list[Candidate]:
    """Fill in each candidate's attention percentile within its own congress."""
    per_congress: dict[int, list[int]] = {}
    for c in cands:
        per_congress.setdefault(c.congress, []).append(c.signals.attention)
    for values in per_congress.values():
        values.sort()

    out = []
    for c in cands:
        peers = per_congress[c.congress]
        at_or_below = bisect_right(peers, c.signals.attention)
        out.append(
            replace(
                c,
                signals=replace(
                    c.signals, attention_percentile=at_or_below / len(peers)
                ),
            )
        )
    return out


def rank(cands: list[Candidate]) -> list[Candidate]:
    """Most-worth-reviewing first.

    Ranks on `attention_percentile` alone, because that is the only signal
    validated against the existing questions — see `Signals`. The other two describe the
    shape of a question rather than its worth, and folding them in would bury
    the near-unanimous ones.

    This is a proxy, and an honest one rather than a good one. The real
    significance signal is whether a law has a popular name, which lives in
    the Law Revision Counsel's table and is a separate bulk download. Until
    that lands, "Congress had to vote on this nine times" is what the corpus
    can say by itself.

    Ties break on date then bill number so the order is reproducible; a review
    queue that reshuffles between runs cannot be worked through.
    """
    return sorted(
        cands,
        key=lambda c: (-c.signals.attention_percentile, c.vote_date, c.bill_number),
    )


def download_corpus(path: Path) -> Path:
    """Fetch the roll call corpus if it isn't already cached locally."""
    if path.exists():
        log.info("voteview corpus already cached at %s", path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading voteview corpus from %s", CORPUS_URL)
    # Through the shared client for retry and the breaker, but `cache=False`:
    # this writes its own 29 MB file and the guard above is its cache, so
    # letting the HTTP layer keep a second copy would double the disk cost for
    # no benefit.
    path.write_bytes(http.request(CORPUS_URL, expect=("text/csv",), cache=False))
    log.info("voteview corpus cached at %s (%d bytes)", path, path.stat().st_size)
    return path


def load_corpus(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
