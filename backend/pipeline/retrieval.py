"""Rule #1 as a query, not as a comment.

    pre-vote  = published_date <  decision_date  AND  role = 'framing'
    post-vote = published_date >= decision_date

That predicate is written out in CLAUDE.md, in `docs/architecture.md`, and in
the column comments on `source_chunks`. Until this module it was written
nowhere that runs. Every consumer assembled its own scope and passed the result
in as a plain argument -- `spoilers.forbidden(reveal, pre_vote_text)` takes the
pre-vote material as text, and `grounding.verify(claim, document_text)` takes
the document as text and never resolves the `document_id` the claim carries.

Both of those fail *open*, which is why the predicate needed an owner:

- Drop `AND role = 'framing'` from the text handed to `spoilers` and it does
  not error. It returns a smaller forbidden set -- possibly empty -- and an
  empty set reads exactly like "this generation is clean".
- Hand `grounding.verify` the wrong document and it returns `Verdict(True)`
  whenever the cited number happens to appear somewhere in it.

Neither has a caller yet, so the first one written would have invented the
predicate a fourth time. This module is that caller's only way in.

**Nothing here takes a scope as a string.** `Scope` is an enum and a non-member
raises, because the failure being prevented is a typo or a stale literal
selecting the wrong half of the corpus and looking like a normal empty result.
Likewise an unknown `question_id` raises rather than returning nothing: "no
such question" and "this question has no material yet" are the same empty list,
and only one of them is safe to carry on from.

This lives in `pipeline/` because the pipeline is its only caller today. The
Phase 3 chatbot will need `Scope.PRE_VOTE` too and is a third path that is
neither `game/` nor `pipeline/` -- when that package appears, this module is
what it should import rather than growing its own copy of the predicate.
"""

import enum
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, bindparam, func, select

from pipeline import grounding
from shared import content
from shared.db import corpus
from shared.db import engine as db_engine
from shared.db.engine import EMBEDDING_DIM, Vector

# The only role a player who has not voted may ever see. A whitelist: a
# rejected amendment's vote counts are `vote_record` and are published *before*
# the decision, so a date filter alone lets them through and `role != 'outcome'`
# would too. See the column comment on `source_chunks.role`.
PRE_VOTE_ROLE = "framing"


class UnknownQuestion(KeyError):
    """No such question id, so no decision date, so no boundary to cut on."""


class Scope(enum.Enum):
    """Which side of the decision the caller is allowed to see.

    An enum rather than a string because every consumer of this module is
    enforcing rule #1, and the cost of a mistyped scope is silently serving
    outcome material to a player who has not voted yet.
    """

    PRE_VOTE = "pre_vote"
    POST_VOTE = "post_vote"


@dataclass(frozen=True)
class Chunk:
    """One retrieved chunk, carrying what a citation needs to be checkable."""

    id: int
    document_id: int
    role: str
    published_date: date
    char_start: int
    char_end: int
    text: str


def _decision_date(question_id: str) -> date:
    decision = content.decision_date(question_id)
    if decision is None:
        raise UnknownQuestion(
            f"no question {question_id!r}, so no decision_date to scope on"
        )
    return decision


def _chunk_predicate(question_id: str, scope: Scope):
    """The one place the pre/post-vote boundary is expressed."""
    decision = _decision_date(question_id)
    c = corpus.source_chunks.c
    if scope is Scope.PRE_VOTE:
        return and_(
            c.question_id == question_id,
            c.published_date < decision,
            c.role == PRE_VOTE_ROLE,
        )
    if scope is Scope.POST_VOTE:
        return and_(c.question_id == question_id, c.published_date >= decision)
    raise TypeError(f"scope must be a Scope, got {scope!r}")


_CHUNK_COLUMNS = (
    corpus.source_chunks.c.id,
    corpus.source_chunks.c.document_id,
    corpus.source_chunks.c.role,
    corpus.source_chunks.c.published_date,
    corpus.source_chunks.c.char_start,
    corpus.source_chunks.c.char_end,
    corpus.source_chunks.c.text,
)


