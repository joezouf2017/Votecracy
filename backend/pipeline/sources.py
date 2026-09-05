"""Which sources can answer which question, and how to ask them.

Pure functions — no network, no LLM, no database. This module decides *where*
material would come from; the fetch layer goes and gets it.

The load-bearing property is that `select_sources` **raises** when nothing in
the whitelist matches, rather than returning an empty tuple or a default.
Three of the four `vote_type` values have no source wired up at all, and a
caller handed an empty list will carry on and build a question with no
grounding behind it. The gap has to be loud, and loud here rather than three
steps later when a reveal turns out to cite nothing.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from shared import content
from shared.db import corpus

# How far back a question's framing material may reach. A judgement call, not
# a fact: long enough to catch the run-up to a decision — Medicare was argued
# over for most of a decade — without dragging in a different era's politics.
#
# It only decides which *sources* are eligible. Which documents end up stored
# is decided by published_date on source_chunks, which is the real boundary.
LOOKBACK_YEARS = 10


@dataclass(frozen=True)
class Source:
    """One retrievable collection, and the facts that decide when it applies.

    Every field is a property of the source itself, not a policy about it. That
    matters because the alternative — labelling whole sources "framing" or
    "outcome" — is what CLAUDE.md's retrieval-scope rule rejects. A 1970
    newspaper retrospective about a 1965 decision is outcome material even
    though newspapers are a "framing source", and the date is what catches it.
    """

    key: str
    # (need, vote_type) pairs this source can actually serve. A set of pairs,
    # not two independent sets, because the answer genuinely differs per
    # combination: the Congressional Record carries the *debate* on a proposed
    # constitutional amendment (framing) but not the *state ratification* votes
    # that decide it (vote_record). Two flat sets would have to claim both or
    # neither.
    serves: frozenset[tuple[str, str]]
    coverage_start: date
    coverage_end: date | None  # None = up to the present
    jurisdictions: frozenset[str] | None  # None = not jurisdiction-specific
    collection: str | None = None  # the API's own name for it, where it has one


def _serves(needs, vote_types) -> frozenset[tuple[str, str]]:
    return frozenset((n, v) for n in needs for v in vote_types)


_CONGRESSIONAL = frozenset({"congressional_passage", "constitutional_ratification"})

# Declaration order is the order results come back in, so it is part of the
# contract: retrieval has to be reproducible run to run.
WHITELIST: tuple[Source, ...] = (
    Source(
        key="voteview",
        # Roll calls and nothing else — that is what the dataset physically is.
        # Congressional only, so a constitutional amendment's ratification by
        # the states is not in it at any date. That absence is spike finding 3,
        # and it is why two questions raise instead of quietly being handed the
        # congressional votes in place of the ones their reveal cites.
        serves=_serves({"vote_record"}, {"congressional_passage"}),
        coverage_start=date(1789, 5, 16),  # measured during the spike
        coverage_end=None,
        jurisdictions=frozenset({"US"}),
    ),
    Source(
        key="govinfo:crecb",
        # Congressional Record, bound edition. Carries debate (framing) and the
        # vote descriptions Voteview lacks before 1990 — but for a ratification
        # question only the debate, never the deciding vote.
        #
        # As an `outcome` source it records what was *said* about effects, not
        # evidence of them. Anything numeric taken from here has to be
        # attributed to the speaker, per CLAUDE.md.
        serves=(
            _serves({"framing", "outcome"}, _CONGRESSIONAL)
            | _serves({"vote_record"}, {"congressional_passage"})
        ),
        coverage_start=date(1873, 1, 1),
        coverage_end=date(2017, 12, 31),
        jurisdictions=frozenset({"US"}),
        collection="CRECB",
    ),
    Source(
        key="govinfo:crec",
        # The daily edition. Same material, much later start — which is exactly
        # why the whitelist is per-collection and not per-site. Treating
        # "govinfo" as one source with one coverage window would either lock the
        # six pre-1990 questions out or claim coverage that isn't there.
        serves=(
            _serves({"framing", "outcome"}, _CONGRESSIONAL)
            | _serves({"vote_record"}, {"congressional_passage"})
        ),
        coverage_start=date(1994, 1, 1),
        coverage_end=None,
        jurisdictions=frozenset({"US"}),
        collection="CREC",
    ),
    Source(
        key="govinfo:statute",
        # Statutes at Large — the bill's own text. In scope because "how is this
        # paid for" is usually answered by the financing provisions rather than
        # by anything anyone said about them.
        serves=_serves({"framing"}, _CONGRESSIONAL),
        coverage_start=date(1789, 1, 1),
        coverage_end=None,
        jurisdictions=frozenset({"US"}),
        collection="STATUTE",
    ),
    Source(
        key="loc:chronicling-america",
        # Digitised newspapers. Not restricted by vote_type — a newspaper covers
        # whatever happened — but hard-stopped at 1963 by copyright, which is
        # what excludes it from the two most recent questions.
        serves=_serves({"framing", "outcome"}, content.VOTE_TYPES),
        coverage_start=date(1777, 1, 1),
        coverage_end=date(1963, 12, 31),
        jurisdictions=frozenset({"US"}),
    ),
)


class NoSourceAvailable(Exception):
    """Nothing in the whitelist can serve this (question, need).

    Carries why each candidate was rejected. "no UK source at all" and "no
    source for state ratification votes" are different gaps needing different
    fixes, and an exception that only says "none" makes them look the same.
    """

    def __init__(self, question_id: str, need: str, reasons: dict[str, str]):
        self.question_id = question_id
        self.need = need
        self.reasons = reasons
        detail = "; ".join(f"{key}: {why}" for key, why in reasons.items())
        super().__init__(f"no source for {question_id!r} need={need!r} — {detail}")


def _years_before(d: date, years: int) -> date:
    """Subtract whole years, surviving 29 February."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def need_window(need: str, decision: date) -> tuple[date | None, date | None]:
    """The publication window a need's material lives in. The end is exclusive.

    `vote_record` reaches up to and including the decision itself where
    `framing` stops strictly short of it. That one day is the whole point: the
    vote *is* the outcome, so it can never sit in framing scope.
    """
    if need == "outcome":
        return (decision, None)
    if need == "vote_record":
        return (_years_before(decision, LOOKBACK_YEARS), decision + timedelta(days=1))
    if need == "framing":
        return (_years_before(decision, LOOKBACK_YEARS), decision)
    raise ValueError(
        f"unknown need {need!r}; expected one of {sorted(corpus.CHUNK_ROLES)}"
    )


