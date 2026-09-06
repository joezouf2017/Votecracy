# What this costs to run

**Date:** 2026-09-05
**Reproduce:** `./.venv/Scripts/python.exe docs/metrics/cost-model.py`

Prices from [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing),
checked 2026-09-05. Corpus sizes measured from the Medicare slice in the local
database. Generation and the eval runs use the Batch API at half price;
embeddings deliberately do not, for a reason that is not about money — see
"Batch and sync are not interchangeable" below.

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
| 8 | $0.08 | $0.07 | $0.00 | **$0.16** | 0.6M embed, 0.4M in, 0.04M out |
| 100 | $1.03 | $0.90 | $0.05 | **$1.98** | 6.9M embed, 4.7M in, 0.48M out |
| 500 | $5.17 | $4.50 | $0.24 | **$9.91** | 34.5M embed, 23.5M in, 2.4M out |

Flash-Lite 3.1, generation on the Batch API and embeddings at full price (see
below for why). On full Flash 3.7 the 500-question build is
$18.48 — the model choice moves the one-off bill by $8, which is not a
decision worth agonising over.

Embedding and generation come out close — $5.17 against $4.50 — and the two
reasons pull against each other. Embedding processes more tokens (34.5M against
23.5M) but has no output side, and output costs 6x input. Generation then gets
the batch discount and embedding does not, which is what closes the gap.

Neither is worth optimising. The entire one-off build is under ten dollars.

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
**$2.39/month for a weekly full eval and a one-off $9.91 to build 500
questions.**

Cutting the eval plan to fit the free tier would trade the phase's only
deliverable for less than the price of a sandwich. Pay.

## One gateway or two

OpenRouter carries embeddings as well as chat, including `gemini-embedding-001`
at the same $0.15/M — provider pricing passes through without markup. So the
choice is not "which vendor" but "one integration or two", and every figure in
this document holds either way:

- **Batch API, 50% off**, covering `/v1/chat/completions`, `/v1/responses`,
  `/v1/messages` and **`/v1/embeddings`**, on a 24-hour window. Text only, which
  is all this pipeline sends.
- **`dimensions` on the embeddings endpoint**, so `gemini-embedding-001` can be
  asked for 768 and fits `vector(768)` unchanged.
- **`input_type`** (`search_document` / `search_query`), which is the
  document/query asymmetry `embedding.py` already implements.
- **Per-provider privacy routing** — providers that train on submitted data can
  be excluded, and a request that cannot be routed privately fails rather than
  quietly going somewhere it should not.
- The judge's "must be a different model family" requirement becomes a string
  change rather than a second vendor integration.

**Use OpenRouter for everything.** If the extra hop hurts the synchronous
chatbot's latency, move that one path direct — it is the only latency-sensitive
caller, and the only reason left to split.

### Batch and sync are not interchangeable, and the difference is not the price

Two of the features above are **dropped on the Batch API**: `input_type` and
`provider` preferences. Neither failure is loud. That splits the work in three:

| | batch? | why |
|---|---|---|
| Embeddings | **no** | Batch drops `input_type`, so documents lose the RETRIEVAL_DOCUMENT instruction. It does not error — it ranks worse. Costs $2.60 more at 500 questions |
| Generation, judge, eval runs | **yes** | `input_type` is meaningless for chat, and the content is public-domain records and our own attack strings, so losing `provider` routing carries nothing |
| Live chatbot | **n/a** | Synchronous by definition, and the one path carrying real user input — which is exactly where `provider` privacy routing must be on |

The batch docs also say embeddings are "rolling out on providers that support
them", so coverage is per-provider and undocumented. Moot here, since
embeddings do not go through batch anyway.

**Avoid `:free` embedding models entirely.** Their model cards state outright
that submitted requests and embeddings may be retained for training.

> Two earlier drafts of this section claimed OpenRouter had no Batch API and no
> `dimensions` parameter. Both were wrong, and wrong the same way: read from the
> FAQ and the docs overview, where the absence of a mention was treated as the
> absence of a feature. The API reference has both. Worth recording because the
> conclusions it produced — "offline costs double", "the embedding path has a
> hole" — were confident, load-bearing, and would have driven a second
> integration for no reason.

### Do not switch embedding models to save money

The catalogue has models at $0.004–$0.01/M against gemini-embedding-001's
$0.15, which would take the 500-question embedding line from $5.17 to about
$0.70. Embedding is half of a one-off build that costs less than a sandwich and
generation dominates it anyway, so the saving is not the point. What matters is
what a switch actually costs, and that is two separate things which are easy to
conflate:

**The column width is a schema constraint.** `chunk_embeddings.embedding` is
`vector(768)`, set in a migration. Some models can meet it and some cannot —
OpenAI's `text-embedding-3-*`, Gemini and Qwen3 all support Matryoshka
truncation to an arbitrary size, while [voyage-4 offers only 256/512/1024/2048](https://docs.voyageai.com/docs/flexible-dimensions-and-quantization)
and bge-m3 and Mistral Embed are fixed at 1024. So "other models need a
migration" is true of some and false of others.

A request for fewer dimensions is standard now, and standardised on OpenAI's
spelling: `dimensions` in the OpenAI API and in gateways that normalise to it,
`outputDimensionality` in Gemini's native API, `output_dimension` at Voyage and
Cohere. The technique underneath is Matryoshka Representation Learning — the
model is trained so that the first *k* components of its vector are themselves a
valid *k*-dimension embedding. **The truncated vector must be re-normalised to
unit length**, which `embedding.py` already does in `_unit()`; skipping it
leaves cosine distance subtly wrong rather than obviously broken.

**Vector spaces are not comparable, and that constraint no schema can remove.**
A 768-dimension OpenAI vector and a 768-dimension Gemini vector describe
different spaces; cosine distance between them is arithmetic without meaning.
This — not the column width — is why `model` is part of the primary key and why
`retrieval.nearest` takes `model` as a required argument with no default. **Any
model switch means re-embedding every chunk**, whatever the dimension, and that
is the real cost.

The one reason that would justify switching is **retrieval quality on OCR'd
1960s newsprint**, which Set 2's recall@k measures directly. Switch on that
evidence, never on the price.

### The schema cannot A/B two embedding models, and that is now a gap

Recommending a recall@k comparison and fixing the column at 768 are in tension:
an A/B against a model that emits 1024 needs both widths present at once, and
`vector(768)` forbids it. The table was designed to make *replacement* easy —
embeddings are a separate table from chunks precisely so "drop the vectors and
re-embed" is an operation you can perform, and `model` in the key lets two
coexist during a swap — but replacement at one width is not comparison at two.

The fixed width was the right call when it was made, and for a reason that
still holds: it makes the database reject a wrong-sized vector at write time. A
dimension mismatch is otherwise a silent ranking bug rather than an error, which
is why `nearest` re-checks the length in Python before building the query.

What changed is that an A/B is now planned. When it happens, the migration is
small: `vector` without a modifier, plus a `dimension` column, moving the check
from the database into the write path. Unconstrained `vector` costs nothing
here specifically because **there is no ANN index** — a fixed width mainly buys
indexability, and that argument was already given up on measurement (1.8 ms
scoped exact scan against 382 ms unscoped). Do it when the A/B is real, not
before.

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
