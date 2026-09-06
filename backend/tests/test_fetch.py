"""The shared HTTP client, and the property that cannot be tested for real.

No network. `urllib.request.urlopen` is replaced throughout, because what is
under test is the policy around a request, not the request.

**The circuit breaker's contract is stronger than "stop retrying".** loc.gov
resets its block countdown on any request received *during* a block, so a polite
retry re-arms the timer and turns an hour into forever. The breaker must
therefore send nothing at all while open — which is asserted here by counting
calls, not by checking a return value.

That is the one behaviour never observed against the real host, because
triggering a block costs an hour. These tests pin the state machine against an
injected clock. It is a weaker claim than "verified", and the module docstring
says so too.
"""

import urllib.error
from io import BytesIO

import pytest

from pipeline import fetch


class _Response:
    def __init__(self, body=b"ok", content_type="text/html"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code):
    return urllib.error.HTTPError("http://x/", code, "boom", {}, BytesIO(b"detail"))


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """Count what actually reaches the network, and never touch the real cache."""
    monkeypatch.setattr(fetch, "CACHE_DIR", tmp_path / "http")
    seen = []

    def install(*responses):
        queue = list(responses)

        def fake(request, timeout=None):
            seen.append(request.full_url)
            item = queue.pop(0) if queue else _Response()
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(fetch.urllib.request, "urlopen", fake)
        return seen

    monkeypatch.setattr(fetch.clock, "sleep", lambda _: None)
    fetch.reset()
    yield install
    fetch.reset()


# --- the circuit breaker ------------------------------------------------------


def test_the_breaker_opens_after_repeated_blocking_and_then_sends_nothing(calls):
    """The property the module exists for.

    A request made during a block restarts loc.gov's own countdown, so "stop
    retrying" is not enough — nothing may leave the process. Asserted by call
    count, because a caller cannot tell a local refusal from a remote one by
    looking at the exception alone.
    """
    seen = calls(*[_http_error(429)] * 20)

    with pytest.raises(fetch.CircuitOpen):
        fetch.request("https://loc.gov/a", cache=False)

    # The breaker opens *during* the request, so a single call cannot spend all
    # MAX_ATTEMPTS against a host that is blocking. That matters: five polite
    # retries would be five more chances to restart loc.gov's countdown.
    assert len(seen) == fetch.CONSECUTIVE_BLOCKS_TO_OPEN < fetch.MAX_ATTEMPTS
    sent_before = len(seen)

    with pytest.raises(fetch.CircuitOpen):
        fetch.request("https://loc.gov/b", cache=False)
    assert len(seen) == sent_before, "a request was sent while the circuit was open"


def test_the_breaker_is_per_host(calls):
    """A block at one host says nothing about another. Sharing one breaker
    would take the whole pipeline down when a single source rate-limits."""
    seen = calls(*[_http_error(429)] * fetch.CONSECUTIVE_BLOCKS_TO_OPEN, _Response())
    with pytest.raises(fetch.CircuitOpen):
        fetch.request("https://loc.gov/a", cache=False)
    with pytest.raises(fetch.CircuitOpen):
        fetch.request("https://loc.gov/b", cache=False)

    before = len(seen)
    assert fetch.request("https://api.govinfo.gov/x", cache=False) == b"ok"
    assert len(seen) == before + 1


def test_a_server_fault_does_not_trip_the_breaker(calls):
    """500 is "that broke", not "you are being kept out". Refusing to talk to a
    host over a transient fault would only prolong the outage — and unlike a
    block, retrying does not make it worse."""
    seen = calls(*[_http_error(500)] * 20)
    with pytest.raises(fetch.FetchError):
        fetch.request("https://api.govinfo.gov/a", cache=False)
    assert fetch._breakers["api.govinfo.gov"].opened_at is None
    # And it does spend all its attempts, unlike the blocking case above.
    assert len(seen) == fetch.MAX_ATTEMPTS


