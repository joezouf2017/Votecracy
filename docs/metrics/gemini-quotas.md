# Google AI Studio free-tier quotas, as measured

**Date:** 2026-09-05
**Why this file exists:** the Phase 3 plan was written against an assumed
"1,000 requests/day, ~3 calls per question, comfortable". The live console says
otherwise, and the gap is about 50x on the number that binds.

## The limits

| model | RPM | TPM | **RPD** |
|---|---|---|---|
| `gemini-2.5-flash`, `gemini-3-flash` | 5 | 250K | **20** |
| `gemini-2.5-flash-lite` | 10 | 250K | **20** |
| `gemini-embedding-001` | 100 | **30K** | 1,000 |

Generation is capped at **20 requests per day**. At ~3 calls per question that
is six or seven questions a day.

## Embedding is not the problem

It is bounded by tokens rather than requests. Peak measured throughput was
**23.17K tokens/minute against a 30K ceiling** — close enough to the limit that
it is the real constraint, but the limit is generous relative to the work.

One question's 215 chunks are ~54K tokens, so roughly two minutes each. 500
questions is ~17 hours of wall clock, and the 1,000 RPD allowance covers ~45
questions a day. Slow, offline, and fine.

## Generation is the problem, by roughly 7x

The [evaluation plan](../evaluation.md) is what the cap actually breaks, not
development:

- Set 1 alone is ~40 attack shapes across every question.
- The plan asks for the full sets nightly, plus a ~30-case smoke subset per
  commit. **One smoke subset exceeds a whole day's quota.**

Day-to-day pipeline work fits inside 20 requests. The acceptance gate does not,
and it is the acceptance gate that produces the numbers the phase is judged on.

## Three ways out, none yet chosen

1. **Pay for a tier.** Flash is cheap per token; the free-tier cap is on
   requests, not on spend, so this is a small bill rather than a big one.
2. **The Batch API.** Separately quotaed and half price, for work that is
   already offline and overnight — which describes the entire eval run.
3. **Cut the eval plan to fit.** Cheapest, and the one that costs the most,
   because the eval plan is the deliverable.

This has to be settled **before Step 7**. Nothing earlier depends on it.
