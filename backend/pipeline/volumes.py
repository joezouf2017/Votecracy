"""Bound volumes on disk, and the date rule that decides which side they land on.

A Congressional Record volume is two to three weeks of proceedings in one file.
That granularity is the problem this module exists for: **four of the five
decision volumes straddle their question's decision date**, so the volume is not
a thing that can be placed on one side of the pre/post-vote boundary by its
title alone.

    published_date = the volume's LAST day, never its first

Taking the first day is a rule #1 leak, and not a subtle one. The Clean Air
volume opens 4 June against a 10 June decision, so a `published_date` of 4 June
satisfies `published_date < decision_date` for every chunk in it — including the
roll call. The margin becomes framing material and a player sees the result
before voting.

Taking the last day fails the other way: the volume is marked 12 June, lands in
post-vote scope, and the six days of pre-decision debate inside it are lost
rather than leaked. That is the direction to fail in. It costs 1, 10, 6 and 8
days across the four straddling volumes — and those are the days closest to the
vote, so it is the most relevant framing material there is.

Recovering them needs per-page dating. The running page headers support it on
the 1965 and 1970 volumes (1,363–1,451 headers, full coverage) and not on 1913,
1919 or 1956 (1–7). Measured in `docs/content-audit.md`, along with two traps
worth not rediscovering. Until then, this rule holds and is safe.

Every *framing* volume ends strictly before its decision date, so nothing rule
#1 protects straddles anything, and the whole pre-vote corpus is available
under this rule with no per-page work at all.
"""

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pipeline import ingest
from shared import content
from shared.db import corpus
from shared.db import engine as db_engine

log = logging.getLogger(__name__)

# Gitignored: 128 MB of OCR text that is rebuildable from archive.org but not
# cheap to re-fetch. `docs/metrics/manual-download-list.md` has the URLs.
CACHE = Path(__file__).resolve().parents[1] / ".cache"

SOURCE_KEY = "archive:sim-congressional-record"
_ARCHIVE_PREFIX = "sim_congressional-record-proceedings-and-debates_"
# Not every volume uses the long form — the 1909 one is spelled this way.
_SHORT_PREFIX = "sim_congressional-record_"


@dataclass(frozen=True)
class Volume:
    """One bound volume, and which question it was fetched for."""

    identifier: str  # the archive.org identifier, which is also the provenance
    question_id: str
    starts: date
    ends: date

    @property
    def path(self) -> Path:
        return CACHE / f"{self.identifier}.txt"

    @property
    def url(self) -> str:
        return f"https://archive.org/details/{self.identifier}"

    @property
    def published_date(self) -> date:
        """The volume's last day. See the module docstring for why never its
        first — that choice is the difference between losing material and
        leaking an outcome."""
        return self.ends

    @property
    def role(self) -> str:
        """`framing` only if the whole volume predates the decision.

        Derived, not declared. A volume that straddles is post-vote material
        under the date rule above, and labelling it `framing` by hand would put
        the roll call one boolean away from a player who has not voted.
        """
        decision = content.decision_date(self.question_id)
        if decision is None:
            raise ValueError(f"no question {self.question_id!r}")
        return "framing" if self.ends < decision else "vote_record"

    @property
    def external_id_prefix(self) -> str:
        """Matches what the Medicare slice already stored, so re-ingesting that
        volume collides on the UNIQUE constraint instead of duplicating it.

        Both archive.org spellings are stripped before the canonical one is
        put back. The 1909 volume already begins with the short form, and
        removing only the long form would have produced
        `sim_congressional-record_sim_congressional-record_...` — unique, so
        nothing would have failed, and wrong in the column that is supposed to
        be this document's provenance.
        """
        tail = self.identifier
        for prefix in (_ARCHIVE_PREFIX, _SHORT_PREFIX):
            tail = tail.removeprefix(prefix)
        return _SHORT_PREFIX + tail

    @property
    def title(self) -> str:
        return f"Congressional Record, {self.starts} to {self.ends}"


def _v(ident: str, question_id: str, starts: date, ends: date) -> Volume:
    """Most identifiers share the long prefix, so entries below give the tail.

    Not all of them. The 1909 volume is `sim_congressional-record_...` with no
    `-proceedings-and-debates`, so it is written out in full. Assuming one
    prefix 404s on exactly one of thirteen, which is the kind of exception that
    is invisible until it fires.
    """
    return Volume(_ARCHIVE_PREFIX + ident, question_id, starts, ends)


