"""Regenerate the measured parts of `docs/`, so they cannot go quietly stale.

    ./.venv/Scripts/python.exe docs/refresh.py            # rewrite the blocks
    ./.venv/Scripts/python.exe docs/refresh.py --check     # fail if any is stale

**Why this exists.** A measured number typed into prose has no link to whatever
produced it, so nothing can tell when it stops being true — including whoever
wrote it. `data-acquisition.md`'s corpus figures were updated, then invalidated
by an ingest run twenty-three minutes later, and neither the tests nor CI nor
the author noticed.

That is the same failure this project refuses everywhere else: a composite
foreign key instead of a convention, `test_layering` instead of remembering,
`role` derived instead of declared. Documentation was the one place the
discipline had not been applied. `cost-model.py` already had the right shape —
the script is the source of truth and the document quotes it — and this
generalises it.

**Prose stays hand-written.** Reasoning, decisions and lessons do not go stale:
"loc.gov accepts boolean operators and does not honour them" is true forever.
Only the measurements are generated, and they are the only part that rots.

**Not a pytest test, deliberately.** The suite is container-free by design —
Postgres and Redis are replaced by in-process stand-ins — and these figures have
to be measured against the live database. That makes this the same category as
`alembic check`: needs a real database, lives outside the suite, run before
committing a claim about the corpus.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select  # noqa: E402

from pipeline import retrieval as r  # noqa: E402
from pipeline.grounding import numbers_in  # noqa: E402
from shared import content  # noqa: E402
from shared.db import corpus  # noqa: E402
from shared.db import engine as db_engine  # noqa: E402

DOCS = Path(__file__).resolve().parent

# A generated block is delimited so a human can see exactly which lines are not
# theirs to edit. Anything outside the markers is hand-written and untouched.
_BLOCK = "<!-- generated: {name} -->\n{body}\n<!-- /generated -->"
_PATTERN = "<!-- generated: {name} -->.*?<!-- /generated -->"


def corpus_status() -> str:
    with db_engine.get_engine().connect() as conn:
        documents = conn.execute(
            select(func.count()).select_from(corpus.source_documents)
        ).scalar_one()
        chunks = conn.execute(
            select(func.count()).select_from(corpus.source_chunks)
        ).scalar_one()
        by_source = conn.execute(
            select(corpus.source_documents.c.source_key, func.count())
            .group_by(corpus.source_documents.c.source_key)
            .order_by(func.count().desc())
        ).all()
        vectors = conn.execute(
            select(corpus.chunk_embeddings.c.model, func.count()).group_by(
                corpus.chunk_embeddings.c.model
            )
        ).all()

    lines = [
        f"**{documents:,} documents · {chunks:,} chunks · "
        f"{len(by_source)} document sources.** Voteview is a fifth source and "
        "appears nowhere in this table: it supplies candidates, not documents.",
        "",
        "| source | documents |",
        "|---|---|",
    ]
    lines += [f"| `{k}` | {n:,} |" for k, n in by_source]
    lines += ["", "| embedding model | vectors |", "|---|---|"]
    lines += [f"| `{m}` | {n:,} |" for m, n in vectors]
    return "\n".join(lines)


def question_coverage() -> str:
    rows = []
    for q in content.all_questions():
        pre = r.chunks(q["id"], r.Scope.PRE_VOTE)
        post = r.chunks(q["id"], r.Scope.POST_VOTE)
        decision = content.decision_date(q["id"])
        violations = sum(1 for c in pre if c.published_date >= decision) + len(
            {c.role for c in pre} - {"framing"}
        )
        reach = (max(c.published_date for c in post) - decision).days if post else None
        rows.append((q["id"], len(pre), len(post), reach, violations))
    rows.sort(key=lambda x: -x[1])

    out = [
        "| question | pre-vote | post-vote | outcome reaches | rule #1 violations |",
        "|---|---|---|---|---|",
    ]
    out += [
        f"| {qid} | {pre:,} | {post:,} | "
        f"{'—' if reach is None else f'+{reach:,}d'} | {bad} |"
        for qid, pre, post, reach, bad in rows
    ]
    total_bad = sum(row[4] for row in rows)
    out += [
        "",
        f"**Rule #1 violations across all questions: {total_bad}.** Every pre-vote "
        "chunk is `role='framing'` with `published_date` strictly before its "
        "decision.",
    ]
    return "\n".join(out)


def outcome_support() -> str:
    out = [
        "| question | outcome figures the corpus carries | still missing |",
        "|---|---|---|",
    ]
    for q in content.all_questions():
        post = numbers_in(r.scope_text(q["id"], r.Scope.POST_VOTE))
        claimed = sorted(numbers_in(q["reveal"]["outcome"]))
        missing = [n for n in claimed if n not in post]
        held = len(claimed) - len(missing)
        shown = ", ".join(f"{n:,.0f}" for n in missing) or "—"
        out.append(f"| {q['id']} | {held}/{len(claimed)} | {shown} |")
    out += [
        "",
        "**This table overstates support and is kept anyway, because the way it "
        "overstates is the point.** It asks whether a value appears *anywhere* in "
        "the post-vote corpus, not whether it appears as the thing the sentence "
        "claims. Measured live: NHS's `5,000,000` reads as supported, and the two "
        'actual occurrences are "£5 million above what was originally '
        'anticipated" and "an additional sum of nearly £5 million is required" '
        "— money, not dental treatments.",
        "",
        "That is exactly why [`evaluation.md`](evaluation.md) requires a quote "
        "carrying the value in its own sentence's context. Until the generator "
        "enforces it, read this as an upper bound.",
    ]
    return "\n".join(out)


BLOCKS = {
    "data-acquisition.md": {
        "corpus-status": corpus_status,
        "question-coverage": question_coverage,
    },
    "content-audit.md": {
        "outcome-support": outcome_support,
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if any block is out of date, and change nothing",
    )
    args = parser.parse_args()

    stale, written, missing = [], [], []
    for filename, blocks in BLOCKS.items():
        path = DOCS / filename
        text = original = path.read_text(encoding="utf-8")
        for name, build in blocks.items():
            pattern = re.compile(_PATTERN.format(name=re.escape(name)), re.S)
            if not pattern.search(text):
                missing.append(f"{filename}:{name}")
                continue
            fresh = _BLOCK.format(name=name, body=build())
            current = pattern.search(text).group(0)
            if current != fresh:
                stale.append(f"{filename}:{name}")
                # Spliced rather than `pattern.sub`, which would read backslashes
                # and `\g<...>` in generated text as replacement syntax — and
                # generated text is full of Windows paths and regex snippets.
                start, end = pattern.search(text).span()
                text = text[:start] + fresh + text[end:]
        if text != original and not args.check:
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(filename)

    for name in missing:
        print(f"  no such block: {name}")
    if args.check:
        for name in stale:
            print(f"  STALE  {name}")
        if stale or missing:
            print(
                f"\n{len(stale)} stale, {len(missing)} missing. "
                "Run docs/refresh.py to update."
            )
            return 1
        print("every generated block is current")
        return 0

    if stale:
        print(f"refreshed {len(stale)} block(s) in {', '.join(written)}")
    else:
        print("every generated block was already current")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
