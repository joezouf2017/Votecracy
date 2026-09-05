"""Normalising, extracting and chunking — the steps between a download and a row.

Solitary: these are text functions. The one that touches the database
(`store_passage`) is exercised through the same SQLite stand-in the rest of the
suite uses, via the autouse fixtures in conftest.
"""

import pytest

from pipeline import ingest

# --- normalise ----------------------------------------------------------------
#
# One 1965 volume carries 66,119 line-break hyphenations, splitting about 8% of
# a given word's occurrences. Rejoining them is what makes the text searchable
# and quotable; not damaging real compounds is what makes it safe.


def test_a_word_split_across_lines_is_rejoined():
    assert "hospital insurance" in ingest.normalise("hos-\npital insurance")


def test_a_hyphenated_proper_noun_is_left_alone():
    """The rule only joins before a lower-case letter, and "King-Anderson" has
    a capital there. Measured on a real volume: King-Anderson, Kerr-Mills and
    Blue Cross all survive."""
    assert ingest.normalise("the King-\nAnderson bill") == "the King-\nAnderson bill"


def test_an_ordinary_hyphen_mid_line_is_left_alone():
    assert ingest.normalise("the Kerr-Mills program") == "the Kerr-Mills program"


def test_runs_of_spaces_collapse_but_line_breaks_survive():
    """Line breaks carry the record's structure; runs of spaces are an artefact
    of column OCR."""
    assert ingest.normalise("a    b\nc") == "a b\nc"


def test_normalising_is_idempotent():
    once = ingest.normalise("hos-\npital   care")
    assert ingest.normalise(once) == once


# --- extract_passages ---------------------------------------------------------
#
# A Congressional Record volume is 14.3M characters of everything Congress did.
# Against the Medicare terms, 1.18% of it is relevant.


def test_a_passage_is_centred_on_the_term():
    text = "x" * 500 + "medicare" + "y" * 500
    [p] = ingest.extract_passages(text, ["medicare"], radius=100)
    assert "medicare" in p.text
    # radius from both ends of the match: 500-100 .. (500+8)+100
    assert (p.start, p.end) == (400, 608)


def test_overlapping_passages_are_merged():
    """Two hits 20 characters apart would otherwise produce near-identical
    passages, indexing the same sentence twice under two document ids."""
    text = "a" * 300 + "medicare" + "b" * 20 + "medicare" + "c" * 300
    passages = ingest.extract_passages(text, ["medicare"], radius=100)
    assert len(passages) == 1


def test_distant_hits_stay_separate():
    text = "a" * 300 + "medicare" + "b" * 900 + "medicare" + "c" * 300
    assert len(ingest.extract_passages(text, ["medicare"], radius=100)) == 2


def test_matching_ignores_case_and_accepts_several_terms():
    text = "..." * 50 + "MEDICARE" + "..." * 50 + "King-Anderson" + "..." * 50
    passages = ingest.extract_passages(text, ["medicare", "King-Anderson"], radius=40)
    assert len(passages) == 2


def test_a_document_with_no_hits_yields_nothing():
    assert ingest.extract_passages("nothing relevant here", ["medicare"]) == []


def test_passage_offsets_point_back_into_the_source():
    text = "a" * 500 + "medicare" + "b" * 500
    [p] = ingest.extract_passages(text, ["medicare"], radius=100)
    assert text[p.start : p.end] == p.text


# --- chunk --------------------------------------------------------------------


def test_chunks_tile_the_text_and_their_offsets_are_exact():
    text = "".join(str(i % 10) for i in range(2500))
    pieces = list(ingest.chunk(text, size=1000, overlap=150))
    assert pieces[0][1] == 0
    assert pieces[-1][2] == len(text)
    for ordinal, start, end in pieces:
        assert 0 <= start < end <= len(text)
        assert isinstance(ordinal, int)


