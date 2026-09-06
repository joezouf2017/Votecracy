"""How to ask a source for what a question needs.

Split from `sources`, which answers the other half: *which* source can serve a
need at all. The seam was already marked with a comment in that file, and the
fetch layer grows this side — per-source adapters are about addressing and
parsing, not about selection. Separating them while the file was 317 lines was
cheaper than separating them after five adapters had been added on top.

The dependency runs one way: this module knows about `Source`, and `sources`
knows nothing about queries.
"""

from datetime import date, timedelta

from pipeline.sources import Source, need_window

_JOINT_RESOLUTION = (("SJRES", "SJR"), ("HJRES", "HJR"))


def normalize_bill_number(raw: str) -> str:
    """`S.J.Res. 17` -> `SJR17`, the spelling Voteview actually uses.

    Spike finding 5. Searching Voteview for the conventional `SJRES17` returns
    zero rows, which looks exactly like the amendment not being in the dataset
    rather than like a formatting mismatch. A silent empty result is the worst
    possible failure here, so the transformation gets its own function and its
    own tests.
    """
    if not raw or not raw.strip():
        raise ValueError("bill number is empty")
    compact = "".join(ch for ch in raw.upper() if ch.isalnum())
    for conventional, voteview in _JOINT_RESOLUTION:
        if compact.startswith(conventional):
            return voteview + compact[len(conventional) :]
    return compact


def _bounds(source: Source, window: tuple[date | None, date | None]):
    """Turn the half-open need window into inclusive API date bounds.

    Clamped to what the source actually holds, so a query never asks
    Chronicling America for 1970 and reads the empty result as "nothing was
    written about this".
    """
    w_start, w_end = window
    start = max(w_start, source.coverage_start) if w_start else source.coverage_start
    end = w_end - timedelta(days=1) if w_end else None  # window end is exclusive
    if source.coverage_end and (end is None or source.coverage_end < end):
        end = source.coverage_end
    return start, end


def formulate_query(question: dict, source: Source, need: str) -> dict:
    """The request to send this source for this need. Still no network.

    The date ceiling is baked into the query rather than left to a filter on
    the way back in. For `need="framing"` it is the day before the decision, so
    a framing fetch cannot *ask* for outcome material — the same invariant the
    `published_date` predicate enforces on `source_chunks`, applied a step
    earlier where an accident is cheaper.
    """
    decision = date.fromisoformat(question["decision_date"])
    start, end = _bounds(source, need_window(need, decision))
    retrieval = question["retrieval"]

    if source.key == "voteview":
        bill = retrieval.get("bill_number")
        congress = retrieval.get("congress")
        if not bill or not congress:
            raise ValueError(
                f"{question['id']!r} has no bill_number/congress, which "
                f"{source.key} needs to identify the measure"
            )
        # No date bounds: Voteview is one bulk download keyed by bill, and the
        # roll calls for a measure are the roll calls for that measure.
        return {"congress": congress, "bill_number": normalize_bill_number(bill)}

    terms = " OR ".join(f'"{t}"' for t in retrieval["search_terms"])
    if source.key.startswith("govinfo:"):
        return {
            "collection": source.collection,
            "query": terms,
            "published_from": start,
            "published_to": end,
        }
    if source.key == "loc:chronicling-america":
        # `dates=FROM/TO`, not start_date/end_date. loc.gov accepts the latter,
        # returns HTTP 200, and **silently ignores them**: asking for
        # 1909-01-16..1919-01-15 came back with pages from 1933, 1926 and 1930 —
        # 31,560 matches instead of 75. For the Prohibition question that means
        # a 1933 article about repeal filed as pre-vote framing for "do you
        # support ratification?", which is rule #1 failing in the quietest
        # possible way.
        #
        # The fetch layer must still check `published_date` on every returned
        # record. A parameter the API accepts is not a parameter the API honours,
        # and only the response can settle it.
        return {"q": terms, "dates": f"{start}/{end}", "fo": "json"}

    if source.key == "hansard":
        # Hansard is browsed, not searched: there is no query endpoint taking
        # terms and a date range. What it has is a sittings index per day, and
        # sections addressed under it — so the "query" is the window to walk,
        # and `pipeline/hansard.py` does the walking.
        #
        # That is a better position than it sounds. Every other source here
        # returns records whose dates have to be re-checked on arrival, because
        # a date parameter the API accepts is not one it honours. Here the date
        # is the address: a sitting fetched from `/commons/1946/apr/30/...` is
        # from 30 April 1946 or it is a 404. The boundary cannot be got wrong by
        # trusting a filter, because there is no filter to trust.
        return {
            "house": "commons",
            "from": start,
            "to": end,
            "terms": retrieval["search_terms"],
        }

    raise ValueError(f"no query shape defined for source {source.key!r}")
