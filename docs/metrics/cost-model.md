# What this costs to run

**Date:** 2026-09-05
**Reproduce:** `./.venv/Scripts/python.exe docs/metrics/cost-model.py`

Prices from [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing),
checked 2026-09-05. Corpus sizes measured from the Medicare slice in the local
database. Everything offline uses the Batch API, which is half price and covers
the entire pipeline and the entire eval run.

## Measured, per question

| | |
|---|---|
| chunks | 218 |
| pre-vote corpus | 183,791 chars ≈ **45,947 tokens** |
| tokens per chunk | ~210 |
| a chatbot turn (k=8 + system prompt) | ~3,180 tokens in, ~300 out |

The one corpus number with no measurement behind it is the outcome share —
nothing post-vote has been fetched yet, so the model assumes it adds half again
(69K tokens per question all told).

## Building the content — one-off

| questions | embed | generate | judge | **total** | tokens |
|---|---|---|---|---|---|
| 8 | $0.04 | $0.07 | $0.00 | **$0.12** | 0.6M embed, 0.4M in, 0.04M out |
| 100 | $0.52 | $0.90 | $0.05 | **$1.46** | 6.9M embed, 4.7M in, 0.48M out |
| 500 | $2.58 | $4.50 | $0.24 | **$7.32** | 34.5M embed, 23.5M in, 2.4M out |

Flash-Lite 3.1 on the Batch API. On full Flash 3.7 the 500-question build is
$15.90 — the model choice moves the one-off bill by $8, which is not a
decision worth agonising over.

Generation dominates embedding despite embedding processing 34.5M tokens
against generation's 23.5M, because output tokens cost 6x input.

## The acceptance gate — recurring

One full pass of all three eval sets:

| questions | cases | turns | cost | same run with no retrieval |
|---|---|---|---|---|
| 8 | 480 | 960 | **$0.60** | $5.91 (10x) |
| 100 | 6,000 | 12,000 | **$7.47** | $73.87 (10x) |
| 500 | 30,000 | 60,000 | **$37.35** | $369.35 (10x) |

At today's 8 questions: **$17.93/month nightly, $2.39/month weekly.**

**Retrieval is worth 10x here, and that is the argument for it that has nothing
to do with quality.** The right-hand column is the same eval with the whole
pre-vote corpus stuffed into every prompt instead of the k=8 nearest chunks. The
scoped query in `pipeline/retrieval.py` is what keeps a nightly eval at the
price of a coffee rather than a phone bill.

## The live chatbot — ongoing, and the only line that scales

Synchronous, so no batch discount.

| conversations/day | per day | per month |
|---|---|---|
| 50 | $0.37 | $11.21 |
| 500 | $3.73 | $112.05 |
| 5,000 | $37.35 | $1,120.50 |

**$0.0075 per conversation**, at six turns each.

This is the number that matters. Content is built once; the eval runs on a
schedule you control; the chatbot's cost is a function of how many people play.
At 5,000 conversations a day it is 150x the entire content build. Anything that
caps it — a turn limit, caching the system prompt, refusing to re-retrieve on
follow-ups — pays for itself faster than anything in the pipeline.

## So: settle the paid-tier decision by paying

[`gemini-quotas.md`](gemini-quotas.md) leaves this open. The numbers close it.

The free tier's binding limit is **20 requests per day** — a limit on *requests*,
not on spend. One smoke subset of the eval exceeds it, so the acceptance gate
cannot run at all. Removing that cap costs, at the project's real scale,
**$2.39/month for a weekly full eval and a one-off $7.32 to build 500
questions.**

Cutting the eval plan to fit the free tier would trade the phase's only
deliverable for less than the price of a sandwich. Pay.

## Caveats worth naming

- **`CHARS_PER_TOKEN = 4` is English-prose optimistic.** OCR of 1965 newsprint
  tokenises worse — mangled words split into more pieces. Worth measuring once
  the fetch layer is real; a 25% miss here moves every figure by 25%.
- **The judge is priced at Google rates, and it must not be a Google model.**
  `docs/evaluation.md` requires a different model family from the generator,
  because a same-family judge shares the generator's blind spots. The line is
  $0.24 at 500 questions either way, so the design constraint costs nothing —
  but the figure above is a placeholder for whatever gets chosen.
- **Set 2 and Set 3 sizes are assumed at 10 per question.** Only Set 1's ~40
  attack shapes is stated in the eval plan.
- **No infrastructure here.** This is model spend only: RDS, ElastiCache and
  Fargate are Phase 6 and dwarf all of the above at low player counts.
