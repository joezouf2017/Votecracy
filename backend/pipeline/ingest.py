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

from sqlalchemy import func, select

from shared.db import corpus
from shared.db import engine as db_engine
from shared.settings import get_settings

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
    """The canonical form. Offsets in the database refer to this, not the raw.

    NUL bytes go first, and unconditionally. GovInfo's HTML carries them, and
    Postgres rejects them outright — "text fields cannot contain NUL (0x00)
    bytes" — so a document containing one cannot be stored at all. Stripping
    here rather than in the GovInfo adapter is deliberate: this is the function
    that defines what stored text *is*, and a NUL is not part of any source's
    content under any encoding. Doing it per-adapter would mean the next source
    to deliver one fails the same way.

    Offsets are unaffected. Everything downstream chunks and cites against this
    return value, never against the raw bytes.
    """
    return _RUNS_OF_SPACE.sub(" ", _SOFT_HYPHEN.sub(r"\1\2", text.replace("\x00", "")))


@dataclass(frozen=True)
class Passage:
    """A relevant stretch of a larger document, with its origin recorded."""

    start: int  # offset into the normalised source text
    end: int
    text: str


# A term's words may be separated in the text by any run of whitespace or
# hyphens, or by nothing at all. Three different corruptions make that
# necessary, and all three were measured rather than guessed:
#
# 1. `normalise` collapses spaces and tabs but deliberately leaves newlines —
#    `chunk` snaps to `\n\n` as its best boundary, so flattening them would
#    cost every chunk edge. The Record's narrow columns then put a newline
#    every ~40 characters, and a literal multi-word match has to be lucky to
#    fit between two. "highway trust fund" went 16 hits to 22.
# 2. The same phrase appears hyphenated and unhyphenated in one volume:
#    "income tax" 804 times and "income-tax" 346 in the 1909 record.
# 3. `normalise`'s de-hyphenation rejoins a line-broken word, and a genuinely
#    hyphenated lower-case compound is indistinguishable from one. Its
#    docstring called that a residual risk not yet seen; it fires 60 times
#    across this cache, 22 of them turning "income-tax" into "incometax" —
#    on the single most important term for that question.
#
# Zero-or-more rather than one-or-more is what covers case 3. It cannot widen a
# match beyond the term's own words, and the search runs against the same
# stored string, so every char_span stays valid.
_TERM_SEPARATOR = re.compile(r"[\s-]+")


def _term_pattern(term: str) -> str:
    """A search term, matched across whatever the OCR did to the gaps in it."""
    return r"[\s-]*".join(
        re.escape(word) for word in _TERM_SEPARATOR.split(term.strip()) if word
    )


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
        for m in re.finditer(_term_pattern(t), text, re.IGNORECASE)
    )
    spans: list[list[int]] = []
    for h_start, h_end in hits:
        lo, hi = max(0, h_start - radius), min(len(text), h_end + radius)
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])

    # Snap each passage to a boundary, for the same reason chunks are snapped:
    # a fixed radius lands wherever it lands. Measured before this, 67% of
    # passages began part-way through a word, and the first and last chunk of
    # every one of them inherited that edge — which is how a chunk could still
    # open on "ost of them" after `chunk` itself was fixed.
    window = radius // 4
    snapped = []
    for lo, hi in spans:
        snapped.append(
            (
                _start_at_boundary(text, lo, min(lo + window, hi)),
                _end_at_boundary(text, hi, max(hi - window, lo + 1)),
            )
        )
    return [Passage(lo, hi, text[lo:hi]) for lo, hi in snapped]


# Where a chunk is allowed to end, best first. A paragraph break beats a
# sentence end beats a line break beats a space; a hard cut is the last resort.
_BREAKS = ("\n\n", ". ", ".\n", "\n", " ")
# How far back a boundary may be searched for, as a share of the chunk. Too
# small and most chunks still hard-cut; too large and chunks become uneven.
_SNAP_WINDOW = 0.25


def _end_at_boundary(text: str, hard_end: int, floor: int) -> int:
    """The best break at or before `hard_end`, never earlier than `floor`."""
    if hard_end >= len(text):
        return len(text)
    for sep in _BREAKS:
        found = text.rfind(sep, floor, hard_end)
        if found != -1:
            return found + len(sep)
    return hard_end


