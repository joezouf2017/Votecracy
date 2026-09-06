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
It is **persisted to disk**, which is not a refinement — an in-process breaker
would be defeated by the exact sequence it exists to prevent. loc.gov blocks
you, the run dies or you interrupt it, you start it again, and a fresh process
begins with an empty breaker: the first thing it does is send a request during
the block and restart the host's countdown. So the state is written to
`.cache/http/breakers.json` and read back on load, and `opened_at` is wall-clock
rather than monotonic, because a monotonic clock's epoch is meaningless across
a restart.
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

import contextlib
import hashlib
import http.client
import json
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
#
# 520-527 are Cloudflare's own range, and they are in here because loc.gov threw
# a 520 on the first real run of this module. loc.gov sits behind Cloudflare —
# the same reason the spike met a challenge page instead of an HTTP error — and
# a 520 is "the origin did something the proxy could not parse", which is a
# transient fault rather than a refusal. Retryable, and deliberately *not* in
# BLOCKING: treating it as a block would open the breaker on a bad minute and
# lock the source out for an hour.
RETRYABLE = frozenset({429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 527})
# Codes that mean "you are being kept out", as opposed to "that broke". These
# trip the breaker; a 500 does not, because a server fault is not a block and
# refusing to talk to the host would only prolong the outage.
BLOCKING = frozenset({403, 429})

# A floor on how often one host is contacted, in seconds. **A floor, not a
# quota**: it stops a tight loop hammering a source, and it does not enforce
# GovInfo's 1,000-per-hour gateway cap, which would need a token bucket and a
# longer memory. Worth having anyway, because for loc.gov the reactive path is
# the expensive one — a 429 there costs an hour, so not earning it is worth
# more than reacting well to it.
MIN_INTERVAL = {
    "www.loc.gov": 1.0,
    "api.govinfo.gov": 0.35,
    "api.parliament.uk": 0.35,
}
_last_request: dict[str, float] = {}

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


_BREAKER_STATE = "breakers.json"
_breakers: dict[str, _Breaker] | None = None


def _state_path() -> Path:
    return CACHE_DIR / _BREAKER_STATE


def _load() -> dict[str, _Breaker]:
    """Read persisted breaker state, tolerating anything unreadable.

    A corrupt or missing file must not stop the pipeline — it degrades to an
    empty breaker, which is the behaviour before this existed. The failure it
    protects against is a restart *during* a block, and that is a file that
    was written seconds ago by a process that then died.
    """
    global _breakers
    if _breakers is not None:
        return _breakers
    _breakers = {}
    try:
        raw = json.loads(_state_path().read_text())
        for host, value in raw.items():
            _breakers[host] = _Breaker(
                consecutive_blocks=int(value.get("consecutive_blocks", 0)),
                opened_at=value.get("opened_at"),
            )
    except (OSError, ValueError, AttributeError):
        pass
    return _breakers


def _save() -> None:
    """Write only the hosts that are open or part-way there.

    A closed breaker is the default, so persisting it would grow the file with
    every host ever contacted for no benefit.
    """
    state = {
        host: {"consecutive_blocks": b.consecutive_blocks, "opened_at": b.opened_at}
        for host, b in (_breakers or {}).items()
        if b.opened_at is not None or b.consecutive_blocks
    }
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except OSError:  # a cache we cannot write is not a reason to stop fetching
        log.warning("could not persist circuit-breaker state to %s", _state_path())


@dataclass
class _Clock:
    """Injectable so the breaker's state machine is testable without waiting an
    hour, which is exactly why the real behaviour has never been observed.

    `now` is wall-clock, not monotonic. Monotonic would be the better choice for
    in-process timing and is the wrong one here: the breaker is persisted across
    restarts, and a monotonic reading from a dead process means nothing to a
    live one.
    """

    now: object = field(default=time.time)
    sleep: object = field(default=time.sleep)


clock = _Clock()


def reset() -> None:
    """Forget every breaker, on disk as well. For tests, and for a deliberate
    operator override when a host is known to be available again."""
    global _breakers
    _breakers = {}
    with contextlib.suppress(OSError):
        _state_path().unlink(missing_ok=True)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def _check_closed(host: str) -> None:
    breaker = _load().get(host)
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
    _save()
    log.info("%s: circuit half-open, sending one probe", host)


def _record(host: str, *, blocked: bool) -> None:
    breaker = _load().setdefault(host, _Breaker())
    if not blocked:
        breaker.consecutive_blocks = 0
        breaker.opened_at = None
        _save()
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
    _save()


def _wait_turn(host: str) -> None:
    """Hold off until this host's minimum interval has passed."""
    floor = MIN_INTERVAL.get(host)
    if not floor:
        return
    since = clock.now() - _last_request.get(host, 0.0)
    if since < floor:
        clock.sleep(floor - since)
    _last_request[host] = clock.now()


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
        _wait_turn(host)
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
        except (http.client.HTTPException, TimeoutError, ConnectionError) as exc:
            # The transport failed rather than the request. loc.gov produced an
            # `IncompleteRead(6542 bytes read, 6691 more expected)` on this
            # module's first real run — the connection dropped part-way through
            # the body, which happens *inside* `response.read()` and so is
            # neither an HTTPError nor a URLError. Nothing above catches it, and
            # before this layer existed it simply killed the run.
            #
            # Also not a block: the host was willing to talk. Retry, do not trip
            # the breaker.
            if attempt == MAX_ATTEMPTS - 1:
                raise FetchError(f"{url} transport failed: {exc!r}") from exc
            log.warning("%s: %s; retrying", host, type(exc).__name__)
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
