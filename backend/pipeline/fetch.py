"""One HTTP client for every source, and the four things it exists to do.

Before this there were four clients: `embedding` had retry and backoff,
`voteview` had a bare `urlopen`, and `hansard` and `govinfo` had nothing at all
— so a transient 503 partway through ingesting 59 GovInfo granules would simply
crash the run. Writing the adapters first was the right order (a shared layer
extracted from three real cases beats one guessed at in advance) but three
unprotected clients was not the place to stop.

**1. Retry with backoff and jitter.** A 429 or a 503 is a rate limit, not a
failure; the same request succeeds shortly. Jitter matters because parallel
workers that all sleep the same interval come back in lockstep and re-trigger
the limit together.

**2. A per-host circuit breaker, and this is the one that is not decoration.**
loc.gov resets its block countdown on *any* request made during a block, so
ordinary per-request backoff turns a one-hour block into an indefinite one —
each polite retry re-arms the timer. The breaker's contract is therefore
stronger than "stop retrying": while open it must **send nothing at all** to
that host, failing locally instead. Everything else here is an optimisation;
this is a correctness property, and it is the reason the module exists.

It is also the one piece that has never been tested against reality, because
triggering a real block costs an hour. What is tested is the state machine,
against an injected clock. That is a weaker claim and is worth saying out loud.

**3. Content-type validation.** The source-retrieval spike found loc.gov's older
OCR path answering a Cloudflare challenge *page* rather than an HTTP error — 200
OK, `text/html`, and a naive client stores the challenge as though it were the
source text. Callers declare what they expect and a mismatch raises.

**4. A content-addressed cache.** Keyed on the request, so re-fetching after a
corpus rebuild costs nothing. `source_documents` already records a sha256 of the
stored text, but that is provenance for what was kept, not a way to avoid the
network for what was dropped — deleting a question's documents and re-ingesting
currently re-downloads every one.
"""

import hashlib
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from shared.settings import get_settings

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "http"

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 2.0
# Status codes worth retrying: a rate limit or a transient server fault, never
# a 404 or a 401, which will say the same thing however long you wait.
RETRYABLE = frozenset({429, 500, 502, 503, 504})
# Codes that mean "you are being kept out", as opposed to "that broke". These
# trip the breaker; a 500 does not, because a server fault is not a block and
# refusing to talk to the host would only prolong the outage.
BLOCKING = frozenset({403, 429})

CONSECUTIVE_BLOCKS_TO_OPEN = 3
OPEN_SECONDS = 3600.0  # loc.gov's block is an hour; assume the worst elsewhere


class FetchError(RuntimeError):
    """The request failed and is not worth retrying here."""


class CircuitOpen(FetchError):
    """This host is blocking us and must not be contacted at all yet.

    Distinct from `FetchError` so a caller can tell "this source is
    unavailable, come back later" from "this document is not there".
    """


class WrongContentType(FetchError):
    """A 200 that is not what was asked for — usually a challenge or error page
    served with a success status."""


@dataclass
class _Breaker:
    consecutive_blocks: int = 0
    opened_at: float | None = None


_breakers: dict[str, _Breaker] = {}


@dataclass
class _Clock:
    """Injectable so the breaker's state machine is testable without waiting an
    hour, which is exactly why the real behaviour has never been observed."""

    now: object = field(default=time.monotonic)
    sleep: object = field(default=time.sleep)


clock = _Clock()


def reset() -> None:
    """Forget every breaker. For tests, and for a deliberate operator override."""
    _breakers.clear()


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def _check_closed(host: str) -> None:
    breaker = _breakers.get(host)
    if breaker is None or breaker.opened_at is None:
        return
    elapsed = clock.now() - breaker.opened_at
    if elapsed < OPEN_SECONDS:
        raise CircuitOpen(
            f"{host} is blocking; {OPEN_SECONDS - elapsed:.0f}s left before the "
            "next probe. Nothing is being sent — a request during a block can "
            "restart the host's own countdown."
        )
    # Half-open: let exactly one request through. Clearing `opened_at` first
    # means a failure re-opens from scratch rather than extending the old
    # window, so the cooldown is measured from the last refusal.
    breaker.opened_at = None
    breaker.consecutive_blocks = CONSECUTIVE_BLOCKS_TO_OPEN - 1
    log.info("%s: circuit half-open, sending one probe", host)


def _record(host: str, *, blocked: bool) -> None:
    breaker = _breakers.setdefault(host, _Breaker())
    if not blocked:
        breaker.consecutive_blocks = 0
        breaker.opened_at = None
        return
    breaker.consecutive_blocks += 1
    if breaker.consecutive_blocks >= CONSECUTIVE_BLOCKS_TO_OPEN:
        breaker.opened_at = clock.now()
        log.warning(
            "%s: circuit OPEN after %d blocking responses; sending nothing for %.0fs",
            host,
            breaker.consecutive_blocks,
            OPEN_SECONDS,
        )


def _cache_path(key: str) -> Path:
    return CACHE_DIR / key[:2] / f"{key}.bin"


def _cache_key(url: str, data: bytes | None) -> str:
    digest = hashlib.sha256(url.encode())
    if data:
        digest.update(b"\0")
        digest.update(data)
    return digest.hexdigest()


def request(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    expect: tuple[str, ...] | None = None,
    cache: bool = True,
    timeout: int = 90,
) -> bytes:
    """Fetch `url`, politely, and return the body.

    `expect` is a tuple of content-type prefixes. Supplying it is what
    distinguishes "the source answered" from "something answered with a 200".
    """
    key = _cache_key(url, data)
    path = _cache_path(key)
    if cache and path.exists():
        log.debug("cache hit for %s", url)
        return path.read_bytes()

    host = _host(url)
    sent = {"User-Agent": get_settings().user_agent} | (headers or {})

    for attempt in range(MAX_ATTEMPTS):
        _check_closed(host)  # before every attempt, including retries
        try:
            with urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(url, data=data, headers=sent), timeout=timeout
            ) as response:
                body = response.read()
                content_type = (response.headers.get("Content-Type") or "").lower()
        except urllib.error.HTTPError as exc:
            blocked = exc.code in BLOCKING
            _record(host, blocked=blocked)
            if exc.code not in RETRYABLE or attempt == MAX_ATTEMPTS - 1:
                detail = exc.read()[:200].decode("utf-8", "replace")
                raise FetchError(f"{url} returned HTTP {exc.code}: {detail}") from exc
            delay = BASE_BACKOFF_SECONDS * 2**attempt + random.uniform(0, 1)
            log.warning(
                "%s returned %d; backing off %.1fs (attempt %d/%d)",
                host,
                exc.code,
                delay,
                attempt + 1,
                MAX_ATTEMPTS,
            )
            clock.sleep(delay)
            continue
        except urllib.error.URLError as exc:
            # A refused connection or a DNS failure is not the host blocking us.
            if attempt == MAX_ATTEMPTS - 1:
                raise FetchError(f"{url} unreachable: {exc.reason}") from exc
            clock.sleep(BASE_BACKOFF_SECONDS * 2**attempt + random.uniform(0, 1))
            continue

        _record(host, blocked=False)
        if expect and not any(content_type.startswith(e) for e in expect):
            # 200 OK with the wrong body. The spike's Cloudflare challenge page
            # arrived exactly like this, and storing it would have put an
            # interstitial into the corpus as source text.
            raise WrongContentType(
                f"{url} returned Content-Type {content_type!r}, expected one of "
                f"{list(expect)} — a success status with the wrong body is how "
                "a challenge page gets stored as source material"
            )
        if cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        return body

    raise FetchError("unreachable")
