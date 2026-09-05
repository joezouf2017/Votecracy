# Engineering practices, and what was deliberately left undone

A hygiene audit on 2026-09-05 went looking for the ordinary things a project
accumulates when it is built feature-first. It found six, and two real bugs
underneath them. Both bugs are fixed and CI now guards both.

The more useful half of this file is the second one: the things that were
considered and **not** done, with the reason. A backlog says what someone
intended; a list of deliberate omissions says what they decided.

## The two bugs

**A cold start that had never worked.** `docker compose up` on a clean machine
crashed with `psycopg.OperationalError`. `depends_on` waits for a *container* to
exist, not for the service inside it to accept connections, and the backend runs
`alembic upgrade head` before uvicorn — so the migration raced Postgres and lost.
Fixed with healthchecks and `condition: service_healthy`. Nobody had hit it
because nobody had started from an empty volume in a long time.

**Test caches copied into the image.** `.pytest_cache` and `__pycache__` were
inside the build context, so every local test run invalidated the Docker layer
cache and the next build reinstalled dependencies from scratch. Fixed with
`backend/.dockerignore`.

Both are now covered: CI's `cold-start` job does `docker compose up -d --build`
from a clean checkout and polls `/health` until the stack answers.

## What was added

| | why it mattered |
|---|---|
| `pydantic-settings` (`backend/shared/settings.py`) | Four files read raw `os.environ.get`. `CORS_ORIGIN` instead of `CORS_ORIGINS` silently fell back to the localhost default, and every request would fail CORS in production with nothing in the logs. One typed object, validated at startup. |
| ruff, lint and format | There was no linter. Checking for unused imports during the audit meant hand-writing an AST script, which is a tool's job. |
| eslint 9 + prettier | Same, for the frontend. |
| `.gitattributes` | Every commit printed "LF will be replaced by CRLF", and line endings depended on who cloned. It was not cosmetic: `loadtest/run.sh` had a CRLF shebang, so on Linux it failed with `can't execute 'bash'` — a message that names the wrong problem. |
| `pyproject.toml` | Removed two `sys.path.insert` hacks that existed only because `backend/` was not a package, so `from main import app` worked by accident of the working directory. |
| Logging configuration | `log.error` reached container logs with no level, timestamp or logger name. The store-divergence error in `daily.persist_vote` is documented as the *only* signal that Redis and Postgres disagree, and in aggregate logs it could not be filtered for. |

One sequencing rule came out of this and is worth keeping: **do hygiene work as
one batch, immediately after a feature lands, never alongside one.** Three of
those six rewrite every file in the repository. Run against in-flight work they
produce a conflict in everything they touch, and one batch is also one reformat
commit instead of three.

### The settings bug this created

Worth recording because it is the audit's own footgun. `CORS_ORIGINS` was first
declared as `list[str]`, and pydantic-settings JSON-decodes complex types
*before* validators run — so the plain comma-separated value compose sets raised
`JSONDecodeError` at import. The whole suite passed against a backend that could
not boot, because nothing in it imports the settings the way the container does.

It is a `str` with a `cors_origin_list` property now, and the regression test
starts from the exact value compose passes. Anything else added there — API
keys, rate limits, a User-Agent — must avoid `list[str]` and `dict` for the same
reason.

## CI

Four jobs, on push and pull request.

| job | what it proves |
|---|---|
| `lint` | ruff check and ruff format over the backend |
| `backend` | pytest, with no `services:` block — Redis is `fakeredis` running the real Lua script and Postgres is SQLite, so the suite needs no containers and finishes in about ten seconds |
| `frontend` | eslint, prettier `--check`, vitest, and a real `vite build` |
| `cold-start` | the whole stack from an empty volume, polled until `/health` answers, then `docker compose down -v` |

The split is deliberate. The first three are fast and cover most commits; the
fourth is slow and exists to catch exactly one class of bug — the kind that only
appears when there is no state left over from last time.

## Deliberately not done

**A Python lock file.** Direct dependencies are all pinned with `==`; only
transitive ones float, which is acceptable at this size. pip-tools would turn
`requirements.txt` into a generated file exactly as Phase 3 starts adding to it,
which is the worst moment to add a compile step.

**Coverage measurement.** This suite is designed rather than coverage-chased —
see the testing section in [`architecture.md`](architecture.md#testing-strategy).
Chasing a percentage is how tests that assert nothing get written, and there is
already one example in this project's history of an assertion that passed
against injected bugs.

**TypeScript on the frontend.** It is small and slated for a redesign. Typing
code you intend to replace is work with a known expiry date.

**A dependency-injection framework.** Modules call `get_settings()` and
`engine.get_engine()` directly rather than receiving them, which is what makes
the test suite need monkeypatching. Passing dependencies through would be more
testable and much more ceremony, and direct calls are the FastAPI norm. The risk
is contained instead by `test_layering`, which forbids importing any patched
name — that is what turns the convention into something that fails loudly.
