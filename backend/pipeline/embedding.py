"""Turning chunks into vectors, and the two ways that goes quietly wrong.

Both were measured against the live API rather than read in a doc, and both
fail without an error — the queries keep working and just return worse matches,
which is the hardest kind of bug to notice in a retrieval system.

**Truncated vectors are not unit vectors.** `gemini-embedding-001` returns 3072
dimensions with norm 1.0. Asking for 768 gives exactly the first 768 components
of that vector — a prefix, confirmed — whose norm is about 0.59. pgvector's
cosine operator `<=>` normalises internally and survives this; inner product
`<#>` and L2 `<->` do not. Renormalising on write means all three operators are
correct, rather than leaving a trap for whoever later swaps the operator for
the faster one.

**Documents and queries are embedded differently.** The model is asymmetric:
`RETRIEVAL_DOCUMENT` and `RETRIEVAL_QUERY` produce different vectors for the
same text. Using one everywhere costs recall silently.

The 768 dimensions are a deliberate choice, not the default. Retrieval here is
always scoped to one question — a few hundred chunks, not the whole table — so
the ranking problem is easy and the extra dimensions buy little. 768 keeps the
stored vectors a quarter the size: measured at 500 questions, 472 MB against
roughly 1.9 GB.
"""

import json
import logging
import math
import random
import time
import urllib.error
import urllib.request

from shared.settings import get_settings

log = logging.getLogger(__name__)

MODEL = "gemini-embedding-001"
DIMENSIONS = 768
_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}"
# Measured, not guessed: a batch of 50 draws a 429 on the free tier while
# single requests succeed, so the limit bites on batch size rather than on the
# request count alone.
BATCH_SIZE = 10
# A 429 here is a rate limit, not a failure — the same request will succeed
# shortly. What must not happen is retrying immediately: loc.gov resets its
# block countdown on any request made during the block, and an eager retry
# loop turns a one-minute wait into an indefinite one. Assume every API works
# that way.
MAX_ATTEMPTS = 6
BASE_BACKOFF_SECONDS = 2.0


class EmbeddingError(RuntimeError):
    """The API refused. Never swallowed: a chunk with no vector is invisible to
    retrieval, and a pipeline that carried on would produce a question whose
    sources exist but cannot be found."""


def _post(path: str, body: dict) -> dict:
    key = get_settings().gemini_api_key.get_secret_value()
    if not key:
        raise EmbeddingError("GEMINI_API_KEY is not set; see .env.example")
    request = urllib.request.Request(
        f"{_ENDPOINT}:{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
            "User-Agent": "votecracy/0.1",
        },
    )
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:300]
            if exc.code not in (429, 500, 503) or attempt == MAX_ATTEMPTS - 1:
                raise EmbeddingError(
                    f"{MODEL} returned HTTP {exc.code}: {detail}"
                ) from exc
            # Exponential, with jitter so parallel workers do not all come back
            # at the same instant and re-trigger the limit together.
            delay = BASE_BACKOFF_SECONDS * 2**attempt + random.uniform(0, 1)
            log.warning(
                "%s returned %d; backing off %.1fs (attempt %d/%d)",
                MODEL,
                exc.code,
                delay,
                attempt + 1,
                MAX_ATTEMPTS,
            )
            time.sleep(delay)
    raise EmbeddingError("unreachable")


def _unit(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in vector))
    if length == 0:
        raise EmbeddingError("the API returned a zero vector, which has no direction")
    return [x / length for x in vector]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Vectors for chunks being stored. Unit length, `DIMENSIONS` long."""
    out: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        payload = {
            "requests": [
                {
                    "model": f"models/{MODEL}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": "RETRIEVAL_DOCUMENT",
                    "outputDimensionality": DIMENSIONS,
                }
                for text in batch
            ]
        }
        response = _post("batchEmbedContents", payload)
        vectors = [e["values"] for e in response["embeddings"]]
        if len(vectors) != len(batch):
            raise EmbeddingError(
                f"asked for {len(batch)} vectors, got {len(vectors)} — a silent "
                "misalignment would attach every vector to the wrong chunk"
            )
        out.extend(_unit(v) for v in vectors)
        log.info("embedded %d/%d chunks", len(out), len(texts))
    return out


def embed_query(text: str) -> list[float]:
    """A vector for something being searched *for*, not stored."""
    response = _post(
        "embedContent",
        {
            "content": {"parts": [{"text": text}]},
            "taskType": "RETRIEVAL_QUERY",
            "outputDimensionality": DIMENSIONS,
        },
    )
    return _unit(response["embedding"]["values"])
