"""Solitary unit tests — one function each, no HTTP, no Redis, no Postgres.

Everything else in this suite goes through the ASGI stack, which is the right
shape for the bugs this system actually has (races, cache/store divergence).
It's the wrong shape for the functions here, which are pure and whose failure
modes are entirely local.

Some of these are dataset assertions rather than code ones — that every
question carries a `decision_date` in the year its prompt claims, for instance.
They live here because the thing they protect (the pre-vote retrieval boundary)
is a property of the content, and there is no request to route through to check
it.

The one impurity left: importing `content` reads questions.json once at import
time. Not worth restructuring the module to avoid, but worth naming.
"""

import re
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

import content
import daily
import identity
import settings
from identity import ANON_PREFIX

# --- daily.tally_available_at / tally_is_unlocked -----------------------------
#
# The community split unlocks at midnight UTC at the end of the day. Nothing
# tested this boundary before: the endpoint tests sit at midday and 00:01, far
# enough from the edge that an off-by-one hour or day would sail through.


def test_tally_unlocks_at_midnight_utc_ending_the_day():
    assert daily.tally_available_at(date(2026, 3, 14)) == datetime(
        2026, 3, 15, 0, 0, tzinfo=UTC
    )


def test_unlock_time_is_utc_aware():
    """A naive datetime here would compare-error against `now()` at runtime."""
    assert daily.tally_available_at(date(2026, 3, 14)).tzinfo == UTC


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
        (datetime(2026, 3, 14, 0, 0, 0, tzinfo=UTC), False),  # day opens
        (datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC), False),  # midday
        (datetime(2026, 3, 14, 23, 59, 59, tzinfo=UTC), False),  # last second
        (datetime(2026, 3, 15, 0, 0, 0, tzinfo=UTC), True),  # the boundary
        (datetime(2026, 3, 15, 0, 0, 1, tzinfo=UTC), True),
        (datetime(2026, 3, 20, 0, 0, 0, tzinfo=UTC), True),  # long after
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
    "jurisdiction": "US",
    "vote_type": "congressional_passage",
    "decision_date": "1965-04-08",
    "reveal": {"actual_vote": "Passed", "outcome": "...", "source": "..."},
}


def test_public_view_strips_the_reveal():
    assert "reveal" not in content.public_view(SAMPLE)


def test_public_view_returns_exactly_the_player_facing_fields():
    assert content.public_view(SAMPLE) == {
        "id": "q1",
        "category": "medical",
        "era": "historical",
        "prompt": "Do you vote for it?",
        "options": ["Support", "Oppose"],
    }


@pytest.mark.parametrize("field", ["jurisdiction", "vote_type", "decision_date"])
def test_public_view_strips_pipeline_metadata(field):
    """These describe how content is built, not what a player is playing."""
    assert field not in content.public_view(SAMPLE)


def test_public_view_drops_fields_it_has_never_heard_of():
    """The point of the whitelist. A blacklist passes every test above and
    still leaks the next field someone adds — which is exactly what happened
    when jurisdiction/vote_type/decision_date arrived."""
    assert content.public_view(
        {**SAMPLE, "internal_scratch": "leak me"}
    ) == content.public_view(SAMPLE)


def test_public_view_does_not_mutate_its_input():
    """The question store is a module-level dict loaded once. A `del q["reveal"]`
    implementation would pass the strip test and permanently destroy the reveal
    for every later request — the kind of bug that only shows up on request two."""
    original = dict(SAMPLE)
    content.public_view(SAMPLE)
    assert original == SAMPLE
    assert "reveal" in SAMPLE


def test_public_view_is_idempotent():
    once = content.public_view(SAMPLE)
    assert content.public_view(once) == once


# --- the questions' pipeline metadata -----------------------------------------
#
# Dataset assertions, not code ones: a failure here means questions.json is
# wrong, and `content._validate` would already have refused to import.
# `decision_date` is the pre-vote retrieval boundary, so "it parses" and "it is
# in the year the prompt puts the player in" are safety properties, not tidiness.


@pytest.mark.parametrize("q", content.all_questions(), ids=lambda q: q["id"])
def test_every_question_carries_a_usable_decision_date(q):
    assert content.decision_date(q["id"]) == date.fromisoformat(q["decision_date"])


@pytest.mark.parametrize("q", content.all_questions(), ids=lambda q: q["id"])
def test_every_question_has_a_routable_vote_type(q):
    """`select_sources` routes on this and is required to raise rather than
    fall back to a default, so an unrecognised value is a hard failure later."""
    assert q["vote_type"] in content.VOTE_TYPES
    assert q["jurisdiction"]


