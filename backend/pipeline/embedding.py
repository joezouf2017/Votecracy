"""Turning chunks into vectors, and the ways that goes quietly wrong.

Every failure named here was measured against a live API rather than read in a
doc, and **none of them raise**. The queries keep working and return worse
matches, which is the hardest kind of bug to notice in a retrieval system.

**Truncated vectors are not unit vectors.** A model that natively emits 3072
dimensions with norm 1.0 returns, when asked for 768, exactly the first 768
components — a prefix, confirmed — whose norm is about 0.59. pgvector's cosine
operator `<=>` normalises internally and survives this; inner product `<#>` and
L2 `<->` do not. Renormalising on write means all three operators are correct
rather than leaving a trap for whoever swaps the operator for a faster one.

**Documents and queries are embedded differently.** Retrieval models are
asymmetric: the same text embedded as a document and as a query gives different
vectors. Using one everywhere costs recall silently. OpenRouter spells this
`input_type`; Gemini's own API calls it `taskType`.

**Results come back with an index, and it is not decoration.** The response is
sorted by `index` before use. Trusting array order is how every vector ends up
attached to the wrong chunk, which produces a retrieval system that is
confidently, unfalsifiably wrong.

**`input_type` is dropped by the Batch API.** So embedding does not go through
it, and pays full price for the asymmetry — $2.60 more across 500 questions.
See `docs/metrics/cost-model.md`.

The dimension is not configurable here. `chunk_embeddings.embedding` is
`vector(768)` in a migration, so the width belongs to `shared.db.engine` and is
imported from there; a second 768 in this file is exactly the kind of pair that
drifts. The *model* is configurable, and swapping it is safe because `model` is
part of `chunk_embeddings`' primary key — two models' vectors coexist and
`retrieval.nearest` makes the caller name one.
"""

import json
import logging
import math
import random
import time
import urllib.error
import urllib.request

from shared.db.engine import EMBEDDING_DIM
from shared.settings import get_settings

log = logging.getLogger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/embeddings"

# How many texts go in one request. OpenRouter accepts an array on `input` and
# embeds every element in one call. 10 was measured against Google's free tier,
# where a batch of 50 drew a 429 while single requests succeeded — the limit
# bit on batch size rather than request count. The gateway's limits are its
# own, so this is a starting point to re-measure, not a finding.
BATCH_SIZE = 10

# A 429 is a rate limit, not a failure: the same request succeeds shortly. What
# must not happen is retrying immediately — loc.gov resets its block countdown
# on any request made during a block, and an eager retry loop turns a
# one-minute wait into an indefinite one. Assume every API works that way.
MAX_ATTEMPTS = 6
BASE_BACKOFF_SECONDS = 2.0

# Refuse providers that train on what is sent to them. A request that cannot be
# routed to one fails rather than quietly going somewhere it should not — which
# is the behaviour worth having, because the alternative is silent.
_PROVIDER_POLICY = {"data_collection": "deny"}

# Models do not agree on how a query is marked as a query, and disagreeing with
# them is silent. Measured through OpenRouter on 2026-09-05, embedding one
# sentence both ways and comparing:
#
#   google/gemini-embedding-001   input_type honoured      doc vs query  0.8786
#   qwen/qwen3-embedding-8b       input_type IGNORED       doc vs query  1.000000
#
# Qwen3's convention is an instruction prefix inside the text rather than a
# request parameter. Applying it moved separation on a three-document probe
# from +0.3956 to +0.4247, so the asymmetry is real and recoverable — but only
# if this table exists. Without it Qwen3 silently embeds queries as documents
# and simply retrieves slightly worse, forever.
#
# Keyed on the model slug because that is what `chunk_embeddings.model` stores,
# so a vector and the convention that produced it cannot come apart.
_QUERY_TEMPLATES = {
    "qwen/qwen3-embedding-8b": (
        "Instruct: Given a web search query, retrieve relevant passages that "
        "answer the query\nQuery: {text}"
    ),
    "qwen/qwen3-embedding-4b": (
        "Instruct: Given a web search query, retrieve relevant passages that "
        "answer the query\nQuery: {text}"
    ),
}


class EmbeddingError(RuntimeError):
    """The API refused, and it is never swallowed.

    A chunk with no vector is invisible to retrieval, so a pipeline that
    carried on would build a question whose sources exist and cannot be found.
    """


def _post(body: dict) -> dict:
    settings = get_settings()
    key = settings.openrouter_api_key.get_secret_value()
    if not key:
        raise EmbeddingError("OPENROUTER_API_KEY is not set; see .env.example")
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": settings.user_agent,
        },
    )
    model = body.get("model")
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:300]
            if exc.code not in (429, 500, 502, 503) or attempt == MAX_ATTEMPTS - 1:
                raise EmbeddingError(
                    f"{model} returned HTTP {exc.code}: {detail}"
                ) from exc
            # Exponential, with jitter so parallel workers do not all return at
            # the same instant and re-trigger the limit together.
            delay = BASE_BACKOFF_SECONDS * 2**attempt + random.uniform(0, 1)
            log.warning(
                "%s returned %d; backing off %.1fs (attempt %d/%d)",
                model,
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


def _vectors(response: dict, expected: int, model: str) -> list[list[float]]:
    """Unpack the OpenAI-shaped response, ordered by `index`, not by arrival."""
    data = response.get("data")
    if not isinstance(data, list):
        raise EmbeddingError(f"{model} returned no 'data' array: {str(response)[:200]}")
    if len(data) != expected:
        raise EmbeddingError(
            f"asked {model} for {expected} vectors, got {len(data)} — a silent "
            "misalignment would attach every vector to the wrong chunk"
        )
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    vectors = [item["embedding"] for item in ordered]
    for vector in vectors:
        if len(vector) != EMBEDDING_DIM:
            raise EmbeddingError(
                f"{model} returned {len(vector)} dimensions, not {EMBEDDING_DIM}; "
                "the 'dimensions' request was ignored and this will not fit "
                "chunk_embeddings.embedding"
            )
    return [_unit(v) for v in vectors]


def _request(texts: list[str], input_type: str) -> dict:
    return {
        "model": get_settings().embedding_model,
        "input": texts,
        "dimensions": EMBEDDING_DIM,
        "input_type": input_type,
        "provider": _PROVIDER_POLICY,
    }


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Vectors for chunks being stored. Unit length, `EMBEDDING_DIM` long."""
    model = get_settings().embedding_model
    out: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        response = _post(_request(batch, "search_document"))
        out.extend(_vectors(response, len(batch), model))
        log.info("embedded %d/%d chunks with %s", len(out), len(texts), model)
    return out


def embed_query(text: str) -> list[float]:
    """A vector for something being searched *for*, not stored.

    Both mechanisms are applied: `input_type`, which some models honour, and
    the instruction prefix, which the ones that ignore `input_type` want
    instead. Sending both is safe — a model that reads the parameter treats the
    prefix as part of the query text, and the prefix describes the retrieval
    task rather than adding a topic.
    """
    model = get_settings().embedding_model
    template = _QUERY_TEMPLATES.get(model)
    payload = template.format(text=text) if template else text
    response = _post(_request([payload], "search_query"))
    return _vectors(response, 1, model)[0]
