"""The content pipeline's source store — the offline half of the schema.

Nothing on the vote path reads these tables. They share `metadata` with the
vote log only so one Alembic chain owns the whole schema; see `db/__init__`
for why both halves must be imported together.
"""

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from shared.db.engine import EMBEDDING_DIM, Vector, metadata

# --- Phase 3: the content pipeline's source store ---------------------------
#
# Three tables for the three storage layers, ordered by how expensive they are
# to lose:
#
#   source_documents   only rebuildable by going back to the network — durable
#   source_chunks      rebuildable from documents, locally, for free
#   chunk_embeddings   rebuildable from chunks, but costs embedding API calls
#
# Embeddings are a separate table rather than a column on chunks so that
# "drop the vector index and rebuild it" is an operation you can actually
# perform. If dropping the vectors meant re-fetching from the network, the
# layering would be wrong.

source_documents = Table(
    "source_documents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("question_id", String(128), nullable=False, index=True),
    # voteview | govinfo | loc | fred | pubmed — see `select_sources`.
    Column("source_key", String(64), nullable=False),
    # Whatever identifies this document to that source: LCCN + page, a GovInfo
    # package id, a PMID. Paired with source_key it's the natural key.
    Column("external_id", String(256), nullable=False),
    Column("url", Text, nullable=False),
    Column("title", Text),
    # NOT NULL on purpose. The pre/post-vote boundary is `published_date <
    # decision_date`, so a document with no date can't be placed on either
    # side of it — and a nullable column invites a query that treats NULL as
    # "before", which is the failure that leaks an outcome. Making it required
    # pushes the problem to where it belongs: if the fetcher can't establish a
    # date, it must not store the document. Every whitelisted source carries
    # one, so this costs nothing today.
    Column("published_date", Date, nullable=False),
    # Recorded because the spike found loc.gov's old OCR path answers a
    # Cloudflare challenge *page* rather than an HTTP error — a naive client
    # stores the HTML and calls it source text. Validate before writing.
    Column("content_type", String(128), nullable=False),
    Column("sha256", String(64), nullable=False),  # content-addressed cache key
    Column("text", Text, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    # `question_id` is part of this on purpose, and leaving it out was a bug.
    #
    # A cached volume is not a per-question asset. One Congressional Record
    # volume is two to three weeks of everything Congress did, and a question
    # extracts 1-11% of it. The same volume legitimately serves several
    # questions — the March 1956 volume was fetched for the highway bill and
    # also contains every other roll call of that fortnight.
    #
    # Extraction is per question, so the same volume yields *different*
    # passages under different search terms. Those are genuinely different
    # documents. Without `question_id` here they collide on `#p0` and the
    # second question simply cannot be ingested, which quietly makes every
    # download single-use.
    UniqueConstraint(
        "source_key",
        "external_id",
        "question_id",
        name="uq_source_documents_source_external",
    ),
    # Not useful on its own — it exists so source_chunks can point a composite
    # foreign key at these three columns. See the note there.
    UniqueConstraint(
        "id", "question_id", "published_date", name="uq_source_documents_scope_key"
    ),
)

# What a chunk's `role` may be. Values are ordered from safest to most
# dangerous, which is also roughly how they're used.
CHUNK_ROLES = ("framing", "vote_record", "outcome")

source_chunks = Table(
    "source_chunks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("document_id", Integer, nullable=False),
    # question_id and published_date are copied down from the parent document
    # so that the pre-vote filter is one indexed predicate on the table the
    # retriever actually queries. Left on source_documents, every retrieval
    # would need a join — and the failure mode of forgetting that join is
    # silently returning outcome material, which is rule #1.
    #
    # The composite FK below is what makes the copy safe: the three columns
    # are a foreign key into (id, question_id, published_date) on the parent,
    # so a chunk whose scope disagrees with its document cannot be written at
    # all. That's a constraint, not a convention backed by a test.
    Column("question_id", String(128), nullable=False),
    Column("published_date", Date, nullable=False),
    # framing | vote_record | outcome. Date alone can't separate an amendment's
    # *description* (pre-vote — knowing what else was on the table doesn't
    # reveal whether the bill passed) from its *vote counts* (post-vote —
    # margins leak the outcome), because both sit in the same Congressional
    # Record document on the same day.
    #
    # This is a second filter, never an alternative one. Pre-vote scope is
    # `published_date < decision_date AND role = 'framing'` — a conjunction, so
    # role can only ever remove material from the date window, never admit
    # anything published after the decision.
    #
    # A whitelist rather than `role != 'outcome'`, and the difference is real:
    # a rejected amendment's vote counts are `vote_record` and are published
    # *before* the decision, so neither the date filter nor an exclusion of
    # `outcome` would catch them — and margins are exactly what leaks the
    # result. Only `framing` reaches a player who hasn't voted.
    Column("role", String(32), nullable=False),
    Column("ordinal", Integer, nullable=False),  # position within the document
    # Offsets into source_documents.text. Not optional: rule #2 is enforced by
    # code checking that a claim's (document_id, char_span) exists and that the
    # span text contains the cited value. Without offsets that check would have
    # to be an LLM judging an LLM, which is the thing rule #2 exists to prevent.
    Column("char_start", Integer, nullable=False),
    Column("char_end", Integer, nullable=False),
    Column("text", Text, nullable=False),
    ForeignKeyConstraint(
        ["document_id", "question_id", "published_date"],
        [
            "source_documents.id",
            "source_documents.question_id",
            "source_documents.published_date",
        ],
        name="fk_source_chunks_document_scope",
        ondelete="CASCADE",
        # A corrected publication date on the document propagates to its
        # chunks rather than leaving them behind on the old boundary.
        onupdate="CASCADE",
    ),
    UniqueConstraint(
        "document_id", "ordinal", name="uq_source_chunks_document_ordinal"
    ),
    Index("ix_source_chunks_question_published", "question_id", "published_date"),
)

chunk_embeddings = Table(
    "chunk_embeddings",
    metadata,
    Column(
        "chunk_id",
        Integer,
        ForeignKey("source_chunks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # Part of the primary key so vectors from two models can coexist during a
    # re-embed, and so "which model produced this" can't be lost. Swapping the
    # embedding model without re-embedding doesn't error — queries keep working
    # and just return worse matches — so it has to be impossible to forget.
    Column("model", String(128), primary_key=True),
    Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# No ANN index (HNSW/IVFFlat) yet, deliberately. At a few thousand chunks an
# exact scan is both faster and exactly correct, and IVFFlat built on an empty
# table is actively harmful — it needs rows to pick its centroids. Add one in
# a migration of its own once there's enough data to measure recall against.


# --- Step 4: candidates, and the one column a re-run must never touch --------
#
# `voteview.candidates()` recomputes 10,593 ranked candidates from the bulk
# corpus in 0.6 seconds, so the candidates themselves are not worth storing for
# their own sake. What has no other home is **the record that a human looked at
# one**. That is the whole reason this table exists.
#
# Which makes `status` the only interesting thing here: re-reading the corpus
# refreshes every derived column and must leave the review columns exactly as
# they were. A re-run that reset a rejection to `pending` would silently undo
# human work and look like nothing happened. `upsert` enforces that by never
# naming those columns in an update.

CANDIDATE_STATUSES = ("pending", "approved", "rejected")

candidates = Table(
    "candidates",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # The natural key. Measured across the whole corpus: (congress,
    # bill_number) is unique over all 10,593 candidates, because `candidates()`
    # already collapses a bill's roll calls to the one worth asking about.
    Column("congress", Integer, nullable=False),
    Column("bill_number", String(32), nullable=False),
    # --- refreshed from the corpus on every run -----------------------------
    Column("chamber", String(16), nullable=False),
    Column("vote_date", Date, nullable=False),
    Column("vote_type", String(64), nullable=False),
    Column("subject", String(64)),
    Column("description", Text),
    # The margin. Recorded because review needs it and generation must not see
    # it — `voteview.for_prompt_generation` is the whitelist projection that
    # keeps these two columns away from the model, the same shape as
    # `content.public_view`.
    Column("yea", Integer, nullable=False),
    Column("nay", Integer, nullable=False),
    # Why this vote was ranked where it was, kept so a reviewer can disagree
    # with the ranking rather than only with the result.
    Column("attention_percentile", Float, nullable=False),
    Column("closeness", Float, nullable=False),
    Column("coalition_break", Float, nullable=False),
    # What `select_sources` could not supply for this candidate, if anything.
    Column("gaps", Text),
    # --- human review; a corpus re-run must not write these -----------------
    Column("status", String(16), nullable=False, server_default="pending"),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("review_note", Text),
    # --- provenance ----------------------------------------------------------
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("refreshed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("congress", "bill_number", name="uq_candidates_bill"),
    # Reviewing is SQL, matching how `daily_questions` is handled — no admin UI.
    # The query is "the highest-ranked thing nobody has looked at yet", so the
    # index is on that pair.
    Index("ix_candidates_status_rank", "status", "attention_percentile"),
)