def chunks(question_id: str, scope: Scope, *, limit: int | None = None) -> list[Chunk]:
    """Every chunk this scope admits, by document then position within it.

    Ordered rather than left to the planner because the generator's prompt is
    built from this, and a prompt that reshuffles between runs makes a
    regression impossible to read.
    """
    stmt = (
        select(*_CHUNK_COLUMNS)
        .where(_chunk_predicate(question_id, scope))
        .order_by(corpus.source_chunks.c.document_id, corpus.source_chunks.c.ordinal)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    with db_engine.get_engine().connect() as conn:
        return [Chunk(**row._mapping) for row in conn.execute(stmt)]


def scope_text(question_id: str, scope: Scope) -> str:
    """The scope's material as one string, for the token-level checks.

    This is what `spoilers.forbidden` wants for its `pre_vote_text` argument.
    Built from *chunks*, never from documents, and the difference is the whole
    point of `role`: one Congressional Record page holds an amendment's
    description and its vote counts, so the document is a mix of `framing` and
    `vote_record` while only the framing chunks of it are pre-vote material.
    Concatenating documents here would leak margins into the very set that
    defines what a leaked margin is.
    """
    return "\n\n".join(c.text for c in chunks(question_id, scope))


def document_text(document_id: int, *, question_id: str, scope: Scope) -> str | None:
    """The full text of a cited document, or None if this scope cannot see it.

    Scoped on the document's own `published_date`, which is the only filter
    that applies: `role` lives on the chunk, and a document legitimately
    contains chunks of more than one role.

    So the text returned here may include spans a pre-vote player must not see.
    That is correct for checking a citation -- rule #2 asks whether the source
    says what was claimed -- and wrong for anything else. Use `scope_text` to
    build material, and this only to verify a span against its source.
    """
    decision = _decision_date(question_id)
    d = corpus.source_documents.c
    if scope is Scope.PRE_VOTE:
        where = and_(
            d.id == document_id,
            d.question_id == question_id,
            d.published_date < decision,
        )
    elif scope is Scope.POST_VOTE:
        where = and_(
            d.id == document_id,
            d.question_id == question_id,
            d.published_date >= decision,
        )
    else:
        raise TypeError(f"scope must be a Scope, got {scope!r}")
    with db_engine.get_engine().connect() as conn:
        return conn.execute(select(d.text).where(where)).scalar_one_or_none()


def verify(claim: grounding.Claim, *, question_id: str, scope: Scope):
    """`grounding.verify` with the claim's document resolved, not supplied.

    The span check is unchanged and still involves no model. What changes is
    that the document is fetched *through* the scope predicate, so a claim
    citing something this scope cannot see fails instead of being checked
    against whatever text the caller happened to pass. A pre-vote prompt citing
    a document published after the decision is a rule #1 leak, and before this
    it was a rule #2 pass.
    """
    text = document_text(claim.document_id, question_id=question_id, scope=scope)
    if text is None:
        return grounding.Verdict(
            False,
            f"document {claim.document_id} is not in {scope.value} scope for "
            f"question {question_id!r}",
        )
    return grounding.verify(claim, text)


def nearest(
    question_id: str,
    scope: Scope,
    vector: list[float],
    *,
    model: str,
    k: int = 8,
) -> list[Chunk]:
    """The k nearest chunks *within the scope*, by cosine distance.

    The scope predicate is a plain WHERE and there is deliberately no ANN
    index, so this is an exact scan over the few hundred chunks one question
    has: measured at 1.8 ms against 382 ms for the same search unscoped.
    IVFFlat and HNSW search globally and filter afterwards, so the tighter the
    predicate the fewer rows survive -- the opposite of what is wanted when the
    predicate is a safety boundary rather than an optimisation.

    `model` has no default. `chunk_embeddings` is keyed on (chunk_id, model)
    so two models can coexist during a re-embed; a query that forgot to say
    which one would silently mix two vector spaces and just rank badly.
    """
    if len(vector) != EMBEDDING_DIM:
        raise ValueError(
            f"expected a {EMBEDDING_DIM}-dimension vector, got {len(vector)}"
        )
    probe = bindparam("probe", value=vector, type_=Vector(EMBEDDING_DIM))
    distance = func.cosine_distance(corpus.chunk_embeddings.c.embedding, probe)
    stmt = (
        select(*_CHUNK_COLUMNS)
        .join(
            corpus.chunk_embeddings,
            corpus.chunk_embeddings.c.chunk_id == corpus.source_chunks.c.id,
        )
        .where(
            and_(
                _chunk_predicate(question_id, scope),
                corpus.chunk_embeddings.c.model == model,
            )
        )
        .order_by(distance)
        .limit(k)
    )
    with db_engine.get_engine().connect() as conn:
        return [Chunk(**row._mapping) for row in conn.execute(stmt)]
