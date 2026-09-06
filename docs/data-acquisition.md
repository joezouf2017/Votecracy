# Where the remaining data comes from

**Written 2026-09-05**, after four rounds of "download some, discover something,
download more". This document exists so that does not happen again: the rule is
now that a question's volume set is settled **before** anything is fetched, from
legislative history, not by looking at what the last batch turned up.

Everything below is marked **verified** (I called it) or **lead** (plausible,
unchecked). Do not treat a lead as a plan.

## Status per question

| question | pre-vote chunks | what is missing | source it needs |
|---|---|---|---|
| medicare 1965 | 3,560 | — | done |
| income tax 1913 | 1,640 | — | done |
| prohibition 1919 | 790 | — | done |
| interstate highway 1956 | 746 | — | done |
| **clean air 1970** | 213 | the rest of its debate | archive.org, one round |
| **ACA 2010** | 0 | everything | GovInfo `CREC` |
| **net neutrality 2015** | 0 | everything | FCC, or the Federal Register |
| **NHS 1946** | 0 | everything | Hansard |

Four questions are finished. **One more archive.org round closes clean air, and
then that source is done** — the remaining three cannot be served by it at all,
so they wait for the fetch layer rather than for more manual downloading.

---

## 1. archive.org — bound Congressional Record, 1873 to 2008-06-23

**Verified.** The workhorse so far: 24 volumes, 315 MB, no key, no rate limit
encountered, no circuit breaker needed.

```
resolve   https://archive.org/advancedsearch.php?q=collection:pub_congressional-record-proceedings-and-debates+AND+date:[FROM TO]
whole set https://archive.org/services/search/v1/scrape?q=collection:pub_congressional-record-proceedings-and-debates   (2,402 items, one call)
fetch     https://archive.org/download/<identifier>/<identifier>_djvu.txt
```

**Gotchas, all of which cost time once:**

- **The collection is `pub_…` and the identifiers are `sim_…`.** Searching
  `collection:(sim_…)` returns zero and looks exactly like "no coverage".
- **82 items use the short prefix** `sim_congressional-record_` with no
  `-proceedings-and-debates`. They are three islands (vols 1–8, 42–45, 98), not
  an era, and vols 42/45/98 contain *both* spellings — vol 45 splits mid-volume
  by date. There is no rule to encode; resolve from the collection index.
- **Exclude `index` and `appendix` volumes.** A page index is not debate and an
  appendix is extensions of remarks. The naive nearest-volume query picks up
  both.
- **The only span information is the item title**, and it parses for 17 of 25.
- **Series ends 2008-06-23.** Anything later needs GovInfo.

**Selection rule, which is the thing that went wrong:** pick volumes from the
bill's legislative history, then confirm with term density. Proximity to the
decision date is a bad proxy — Medicare's 1962 volume outscores the one nearest
the vote by 3.9x, because that is where King-Anderson died 52-48.

**Ranking traps:** `interstate` is 343 Interstate *Commerce* against 48
highway-sense in one 1956 volume and the reverse in another, so rank on the
qualified phrase. Bare bill numbers collide with service numbers and page
numbers; use `H\.?\s*R\.?\s*4260`.

## 2. GovInfo — `CREC` 1994+, `CRECB` 1873–2017, `STATUTE`, `BILLS`

**Verified: bulk data does *not* include the Congressional Record.**
`govinfo.gov/bulkdata` carries Bills, Bill Status, CFR, Federal Register,
Public and Private Laws, Statutes at Large and Supreme Court decisions — no
CREC or CRECB. So the Record needs the keyed API; there is no open-access route.

```
api.govinfo.gov, key from api.data.gov (free, instant)
Gateway limit: 1,000 requests/hour
```

- **Its date filter applies to the volume, not the proceedings inside.** Drift
  measured at 0–23 days, and the single most relevant document for Medicare sat
  in a volume dated three weeks later. Read `published_date` off the record.
- **PDFs are 20–65x the size** of archive.org's plain text for the same content,
  which is why archive.org is preferred wherever the years overlap.

**Needed for: ACA 2010 only**, because archive.org stops in 2008.

**Also worth taking from here:** bill text and Statutes at Large for the
pre-vote scope. The Record carries what was *said*; "how is this funded" is
usually answered by the bill's own financing provisions. Those *are* in bulk
data, so no key needed.

## 3. Voteview — roll calls, 1789 to present

**Verified**, already cached (`HSall_rollcalls.csv`, 29 MB, one URL, no key).

Beyond the tallies it carries **member-level positions and DW-NOMINATE
coordinates**, which is the vote-breakdown visual — 423 dots by party says what
"307–116" cannot, at no extra retrieval cost.

Gotcha: pre-1990 rows have no `vote_desc`, so amendment descriptions have to
come from the Record.

