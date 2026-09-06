"""Chronicling America — the only source that is not a government proceeding.

Everything else in this pipeline is what officials said in a chamber. This is
what was printed for everyone else to read, which is the other half of "how did
this look at the time" and the only source that can answer it.

**Public domain by construction.** The collection stops at 1963, so the four
questions it can serve are the pre-1963 ones and there is no rights question to
settle. That end date is also what excludes it from Clean Air, the ACA and net
neutrality, which is why `sources.Source.coverage_end` exists at all.

Two behaviours, both measured against the live API rather than read in a doc,
and both of the same shape: **the parameter is accepted, the response is 200,
and something other than what was asked for comes back.**

**1. `start_date`/`end_date` are ignored.** Measured: `q=Medicare` returns 2,279
results with no date filter, 2,279 with `start_date`/`end_date`, and 957 with
`dates=FROM/TO`. The spike found this the hard way — a request for a pre-vote
window came back with pages from 1933, the year Prohibition was repealed.

**2. Boolean operators are accepted and not honoured.** Measured on the Medicare
terms over 1955-1963:

    "Medicare"                    957
    "Kerr-Mills"                  329
    "Medicare" OR "Kerr-Mills"     85     <- fewer than either alone
    "Medicare" AND "Kerr-Mills"    86     <- and OR is AND
    all seven terms ORed            0

OR and AND landing one result apart means neither is applied. So a union has to
be built client-side: one request per term, merged here. An ORed query returns a
fraction of what was asked for, and with enough terms returns nothing — which
is indistinguishable from the source having no coverage.

**The image URLs are free.** Each result carries `image_url` pointing at
`tile.loc.gov/image-services/iiif/...`, so a headline crop is a URL parameter
rather than a stored derivative. Recorded on the document; nothing fetches the
pixels here, and per CLAUDE.md the model must never describe an image — it can
be shown as artefact plus citation, and that is all.
"""

import json
import logging
import urllib.parse
from dataclasses import dataclass
from datetime import date

from pipeline import fetch as http
from pipeline import ingest

log = logging.getLogger(__name__)

COLLECTION = "https://www.loc.gov/collections/chronicling-america/"
SOURCE_KEY = "loc:chronicling-america"
# Chronicling America's own hard stop. Stated here as well as in the whitelist
# because a page fetched past it would be a rights problem, not just a routing
# mistake.
COVERAGE_END = date(1963, 12, 31)


class NewspaperError(RuntimeError):
    """The collection answered with something unusable."""


@dataclass(frozen=True)
class Page:
    """One newspaper page: its OCR text, its date, and where its image lives."""

    question_id: str
    item_id: str  # the loc.gov item URL, which is its identifier
    title: str
    day: date
    text: str
    image_url: str | None

    @property
    def external_id(self) -> str:
        return self.item_id


def _page_from(result: dict, question_id: str) -> Page | None:
    """Build a Page, or None if the record cannot be dated or has no text.

    Both refusals matter. `source_documents.published_date` is NOT NULL
    precisely so an undateable document cannot be filed on either side of the
    pre/post-vote boundary, and a result with no OCR is a catalogue entry rather
    than a source.
    """
    raw_date = result.get("date") or ""
    try:
        day = date.fromisoformat(raw_date[:10])
    except ValueError:
        return None
    text = (
        " ".join(result.get("description") or []) if result.get("description") else ""
    )
    if not text.strip():
        return None
    images = result.get("image_url") or []
    return Page(
        question_id=question_id,
        item_id=result.get("id") or "",
        title=(result.get("title") or "untitled")[:200],
        day=day,
        text=text,
        image_url=images[0] if images else None,
    )


def search(term: str, dates: str, *, limit: int = 20) -> list[dict]:
    """One term, one request. See the module docstring for why not one query."""
    url = (
        COLLECTION
        + "?"
        + urllib.parse.urlencode(
            {
                "q": f'"{term}"',
                "dates": dates,
                "fo": "json",
                "c": limit,
                "at": "results,pagination",
            }
        )
    )
    body = http.request(url, expect=("application/json",))
    payload = json.loads(body)
    results = payload.get("results") or []
    log.info(
        "loc.gov: %r over %s -> %s results, taking %d",
        term,
        dates,
        payload.get("pagination", {}).get("of"),
        len(results),
    )
    return results


def pages_for(question_id: str, query: dict, *, per_term: int = 20) -> list[Page]:
    """The union across every term, deduplicated on the item id.

    Deduplication is not optional: the terms overlap heavily — a page about
    Kerr-Mills usually mentions Medicare — and the same page arriving twice
    would be stored as two documents and cited as two independent sources.
    """
    seen: dict[str, Page] = {}
    for term in query["terms"]:
        for result in search(term, query["dates"], limit=per_term):
            page = _page_from(result, question_id)
            if page and page.external_id and page.external_id not in seen:
                seen[page.external_id] = page
    return list(seen.values())


def ingest_page(page: Page, decision: date) -> int:
    if page.day > COVERAGE_END:
        raise NewspaperError(
            f"{page.external_id} is dated {page.day}, past Chronicling America's "
            f"{COVERAGE_END} public-domain cutoff"
        )
    if ingest.already_stored(SOURCE_KEY, page.external_id, page.question_id):
        return 0
    text = ingest.normalise(page.text)
    return ingest.store_passage(
        question_id=page.question_id,
        source_key=SOURCE_KEY,
        external_id=page.external_id,
        url=page.item_id,
        # The IIIF base is kept on the title for now rather than earning a
        # column: nothing renders it yet, and a nullable `iiif_base` on
        # source_documents is a migration to make when the frontend needs it.
        title=f"{page.title} [image: {page.image_url}]"
        if page.image_url
        else page.title,
        published_date=page.day,
        content_type="application/json",
        passage=ingest.Passage(0, len(text), text),
        role="framing" if page.day < decision else "outcome",
    )
