"""Turn a fetched source into rows: normalise, extract, chunk, store.

Three steps between a download and something retrievable, each with a reason
it cannot be skipped.

**Normalise first, and store what you normalised.** The rule #2 check verifies
a `(document_id, char_span)` citation against `source_documents.text`.
Normalising moves every offset after the first edit, so storing the raw text
and chunking the normalised version would point every citation into a
different string. The raw download belongs in the fetch cache; what goes in
the database is the text we cite against.

**Extract before chunking.** One Congressional Record volume is 14.3M
characters — two weeks of everything Congress did. Measured against the
Medicare terms, 1.28% of it is relevant: 46 passages, 184K characters, about
183 chunks instead of 14,307. Chunking whole volumes would fill the index with
strawberry toasts and bury the debate.

**One document per passage.** Concatenating disjoint passages would let a
citation span straddle two unrelated pieces of the record, which is precisely
the kind of citation that looks valid and proves nothing.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

import db

log = logging.getLogger(__name__)

# Line-break hyphenation, rejoined only before a lower-case letter. That
# restriction is what protects real compounds: "King-Anderson" has a capital
# after the hyphen and is left alone. Measured on one 1965 volume, this
# recovers 66,119 splits — "hospital" goes from 814 occurrences to 889 — while
# King-Anderson, Kerr-Mills, Blue Cross and old-age all come through unchanged.
#
# The residual risk is a lower-case compound that breaks at its own hyphen:
# `old-\nage` would become `oldage`. Not seen in the sampled volume.
_SOFT_HYPHEN = re.compile(r"([a-z])-[ \t]*\n[ \t]*([a-z])")
_RUNS_OF_SPACE = re.compile(r"[ \t]+")

CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150
# How far either side of a term hit a passage reaches. Wide enough to carry an
# argument, and known to be imperfect: the Congressional Record is a stream of
# unrelated items, so an edge can open mid-way through a different subject.
PASSAGE_RADIUS = 1500


def normalise(text: str) -> str:
    """The canonical form. Offsets in the database refer to this, not the raw."""
    return _RUNS_OF_SPACE.sub(" ", _SOFT_HYPHEN.sub(r"\1\2", text))


@dataclass(frozen=True)
class Passage:
    """A relevant stretch of a larger document, with its origin recorded."""

    start: int  # offset into the normalised source text
    end: int
    text: str


def extract_passages(text: str, terms: list[str], radius: int = PASSAGE_RADIUS):
    """Passages around every mention of any term, overlaps merged.

    Merging matters: two hits 200 characters apart would otherwise produce two
    passages sharing almost all their text, and the same sentence would be
    indexed and citable twice under different document ids.
    """
    # The radius is measured from both ends of the match, not just the start:
    # "hospital insurance for the aged" is 31 characters, and anchoring only on
    # the start would eat that much off the trailing context.
    hits = sorted(
        (m.start(), m.end())
        for t in terms
        for m in re.finditer(re.escape(t), text, re.IGNORECASE)
    )
    spans: list[list[int]] = []
    for h_start, h_end in hits:
        lo, hi = max(0, h_start - radius), min(len(text), h_end + radius)
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    return [Passage(lo, hi, text[lo:hi]) for lo, hi in spans]


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP):
    """Fixed-width chunks with their offsets into `text`.

    Yields `(ordinal, start, end)`. Offsets rather than substrings because the
    caller has to store them: a chunk that cannot say where it came from cannot
    support a citation.
    """
    if size <= overlap:
        raise ValueError("chunk size must exceed the overlap, or this never advances")
    ordinal, start = 0, 0
    while start < len(text):
        end = min(start + size, len(text))
        yield ordinal, start, end
        if end == len(text):
            return
        ordinal, start = ordinal + 1, end - overlap


def store_passage(
    *,
    question_id: str,
    source_key: str,
    external_id: str,
    url: str,
    title: str | None,
    published_date: date,
    content_type: str,
    passage: Passage,
    role: str,
) -> int:
    """Write one passage and its chunks. Returns the document id.

    `published_date` is the caller's responsibility and must come from the
    record, never from the query that found it — loc.gov accepts a date filter
    and ignores it, and GovInfo applies its own to the volume rather than the
    contents.
    """
    if role not in db.CHUNK_ROLES:
        raise ValueError(
            f"unknown role {role!r}; expected one of {sorted(db.CHUNK_ROLES)}"
        )

    digest = hashlib.sha256(passage.text.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    engine = db.get_engine()
    with engine.begin() as conn:
        document_id = conn.execute(
            db.source_documents.insert().returning(db.source_documents.c.id),
            {
                "question_id": question_id,
                "source_key": source_key,
                "external_id": external_id,
                "url": url,
                "title": title,
                "published_date": published_date,
                "content_type": content_type,
                "sha256": digest,
                "text": passage.text,
                "fetched_at": now,
            },
        ).scalar_one()

        rows = [
            {
                "document_id": document_id,
                # Copied down so the pre-vote filter is one indexed predicate.
                # The composite FK rejects any row that disagrees with its
                # parent, so this cannot drift.
                "question_id": question_id,
                "published_date": published_date,
                "role": role,
                "ordinal": ordinal,
                "char_start": start,
                "char_end": end,
                "text": passage.text[start:end],
            }
            for ordinal, start, end in chunk(passage.text)
        ]
        conn.execute(db.source_chunks.insert(), rows)

    log.info(
        "stored %s passage %s as document %d (%d chars, %d chunks)",
        source_key,
        external_id,
        document_id,
        len(passage.text),
        len(rows),
    )
    return document_id