def _overlaps(source: Source, window: tuple[date | None, date | None]) -> bool:
    """Does the source's coverage overlap the window at all?

    Both ends matter. Checking only `coverage_start < window_end` would keep
    Chronicling America eligible for a 2015 question — it does start in 1777,
    after all — when it stops dead in 1963 and can supply nothing.
    """
    w_start, w_end = window
    starts_after_window = w_end is not None and source.coverage_start >= w_end
    ends_before_window = (
        source.coverage_end is not None
        and w_start is not None
        and source.coverage_end < w_start
    )
    return not (starts_after_window or ends_before_window)


def select_sources(question: dict, need: str) -> tuple[Source, ...]:
    """Every whitelisted source that can serve this need for this question.

    Never returns empty — see NoSourceAvailable. An empty tuple would read to a
    caller as "nothing matched this time" and the run would carry on.
    """
    if need not in corpus.CHUNK_ROLES:
        raise ValueError(
            f"unknown need {need!r}; expected one of {sorted(corpus.CHUNK_ROLES)}"
        )

    decision = date.fromisoformat(question["decision_date"])
    window = need_window(need, decision)
    jurisdiction = question["jurisdiction"]
    vote_type = question["vote_type"]

    chosen: list[Source] = []
    rejected: dict[str, str] = {}
    for source in WHITELIST:
        if (
            source.jurisdictions is not None
            and jurisdiction not in source.jurisdictions
        ):
            rejected[source.key] = f"does not cover jurisdiction {jurisdiction}"
        elif (need, vote_type) not in source.serves:
            rejected[source.key] = (
                f"does not serve need={need} for vote_type={vote_type}"
            )
        elif not _overlaps(source, window):
            rejected[source.key] = (
                f"coverage {source.coverage_start}..{source.coverage_end or 'present'} "
                f"misses {window[0]}..{window[1] or 'present'}"
            )
        else:
            chosen.append(source)

    if not chosen:
        raise NoSourceAvailable(question["id"], need, rejected)
    return tuple(chosen)
