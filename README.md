# Votecracy

**Vote on real policy questions from history — then find out what actually happened.**

![Votecracy](docs/img/quick-play.png)

You are shown a decision as the people of the time faced it: 1965, Congress is
debating Medicare; 1919, Prohibition is on the table; 1913, the income tax. You
get the arguments, not the answer. You vote. Only then does the game reveal how
the vote actually went, and what followed.

**Nothing about the outcome is visible before you vote** — not the historical
result, not what happened afterwards, not how other players are voting. That
rule is enforced on the server, not in the UI, and it has tests whose only job
is to keep it that way.

## Game modes

- **Quick play** — pull a random question, vote, see the reveal immediately.
- **Daily vote** — one question per UTC day, shared by everyone. Your vote is
  counted the moment you cast it; the community split unlocks when the day ends.

## Running it

Requires Docker and Node 18+.

```bash
docker compose up --build
```

Brings up the backend on `:8000`, Postgres on `:5432` and Redis on `:6379`. The
container runs `alembic upgrade head` before starting the app, so the schema is
migrated on boot. Then the frontend:

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend. API docs are at
http://localhost:8000/docs.

To wipe all local state:

```bash
docker compose down -v
```

## Developing

Running the game needs only Docker and Node — the backend's Python lives in the
container. A local Python environment is for the things that run on the host:
the test suite and Alembic.

```bash
python -m venv .venv
```

```bash
source .venv/Scripts/activate      # Windows, Git Bash
.venv\Scripts\Activate.ps1          # Windows, PowerShell
source .venv/bin/activate           # macOS / Linux
```

```bash
pip install -r backend/requirements-dev.txt
```

`requirements.txt` is the runtime set that the container installs;
`requirements-dev.txt` adds it plus the test and lint tools.

## Tests

```bash
python -m pytest backend -q        # backend, venv active

cd frontend && npm test            # frontend
```

Both run on the host in seconds, with no containers. Redis and Postgres are
replaced by in-process stand-ins — including two tests that drive the vote path
under a thread pool and assert that N concurrent voters produce exactly N
counted votes.

Tests build the schema from the SQLAlchemy metadata rather than replaying the
migration chain, which is what keeps them container-free. The tradeoff is that
a model change without a matching migration passes here and fails on deploy;
`alembic check` against a live database is what catches it.

## Migrations

```bash
cd backend
alembic revision --autogenerate -m "what changed"
alembic upgrade head
alembic check                      # models vs live database: any drift?
```

Needs the venv active and Postgres up. The container applies migrations itself
before starting the app, so this is only for authoring them.

## Keeping the docs honest

```bash
python docs/refresh.py            # rewrite the measured blocks
python docs/refresh.py --check    # fail if any is stale, change nothing
```

Parts of `docs/` are measurements of the live corpus, and a number typed into
prose has no link to whatever produced it — so nothing notices when it stops
being true. Those parts sit inside `<!-- generated: … -->` markers and come from
this script; everything outside them is hand-written and does not rot.

```bash
python docs/refresh.py --markers  # structure only, no database needed
```

**Freshness cannot be checked in CI, and the reason is not containers.** CI does
run them — the `cold-start` job brings the whole stack up — but those containers
are empty. The corpus is 477 MB of cached source text plus a database that has
never been in version control, so a runner has no ground truth to compare
against and would call every figure stale.

So the work is split by what each place can actually know:

| | checks | where |
|---|---|---|
| `--markers` | blocks present, paired, not hand-edited | CI, in the lint job |
| `--check` | the figures are current | locally, needs the corpus |
| the ingest paths | say so the moment they invalidate a figure | automatic |

That last one is the part that matters. A measurement expires the instant
something changes the corpus, and the only thing that knows is whatever changed
it — not the person who edits that document next, which is what this project
relied on and what let a status table go stale twenty-three minutes after it was
written.

## Load testing

```bash
bash loadtest/run.sh
```

Resets state, drives k6 at the vote endpoint, then verifies the HTTP 200 count,
the Redis tally and the Postgres row count are all identical. Results are
written up in [`docs/metrics/`](docs/metrics/).

## How it works

Two systems that never touch each other on the request path: an offline content
pipeline that writes to a curated store, and a live game path that votes against
it. The live path never calls a language model.

A vote is deduplicated and tallied by a single Lua script so Redis performs both
atomically, then written to Postgres by a background task after the response is
sent — the cache keeps the count correct under concurrency, the database keeps
it after a restart.

[**docs/architecture.md**](docs/architecture.md) covers this properly: the
atomic vote path, what happens when Redis is unreachable versus merely empty,
the anonymous identity model and its limits, the package layout and the import
rule that enforces it, and the testing strategy.

The rest of `docs/` is the reasoning behind the content side:

| | |
|---|---|
| [`candidate-generation.md`](docs/candidate-generation.md) | where questions come from, and why the generator must not see the margin |
| [`data-acquisition.md`](docs/data-acquisition.md) | where every remaining source comes from, what it costs, and what bites |
| [`content-audit.md`](docs/content-audit.md) | how each question's `decision_date` was established, and three errors it turned up |
| [`evaluation.md`](docs/evaluation.md) | how the no-spoiler, grounding and neutrality rules get measured rather than asserted |
| [`engineering-practices.md`](docs/engineering-practices.md) | what the tooling audit changed, and what it deliberately left alone |
| [`spike-source-retrieval.md`](docs/spike-source-retrieval.md), [`spike-fetch-reality.md`](docs/spike-fetch-reality.md) | what the source APIs actually do, measured against them |

## Stack

| | |
|---|---|
| Backend | Python 3.12, FastAPI |
| Frontend | React (Vite), PWA |
| Durable store | PostgreSQL |
| Cache / tally | Redis, AOF persistence |
| Local dev | Docker Compose |
| Load testing | k6 |

## Layout

The backend is split by system, not by layer. The two halves never touch: an
offline pipeline that gathers and verifies source material, and a live game
path that serves votes. `backend/tests/test_layering.py` parses the imports and
fails if anything in `game/` reaches into `pipeline/` — if the vote path could
depend on retrieval, a broken pipeline could take voting down with it.

```
backend/
  game/           the live vote path — never calls a model
    main.py       app wiring, quick-play endpoints
    daily.py      daily mode: vote path and degradation policy
    cache.py      Redis, the atomic dedupe + tally script
    identity.py   anonymous voter cookie
    schemas.py    request/response shapes
  pipeline/       the offline content pipeline — never on a request path
    voteview.py   reads a roll-call corpus, emits question candidates
    sources.py    which source can answer which need, and how to ask it
    ingest.py     normalise, extract, chunk, store
    embedding.py  chunks to vectors
    retrieval.py  rule #1: the pre/post-vote boundary, as the only query
    grounding.py  rule #2: a claim's span citation, checked by code
    spoilers.py   rule #1: what the reveal says that the sources never did
  shared/
    settings.py   every environment variable, in one typed place
    content.py    the curated question store
    db/           engine + votes (game) + corpus (pipeline)
  alembic/        migrations own the schema
  tests/
frontend/src/     pages, components, api layer, tests
loadtest/         k6 script and the verification runner
docs/             architecture, design decisions, source spikes, metrics
```

## License

[MIT](LICENSE)
