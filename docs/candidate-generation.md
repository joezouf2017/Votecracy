# Where questions come from

Every other part of the pipeline takes a question as input and goes looking for
material about it. `select_sources`, the fetch layer, retrieval, generation —
all of them enrich a question that already exists. None of them produce one.

The eight questions the game shipped with were hand-verified against primary
sources. That is the right way to build the first eight and no way at all to
build a few hundred, which is what daily mode needs: 365 a year, plus enough
headroom that a returning player is not shown one they have already had
revealed.

`backend/pipeline/voteview.py` is the half of the pipeline that runs the other
way — it reads a corpus and emits *candidates*.

## Why Voteview can generate rather than only serve

It is a corpus, not a query API. One 29 MB CSV holds all 113,524 congressional
roll calls back to 1789, so it can be retrieved **with no question in hand** —
the property that makes generation possible at all. A per-question search API,
however good, cannot tell you what to ask about.

Each row carries date, chamber, bill number and margin, which is the entire
*structured* half of a question: `decision_date`, `vote_type`, `jurisdiction`,
`retrieval.bill_number`, `congress`, `reveal.actual_vote`. Derived, not
invented.

So there are two passes, and only the second one touches the network per
question:

| | needs | produces |
|---|---|---|
| Pass 1 | one bulk download | the structured skeleton |
| Pass 2 | per-question retrieval | `prompt`, `options`, `reveal.outcome` |

A candidate is not playable. It is the skeleton a question is built on.

## The generator must not see the margin

A candidate row carries `yea` and `nay`. Handing the whole row to a prompt
generator would put the outcome in its context — rule #1 broken *inside the
pipeline*, where no player-facing test would ever catch it.

`for_prompt_generation` is a whitelist projection, structurally identical to
`content.public_view` and a whitelist for the same reason: a blacklist starts
leaking the next field someone adds.

This hazard is created by the ordering. It did not exist while questions were
written by hand, and it appears the moment generation is seeded from a corpus
that knows how the vote went.

## Which roll call is *the* vote

The hard part. Medicare has 16 roll calls under H.R. 6675; only one of them is
the passage vote the reveal should cite.

[`spike-source-retrieval.md`](spike-source-retrieval.md) measured `vote_question`
— 0–1% filled before 1990 — and concluded nothing machine-readable ranks them in
the historical era. **That finding is superseded.** `dtl_desc` is 100% filled
before 1990 and only 27% after, and it states the motion outright: "TO PASS H.R.
6675". The two fields are near-complements.

Measured over all 29,457 measures carrying a bill number, the combination
identifies a passage vote for **43% of 1950–1989 measures against 39% of
post-1990 ones**. The historical era is no worse served than the modern one,
which matters because historical distance is most of the game's appeal — the
pessimistic reading would have restricted automation to exactly the half of the
corpus the game cares least about.

The residual ~60% is mostly genuine rather than a parsing failure: plenty of
measures passed on a voice vote, and only their procedural motions were recorded
by roll call. 10,593 candidates against a target of a few hundred is 30x
headroom.

### The bill has to be the motion's object, not merely mentioned

Roll call 267 of 1970 is filed under H.R. 17255 and reads:

> TO ADOPT H.RES. 1069, THE RULE UNDER WHICH THE HOUSE CONSIDERS H.R. 17255

That is a vote on the *rule*. Matching on the verb alone accepted it and picked
336–40 as the Clean Air Act's decisive vote instead of the 375–1 passage two
roll calls later. Loosening the test to "is H.R. 17255 mentioned anywhere"
accepts it too, because it is. Only checking that the bill is the motion's
grammatical object rejects it.

Both sides of that comparison run through `normalize_bill_number`, because the
corpus spells its own numbers inconsistently — the 16th Amendment is filed as
`SJR40` and described as "S. J. RES. 40". That is spike finding 5 again, this
time *within* a single file rather than between two sources.

## Ranking, and one signal that is honest rather than good

Human review is the only hard bottleneck, so the deliverable is a good candidate
*ranking* that a person picks from — not a fully automatic pipeline.

Four signals are recorded, and deliberately **not** combined into one score.
Checked against the eight hand-written questions, a combined score would have
been actively wrong:

| | margin | closeness | coalition break |
|---|---|---|---|
| Medicare 1965 | 313–115 | 0.269 | 0.246 |
| Clean Air 1970 | 375–1 | 0.003 | 0.000 |
| ACA 2009 | 60–39 | 0.394 | 0.002 |

All three are good questions and they have nothing in common on either axis.
Clean Air was near-unanimous, which is exactly what makes it good — a modern
reader thinks the answer is obvious and the industry of 1970 did not. ACA was
nearly a tie *and* almost perfectly predicted by ideology, the signature of a
party-line vote. Any single score buries Clean Air.

So `attention` — how many roll calls the measure took in total — does the
ranking, and the others describe *what kind* of question it is. The median
measure takes one roll call; all six hand-written questions with a bill number
sit at the 86th–100th percentile. Congress voting on something nine times is
Congress struggling with it.

**Ranking on raw attention over-selected the 19th century by 1.55x in the top
1,000.** That is precisely the set-level skew the Phase 3 balance audit exists
to catch, and it turned up inside the ranking itself. Normalising each measure
against its own congress makes the ranking era-neutral, which turns "how many
questions come from each era" into a separate and *visible* decision rather than
something smuggled into a score.

The signal this is all a proxy for is whether a law has a **popular name** — a
law people argued over is a law that got one. The Office of the Law Revision
Counsel maintains that table, and it is a separate bulk download that has not
landed yet. Until it does, "Congress had to vote on this nine times" is what the
corpus can say by itself.

Significance must come from a list like that rather than from a model. Asking an
LLM which votes are interesting produces the landmark-wins skew described in
[`evaluation.md`](evaluation.md), and it produces it in the selection step, where
no per-question check can see it.

## What the corpus cannot supply

- **State ratification.** Voteview is a congressional dataset. It records
  Congress *proposing* a constitutional amendment; the question turns on
  ratification by the states, years later. Those candidates are emitted with an
  explicit `gaps` entry rather than a plausible wrong `decision_date` — the same
  reason `select_sources` raises instead of falling back.
- **`search_terms`.** Every other source needs them and `vote_desc` is 0% filled
  before 1990. `dtl_desc` carries a CQ-style subject line in some eras — "HR
  10660. HIGHWAY CONSTRUCTION ACT. AMEND AND SUPPLEMENT…" — which seeds them.
  It is a *subject*, not a popular name: sometimes it reads as one, sometimes as
  a description of the action, so it is not the significance signal.
- **Review state.** Candidates currently exist only as a function's return
  value. Recomputing them costs 0.6 s, so the corpus is not the problem — there
  is nowhere to record that a human looked at one. That needs a `candidates`
  table with a `status`, upserted on `(congress, bill_number)` in a way that
  **cannot overwrite `status`**, or re-running the corpus erases human review.