# Two volumes per question — the one ending before the decision and the one
# containing it — plus three for the pair whose real debate is years earlier.
# Spans come from the archive.org titles.
#
# us-affordable-care-act-2010 has no entry: the scanned series runs 1873-01-01
# to 2008-06-23, so that question needs GovInfo's CREC daily edition and a key.
VOLUMES = (
    _v(
        "march-24-1965-april-6-1965_111",
        "us-medicare-1965",
        date(1965, 3, 24),
        date(1965, 4, 6),
    ),
    _v(
        "april-7-1965-april-27-1965_111",
        "us-medicare-1965",
        date(1965, 4, 7),
        date(1965, 4, 27),
    ),
    _v(
        "december-02-1918-january-04-1919_57",
        "us-prohibition-1919",
        date(1918, 12, 2),
        date(1919, 1, 4),
    ),
    _v(
        "january-06-26-1919_57",
        "us-prohibition-1919",
        date(1919, 1, 6),
        date(1919, 1, 26),
    ),
    _v(
        "march-28-april-26-1956_102",
        "us-interstate-highway-1956",
        date(1956, 3, 28),
        date(1956, 4, 26),
    ),
    _v(
        "april-27-may-21-1956_102",
        "us-interstate-highway-1956",
        date(1956, 4, 27),
        date(1956, 5, 21),
    ),
    _v(
        "may-25-june-3-1970_116",
        "us-clean-air-act-1970",
        date(1970, 5, 25),
        date(1970, 6, 3),
    ),
    _v(
        "june-4-12-1970_116",
        "us-clean-air-act-1970",
        date(1970, 6, 4),
        date(1970, 6, 12),
    ),
    _v(
        "january-6-1913-january-25-1913_49",
        "us-income-tax-1913",
        date(1913, 1, 6),
        date(1913, 1, 25),
    ),
    _v(
        "january-26-1913-february-12-1913_49",
        "us-income-tax-1913",
        date(1913, 1, 26),
        date(1913, 2, 12),
    ),
    # --- the debates that actually decided these two -------------------------
    #
    # Both questions are `constitutional_ratification`, so their decision_date
    # is the day the 36th state ratified — and Congress voted years earlier.
    # Picking volumes around the decision date gave income tax ZERO pre-vote
    # chunks: the January 1913 volume says "income tax" once and "tariff" 98
    # times, because Congress was busy with tariffs.
    #
    # These volumes still satisfy `published_date < decision_date` (1909 < 1913,
    # 1917 < 1919) and sit inside the ten-year framing lookback, so they need no
    # new mechanism. It is the same rule pointed at the right debate.
    #
    # Note the first identifier does NOT carry `-proceedings-and-debates`.
    Volume(
        "sim_congressional-record_june-17-july-13-1909_44",
        "us-income-tax-1913",
        date(1909, 6, 17),
        date(1909, 7, 13),  # S.J.Res. 40: Senate 5 July, House 12 July — both here
    ),
    _v(
        "july-24-august-29-1917_55",
        "us-prohibition-1919",
        date(1917, 7, 24),
        date(1917, 8, 29),  # S.J.Res. 17 passed the Senate 1 August
    ),
    _v(
        "december-03-1917-january-19-1918_56",
        "us-prohibition-1919",
        date(1917, 12, 3),
        date(1918, 1, 19),  # and the House 17 December
    ),
)


def already_ingested(volume: Volume) -> int:
    """How many of this volume's documents are already stored.

    Re-running the ingest has to be free. `source_documents` is UNIQUE on
    (source_key, external_id), so a second run would raise partway through and
    leave the corpus half-written.
    """
    stmt = corpus.source_documents.select().where(
        corpus.source_documents.c.source_key == SOURCE_KEY,
        corpus.source_documents.c.external_id.like(f"{volume.external_id_prefix}#%"),
    )
    with db_engine.get_engine().connect() as conn:
        return len(conn.execute(stmt).fetchall())


def ingest_volume(volume: Volume) -> tuple[int, int]:
    """Read, normalise, extract, chunk and store. Returns (documents, skipped).

    Normalisation happens before anything is stored, and what is stored is the
    normalised text — every char_span in the database refers to it. Chunking the
    normalised text while storing the raw would move every citation offset.
    """
    if not volume.path.exists():
        log.warning("%s is not in the cache yet; skipping", volume.identifier)
        return 0, 0

    existing = already_ingested(volume)
    if existing:
        log.info("%s already has %d documents; skipping", volume.identifier, existing)
        return 0, existing

    question = content.get_question(volume.question_id)
    terms = question["retrieval"]["search_terms"]

    raw = volume.path.read_text(encoding="utf-8", errors="replace")
    text = ingest.normalise(raw)
    passages = ingest.extract_passages(text, terms)

    written = 0
    for index, passage in enumerate(passages):
        ingest.store_passage(
            question_id=volume.question_id,
            source_key=SOURCE_KEY,
            external_id=f"{volume.external_id_prefix}#p{index}",
            url=volume.url,
            title=volume.title,
            published_date=volume.published_date,
            content_type="text/plain",
            passage=passage,
            role=volume.role,
        )
        written += 1

    log.info(
        "%s -> %d documents, role=%s, published_date=%s",
        volume.identifier,
        written,
        volume.role,
        volume.published_date,
    )
    return written, 0
