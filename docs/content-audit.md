# Content audit: where each `decision_date` came from

**Date:** 2026-09-05
**Why this file exists:** `decision_date` is the pre-vote retrieval boundary.
Everything published before it can reach a player who hasn't voted; everything
from it onwards cannot. That makes each of these eight dates a safety
parameter, and a safety parameter nobody can trace is one nobody can check.

This is also the record of the re-check the spike asked for:

> Existing content inconsistent → Choose a "which vote counts" rule, document
> it, re-check the existing 8.

## The rule

> `decision_date` is the earliest point at which the outcome became public,
> **within the year the question's prompt places the player in.**

Both halves do work.

*Earliest* — not final enactment. If the House passes a bill in April and the
Senate in July, a newspaper printed in May already reports a result. Cutting at
enactment would leave that inside pre-vote scope, where rule #1 would then rest
on a spoiler test noticing the leak rather than on the index being unable to
contain it.

*Within the prompt's year* — the prompt opens "It's 1965." A boundary earlier
than that year makes the scene the question describes unreachable: everything
the player is being asked to reason about is already classified as outcome, and
the pre-vote corpus comes out near-empty. `test_decision_date_falls_in_the_year_the_prompt_claims`
enforces this.

The second half is what settles amendments. A single state ratifying does not
reveal whether an amendment passes — 36 states is the outcome, one state is a
data point. So ratification questions cut at completion, not at the first
state. Cutting at the first state (1918-01-08 for Prohibition, 1909-08-10 for
the 16th Amendment) would have left both questions with essentially no pre-vote
material, and both prompts sit years later anyway.

## The dates

| Question | `vote_type` | `decision_date` | What happened on that date |
|---|---|---|---|
| `us-medicare-1965` | `congressional_passage` | 1965-04-08 | House passed H.R. 6675, 313–115 |
| `us-prohibition-1919` | `constitutional_ratification` | 1919-01-16 | Nebraska ratifies as the 36th state — 18th Amendment adopted |
| `us-interstate-highway-1956` | `congressional_passage` | 1956-04-27 | House passed H.R. 10660, 388–19 |
| `us-clean-air-act-1970` | `congressional_passage` | 1970-06-10 | House passed H.R. 17255, 375–1 |
| `us-net-neutrality-2015` | `agency_rule` | 2015-02-26 | FCC adopts the Open Internet Order, 3–2 |
| `us-affordable-care-act-2010` | `congressional_passage` | 2010-03-21 | House agreed to the Senate amendment to H.R. 3590, 219–212 |
| `us-income-tax-1913` | `constitutional_ratification` | 1913-02-03 | Delaware ratifies as the 36th state — 16th Amendment adopted |
| `uk-national-health-service-1946` | `parliamentary_division` | 1946-05-02 | Commons second reading division, 359–172 |