def _start_at_boundary(text: str, hard_start: int, ceiling: int) -> int:
    """The best break at or after `hard_start`, never later than `ceiling`."""
    if hard_start <= 0:
        return 0
    for sep in _BREAKS:
        found = text.find(sep, hard_start, ceiling)
        if found != -1:
            return found + len(sep)
    return hard_start


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP):
    """Chunks that end on a boundary, with their offsets into `text`.

    `size` is a ceiling, not a width. The first version cut at exactly `size`
    wherever that landed. Measured against the source volume — which is the
    scope that matters, since that is where a citation points — **271 of the
    430 chunk edges fell between two letters, 63%**. `'tute amendment'` from
    "substitute amendment", `'ost of them'` from "most of them". The embedding
    model was handed mutilated openings and endings for no gain. Snapping
    takes it to 0, at the cost of 218 slightly uneven chunks instead of 215
    even ones.

    Two earlier figures for this were wrong, both by measuring too narrow a
    scope, which is worth recording because it is the easy mistake here. "72%"
    counted chunks *starting with a lower-case letter* — a chunk opening
    cleanly on "the" fails that test. "51%" then measured cuts correctly but
    only *within* each passage, missing that `extract_passages` was itself
    landing mid-word 67% of the time, so every passage's first and last chunk
    inherited a bad edge. Only a measurement against the whole volume answers
    the actual question.

    Yields `(ordinal, start, end)`. Offsets rather than substrings because the
    caller has to store them: a chunk that cannot say where it came from
    cannot support a citation.
    """
    if size <= overlap:
        raise ValueError("chunk size must exceed the overlap, or this never advances")
    window = int(size * _SNAP_WINDOW)
    ordinal, start = 0, 0
    while start < len(text):
        end = _end_at_boundary(
            text, start + size, max(start + 1, start + size - window)
        )
        yield ordinal, start, end
        if end >= len(text):
            return
        # The next chunk starts inside the overlap, snapped forward to a word
        # boundary so it does not open mid-word either.
        back = max(start + 1, end - overlap)
        space = text.find(" ", back, end)
        ordinal, start = ordinal + 1, (space + 1 if space != -1 else back)


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
    if role not in corpus.CHUNK_ROLES:
        raise ValueError(
            f"unknown role {role!r}; expected one of {sorted(corpus.CHUNK_ROLES)}"
        )

    digest = hashlib.sha256(passage.text.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    engine = db_engine.get_engine()
    with engine.begin() as conn:
        document_id = conn.execute(
            corpus.source_documents.insert().returning(corpus.source_documents.c.id),
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
        conn.execute(corpus.source_chunks.insert(), rows)

    log.info(
        "stored %s passage %s as document %d (%d chars, %d chunks)",
        source_key,
        external_id,
        document_id,
        len(passage.text),
        len(rows),
    )
    return document_id


def embed_pending(*, batch: int = 200) -> tuple[int, int]:
    """Vector every chunk that has none for the configured model.

    Returns (embedded, already_had). Re-runnable by construction: the work list
    is "chunks with no row in chunk_embeddings for this model", so a run that
    dies partway costs only the batch it was in.

    `model` is part of `chunk_embeddings`' primary key, so this can be run once
    per model and both sets of vectors coexist — which is what makes an A/B
    possible without a migration, provided both emit `EMBEDDING_DIM`.
    """
    from pipeline import embedding

    model = get_settings().embedding_model
    engine = db_engine.get_engine()

    have = (
        select(corpus.chunk_embeddings.c.chunk_id)
        .where(corpus.chunk_embeddings.c.model == model)
        .scalar_subquery()
    )
    todo_stmt = (
        select(corpus.source_chunks.c.id, corpus.source_chunks.c.text)
        .where(corpus.source_chunks.c.id.notin_(have))
        .order_by(corpus.source_chunks.c.id)
    )
    with engine.connect() as conn:
        todo = conn.execute(todo_stmt).all()
        total = conn.execute(
            select(func.count()).select_from(corpus.source_chunks)
        ).scalar_one()

    log.info(
        "%s: %d chunks to embed, %d already done", model, len(todo), total - len(todo)
    )
    done = 0
    for start in range(0, len(todo), batch):
        window = todo[start : start + batch]
        vectors = embedding.embed_documents([text for _, text in window])
        now = datetime.now(UTC)
        rows = [
            {"chunk_id": cid, "model": model, "embedding": vec, "created_at": now}
            for (cid, _), vec in zip(window, vectors, strict=True)
        ]
        with engine.begin() as conn:
            conn.execute(corpus.chunk_embeddings.insert(), rows)
        done += len(rows)
        log.info("%s: stored %d/%d", model, done, len(todo))
    return done, total - len(todo)


def already_stored(source_key: str, external_id: str, question_id: str) -> bool:
    """Has this exact document been ingested for this question already?

    One implementation rather than the four that had grown up in the adapters,
    all asking the same question of the same three columns. Those three are also
    exactly `uq_source_documents_source_external`, so this is the check that
    corresponds to the constraint — including `question_id`, without which the
    same volume could not serve a second question at all.
    """
    stmt = (
        select(func.count())
        .select_from(corpus.source_documents)
        .where(
            corpus.source_documents.c.source_key == source_key,
            corpus.source_documents.c.external_id == external_id,
            corpus.source_documents.c.question_id == question_id,
        )
    )
    with db_engine.get_engine().connect() as conn:
        return conn.execute(stmt).scalar_one() > 0
