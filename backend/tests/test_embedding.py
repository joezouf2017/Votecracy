"""The embedding client's guards — every one of them against a silent failure.

No network. `_post` is replaced, because what is under test is not HTTP: it is
the unpacking, and the unpacking is where a retrieval system goes confidently
wrong. A vector attached to the wrong chunk does not raise, does not look odd
in the database, and produces answers that cite the wrong source forever.

The live behaviours these guards exist for were measured against OpenRouter on
2026-09-05 and are recorded in the module docstring and in
`docs/metrics/cost-model.md`. What is pinned here is that the code notices.
"""

import math

import pytest

from pipeline import embedding
from shared.db.engine import EMBEDDING_DIM
from shared.settings import get_settings


def _vec(seed: float, dim: int = EMBEDDING_DIM) -> list[float]:
    return [seed + i * 1e-6 for i in range(dim)]


@pytest.fixture
def model(monkeypatch):
    """Pin the model without touching the real environment."""

    def _set(slug: str) -> str:
        monkeypatch.setenv("EMBEDDING_MODEL", slug)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        get_settings.cache_clear()
        return slug

    yield _set
    get_settings.cache_clear()


# --- the dimension is not declared twice --------------------------------------


def test_the_request_asks_for_the_width_the_column_actually_is():
    """`chunk_embeddings.embedding` is `vector(EMBEDDING_DIM)`. A second copy of
    768 in this module is the pair that drifts, so there isn't one."""
    assert not hasattr(embedding, "DIMENSIONS")
    assert embedding._request(["x"], "search_document")["dimensions"] == EMBEDDING_DIM


def test_the_request_refuses_providers_that_train_on_it():
    assert embedding._request(["x"], "search_document")["provider"] == {
        "data_collection": "deny"
    }


# --- unpacking ----------------------------------------------------------------


def test_vectors_are_ordered_by_index_not_by_arrival():
    """The single highest-consequence line in this module.

    OpenRouter returns an `index` per embedding. Trusting array order attaches
    every vector to the wrong chunk — which never errors, and makes every later
    citation point at a source that does not support it.
    """
    response = {
        "data": [
            {"index": 1, "embedding": _vec(2.0)},
            {"index": 0, "embedding": _vec(1.0)},
        ]
    }
    first, second = embedding._vectors(response, 2, "test")
    assert first[0] < second[0]


def test_a_short_response_raises_rather_than_silently_misaligning():
    response = {"data": [{"index": 0, "embedding": _vec(1.0)}]}
    with pytest.raises(embedding.EmbeddingError, match="asked .* for 2 vectors"):
        embedding._vectors(response, 2, "test")


def test_a_wrong_width_raises_and_says_dimensions_was_ignored():
    """A model that ignores the `dimensions` request returns its native size.
    That does not fit the column, and the message has to say why rather than
    surfacing later as a database error nobody can place."""
    response = {"data": [{"index": 0, "embedding": _vec(1.0, dim=3072)}]}
    with pytest.raises(embedding.EmbeddingError, match="3072 dimensions"):
        embedding._vectors(response, 1, "test")


def test_a_malformed_response_raises():
    with pytest.raises(embedding.EmbeddingError, match="no 'data' array"):
        embedding._vectors({"error": "nope"}, 1, "test")


def test_vectors_come_back_normalised():
    """Truncated vectors are not unit vectors, and `<#>`/`<->` are wrong on
    non-unit input while `<=>` silently survives. Normalising on write means
    swapping the operator later cannot introduce a bug."""
    out = embedding._vectors({"data": [{"index": 0, "embedding": _vec(5.0)}]}, 1, "t")
    assert math.isclose(math.sqrt(sum(x * x for x in out[0])), 1.0, rel_tol=1e-9)


def test_a_zero_vector_raises():
    with pytest.raises(embedding.EmbeddingError, match="no direction"):
        embedding._unit([0.0] * EMBEDDING_DIM)


# --- the document / query asymmetry -------------------------------------------


def test_qwen_gets_an_instruction_prefix_and_gemini_does_not(model, monkeypatch):
    """Measured: Gemini honours `input_type`, Qwen3 ignores it entirely — the
    same sentence embedded as document and as query came back at cosine
    1.000000. Qwen3 wants the instruction in the text instead, so the table
    exists; without it Qwen3 embeds every query as a document and just
    retrieves worse."""
    sent = {}

    def fake_post(body):
        sent["input"] = body["input"]
        sent["input_type"] = body["input_type"]
        return {"data": [{"index": 0, "embedding": _vec(1.0)}]}

    monkeypatch.setattr(embedding, "_post", fake_post)

    model("qwen/qwen3-embedding-8b")
    embedding.embed_query("how was it funded?")
    assert sent["input"][0].startswith("Instruct:")
    assert "how was it funded?" in sent["input"][0]

    model("google/gemini-embedding-001")
    embedding.embed_query("how was it funded?")
    assert sent["input"] == ["how was it funded?"]


def test_input_type_is_always_sent_as_well(model, monkeypatch):
    """Both mechanisms, always. The prefix is for models that ignore the
    parameter; the parameter is for models that ignore the prefix."""
    seen = []
    monkeypatch.setattr(
        embedding,
        "_post",
        lambda body: seen.append(body["input_type"])
        or {"data": [{"index": 0, "embedding": _vec(1.0)}]},
    )
    model("qwen/qwen3-embedding-8b")
    embedding.embed_query("q")
    embedding.embed_documents(["d"])
    assert seen == ["search_query", "search_document"]


def test_documents_are_never_given_the_query_prefix(model, monkeypatch):
    sent = {}
    monkeypatch.setattr(
        embedding,
        "_post",
        lambda body: sent.update(body)
        or {"data": [{"index": 0, "embedding": _vec(1.0)}]},
    )
    model("qwen/qwen3-embedding-8b")
    embedding.embed_documents(["a debate about hospital insurance"])
    assert sent["input"] == ["a debate about hospital insurance"]


# --- batching -----------------------------------------------------------------


def _one_hot(position: int) -> list[float]:
    """Survives normalisation unchanged, so the text it came from is recoverable
    from the vector — which is what lets this test check ordering at all."""
    v = [0.0] * EMBEDDING_DIM
    v[position % EMBEDDING_DIM] = 1.0
    return v


def test_documents_are_sent_in_batches_and_reassembled_in_order(model, monkeypatch):
    """Two batches, and the second one's vectors must land after the first's.

    Batching is where order is most easily lost, and losing it here is the same
    silent misalignment as ignoring `index` — just one level up.
    """
    calls = []

    def fake_post(body):
        calls.append(list(body["input"]))
        # The vector encodes the text it came from, deliberately returned in
        # reverse so a caller that trusts arrival order fails this test.
        return {
            "data": [
                {"index": i, "embedding": _one_hot(int(t))}
                for i, t in reversed(list(enumerate(body["input"])))
            ]
        }

    monkeypatch.setattr(embedding, "_post", fake_post)
    model("google/gemini-embedding-001")

    texts = [str(i) for i in range(embedding.BATCH_SIZE + 3)]
    out = embedding.embed_documents(texts)

    assert len(calls) == 2
    assert calls[0] == texts[: embedding.BATCH_SIZE]
    assert calls[1] == texts[embedding.BATCH_SIZE :]
    assert len(out) == len(texts)
    assert [v.index(1.0) for v in out] == [int(t) for t in texts]


def test_a_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(embedding.EmbeddingError, match="OPENROUTER_API_KEY"):
            embedding._post({"model": "m", "input": ["x"]})
    finally:
        get_settings.cache_clear()
