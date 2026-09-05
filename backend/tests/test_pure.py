"""Solitary unit tests — one function each, no HTTP, no Redis, no Postgres.

Everything else in this suite goes through the ASGI stack, which is the right
shape for the bugs this system actually has (races, cache/store divergence).
It's the wrong shape for these four, which are pure functions whose failure
modes are entirely local — and two of them had no direct coverage at all.

The one impurity left: importing `content` reads questions.json once at import
time. Not worth restructuring the module to avoid, but worth naming.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

import content
import daily
import identity
from identity import ANON_PREFIX

# --- daily.tally_available_at / tally_is_unlocked -----------------------------
#
# The community split unlocks at midnight UTC at the end of the day. Nothing
# tested this boundary before: the endpoint tests sit at midday and 00:01, far
# enough from the edge that an off-by-one hour or day would sail through.


def test_tally_unlocks_at_midnight_utc_ending_the_day():
    assert daily.tally_available_at(date(2026, 3, 14)) == datetime(
        2026, 3, 15, 0, 0, tzinfo=timezone.utc
    )


def test_unlock_time_is_utc_aware():
    """A naive datetime here would compare-error against `now()` at runtime."""
    assert daily.tally_available_at(date(2026, 3, 14)).tzinfo == timezone.utc


@pytest.mark.parametrize(
    "day, expected",
    [
        (date(2026, 1, 31), date(2026, 2, 1)),  # month boundary
        (date(2026, 12, 31), date(2027, 1, 1)),  # year boundary
        (date(2024, 2, 28), date(2024, 2, 29)),  # leap day
        (date(2026, 2, 28), date(2026, 3, 1)),  # non-leap February
    ],
)
def test_unlock_time_rolls_the_date_correctly(day, expected):
    assert daily.tally_available_at(day).date() == expected


@pytest.mark.parametrize(
    "at, unlocked",
    [
        (datetime(2026, 3, 14, 0, 0, 0, tzinfo=timezone.utc), False),  # day opens
        (datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc), False),  # midday
        (datetime(2026, 3, 14, 23, 59, 59, tzinfo=timezone.utc), False),  # last second
        (datetime(2026, 3, 15, 0, 0, 0, tzinfo=timezone.utc), True),  # the boundary
        (datetime(2026, 3, 15, 0, 0, 1, tzinfo=timezone.utc), True),
        (datetime(2026, 3, 20, 0, 0, 0, tzinfo=timezone.utc), True),  # long after
    ],
)
def test_tally_unlocks_exactly_at_the_boundary(at, unlocked):
    """23:59:59 hidden, 00:00:00 shown. One second either way is a spoiler bug
    or a day-long outage of the results screen."""
    assert daily.tally_is_unlocked(date(2026, 3, 14), at) is unlocked


# --- content.public_view ------------------------------------------------------
#
# The single enforcement point for non-negotiable rule #1.

SAMPLE = {
    "id": "q1",
    "category": "medical",
    "era": "historical",
    "prompt": "Do you vote for it?",
    "options": ["Support", "Oppose"],
    "reveal": {"actual_vote": "Passed", "outcome": "...", "source": "..."},
}


def test_public_view_strips_the_reveal():
    assert "reveal" not in content.public_view(SAMPLE)


def test_public_view_keeps_everything_else():
    assert content.public_view(SAMPLE) == {k: v for k, v in SAMPLE.items() if k != "reveal"}


def test_public_view_does_not_mutate_its_input():
    """The question store is a module-level dict loaded once. A `del q["reveal"]`
    implementation would pass the strip test and permanently destroy the reveal
    for every later request — the kind of bug that only shows up on request two."""
    original = dict(SAMPLE)
    content.public_view(SAMPLE)
    assert SAMPLE == original
    assert "reveal" in SAMPLE


def test_public_view_is_idempotent():
    once = content.public_view(SAMPLE)
    assert content.public_view(once) == once


# --- content.rotation ---------------------------------------------------------


def test_rotation_is_sorted_so_the_file_order_cannot_change_it():
    """The daily question is `rotation[ordinal % len]`. If that list followed
    questions.json's order, reordering the file would silently reassign which
    question every past and future day maps to."""
    assert content.rotation() == sorted(content.rotation())


def test_rotation_covers_every_question_exactly_once():
    ids = [q["id"] for q in content.all_questions()]
    assert sorted(content.rotation()) == sorted(ids)
    assert len(content.rotation()) == len(set(content.rotation()))


# --- identity._valid ----------------------------------------------------------
#
# The gate that keeps an attacker-supplied string out of Redis key names.
# Previously exercised only through 12 HTTP round trips.


def test_valid_accepts_an_id_we_issued():
    assert identity._valid(f"{ANON_PREFIX}{uuid4().hex}") is True


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "0" * 32,  # right shape, but no kind prefix
        "user:" + "0" * 32,  # a kind we do not issue yet
        ANON_PREFIX + "0" * 31,  # too short
        ANON_PREFIX + "0" * 33,  # too long
        ANON_PREFIX + "A" * 32,  # uppercase — uuid4().hex is lowercase
        ANON_PREFIX + "g" * 32,  # right length, not hex
        ANON_PREFIX + "0123456789abcdef-0123456789abcd",  # a dash sneaks in
        # A *trailing* newline is what `$` lets through: it matches at the end
        # of a string or just before a final newline, so `^...$` would accept
        # this and let a stray byte into a Redis key. `\A...\Z` does not.
        ANON_PREFIX + "0" * 32 + "\n",
        ANON_PREFIX + "0" * 32 + "\n" + "0" * 32,  # newline mid-string
        "x" * 5000,
    ],
)
def test_valid_rejects_anything_we_did_not_issue(value):
    assert identity._valid(value) is False


def test_validator_accepts_what_the_issuer_produces():
    """Ties the two halves together. Swapping `uuid4().hex` for `str(uuid4())`
    would add dashes and the validator would start rejecting every id it just
    handed out — an outage no test above would catch."""
    assert all(identity._valid(f"{ANON_PREFIX}{uuid4().hex}") for _ in range(200))