def test_consecutive_chunks_overlap_by_the_stated_amount():
    """Overlap is what stops a sentence being cut in half and lost to both
    chunks."""
    text = "z" * 2500
    pieces = list(ingest.chunk(text, size=1000, overlap=150))
    assert pieces[1][1] == pieces[0][2] - 150


def test_text_shorter_than_one_chunk_gives_exactly_one():
    assert list(ingest.chunk("short", size=1000, overlap=150)) == [(0, 0, 5)]


def test_an_overlap_that_would_never_advance_is_rejected():
    """size <= overlap makes the loop stand still, filling the table until the
    disk does."""
    with pytest.raises(ValueError, match="exceed the overlap"):
        list(ingest.chunk("whatever", size=100, overlap=100))


# --- store_passage ------------------------------------------------------------


def test_an_unknown_role_is_refused_before_anything_is_written():
    with pytest.raises(ValueError, match="unknown role"):
        ingest.store_passage(
            question_id="q",
            source_key="s",
            external_id="e",
            url="u",
            title=None,
            published_date=__import__("datetime").date(1965, 4, 6),
            content_type="text/plain",
            passage=ingest.Passage(0, 5, "hello"),
            role="spoiler",
        )


# --- boundary-aware chunking --------------------------------------------------
#
# `size` is a ceiling, not a width. Cutting at exactly `size` split 51% of the
# real chunks part-way through a word at both ends.


def test_a_chunk_never_starts_or_ends_inside_a_word():
    """The property, stated directly: no cut may fall between two letters."""
    text = (
        "The committee bill provides hospital insurance for the aged. " * 60
    ).strip()
    for _, start, end in ingest.chunk(text, size=200, overlap=40):
        assert not (start > 0 and text[start - 1].isalpha() and text[start].isalpha())
        assert not (end < len(text) and text[end - 1].isalpha() and text[end].isalpha())


def test_a_paragraph_break_is_preferred_over_a_sentence_end():
    """Best-first: a paragraph boundary keeps a whole argument together where a
    sentence boundary would cut through the middle of one."""
    text = "a" * 120 + ". " + "b" * 30 + "\n\n" + "c" * 200
    first_end = next(ingest.chunk(text, size=200, overlap=20))[2]
    assert text[:first_end].endswith("\n\n")


def test_size_is_a_ceiling_never_exceeded():
    text = "word " * 500
    assert all(e - s <= 300 for _, s, e in ingest.chunk(text, size=300, overlap=50))


def test_chunks_still_tile_the_whole_text():
    """Snapping must not drop characters between chunks."""
    text = "The bill provides hospital insurance. " * 40
    pieces = list(ingest.chunk(text, size=250, overlap=50))
    assert pieces[0][1] == 0
    assert pieces[-1][2] == len(text)
    for (_, _, end), (_, nxt, _) in zip(pieces, pieces[1:], strict=False):
        assert nxt < end, "the next chunk must start inside the previous one"


def test_text_with_no_boundary_at_all_still_advances():
    """A run of letters with nowhere to break must hard-cut rather than loop."""
    pieces = list(ingest.chunk("x" * 2000, size=500, overlap=100))
    assert len(pieces) > 1
    assert pieces[-1][2] == 2000


def test_no_chunk_edge_falls_inside_a_word_in_the_source():
    """The property at the scope that matters.

    Chunks are cut from passages and passages from the source, so a chunk can
    sit on clean boundaries *within its passage* while the passage itself
    opened mid-word. Measuring inside the passage said 0% while 63% of edges
    against the source were still bad. This measures against the source."""
    source = (
        "The committee bill provides hospital insurance for the aged. " * 80
    ).strip()
    for passage in ingest.extract_passages(source, ["hospital"], radius=300):
        for _, start, end in ingest.chunk(passage.text, size=200, overlap=40):
            for cut in (passage.start + start, passage.start + end):
                assert not (
                    0 < cut < len(source)
                    and source[cut - 1].isalpha()
                    and source[cut].isalpha()
                ), f"cut at {cut}: {source[cut - 20 : cut + 20]!r}"
