# What the sources actually do, measured against them

**Date:** 2026-09-05
**Why:** Steps 2 and 3 built the retrieval scope and the query layer without
ever calling the sources. This is the first end-to-end slice — one question,
fetched for real — and it found four things that no amount of reading the
schema would have.

The headline, which every finding below is an instance of:

> **An API's date filter is a performance optimisation, never the safety
> boundary.** Rule #1 has to be enforced on the record we get back, not on the
> parameter we sent.

## 1. loc.gov silently ignores `start_date`/`end_date`

Asking Chronicling America for the Prohibition question's pre-vote window:

| parameter | matches | first three dates |
|---|---|---|
| `start_date=1909-01-16&end_date=1919-01-15` | 31,560 | 1933, 1926, 1930 |
| `dates=1909-01-16/1919-01-15` | **75** | 1919-01-01, 1914-08-21, 1913-10-18 |

HTTP 200 both times. The first form is accepted and discarded.

1933 is the year Prohibition was repealed. A fetcher trusting that parameter
files an article about the repeal as pre-vote framing for a question that asks
"do you support ratification?" — rule #1 failing in the quietest available way.

The test written alongside `formulate_query` passed throughout, because it
asserted against the query dict rather than against what the API did with it.

## 2. GovInfo honours its filter, but on the wrong date

`publishdate:range(...)` filters on `dateIssued`, which for the bound
Congressional Record is the **volume's** date, not the date of the proceedings
inside it. Measured over 92 granules from CRECB 1965:

- the volume date runs **0 to 23 days later** than the real date
- the 1965-04-07 House record — the day before the Medicare vote, the single
  most relevant document for that question — sits in a volume dated
  **1965-04-27**

Consequences run both ways:

- **Lossy**: filtering with `published_to = 1965-04-07` excludes that volume,
  and with it the entire March–April debate. The direction is safe (a volume's
  end date is ≥ every date inside it, so a volume ending before the decision
  contains nothing after it) but it discards exactly the material worth having.
- **Not a guarantee**: querying the full year returned 92 granules of which
  **21 were dated on or after the decision**. The filter cannot be the thing
  keeping outcome material out.

The working shape is therefore: **widen** the query range deliberately, parse
each granule's true date, and apply the real boundary ourselves.

The true date is only in the granule **title** — `"House of Representatives:
April 8, 1965"`. `dateIssued` on the granule repeats the volume's date. Title
parsing succeeded on 100% of granules in two sampled packages (58/58 and
32/32), and can be cross-checked against the span the package title declares
(`"Volume 111, Part 6 (April 5, 1965 to April 27, 1965)"`). Two independent
statements agreeing; if they disagree, refuse to store — which is what
`source_documents.published_date NOT NULL` was put there to force.

## 3. Chronicling America's coverage is a decay, not a cliff

`Source.coverage_end = 1963-12-31` models a wall. The reality:

| decade | pages |
|---|---|
| 1900–09 | 585,374 |
| 1910–19 | **637,055** |
| 1920–29 | 282,200 |
| 1930–39 | 114,190 |
| 1940–49 | 108,901 |
| 1950–59 | **64,826** |
| 1960–63 | 21,851 |

A tenfold collapse from the 1910s to the 1950s. Medicare's framing window
(1955–1965) sits in the trough: the query returned **one** result.

So `select_sources` correctly says loc.gov is *eligible* for Medicare, and in
practice there is nothing there. **Eligibility and productivity are different
questions**, and Step 3's matrix only answers the first. Calling it "validated"
was overclaiming — it was validated structurally, never empirically.

Practical consequence: mid-century questions have no newspaper framing, so
GovInfo's Congressional Record is not one of several options but the only one.
Five of the eight existing questions depend on it.

## 4. Pre-1994 Congressional Record is 40 MB of scanned PDF per day

CRECB granules offer `pdfLink` and `modsLink` — no HTML, no text. One day's
House proceedings is **39.8 MB across 94 pages**, scans with an OCR text layer.

The text layer is good: `pypdf` extracted **948,509 characters in 10.8 s**,
about 10,000 per page. Usable.

But the spike estimated ~100 KB of Congressional Record per question. The real
figure is ~40 MB downloaded per day of debate, of which a fraction is relevant.
Selectivity in *which days* is the only control — which is the same conclusion
the original spike reached about newspaper pages, now with a 400x larger unit.

`pypdf` becomes a required dependency for any pre-1994 congressional material.

## Rate limits, correctly attributed

The earlier note conflated two gateways. They are separate:

| | limit | on exceeding |
|---|---|---|
| **loc.gov** JSON API | 20/min | blocked 1 hour |
| loc.gov Text / Image services | 150/min | blocked 1 hour |
| loc.gov MARCXML | 30/min | blocked **72 hours** |
| **GovInfo** via api.data.gov | 1,000/hour | 429 |

loc.gov needs no key. GovInfo does.

Two behaviours that matter more than the numbers:

- **The block countdown resets on any request made during it.** A naive
  `while True: retry` turns a one-hour block into an indefinite one. Backoff
  must actually back off.
- **Under load loc.gov returns an HTML page with a CAPTCHA rather than a 429.**
  Content-type validation is not only for the dead legacy paths the first spike
  found; it is needed in normal operation.

## Other API-shape traps hit along the way

- `/collections/{code}/{start}/{end}` filters on **lastModified**, not
  publication date. It is for incremental sync. Searching for content by date
  needs `POST /search` with `publishdate:range(...)`.
- A loc.gov search response is **~1.9 MB even for three results**, because it
  carries the whole collection's facet metadata regardless of `c`.
- `chroniclingamerica.loc.gov/ocr.json` → 308 → `www.loc.gov/chroniclingamerica/ocr.json`
  → 404. The bulk OCR listing did not survive the migration, and the HTML page
  behind it answers automated clients with a Cloudflare challenge.

## Is bulk download the answer?

For Chronicling America, no, and the reason generalises. Bulk versus query is
decided by **what fraction of the corpus you need**:

| source | corpus | we need | ratio | verdict |
|---|---|---|---|---|
| Voteview | 29 MB, 113,524 roll calls | all of it, to find candidates | 100% | bulk |
| Chronicling America | 12M pages, ~100 GB of OCR | ~7,500 pages | 0.06% | query |

## What this changes

1. `formulate_query` must emit `dates=FROM/TO` for loc.gov. **Fixed** in
   `2b7fb44`.
2. For GovInfo, it must **widen** the requested range rather than send the true
   boundary, and the fetch layer must filter on the granule's parsed date.
3. `Source` needs a way to express that coverage decays, or the fetch layer
   needs to record yield per (question, source) so a source that is eligible
   but empty is visible rather than silently thin.
4. `pypdf` joins the runtime dependencies if pre-1994 congressional material
   is in scope — and it is, for five of eight questions.
