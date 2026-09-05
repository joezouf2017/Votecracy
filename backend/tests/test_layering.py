"""The import rule the package split exists to create.

CLAUDE.md's architecture principle says the content pipeline and the live game
path are two decoupled systems and "don't blur these". Before the split that
was a sentence in a document; nothing stopped `daily.py` importing the
retrieval layer, and the pipeline had quietly grown to 1.77x the size of the
game while sharing its namespace.

Stating the rule in a comment is what failed the first time. These tests read
the actual import statements, so the next attempt to blur the two fails in CI
rather than in review.
"""

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
PACKAGES = ("game", "pipeline", "shared")


def imports_of(path: Path) -> set[str]:
    """Top-level package names this module imports from the backend."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found & set(PACKAGES)


def modules_in(package: str) -> list[Path]:
    """Every module in the package, subpackages included.

    `glob` rather than `rglob` was the first version, and it stopped seeing
    `shared/db/` the moment that became a package — the layering rule silently
    covered less than it claimed to."""
    return sorted(p for p in (BACKEND / package).rglob("*.py"))


@pytest.mark.parametrize("path", modules_in("game"), ids=lambda p: f"game/{p.name}")
def test_the_game_never_imports_the_pipeline(path):
    """The vote path must not depend on retrieval, embeddings or an LLM client.
    If it ever does, a slow or broken pipeline can take voting down with it."""
    assert "pipeline" not in imports_of(path)


@pytest.mark.parametrize("path", modules_in("shared"), ids=lambda p: f"shared/{p.name}")
def test_shared_depends_on_neither_system(path):
    """Otherwise it is not shared, it is one system with a second one attached."""
    assert imports_of(path) <= {"shared"}


def test_the_pipeline_may_use_shared_but_not_the_game():
    """The pipeline reads questions and writes tables — both live in shared.
    It has no business calling an endpoint or touching the vote cache."""
    for path in modules_in("pipeline"):
        assert "game" not in imports_of(path), path.name


def test_every_backend_module_lives_in_one_of_the_three_packages():
    """A module left at the root belongs to neither system, which is how the
    flat layout started."""
    stray = [p.name for p in BACKEND.glob("*.py")]
    assert stray == []


def test_importing_db_registers_every_table():
    """The guard on `shared/db/__init__`'s side-effect imports.

    SQLAlchemy registers a Table with the metadata when its module is
    imported. Alembic compares that metadata against the live database, so if
    only half the tables have been imported when `alembic revision
    --autogenerate` runs, the other half look like tables that exist and
    should not — and it writes `op.drop_table` for each of them.

    Deleting either import from `db/__init__` looks like tidying up an unused
    name and silently arms a migration that drops half the schema. This is
    what makes that impossible to do quietly."""
    from shared import db

    assert set(db.metadata.tables) == {
        "votes",
        "daily_questions",
        "source_documents",
        "source_chunks",
        "chunk_embeddings",
    }


# --- patchability -------------------------------------------------------------


def _patched_names() -> set[str]:
    """The attribute names conftest replaces, read out of conftest itself.

    Derived rather than listed so the rule below covers a new monkeypatch the
    day it is written, instead of the day someone remembers to update a
    hardcoded set.
    """
    tree = ast.parse(
        (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    )
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            names.add(node.args[1].value)
    return names


def test_nothing_imports_a_patched_function_by_name():
    """`from x import f` binds at import time, so patching `x.f` afterwards
    does not reach the importer — it keeps calling the original.

    This is not theoretical. The db package split changed `votes.py` to
    `from shared.db.engine import get_engine`, and five tests silently stopped
    using the SQLite fixture and started reading the real Postgres: a test
    expecting one vote saw twenty-one. Nothing errored. The suite went green
    on production data.

    Anything the fixtures replace has to be reached through its module.
    """
    patched = _patched_names()
    assert patched, "found no monkeypatch targets — this test has stopped working"

    offenders = []
    for package in PACKAGES:
        for path in modules_in(package):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                for alias in node.names:
                    if alias.name in patched:
                        offenders.append(
                            f"{path.relative_to(BACKEND).as_posix()}:{node.lineno} "
                            f"imports {alias.name}"
                        )
    assert not offenders, (
        "these bind at import time and the fixtures will not reach them: "
        f"{offenders}"
    )