Sources consulted: SSA's [vote tallies for Medicare](https://www.ssa.gov/history/tally65.html);
GovTrack roll calls [House #95 1956](https://www.govtrack.us/congress/votes/84-1956/h95)
and [Senate #396 2009](https://www.govtrack.us/congress/votes/111-2009/s396);
[Hansard, NHS Bill, 2 May 1946](https://api.parliament.uk/historic-hansard/commons/1946/may/02/national-health-service-bill);
Voteview for the Clean Air Act margin.

### One of these needed a second look, and got it

**`uk-national-health-service-1946` — confirmed 2026-09-05.** This was recorded
as an inference: the 359–172 figure was solid and the second reading ran across
30 April, 1 May and 2 May 1946, but 2 May came from the debate's shape rather
than from a division record.

Read directly from Hansard now that the adapter exists —
`/commons/1946/may/02/national-health-service-bill` contains
**`Ayes, 359; Noes, 172`**, and a second division at `Ayes, 180; Noes, 344` for
the defeated opposition amendment. `decision_date` 1946-05-02 stands, and it is
now a reading rather than an inference.

The amendment division is worth noting on its own: a defeated amendment's
counts, published on the decision date, are exactly the `vote_record` case the
`role` column exists for.

**`us-affordable-care-act-2010` — the leak is not a window, and no fetch bound
closes it.** Measured 2026-09-05 against the CREC material now ingested.

This was recorded as a window: the Senate passed 60–39 on 2009-12-24, the
boundary is 2010-03-21, so January-to-March reporting sits in pre-vote scope and
discusses a vote the reveal treats as the outcome. The fix looked like cutting
the fetch at 23 December.

It does not work, because **the 60–39 margin was public from 21 November 2009**.
The Senate took three cloture votes on the bill and every one of them carried
the same margin as the final passage:

| date | roll call | result |
|---|---|---|
| 2009-11-21 | No. 353 | yeas 60, nays 39 |
| 2009-12-22 | No. 386 | yeas 60, nays 39 |
| 2009-12-23 | No. 394 | yeas 60, nays 39 |

All three are four months inside the framing window and cannot be excluded
without excluding the Senate debate itself, which *is* the framing material.

**So this is a content problem, not a retrieval one.** The reveal says "Passed
the Senate 60–39, House 219–212". The decision this question asks the player
about is the House vote of 2010-03-21; the Senate's 60–39 is run-up, not
outcome, and presenting it as outcome is what creates the leak. `219–212` is
absent from the pre-vote corpus and stays absent — that number is safe.

**Recommended: cut the Senate figure from `reveal.actual_vote`.** Left for a
decision because it changes what a player sees, which is the one thing this
pipeline should not change on its own.

This is deliberate — the prompt says "It's 2010", and a 2009 boundary would
leave the question with no framing corpus at all. But it means this question
is the one where a spoiler is most likely to be *legitimately* retrievable.
Seed the Set-1 spoiler attacks for it specifically around "60–39", "sixty to
thirty-nine" and "Christmas Eve".

## Errors found in the existing reveal text

Three now, counting the one the spike already recorded. None are fixed here —
this is a list for whoever regenerates content in step 6.

**`us-medicare-1965` — internally inconsistent pairing.** (From the spike.)
The reveal pairs "307–116 in the House" (the conference report, 1965-07-27)
with "68–21 in the Senate" (initial passage, 1965-07-09). Both are real votes;
no single rule produces that pair. The Senate's conference-report vote was
70–24 on 1965-07-28.

**`us-interstate-highway-1956` — "Passed the Senate unanimously" is wrong.**
The Senate passed H.R. 10660 on 1956-05-29 by **41–39**, about as far from
unanimous as it gets. The conference report on 1956-06-22 went 89–1. Neither
is unanimous, and the near-tie on initial passage is a materially different
story from the one the reveal tells.

**`us-clean-air-act-1970` — "the House by voice vote" is wrong.** The House
passed H.R. 17255 on 1970-06-10 by a recorded vote of **375–1**. The reveal's
larger claim ("one of the most bipartisan bills ever") survives — 375–1 makes
it better, not worse — but the mechanism is misstated.

## Source coverage, as measured in Step 3

Generated from `sources.select_sources`, not written by hand — this is what the
routing table does today, not what it was meant to do.

| Question | `framing` | `vote_record` | `outcome` |
|---|---|---|---|
| `us-medicare-1965` | `govinfo:crecb`, `govinfo:statute`, `loc:chronicling-america` | `voteview`, `govinfo:crecb` | `govinfo:crecb`, `govinfo:crec` |
| `us-prohibition-1919` | `govinfo:crecb`, `govinfo:statute`, `loc:chronicling-america` | **raises** | `govinfo:crecb`, `govinfo:crec`, `loc:chronicling-america` |
| `us-interstate-highway-1956` | `govinfo:crecb`, `govinfo:statute`, `loc:chronicling-america` | `voteview`, `govinfo:crecb` | `govinfo:crecb`, `govinfo:crec`, `loc:chronicling-america` |
| `us-clean-air-act-1970` | `govinfo:crecb`, `govinfo:statute`, `loc:chronicling-america` | `voteview`, `govinfo:crecb` | `govinfo:crecb`, `govinfo:crec` |
| `us-net-neutrality-2015` | **raises** | **raises** | **raises** |
| `us-affordable-care-act-2010` | `govinfo:crecb`, `govinfo:crec`, `govinfo:statute` | `voteview`, `govinfo:crecb`, `govinfo:crec` | `govinfo:crecb`, `govinfo:crec` |
| `us-income-tax-1913` | `govinfo:crecb`, `govinfo:statute`, `loc:chronicling-america` | **raises** | `govinfo:crecb`, `govinfo:crec`, `loc:chronicling-america` |
| `uk-national-health-service-1946` | `hansard` | `hansard` | `hansard` |

Five of the twenty-four cells raise. That is Step 3's designed output, not a
defect list to burn down before moving on:

- **`us-net-neutrality-2015`** raises for everything. It is an `agency_rule`
  and there is no FCC source in the whitelist; the one source not restricted
  by `vote_type` (Chronicling America) stops 52 years before the decision.
- **`uk-national-health-service-1946`** no longer raises. Hansard was wired up
  on 2026-09-05 and serves all three needs, which no other source does for any
  question — a Hansard sitting is one debate on one named day, so the division
  and the debate that preceded it are separately addressable instead of bundled
  into a fortnight's bound volume. It is also the first non-US source, and so
  the first thing `jurisdictions` has had to discriminate on rather than
  merely record.
- **`us-prohibition-1919`** and **`us-income-tax-1913`** raise for
  `vote_record` alone. Framing and outcome are fine; what's missing is the
  *deciding* vote, because both reveals cite state ratification and every
  congressional source — Voteview and the Congressional Record alike — covers
  only what Congress did. Spike finding 3, now measured.

CLAUDE.md had this as two `vote_type` values affecting three questions. It is
three values affecting four: `agency_rule` has no source either, and had been
overlooked.

Two things the matrix makes visible that weren't obvious in advance:

- **Chronicling America still serves the Clean Air Act's framing.** Its
  coverage ends in 1963 and the decision is in 1970, but the ten-year lookback
  opens the window in 1960, so 1960–63 is a genuine overlap. Queries are
  clamped to it rather than asking for years the source cannot hold.
- **`outcome` does not raise for most questions**, because the Congressional
  Record keeps running after a decision. That material records what was *said*
  about effects rather than evidence of them, so anything numeric drawn from
  it must be attributed to a speaker. Statistical outcome sources — FRED,
  PubMed — are still absent, so outcome coverage is thin in quality even
  where it is non-empty.

## What the remaining gaps need

- `constitutional_ratification` needs a source for the **state legislature's
  vote breakdown**. The ratification *event* is not missing: the Congressional
  Record records it on the day it happened, as a floor announcement (Nebraska,
  the 36th state, 1919-01-16) or as a communication from a state's secretary of
  state laid before the House (Delaware, 1913-02-03). Both are dated, citable,
  and already in the corpus. What no congressional dataset carries is how that
  state's members voted.

  Note this vote_type covers **two** vote events. The congressional vote
  *proposing* the amendment (S.J.Res. 40 in 1909, S.J.Res. 17 in 1917) is in
  Voteview like any other roll call — only the ratification is not. Routing
  that raises on `vote_type` alone cannot tell the two apart, which is why the
  discriminator belongs on the *need* rather than the type.
- `parliamentary_division` needs **Hansard**, which would also be the first
  non-US source and so the first test of the `jurisdictions` field.
- `agency_rule` needs the **FCC's own record** (the 2015 order is FCC 15-24 in
  GN Docket No. 14-28).

`select_sources` raising rather than defaulting is what makes these three
countable. A silent fallback would have produced a plausible answer for every
question and left the gaps invisible until a reveal turned out to cite
nothing.

## A document's `published_date`, when the source is a bound volume

*Measured 2026-09-05 against all ten Congressional Record volumes now in
`backend/.cache/`, covering five questions.*

`decision_date` is a property of the question and is settled above. This is the
other half: where a *document's* date comes from when the thing downloaded is a
two-to-three-week bound volume rather than a dated page.

### Four of five decision volumes straddle their decision date

| question | volume | span | straddles? |
|---|---|---|---|
| medicare | Apr 7 – Apr 27 1965 | decision Apr 8 | yes, by 1 day |
| prohibition | Jan 6 – Jan 26 1919 | decision Jan 16 | yes, by 10 days |
| highway | Apr 27 – May 21 1956 | decision Apr 27 | **no** — opens on the decision |
| clean air | Jun 4 – Jun 12 1970 | decision Jun 10 | yes, by 6 days |
| income tax | Jan 26 – Feb 12 1913 | decision Feb 3 | yes, by 8 days |

The five *framing* volumes — the ones that carry the pre-vote debate — all end
strictly before their decision date. **Nothing that rule #1 protects straddles.**

### The rule: `published_date` is the volume's last date, never its first

Taking the first date is the failure this whole document exists to prevent. The
Clean Air decision volume opens 4 June against a 10 June decision, so a
`published_date` of 4 June puts the roll call itself on the pre-vote side of
`published_date < decision_date`. The margin becomes framing material.

The last date fails the other way. The volume is marked 12 June, lands in
post-vote scope, and the six days of pre-decision debate inside it are lost
rather than leaked. That is the direction to fail in, and it costs 1, 10, 6 and
8 days on the four straddling volumes — days that are, unhelpfully, the ones
closest to the vote and so the most relevant framing material there is.

It also matches what is already in the database: the Medicare slice carries
`published_date` 1965-04-06, its volume's last day.

**So all ten volumes can be ingested safely today**, and the entire pre-vote
corpus for five questions is available without any per-page dating at all.

### Per-page dating would recover those days, and works on half the corpus

Each page carries a running header that is a bare date on its own line. Counting
those, per volume:

| volume era | headers found | share of file between first and last |
|---|---|---|
| 1965, 1970 | 1,363 – 1,451 | 100% |
| 1956 | 2 – 6 | 35 – 79% |
| 1913, 1919 | 1 – 7 | 0 – 27% |

So the technique is sound and the OCR is not, and it degrades with age. On the
1970 decision volume it separates 795 pre-decision pages from 568 post-decision
ones, which is exactly the six days the end-date rule throws away.

Two things measured on the way that are worth not rediscovering:

- **Do not match dates anywhere but a line of their own.** A regex for
  "weekday, month day, year" across the whole text returns years from 1799 to
  1985 — those are dates *quoted inside speeches*, and a span built from them is
  meaningless. The first version of this analysis reported that every volume
  straddled, including one known to be clean.
- **Do not require the header to be upper case.** The day-opening masthead reads
  `TuurSDAY, APRIL 8, 1965` in the 1965 OCR. Case-sensitive matching found zero
  headers in three of five volumes.

### One volume in the download list was wrong

The list generator excluded titles containing "index" but not "appendix", so
Prohibition's framing slot resolved to `1919_58_appendix` — extensions of
remarks, not floor debate. The correct framing volume is
`december-02-1918-january-04-1919_57`. Fixed in the list; the appendix is
harmless to keep but carries no debate.
