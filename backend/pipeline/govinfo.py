"""GovInfo — the keyed source, and the two questions only it can serve.

`CREC` (Congressional Record, daily, 1994+) and `FR` (Federal Register) reach
the two questions nothing else can: the ACA, which is past archive.org's
2008 series end, and net neutrality, which is an agency rule and so has no
congressional record at all.

**Bulk data is not an option for either.** `govinfo.gov/bulkdata` carries Bills,
Statutes, CFR and the Federal Register but *not* the Congressional Record, so
CREC needs the keyed API. Verified, not assumed.

**Granules are dated exactly**, like Hansard sittings and unlike bound volumes.
`CREC-2009-11-19-pt1-PgS11582` is 19 November 2009 and nothing else, so
`published_date` is read off the package id rather than guessed conservatively.
The whole straddling problem in `volumes.py` is a bound-edition problem.

**One thing to know about the search index: it is incomplete.** Searching FR for
the 2015 Open Internet Order by its exact title does not return the issue that
contains it, which was found by listing that package's granules directly. The
NPRM was then located by reading the Order's own citation of it ("79 FR 37448,
July 1, 2014"). Treat a zero-result search as "ask another way", never as "not
published" — the same lesson loc.gov taught with its ignored date parameters.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date

from pipeline import fetch as http
from pipeline import ingest
from shared.db import corpus
from shared.db import engine as db_engine
from shared.settings import get_settings

log = logging.getLogger(__name__)

API = "https://api.govinfo.gov"
SOURCE_KEY = "govinfo"

_TAGS = re.compile(r"<[^>]+>")
_SCRIPTS = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_SPACES = re.compile(r"[ \t]+")
_PACKAGE_DATE = re.compile(r"^[A-Z]+-(\d{4})-(\d{2})-(\d{2})")


class GovInfoError(RuntimeError):
    """The API refused, or a package id carried no date."""


@dataclass(frozen=True)
class Granule:
    """One dated document inside a daily package."""

    question_id: str
    package_id: str  # FR-2015-04-13 | CREC-2009-11-19
    granule_id: str
    title: str

    @property
    def day(self) -> date:
        """Read off the package id, which always carries it.

        A granule's date is not inferred from anything — every GovInfo daily
        package is named for its own day. `published_date` being a fact rather
        than a conservative guess is the whole reason this adapter is short.
        """
        m = _PACKAGE_DATE.match(self.package_id)
        if not m:
            raise GovInfoError(f"no date in package id {self.package_id!r}")
        return date(int(m[1]), int(m[2]), int(m[3]))

    @property
    def external_id(self) -> str:
        return f"{self.package_id}/{self.granule_id}"

    @property
    def url(self) -> str:
        return (
            f"https://www.govinfo.gov/app/details/{self.package_id}/{self.granule_id}"
        )


def _key() -> str:
    key = get_settings().govinfo_api_key.get_secret_value()
    if not key:
        raise GovInfoError("GOVINFO_API_KEY is not set; see .env.example")
    return key


def _request(
    url: str, data: bytes | None = None, expect: tuple[str, ...] | None = None
) -> bytes:
    """Through the shared client. Search results are not cached — the whole
    point of a query is that its answer may change — but granule bodies are,
    because a published document does not."""
    headers = {"Content-Type": "application/json"} if data else {}
    try:
        return http.request(
            url, data=data, headers=headers, expect=expect, cache=data is None
        )
    except http.FetchError as exc:
        raise GovInfoError(str(exc)) from exc


def search(query: str, *, limit: int = 100) -> list[Granule]:
    """Run a recorded query. The query is pinned in `QUERIES`, not the results.

    Pinning the query rather than a list of granule ids keeps this reproducible
    without writing out 87 identifiers, and it keeps the *reason* for a set
    visible. The cost is that a re-run could return a different set; the log
    line below is what makes that visible rather than silent.
    """
    body = json.dumps(
        {
            "query": query,
            "pageSize": limit,
            "offsetMark": "*",
            "sorts": [{"field": "publishdate", "sortOrder": "ASC"}],
        }
    ).encode()
    payload = json.loads(_request(f"{API}/search?api_key={_key()}", body))
    log.info("govinfo search returned %s results for %s", payload.get("count"), query)
    return payload.get("results", [])


def fetch(granule: Granule) -> str:
    """The granule's HTML, stripped to text.

    Scripts go before tags for the same reason as Hansard: markup removed
    naively turns an inline analytics blob into what looks like source text.
    """
    raw = _request(
        f"{API}/packages/{granule.package_id}/granules/"
        f"{granule.granule_id}/htm?api_key={_key()}",
        expect=("text/html", "text/plain"),
    ).decode("utf-8", "replace")
    return _SPACES.sub(" ", _TAGS.sub(" ", _SCRIPTS.sub(" ", raw)))


def already_ingested(granule: Granule) -> int:
    stmt = corpus.source_documents.select().where(
        corpus.source_documents.c.source_key == SOURCE_KEY,
        corpus.source_documents.c.external_id == granule.external_id,
        corpus.source_documents.c.question_id == granule.question_id,
    )
    with db_engine.get_engine().connect() as conn:
        return len(conn.execute(stmt).fetchall())


def role_for(granule: Granule, decision: date) -> str:
    return "framing" if granule.day < decision else "vote_record"


def ingest_granule(granule: Granule, decision: date) -> int:
    if already_ingested(granule):
        return 0
    text = ingest.normalise(fetch(granule))
    document_id = ingest.store_passage(
        question_id=granule.question_id,
        source_key=SOURCE_KEY,
        external_id=granule.external_id,
        url=granule.url,
        title=granule.title,
        published_date=granule.day,
        content_type="text/html",
        passage=ingest.Passage(0, len(text), text),
        role=role_for(granule, decision),
    )
    log.info(
        "%s -> document %d, role=%s, %s, %d chars",
        granule.external_id,
        document_id,
        role_for(granule, decision),
        granule.day,
        len(text),
    )
    return document_id


# The queries that define each question's set, and the reasoning for their
# bounds. Pinned here rather than as granule id lists so the *why* survives.
QUERIES: dict[str, tuple[str, ...]] = {
    # **The upper bound is 2009-12-23, not the decision date, and that is the
    # point.** This question's decision_date is 2010-03-21, when the House
    # passed the Senate bill 219-212. But the reveal says "Passed the Senate
    # 60-39, House 219-212", and the Senate's 60-39 happened on 2009-12-24 —
    # inside the framing window.
    #
    # So the ordinary boundary is not enough here: reporting from January to
    # March 2010 is legitimately pre-vote by date and legitimately discusses a
    # vote the reveal presents as the outcome. `docs/content-audit.md` calls
    # this the residual leak window.
    #
    # Cutting the fetch at 23 December closes it without touching the boundary
    # logic, which stays `published_date < decision_date` for every question.
    # This is curation, not a special case in the rule — and it is the reason a
    # question's set is decided by a human before anything is fetched.
    "us-affordable-care-act-2010": (
        'collection:CREC AND "affordable care act" '
        "AND publishdate:range(2009-09-01,2009-12-23)",
        # Post-decision, so vote_record and outcome. Without this the question
        # has framing and nothing else, and the reveal has no source at all.
        'collection:CREC AND "affordable care act" '
        "AND publishdate:range(2010-03-21,2010-06-30)",
    ),
}

# Net neutrality is pinned rather than searched, because **the search index does
# not return either of the two documents that matter**. Querying FR for
# "Protecting and Promoting the Open Internet" across 2014-2015 returns five
# issues, none of them these. Both were found another way: the Order by listing
# FR-2015-04-13's granules directly, and the NPRM by reading the Order's own
# citation of it ("79 FR 37448, July 1, 2014").
#
# Two documents is the whole rulemaking record here. An agency rule has no
# congressional debate, so what stands in for one is the NPRM before the vote
# and the Order after it.
PINNED: dict[str, tuple[Granule, ...]] = {
    "us-net-neutrality-2015": (
        # 2014-07-01, well before the 2015-02-26 decision: framing.
        Granule(
            "us-net-neutrality-2015",
            "FR-2014-07-01",
            "2014-14859",
            "Protecting and Promoting the Open Internet (proposed rule)",
        ),
        # 2015-04-13, after it: vote_record. 836K characters carrying the
        # reasoning and the dissents, not a bare rule text — checked, because
        # "does the published version carry the argument" was the open question
        # that decided whether this route worked at all.
        Granule(
            "us-net-neutrality-2015",
            "FR-2015-04-13",
            "2015-07841",
            "Protecting and Promoting the Open Internet (report and order)",
        ),
    ),
}


# Granule titles that are statutory language rather than argument.
#
# Measured on the first ACA run: 47 of 87 granules were amendment *text*, two of
# them 2.1M characters each, and they produced 21,360 pre-vote chunks against
# Medicare's 3,560 for a comparable question. Retrieval asked "how would this be
# funded" would then return the funding *provision* rather than anyone's case
# for it, which is the wrong half of the question.
#
# Bill text does belong in pre-vote scope — the financing provisions often
# answer what no speech does — but as one document among the debate, not as
# 54% of the corpus. The bill itself is kept; the amendment dumps are not.
_LANGUAGE_NOT_ARGUMENT = (
    "text of amendment",
    "text of senate amendment",
    "amendments submitted",
)


def granules_for(question_id: str) -> list[Granule]:
    """Everything to fetch for a question: pinned first, then searched."""
    found = list(PINNED.get(question_id, ()))
    for query in QUERIES.get(question_id, ()):
        for r in search(query):
            granule = r.get("granuleId")
            title = r.get("title") or granule or ""
            if not granule:
                continue
            if any(p in title.lower() for p in _LANGUAGE_NOT_ARGUMENT):
                continue
            found.append(Granule(question_id, r.get("packageId") or "", granule, title))
    return found
