"""Historic Hansard — the UK source, and the one where dates are structural.

`api.parliament.uk/historic-hansard` covers 1803-2005, needs no key, and returns
one addressable document per sitting per debate. That last property is why this
adapter is short and `volumes.py` is not.

**The whole volume-straddling problem does not arise here.** A bound
Congressional Record volume is two to three weeks in one file, so four of five
decision volumes contain material from both sides of their decision date and
`published_date` has to be a conservative guess (see `volumes.py`). A Hansard
section is one debate on one named day, so `published_date` is a fact rather
than a compromise, and nothing is lost to the safe direction.

There is also no OCR. The Congressional Record route spends `normalise` undoing
line-break hyphenation and `_term_pattern` matching across column breaks; none
of that applies.

**Addressing, which took some working out.** The sitting is JSON:

    /commons/1946/apr/30.js       -> the day's top-level sections

Its sections are *not* nested under their parent in the URL, and `.js` on a
section 404s. Child slugs appear as relative links in the parent section's HTML,
and the child is addressed directly under the date:

    /commons/1946/apr/30/orders-of-the-day              -> lists children
    /commons/1946/apr/30/national-health-service-bill   -> the debate itself

So this fetches HTML and strips it. That is not a workaround for a missing API;
it is the API.

**No extraction step.** A section *is* the relevant material — the whole point
of `extract_passages` is finding the 1.18% of a Congressional Record volume that
is on topic, and a Hansard section is 100% on topic by construction.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date

from pipeline import fetch as http
from pipeline import ingest

log = logging.getLogger(__name__)

BASE = "https://api.parliament.uk/historic-hansard"
SOURCE_KEY = "hansard"

_MONTHS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)
_SCRIPTS = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Sitting:
    """One debate, on one day, in one house — and therefore one exact date."""

    question_id: str
    house: str  # "commons" or "lords"
    day: date
    slug: str  # e.g. "national-health-service-bill"
    title: str

    @property
    def path(self) -> str:
        return (
            f"{self.house}/{self.day.year}/{_MONTHS[self.day.month - 1]}/"
            f"{self.day.day:02d}/{self.slug}"
        )

    @property
    def url(self) -> str:
        return f"{BASE}/{self.path}"

    @property
    def external_id(self) -> str:
        """The Hansard path. It is the source's own identifier, so it is what
        belongs in `external_id` — no invented scheme, and it round-trips to a
        URL a reviewer can open."""
        return self.path


def to_text(html: str) -> str:
    """Strip the page to its words.

    Scripts and styles go first: the page carries an inline analytics blob, and
    stripping tags without removing it would put JavaScript into the corpus as
    though it were debate.
    """
    return _BLANKS.sub(
        "\n\n", _SPACES.sub(" ", _TAGS.sub(" ", _SCRIPTS.sub(" ", html)))
    )


def fetch(sitting: Sitting) -> str:
    """Through the shared client, so this gets backoff and a circuit breaker.

    `expect` is text/html because that is what Hansard serves — and a 200
    carrying anything else means something answered that is not Hansard.
    """
    return http.request(sitting.url, expect=("text/html",)).decode("utf-8", "replace")


def role_for(sitting: Sitting, decision: date) -> str:
    """Derived from the sitting's own date, which is exact.

    No conservative rounding is needed. A sitting either predates the decision
    or it does not, and the day it happened is stated rather than inferred.
    """
    return ingest.role_for_date(sitting.day, decision)


def ingest_sitting(sitting: Sitting, decision: date) -> int:
    """Fetch, strip, chunk and store one debate. Returns chunks written."""
    if ingest.already_stored(SOURCE_KEY, sitting.external_id, sitting.question_id):
        log.info("%s already stored; skipping", sitting.path)
        return 0

    text = ingest.normalise(to_text(fetch(sitting)))
    passage = ingest.Passage(0, len(text), text)
    document_id = ingest.store_passage(
        question_id=sitting.question_id,
        source_key=SOURCE_KEY,
        external_id=sitting.external_id,
        url=sitting.url,
        title=sitting.title,
        published_date=sitting.day,
        content_type="text/html",
        passage=passage,
        role=role_for(sitting, decision),
    )
    log.info(
        "%s -> document %d, role=%s, published_date=%s, %d chars",
        sitting.path,
        document_id,
        role_for(sitting, decision),
        sitting.day,
        len(text),
    )
    return document_id


# Every Commons debate on the National Health Service inside its framing
# window, found by walking the sittings index rather than assumed: 37 sitting
# days from 1 March 1946, and March 1944 for the White Paper that preceded it.
#
# Two things that search settled:
#
# - **Nothing between 1 March and 29 April 1946.** The bill went straight to a
#   three-day second reading, so the framing corpus really is that debate plus
#   1944, and not something a wider crawl would find.
# - **The 1944 White Paper debate is the argument.** "A National Health Service"
#   was debated 16-17 March 1944, two years before the bill and comfortably
#   inside the ten-year lookback. That is where the question the player is being
#   asked was actually contested.
#
# Deliberately excluded: `ministry-of-health-war-services` (1 March 1944), which
# is wartime service organisation rather than the case for a national service.
SITTINGS = (
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1944, 3, 16),
        "national-health-service",
        "National Health Service (White Paper)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1944, 3, 17),
        "national-health-service",
        "National Health Service (White Paper)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1946, 4, 30),
        "national-health-service-bill",
        "National Health Service Bill",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1946, 5, 1),
        "national-health-service-bill",
        "National Health Service Bill",
    ),
    # On the decision date, so vote_record by derivation. The division is here:
    # "Ayes, 359; Noes, 172", plus the defeated opposition amendment at
    # "Ayes, 180; Noes, 344".
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1946, 5, 2),
        "national-health-service-bill",
        "National Health Service Bill",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1946, 5, 2),
        "national-health-service-money",
        "National Health Service (Money)",
    ),
    # --- outcome: the launch year -------------------------------------------
    #
    # Found by walking all 151 sitting days of 1948, the year the service
    # actually started (5 July 1948). Before this the NHS outcome corpus was
    # **zero days wide** — the reveal talked about what the NHS became and the
    # only post-decision material was the sitting the vote happened in.
    #
    # These are `outcome` rather than `vote_record` by derivation: two years
    # past the decision is aftermath, not a record of the division.
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1948, 2, 9),
        "national-health-service",
        "National Health Service",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1948, 2, 9),
        "national-health-service-scotland",
        "National Health Service (Scotland)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1948, 4, 7),
        "national-health-service-doctors",
        "National Health Service (Doctors)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1948, 5, 3),
        "national-health-dental-services",
        "National Health (Dental Services)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1948, 6, 23),
        "national-health-service-regulations",
        "NHS (Regulations)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1948, 6, 23),
        "national-health-service-scotland",
        "National Health Service (Scotland)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1948, 7, 2),
        "national-health-service-lists-of-doctors",
        "NHS (Lists of Doctors)",
    ),
    # --- outcome: the first full year ---------------------------------------
    #
    # 1949, found by walking all 165 of its sitting days. The reveal claims
    # "5 million dental treatments and 8 million optical appointments in its
    # first year", and the first full year is where that would have been
    # reported -- note `national-health-service-dentists` below, which is
    # directly on it.
    #
    # The seventeen debates found include several procedural readings of the
    # Amendment Bill; those are legislative process rather than evidence of how
    # the service was working, so what is taken is the service-operation ones.
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1949, 1, 21),
        "national-health-service",
        "National Health Service",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1949, 2, 9),
        "national-health-service-doctors-lists",
        "NHS (Doctors' Lists)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1949, 2, 15),
        "national-health-service-dentists",
        "NHS (Dentists)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1949, 2, 17),
        "national-health-service-england-and-wales",
        "NHS (England and Wales)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1949, 6, 2),
        "national-health-service-chiropody",
        "NHS (Chiropody)",
    ),
    Sitting(
        "uk-national-health-service-1946",
        "commons",
        date(1949, 7, 27),
        "national-health-service-scotland",
        "National Health Service (Scotland)",
    ),
)
