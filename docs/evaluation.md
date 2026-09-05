# Evaluation: how content and the chatbot are proven, not asserted

Three of the game's rules are claims about behaviour — the outcome never leaks
before a vote, historical claims are grounded, the chatbot does not advocate.
A claim about behaviour that nothing measures is a hope. This file is the
measurement plan, and it is the Phase 3 acceptance gate.

[`architecture.md`](architecture.md#verification-is-layered-and-the-layers-are-not-interchangeable)
describes the three verification layers and why they are not interchangeable:
structural prevention, then code checking span citations for rule #2, then an
LLM judge for neutrality only — the one check with no deterministic ground
truth. What follows is how each layer gets a number attached to it.

## Neutrality is a property of the set, not only of each question

The judge scores one question at a time and is structurally blind to how the
*collection* leans. If eight questions in ten end with history vindicating the
"Support" side, the game teaches "progress always wins" no matter how
even-handed every individual prompt is.

This is not a hypothetical failure. Asking a model to pick "interesting" votes
produces exactly that shape — over-representing landmark wins,
under-representing trade fights and procedural defeats — and the bias lives in
the *selection*, where no per-question check can see it.

So selection criteria have to be explicit and measurable, and the set gets its
own audit: a breakdown by era, category, jurisdiction, `vote_type`, margin, and
**which side history vindicated**. That last column is the one that matters; the
others are how you explain it.

→ `metrics/phase3-question-set-balance.md`

## Calibrating the judge

A judge cannot be trusted without human-labelled data to measure it against. The
first ~100 questions get full human review regardless, and that set is what the
judge is scored on: agreement rate overall and per check, plus where the
disagreements concentrate.

→ `metrics/phase3-judge-agreement.md`

## The chatbot has three correct behaviours

Measuring only one of them is how you optimise into a useless bot. A bot that
never leaks scores perfectly on spoilers by refusing everything; a bot tuned to
be helpful invents sources. So there are three sets, and the targets pull
against each other on purpose.

| Set | Contents | Metric | Target |
|---|---|---|---|
| 1. Spoiler attacks | ~40 attack shapes × every question | leak rate | **0%, non-negotiable** |
| 2. Legitimate, supported | questions written *from* indexed chunks | wrong-refusal rate, plus recall@k and citation validity | low |
| 3. Legitimate, unsupported | relevant questions the corpus cannot answer | admits vs invents | invention rate is the number that decides whether it ships |

→ `metrics/phase3-chatbot-eval.md`

### Set 1 is scored by code, not by a judge

The forbidden content for a question is already in the database:
`tokens(reveal) − tokens(pre-vote material)`. A set difference, not a judgement,
so the leak check inherits none of the judge's uncertainty. `spoilers.forbidden`
computes it; on the Medicare question 28 distinct reveal words reduce to 4, and
the numbers — 307 and 116 — are the high-signal half.

Seed the set automatically and confirm it once during the human review that is
happening anyway. Raw extraction picks up noise like "House" and "Senate", which
a legitimate answer would trip over.

Attack shapes to cover: direct ("just tell me the result"), false premise
("since I already voted Support…"), oblique extraction ("what year was it
repealed?"), numeric probing, other languages, fictional framing, and multi-turn
rapport-building.

### Set 2 must be derived from the index, never imagined

Write the questions *from* actual chunks and store the supporting chunk id with
each. Set 2 then doubles as the retrieval golden set, so recall@k is measurable
rather than eyeballed.

Set 3 is its counter-set: plausible-sounding questions the corpus cannot answer.
It exists because tuning against Set 2 alone pressures the model toward exactly
the hallucination rule #2 prevents — "over-refusal" and "invention" are the two
ends of one dial, and only measuring both locates it.

### Hold out 20% of every set

Tuning prompts against all of it produces a bot that passes this suite and
nothing else. Run the full sets nightly and a ~30-case smoke subset per commit.

The full run costs a few hundred generation calls, which is the constraint
described in [`metrics/gemini-quotas.md`](metrics/gemini-quotas.md) — the free
tier allows 20 a day, so one smoke subset already exceeds it. That is a budget
decision, not an evaluation one, but it has to be settled before this plan can
run.

## Two limits worth stating plainly

**Coverage cannot be guaranteed.** The corpus is incidental: it contains what
happened to be said or printed, not what is true or relevant. "I have no source
for that" is a correct answer, and Set 3 exists because it has to be a *measured*
behaviour rather than an assumed one.

**Some existing reveal prose cannot be grounded at all.** "Most economists
consider it one of the best infrastructure investments ever made" has no
`(document_id, char_span)` that could support it — the rule #2 check verifies
numbers against spans, and a summary judgement is not a number. Several of the
eight hand-written outcome paragraphs lean on this shape, and it is the shape an
LLM produces most readily, so generation will multiply it.

Either attribute such claims to a named source or cut them. The failure to avoid
is letting the grounding check quietly skip what it cannot verify, which turns a
green check into a statement about the checker rather than the content.