@pytest.mark.parametrize("q", content.all_questions(), ids=lambda q: q["id"])
def test_decision_date_falls_in_the_year_the_prompt_claims(q):
    """The prompt opens with "It's <year>". If the boundary sits before that
    year, everything the scene refers to is already outcome material and the
    pre-vote corpus comes out empty; if it sits after, the player is being
    asked about something already decided. Both are content bugs that no
    amount of retrieval code can fix."""
    prompt_year = int(re.search(r"It's (\d{4})", q["prompt"]).group(1))
    assert content.decision_date(q["id"]).year == prompt_year


def test_decision_date_is_none_for_an_unknown_question():
    assert content.decision_date("does-not-exist") is None


@pytest.mark.parametrize(
    "broken",
    [
        {},  # no metadata at all
        {"jurisdiction": "US", "vote_type": "congressional_passage"},  # no date
        {"jurisdiction": "US", "decision_date": "1965-04-08"},  # no vote_type
        {"vote_type": "congressional_passage", "decision_date": "1965-04-08"},
        {
            **{"jurisdiction": "US", "decision_date": "1965-04-08"},
            "vote_type": "referendum",
        },
        {**{"jurisdiction": "US", "vote_type": "agency_rule"}, "decision_date": "1965"},
        {
            **{"jurisdiction": "US", "vote_type": "agency_rule"},
            "decision_date": "not a date",
        },
        {
            **{"jurisdiction": "US", "vote_type": "agency_rule"},
            "decision_date": "1965-13-40",
        },
        {
            **{"jurisdiction": "", "vote_type": "agency_rule"},
            "decision_date": "1965-04-08",
        },
    ],
)
def test_validate_rejects_unusable_pipeline_metadata(broken):
    """Runs on import, so this is what stops the process rather than letting a
    question with no retrieval boundary reach the pipeline."""
    with pytest.raises(ValueError):
        content._validate({"id": "q1", **broken})


def test_validate_accepts_what_the_dataset_actually_contains():
    for q in content.all_questions():
        content._validate(q)


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


# --- settings ---------------------------------------------------------------
#
# Parsing only, no I/O, so these belong in the solitary layer. They exist
# because the first version of Settings declared cors_origins as `list[str]`,
# which pydantic-settings JSON-decodes before any validator runs — so the
# value docker-compose actually sets, `CORS_ORIGINS=http://localhost:5173`,
# crashed the process at startup. Nothing in the suite set that variable, so
# all 120 tests passed against a backend that could not boot in its own
# container.


def _settings(monkeypatch, **env):
    """A Settings built from a controlled environment.

    Constructed directly rather than through get_settings(), which is cached
    for the life of the process.
    """
    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "CORS_ORIGINS",
        "COOKIE_SECURE",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return settings.Settings(_env_file=None)


def test_cors_origins_parses_the_value_compose_actually_sets(monkeypatch):
    s = _settings(monkeypatch, CORS_ORIGINS="http://localhost:5173")
    assert s.cors_origin_list == ["http://localhost:5173"]


def test_cors_origins_splits_and_strips_a_comma_separated_list(monkeypatch):
    s = _settings(monkeypatch, CORS_ORIGINS="https://a.example,  https://b.example ,")
    assert s.cors_origin_list == ["https://a.example", "https://b.example"]


def test_bare_postgres_url_gains_the_psycopg_driver(monkeypatch):
    """compose supplies `postgresql://`; SQLAlchemy needs the driver named."""
    s = _settings(monkeypatch, DATABASE_URL="postgresql://u:p@h:5432/d")
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/d"


def test_an_explicit_driver_is_left_alone(monkeypatch):
    url = "postgresql+psycopg://u:p@h:5432/d"
    assert _settings(monkeypatch, DATABASE_URL=url).database_url == url


@pytest.mark.parametrize(
    "value, expected",
    [("true", True), ("True", True), ("1", True), ("false", False), ("0", False)],
)
def test_cookie_secure_reads_as_a_boolean(monkeypatch, value, expected):
    assert _settings(monkeypatch, COOKIE_SECURE=value).cookie_secure is expected


def test_defaults_need_no_environment_at_all(monkeypatch):
    s = _settings(monkeypatch)
    assert s.cors_origin_list == ["http://localhost:5173"]
    assert s.cookie_secure is False
    assert s.redis_url.startswith("redis://")
    assert s.database_url.startswith("postgresql+psycopg://")


def test_log_level_is_validated_not_silently_ignored(monkeypatch):
    """A typo here used to be undetectable. dictConfig accepts a bad level
    string and then the root logger silently keeps its previous one."""
    with pytest.raises(ValueError, match="log_level must be one of"):
        _settings(monkeypatch, LOG_LEVEL="verbose")


def test_log_level_is_normalised_to_upper_case(monkeypatch):
    assert _settings(monkeypatch, LOG_LEVEL="debug").log_level == "DEBUG"
