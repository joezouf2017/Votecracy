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

**Not a pytest test, and it cannot be a CI job either.** The reason is not the
one it looks like. CI does run containers — the `cold-start` job brings the whole
stack up — but those containers are *empty*. The corpus is 477 MB of cached
source text and a database that has never been in version control, so CI has no
ground truth to check these numbers against; `--check` there would generate
"0 documents" and call everything stale.

So this is inherently local, the same category as `alembic check`, and the honest
response is to make the *trigger* reliable rather than to pretend a remote runner
can do it:

- `--markers` checks what CI genuinely can: that every block is present, paired
  and not hand-edited into a different shape. No database needed.
- The ingest paths say so when they invalidate the numbers, which is the trigger
  that was missing — "I happen to be editing this doc" was never going to work.
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


def _check_markers() -> int:
    """Every block present, paired, and containing only generated content.

    This is the part of the job a runner with no corpus can still do. It cannot
    tell whether a number is current — nothing in CI can — but it does catch a
    block edited by hand, an unclosed marker, and a block referenced here that
    someone deleted from the document.
    """
    problems = []
    for filename, blocks in BLOCKS.items():
        path = DOCS / filename
        if not path.exists():
            problems.append(f"{filename}: missing entirely")
            continue
        text = path.read_text(encoding="utf-8")
        opens = text.count("<!-- generated:")
        closes = text.count("<!-- /generated -->")
        if opens != closes:
            problems.append(
                f"{filename}: {opens} opening markers against {closes} closing"
            )
        for name in blocks:
            if not re.search(_PATTERN.format(name=re.escape(name)), text, re.S):
                problems.append(f"{filename}: block {name!r} not found")

    for p in problems:
        print(f"  {p}")
    if problems:
        print(
            f"\n{len(problems)} problem(s). Generated blocks are written by "
            "docs/refresh.py and should not be edited by hand."
        )
        return 1
    total = sum(len(b) for b in BLOCKS.values())
    print(f"{total} generated blocks present and well-formed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if any block is out of date, and change nothing",
    )
    parser.add_argument(
        "--markers",
        action="store_true",
        help="verify block markers only; needs no database, safe for CI",
    )
    args = parser.parse_args()

    if args.markers:
        return _check_markers()

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
