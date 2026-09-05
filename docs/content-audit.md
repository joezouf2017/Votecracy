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

### Two of these need a second look

**`uk-national-health-service-1946` — day not confirmed.** The 359–172 division
figure is solid, and Hansard shows the second reading debate running 30 April,
1 May and 2 May 1946, with the division at the close. 2 May is an inference
from the debate's shape, not a reading of the division record. Confirm against
Hansard's own division listing before this question's sources are fetched.

**`us-affordable-care-act-2010` — a residual leak window.** The Senate passed
H.R. 3590 on 2009-12-24 by 60–39, and the reveal cites exactly that number.
The boundary is 2010-03-21, so reporting from January to March 2010 sits in
pre-vote scope and discusses a vote the reveal treats as the outcome.

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

## What this implies for step 3

`select_sources` routes on `vote_type`, and two of the four values have no
source wired up yet:

- `constitutional_ratification` — Voteview is a congressional dataset and does
  not cover state ratification at all. Two of the eight questions need
  something else entirely.
- `parliamentary_division` — Hansard, for the one UK question.

CLAUDE.md requires `select_sources` to raise rather than fall back to a default
when nothing matches. With the whitelist as it stands today, three of eight
questions would raise. That's the correct behaviour and the reason to write it
that way: a silent default would have made this gap invisible.