## 4. loc.gov Chronicling America — newspapers to 1963

**Partly verified** (the date-parameter bug was measured; the block behaviour is
reported, not reproduced).

- **`start_date`/`end_date` are silently ignored.** A request for a pre-vote
  window returned 1933 pages — the year Prohibition was repealed. The parameter
  it honours is `dates=FROM/TO`.
- **Needs a per-host circuit breaker.** loc.gov resets its block countdown on
  any request made *during* a block, so ordinary per-request backoff turns a
  one-hour block into an indefinite one. **This is the one genuinely unproven
  piece of Step 5**, untested because triggering a block costs an hour.
- Gives `image_url` pointing at IIIF, so **headline crops are URL parameters**,
  not stored derivatives. Public domain by construction — the collection stops
  at 1963.

## 5. Hansard — UK, 1803–2005 — **newly verified, and it unblocks NHS**

```
https://api.parliament.uk/historic-hansard/commons/1946/apr/30.js
```

**No key, no rate limit encountered, and it returns structured JSON with
per-sitting dates and column numbers.** Verified live: 1946-04-30 returns the
Commons sitting, columns 1–158, with its top-level sections.

This is **materially better than the Congressional Record route**: dates are
structural rather than parsed out of OCR, so the whole volume-straddling problem
(`docs/content-audit.md`) does not arise, and there is no OCR noise.

What still needs working out: the sub-section URL pattern.
`…/apr/30/orders-of-the-day.js` returns 404, so children are addressed some
other way; the HTML view at the same path without `.js` returns 200 and can be
parsed. One session of exploration, not a research project.

**Alternative:** [mySociety ParlParse](https://data.mysociety.org/datasets/uk-hansard/)
publishes Hansard as XML with speaker identifiers, from 1918 for the main
chambers. Worth preferring if speaker attribution matters, since rule #2
requires numeric claims from the Record to be attributed to whoever said them.

**This also settles an open item.** `docs/content-audit.md` records NHS
`decision_date` 1946-05-02 as inferred from a three-day debate rather than
confirmed against a division. Hansard has the sittings; confirm it there before
fetching sources for that question.

## 6. FRED and PubMed — the `outcome` role only

**Lead, not verified.** Neither is in the whitelist yet.

They matter more than their position on this list suggests. Right now `outcome`
retrieval resolves to the Congressional Record, which is **a record of what was
said about effects, not evidence of them** — so everything numeric taken from it
has to be attributed to a speaker. FRED and PubMed are what would make an
outcome claim about the world rather than about a speech.

FRED needs a free key. PubMed's E-utilities need none below 3 requests/second.

## 7. FCC — net neutrality, and the one real lead here

No source in the whitelist, and the direct route (EDOCS, the FCC's own API) is
a new adapter for one question.

**But the 2015 Open Internet Order was published in the Federal Register, and
the Federal Register *is* in GovInfo bulk data** — no key, no gateway limit.
That would make net neutrality reachable without an FCC adapter at all.

**Unverified.** The thing to check: whether FR's published version carries the
order's reasoning or only the rule text, since the pre-vote scope needs the
argument and not just the outcome. Order FCC 15-24, GN Docket No. 14-28,
adopted 2015-02-26.

---

## The rule that would have saved three rounds

Every one of the four download rounds was triggered by discovering something the
previous round could have told me. The pattern was always the same shape: **a
vocabulary or date assumption that nobody had measured.**

- "clean air amendments" scored 0 against a volume with 1,079 uses of
  "pollution"
- "sixteenth amendment" scored 2 where "income tax" scored 1,218
- "Kerr-Mills" scored 0 in 1960 where "Anderson-Kennedy" scored 91 — the debate
  renamed itself twice across six years
- two questions' `decision_date` is a *state ratification* date, so the
  congressional debate is years earlier and the volumes near the decision hold
  almost nothing

So, before fetching anything for a new question:

1. **Write down the legislative history** — introduction, committee, floor votes
   in both chambers, with dates. That determines the volume set.
2. **Check what kind of date `decision_date` is.** A ratification date does not
   point at a congressional debate.
3. **Probe vocabulary on one volume before committing to a term set.** Terms are
   era-specific and the formal name is usually the dead one.
4. **Then** download the whole set, ingest once, embed once.

## Cost, so the sequencing is arguable rather than assumed

Spend to date is **$0.40** across all four rounds, of which roughly 23% was
re-embedding material that a later round replaced.

The lever that matters: **Gemini is 15x Qwen's price** and the corpus is
currently embedded twice, once per model, for an A/B that cannot run until Set 2
exists. Embedding only Qwen until the corpus is frozen, then adding Gemini once
for the comparison, cuts iteration cost by roughly 93% and loses nothing — the
A/B only needs both models over *identical* chunks, and identical is a property
of the end state, not of any intermediate one.