def test_the_breaker_closes_after_the_cooldown_and_probes_once(calls, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(fetch.clock, "now", lambda: now[0])
    seen = calls(*[_http_error(429)] * fetch.CONSECUTIVE_BLOCKS_TO_OPEN, _Response())

    with pytest.raises(fetch.CircuitOpen):
        fetch.request("https://loc.gov/a", cache=False)
    with pytest.raises(fetch.CircuitOpen):
        fetch.request("https://loc.gov/b", cache=False)

    now[0] += fetch.OPEN_SECONDS + 1
    before = len(seen)
    assert fetch.request("https://loc.gov/c", cache=False) == b"ok"
    assert len(seen) == before + 1


def test_a_success_resets_the_failure_count(calls):
    calls(_http_error(429), _Response(), *[_http_error(429)] * 10)
    fetch.request("https://loc.gov/a", cache=False)  # retries once, then succeeds
    assert fetch._breakers["loc.gov"].consecutive_blocks == 0


# --- content-type validation --------------------------------------------------


def test_a_200_with_the_wrong_content_type_raises(calls):
    """The spike found loc.gov's old OCR path answering a Cloudflare challenge
    *page* rather than an HTTP error: 200 OK, text/html, and a naive client
    stores the interstitial as source text."""
    calls(_Response(b"<html>checking your browser</html>", "text/html"))
    with pytest.raises(fetch.WrongContentType, match="challenge page"):
        fetch.request("https://loc.gov/ocr", expect=("text/plain",), cache=False)


def test_no_expectation_means_no_check(calls):
    calls(_Response(b"anything", "application/octet-stream"))
    assert fetch.request("https://x/y", cache=False) == b"anything"


def test_a_rejected_body_is_not_cached(calls, tmp_path):
    """Otherwise the challenge page is served from disk forever after."""
    calls(_Response(b"<html>nope</html>", "text/html"))
    with pytest.raises(fetch.WrongContentType):
        fetch.request("https://loc.gov/ocr", expect=("text/plain",), cache=True)
    assert not list((tmp_path / "http").rglob("*.bin"))


# --- retry --------------------------------------------------------------------


def test_a_retryable_status_is_retried_then_succeeds(calls):
    seen = calls(_http_error(503), _http_error(503), _Response(b"finally"))
    assert fetch.request("https://x/y", cache=False) == b"finally"
    assert len(seen) == 3


def test_a_404_is_not_retried(calls):
    """It will say the same thing however long you wait."""
    seen = calls(_http_error(404))
    with pytest.raises(fetch.FetchError, match="404"):
        fetch.request("https://x/y", cache=False)
    assert len(seen) == 1


def test_retries_are_bounded(calls):
    seen = calls(*[_http_error(503)] * 50)
    with pytest.raises(fetch.FetchError):
        fetch.request("https://x/y", cache=False)
    assert len(seen) == fetch.MAX_ATTEMPTS


# --- the cache ----------------------------------------------------------------


def test_a_cached_response_does_not_reach_the_network(calls):
    seen = calls(_Response(b"body"))
    assert fetch.request("https://x/y") == b"body"
    assert fetch.request("https://x/y") == b"body"
    assert len(seen) == 1


def test_the_cache_key_covers_the_body_not_just_the_url(calls):
    """Two POSTs to one endpoint are different requests. Keying on the URL alone
    would serve the first body's answer to the second."""
    seen = calls(_Response(b"first"), _Response(b"second"))
    assert fetch.request("https://x/y", data=b"a") == b"first"
    assert fetch.request("https://x/y", data=b"b") == b"second"
    assert len(seen) == 2


def test_cache_can_be_declined(calls):
    seen = calls(_Response(b"a"), _Response(b"b"))
    fetch.request("https://x/y", cache=False)
    fetch.request("https://x/y", cache=False)
    assert len(seen) == 2
