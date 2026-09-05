# Spike: can we actually retrieve the sources Phase 3 needs?

**Date:** 2026-09-05
**Question:** before building any pipeline infrastructure, can real source material
be retrieved that would support the `reveal` content already in `questions.json`?
**Verdict:** yes — with three findings that change the design.

Half a day of throwaway scripts against live APIs. No production code. The point
was to fail cheaply if the phase was infeasible, and to surface the surprises
before they were expensive.

## What was tested

| Source | Role | Key needed | Result |
|---|---|---|---|
| [Voteview](https://voteview.com/data) | `vote_record` | No | Works. 113,524 roll calls, back to 1789-05-16, 29 MB CSV |
| [Chronicling America](https://www.loc.gov/apis/additional-apis/chronicling-america-api/) via loc.gov | `framing` | No | Works, but the endpoint everyone documents is dead |

Two questions were used as probes: `us-medicare-1965` (ordinary legislation, the
expected happy path) and `us-prohibition-1919` (constitutional amendment, chosen
as a likely edge case — it was).

## Finding 1: the vote data is there, and it matches

Both numbers in the existing Medicare reveal were located and verified:

| Existing `reveal.actual_vote` | Voteview |
|---|---|
| "Passed 307–116 in the House" | `1965-07-27  House  307-116` ✅ |
| "68–21 in the Senate" | `1965-07-09  Senate  68-21` ✅ |

The 18th Amendment's congressional votes are also present: `1917-08-01 Senate
65-20` and `1917-12-17 House 282-128`, under bill number `SJR17`.

Note the bill-number format: **`SJR17`, not `SJRES17`**. An initial search using
the more conventional spelling returned zero rows and looked like missing data.
The bulk loader needs to normalise these.

## Finding 2: before 1990 there is no way to tell which vote is the vote

Voteview's `vote_question` field distinguishes final passage from procedural
motions. Its fill rate by era:

| Era | Roll calls | `vote_question` | `bill_number` |
|---|---|---|---|
| 1789–1899 | 40,493 | **0%** | 54% |
| 1900–1949 | 12,029 | **0%** | 89% |
| 1950–1989 | 26,096 | **1%** | 90% |
| 1990–present | 34,906 | 100% | 97% |

Medicare has **16 roll calls** under H.R. 6675. SJR17 has **9**. `bill_number` is
well populated, so they group correctly — but nothing machine-readable ranks them.

Six of the eight current questions predate 1990.

A "last vote in each chamber" heuristic is not sufficient:

```
House  last: 1965-07-27  307-116   → matches the reveal
Senate last: 1965-07-28   70-24    → the reveal cites 68-21 (1965-07-09)
```

Both are real. 307-116 is the House agreeing to the conference report; 68-21 is
the Senate's initial passage; 70-24 is the Senate agreeing to the conference
report. The heuristic picks a defensible answer, just not the one already
published.

**Which surfaces a second thing:** the existing hand-written content is
internally inconsistent — it pairs a House conference-report vote with a Senate
initial-passage vote. Not wrong, but no single rule produces it. Once a rule is
chosen, the existing eight questions need re-checking against it.

## Finding 3: constitutional amendments are a different artifact

The Prohibition reveal reads:

> "Ratified by **46 of 48 states** — only Rhode Island and Connecticut refused"

That is **state ratification**, which Voteview does not cover at all — it is a
congressional dataset. The congressional votes exist (65-20, 282-128) but they
are not the number the question cites.

So `actual_vote` spans at least three kinds of record:

- congressional passage (Voteview)
- constitutional ratification (not covered — needs another source)
- parliamentary division, for the UK question (Hansard)

The data model needs a `vote_type`, and `select_sources` has to route on it.

## Finding 4: the Chronicling America endpoint in every tutorial is dead

```
chroniclingamerica.loc.gov/search/pages/results/?...&format=json   →  308 → 404
chroniclingamerica.loc.gov/lccn/.../ocr.txt                        →  403 (Cloudflare)
www.loc.gov/collections/chronicling-america/?fo=json               →  200 ✅
```

The collection moved to the loc.gov API in 2025. Older sample code fails, and
the failure mode on the OCR path is a Cloudflare challenge page rather than an
HTTP error, so a naive client will happily store an HTML error page as source
text. The loader must validate content type.

Retrieval is **three steps**, not one:

1. search the collection → page results
2. fetch the page resource JSON → `fulltext_service` URL
3. fetch that → the OCR text

That triples the request count per page, which makes the fetch cache a
requirement rather than an optimisation.

Content quality was good. A page retrieved for the Prohibition probe:

> "Governors see Dawn of Prohibition Era — Executives of Many States Predict
> Early and Favorable Action on the Federal Constitutional Amendment"

— directly on topic, and speaking to the state-ratification angle that Voteview
cannot supply.

## Data volume

Measured, not estimated: **one newspaper page of OCR is 8,802 characters
(8.6 KB, ~2,200 tokens).**

| | Per question | 100 questions | 1,000 questions |
|---|---|---|---|
| Newspaper OCR (~15 pages) | ~130 KB | 13 MB | 130 MB |
| Congressional Record debate | ~100 KB | 10 MB | 100 MB |
| Abstracts (outcome) | ~20 KB | 2 MB | 20 MB |
| **Raw text** | **~250 KB** | **25 MB** | **250 MB** |
| Chunk copies | | 25 MB | 250 MB |
| Vectors (768-dim float32) | ~370 KB | 37 MB | 370 MB |
| **Total** | | **~90 MB** | **~900 MB** |

Plus Voteview at **29 MB, fixed** — it is one download covering every roll call
since 1789, not a per-question cost.

Storage is a non-issue at this scale. The thing that would make it large is
indiscriminate retrieval: fetching whole newspaper issues rather than the pages
that matched. Selectivity in `retrieve`, not storage limits, is the control.

Note that vectors take roughly as much space as the text they index. `halfvec`
would halve it; not worth doing at this scale.

## Consequence: how content gets validated

The spike changes the review design. Human review was going to be a single
3-minutes-per-question pass over everything. It becomes four layers, cheapest
and most deterministic first.

### 1. Structural prevention (free)

`prompt` is generated from **framing sources only** — the generator never sees
the outcome material. A model that has not been shown the result cannot leak it.

Same principle as `content.public_view()`: rule #1 is not enforced by asking the
model nicely, it is enforced by the outcome not being in scope.

### 2. Deterministic verification (free)

Rule #2 says historical claims must be grounded in the curated store rather than
freely generated. **That check must be code, not a model** — using an LLM to
decide whether an LLM's output is grounded re-introduces exactly what the rule
exists to prevent.

The generator emits claims with span citations via structured output:

```json
{"claim": "enrolled 19 million Americans in its first year",
 "value": 19000000,
 "source_document_id": 4213,
 "char_span": [8120, 8190]}
```

Then plain code verifies: the span exists in that document, the span text
contains the value (including "19,000,000" / "nineteen million" variants), and
every numeral in the generated text has a citation. Instant, testable,
actionable when it fails.

Plus, for spoilers: distinctive tokens from the outcome text must not appear in
the prompt, and a lexical blocklist (`ultimately`, `in hindsight`, `proved to
be`, `turned out`).

### 3. LLM-as-judge (~$0.001/question)

Appropriate for the one check with no deterministic ground truth: **is the
framing balanced?** Emits a structured rubric (arguments per side, loaded-language
share), not a yes/no.

**The judge must be a different model family than the generator.** Same-family
judge shares the generator's blind spots — a political lean the generator has is
one the judge reads as neutral.

### 4. Human review

Only items flagged by (2) or (3), plus a **10% random audit** to keep the judge
honest.

This takes human effort from "3 minutes on every question, forever" to roughly a
fifth of that, while making rule #2 *stronger* — it is enforced by code now
rather than by attention.

### The calibration prerequisite

A judge cannot be trusted without human-labelled data to measure it against. So
the first few dozen questions get full human review regardless — that set is
what the judge is scored on.

That score is also the Phase 3 acceptance gate, which the roadmap was missing.

## Design changes this spike produced

| Finding | Change |
|---|---|
| No `vote_question` before 1990 | `actual_vote` cannot be fully automated for 6 of 8 questions. A disambiguation step is required — human pick from a candidate list, or GovInfo Congressional Record |
| Amendments cite ratification | Add `vote_type` to the data model; route `select_sources` on it |
| Existing content inconsistent | Choose a "which vote counts" rule, document it, re-check the existing 8 |
| `SJR17` vs `SJRES17` | Bill-number normalisation in the bulk loader |
| loc.gov 3-step fetch, Cloudflare on old paths | Fetch cache is required; validate content type before storing |
| Grounding is deterministic, neutrality is not | Split validation into code checks and judge checks — do not blur them |

## Next

1. Alembic, so schema changes to existing tables are possible at all
2. pgvector image, the three source tables, `jurisdiction` and `vote_type` on questions
3. `select_sources` / `formulate_query` as pure functions with unit tests
4. Fetch layer with content-addressed cache, backoff, per-source rate limits
5. Celery worker — with `task_prerun`/`task_postrun` instrumentation from day one,
   because Phase 5's migration story needs a week of real utilisation data and
   there is no way to collect it retroactively
6. LangGraph wiring and the review gate
