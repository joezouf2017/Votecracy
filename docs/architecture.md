# Architecture

Notes on how Votecracy is put together and why. For setup and usage, see the
[README](../README.md).

## Two systems, decoupled

```
Content pipeline (offline, batch)        Live game path (real-time)
──────────────────────────────           ──────────────────────────
retrieve sources                         player votes
  → fact-check                             → atomic dedupe + tally  (Redis)
  → write to the content store             → respond
                                           → durable write          (Postgres)
```

The live path never calls a language model. Historical claims are served from a
curated content store, never generated at request time — a model that invents a
plausible-sounding outcome would break the only promise the game makes.

## Enforcing the vote-first rule

Nothing about the outcome may reach the client before a vote is cast. This is
enforced server-side, in two places:

- `content.public_view()` is the only function that produces the pre-vote shape
  of a question. It returns a new dict containing a **whitelist** of fields, so
  the outcome is never serialised into a pre-vote response. It was a blacklist
  once — strip `reveal`, pass everything else — and that failed open the moment
  the pipeline added `decision_date`, `vote_type` and `jurisdiction` to every
  question. A whitelist cannot leak the next field someone adds.
- `GET /api/daily/results` returns `403` unless the caller has a recorded vote.

Both have tests whose only purpose is to keep it that way, including one
asserting that `public_view` doesn't mutate its input — the question store is a
module-level dict loaded once, so a `del q["reveal"]` implementation would pass
a naive strip test and permanently destroy the reveal for every later request.

The community tally is treated as part of the same rule. It stays hidden until
the day's vote closes, because a running count would nudge later voters toward
the leading option, and noticing the gap between your own judgment and history
is the point of the game.

## The content pipeline

The larger half of the backend, and the one the live path must never touch. It
turns a historical decision into a playable question: find candidates, work out
which sources can answer them, fetch, normalise, chunk, embed, generate, and
verify. Every step runs offline.

```
Voteview bulk corpus  ──►  candidates      (date, chamber, bill, margin)
                             │
select_sources ──────────────┤              which source, for which need
                             ▼
fetch ──► normalise ──► extract ──► chunk ──► embed ──► pgvector
                                                          │
                                        retrieve (scoped) ─┤
                                                          ▼
                                       generate ──► verify ──► human review
```

### The boundary is a date, not a source type

The rule that shapes everything else:

```
pre-vote  = published_date <  decision_date  AND  role = 'framing'
post-vote = published_date >= decision_date
```

Classifying whole sources as "framing" or "outcome" was the first design and it
is wrong in both directions. A statistical series has values *before* the
decision too — US life expectancy in 1965 is a precondition of the Medicare
debate, not a result of it. And a newspaper retrospective printed in 1970 about
a 1965 decision is outcome material even though newspapers are a "framing
source". The date catches both; the type catches neither.

`role` is a second filter, never an alternative one. It exists because the date
cannot separate an amendment's *description* from its *vote counts* when both
sit in the same Congressional Record page on the same day — the description is
legitimate pre-vote material, the margin gives the result away.

### Sources are chosen by coverage, and refused loudly

`select_sources(question, need)` returns every whitelisted source that can serve
a need, and **raises rather than returning an empty list**. Eight of the
twenty-four (question × need) combinations raise today: Voteview is a
congressional dataset and holds no state ratification votes, nothing covers the
UK, and there is no FCC source at all. Those gaps are the output, not a defect
list — an empty list would be read as "nothing matched this run", and the
pipeline would carry on building a question with nothing behind it.

Routing is on each source's real coverage window rather than the question's
coarse `era` label, for the same reason the boundary is a date: the label is a
proxy. Both ends of the window matter. Chronicling America starts in 1777, so
"does it start early enough" is true for every question ever asked; its 1963 end
is what actually decides whether it can supply anything.

### An API's date filter is never the safety boundary

Measured against the live services, each one fails differently:

- **loc.gov** accepts `start_date`/`end_date`, returns HTTP 200, and ignores
  them. A request for the Prohibition question's pre-vote window returned pages
  from 1933 — the year Prohibition was repealed. The parameter it honours is
  `dates=FROM/TO`.
- **GovInfo** honours its filter but applies it to the *volume's* date rather
  than the proceedings inside. The drift runs 0–23 days, and the single most
  relevant document for the Medicare question — the House record of the day
  before the vote — sits in a volume dated three weeks later.
- **archive.org** puts the true span only in the item title, which parses for
  17 of 25 items.

So the fetch layer reads `published_date` off every returned record and applies
the boundary itself. `source_documents.published_date` is NOT NULL to force
this: a document whose date cannot be established belongs on neither side of the
boundary, and a nullable column invites a query that treats NULL as "before".

### Verification is layered, and the layers are not interchangeable

Three checks, cheapest and most deterministic first.

**Structural.** The generator is only ever shown `framing` chunks. A model that
has not seen the outcome cannot leak it, which is stronger than instructing one
not to. The candidate row carries the vote margin, so the prompt generator gets
a whitelist projection of it — the same pattern as `public_view`, a different
consumer.

**Code, for rule #2.** Each factual sentence carries a `(document_id,
char_span)` citation, and `grounding.verify` checks the span exists, is tight
enough to be evidence rather than a haystack, and actually contains the value —
"19 million", "19,000,000" and "nineteen million" being one assertion.
`unsupported_numbers` catches the other half: a number in the prose that no
citation vouches for. **No model is involved**, deliberately — using an LLM to
decide whether an LLM hallucinated reintroduces exactly what the rule prevents.

**A judge, for neutrality only.** The one check with no deterministic ground
truth, and it must be a different model family from the generator: a
same-family judge shares the generator's blind spots.

Rule #1 gets a fourth, post-hoc check anyway, because "the generator could not
have known" is an argument about the pipeline, and an argument is not a test.
`spoilers.forbidden` is a set difference already sitting in the database:
`tokens(reveal)` minus `tokens(pre-vote material)`. Words the reveal shares with
the sources are not spoilers — "medicare" and "hospital" are all over the 1965
record — and on that question 28 distinct reveal words reduce to 4. The numbers
are the half that matters: 307 and 116, the House margin.

### Storage is three layers, ordered by what it costs to lose

| table | rebuilt from | cost |
|---|---|---|
| `source_documents` | the network only | the expensive one — never drop |
| `source_chunks` | documents | free, locally |
| `chunk_embeddings` | chunks | embedding API calls |

Embeddings are a separate table rather than a column on chunks so that "drop the
vectors and re-embed" is an operation you can actually perform. If dropping them
meant re-fetching from the network, the layering would be wrong.

Chunks carry `question_id` and `published_date` copied down from their parent
document, so the pre-vote filter is one indexed predicate on the table the
retriever actually queries. Left on the document, every retrieval needs a join —
and the failure mode of forgetting that join is silently returning outcome
material. The copy is held honest by a composite foreign key into
`(id, question_id, published_date)`, so a chunk that disagrees with its document
cannot be written at all.

There is **no ANN index**, and there may never need to be one. Retrieval is
always scoped to a single question, so a btree narrows 116,000 chunks to a few
hundred before any vector arithmetic happens: measured at 1.8 ms, against 382 ms
for the same search unscoped. pgvector's IVFFlat and HNSW indexes search globally
and then filter, which for a highly selective predicate is worse, not better.

## How a vote is counted

A vote must be counted exactly once, even when hundreds of people vote in the
same second. Two things have to happen together:

1. mark this voter as having voted on this question
2. increment the tally for their choice

Run as two round trips, there is a window where the process can die between
them — the voter is marked as done and their vote is never counted. So they run
as a single Lua script, which Redis executes atomically:

```lua
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
    return redis.call('HINCRBY', KEYS[2], ARGV[1], 1)
else
    return -1   -- already voted
end
```

`SET ... NX` returns nil when the key exists, which is exactly the duplicate
case. The return value is the new count, so each concurrent caller receives a
distinct sequence number — which is what the concurrency tests assert on, since
a lost increment shows up as a gap or a repeat long before the final total
looks wrong.

Postgres holds the durable record, written by a FastAPI background task *after*
the response is sent, so database latency never lands on the player's vote. A
`UNIQUE(question_id, voter_id)` constraint is the backstop if the cache is ever
lost.

## Degradation

Redis can fail in two ways that need different handling.

### Unreachable

It raises, so it is easy to detect. The policy is deliberately asymmetric:

- **Writes fail closed.** Voting returns `503` with `Retry-After`. Without the
  atomic gate there is no way to accept a vote and still promise it was counted
  exactly once. A refused vote is recoverable; a silently wrong tally is not.
- **Reads fail over to Postgres.** Serving the question, remembering that a
  player already voted, and rebuilding the tally all have authoritative answers
  in the durable log. A cache outage costs latency, not correctness.

With both stores down the reveal is withheld, so the vote-first rule holds even
in the worst case.

### Alive but empty

The nastier one. A Redis that restarted empty does not raise; it answers.
`previous_choice` returns `None` and `get_tally` returns `{}`, neither of which
is distinguishable from "nobody has voted". Handled by not trusting an empty
answer:

- The closed-day tally is read from Postgres and written back to Redis as a
  cache, rather than read from Redis and only rebuilt on an exception. The
  tally is a cache of `SELECT choice, count(*) ... GROUP BY choice`; treating
  it as a source of truth is what makes a wiped cache publish a wrong number.
- `voter_choice` falls through to the durable log on an empty answer, not only
  on an error. That costs one indexed lookup per page load for players who have
  not voted yet; casting a vote still never touches Postgres synchronously.
- When the durable log rejects a vote as a duplicate, it is logged as an error.
  The two stores disagreeing about who has voted is the signature of lost voter
  markers, and that log line is the only signal.

Redis runs with `appendonly yes` and `appendfsync everysec`, so a hard kill
loses at most about a second of votes rather than everything since the last RDB
snapshot.

**Known gap:** the `voted:*` markers are not rebuilt after a cache wipe. A
player who votes again in that window is counted in Redis while Postgres
rejects the write, and the stores disagree until the next read. Closing it
needs the markers bulk-loaded from Postgres under a lock on first touch of a
question.

## Identity

One anonymous UUID in an httpOnly cookie. No accounts, no login — voting should
be one tap.

What that buys and does not: it stops the accidental double-vote (refresh, back
button, two tabs) and the casual one. It does not stop someone who clears
cookies or opens a private window. Real vote integrity needs real accounts; the
cookie leaves the seam where an authenticated user id would slot in.

The cookie value is validated as exactly 32 lowercase hex characters before use.
This is load-bearing rather than defensive noise: the value becomes part of a
Redis key, so an unvalidated cookie would let anyone write keys of arbitrary
length and content into the store the vote path depends on.

## Testing strategy

Two layers, chosen deliberately.

**`backend/tests/test_pure.py`** — solitary unit tests. No HTTP, no Redis, no
Postgres. Covers the pure functions whose failure modes are entirely local: the
midnight-UTC unlock boundary, the outcome-stripping filter, the rotation
ordering, the cookie validator.

**Everything else** — integration tests through the ASGI stack. That is the
right level for the bugs this system actually has, which live between components
rather than inside any one function: races, cache/store divergence, cookie
handling, outage behaviour.

Redis is replaced by `fakeredis` running the **real Lua script**, not a mock.
Mocking `cast_vote` would make the concurrency tests pass while verifying
nothing about the atomicity they exist to protect. Postgres is replaced by
SQLite; the schema is portable SQLAlchemy Core and the thing under test is the
unique constraint, which behaves the same on both.

An autouse `frozen_clock` fixture pins both time reads. Without it the daily
rotation picks a different question every day the suite runs, so the tests would
quietly exercise different data depending on the calendar.

## Load testing

The gate the vote path has to pass is not a latency number, it is an equality:

```
HTTP 200 count == Redis tally total == Postgres row count
```

Every k6 iteration uses a distinct voter cookie, so every request should be
accepted and counted exactly once. `loadtest/run.sh` resets state, runs the
test, and then checks all three numbers. A mismatch means a race condition got
through.

Results in [`metrics/`](metrics/).

## Schema changes

Alembic owns the production schema. `create_all()` only ever creates *missing*
tables — it cannot add a column to a table that already exists, and it fails
silently rather than telling you so. Anything past the first schema is beyond it.

```bash
cd backend
alembic revision --autogenerate -m "what changed"   # writes a versioned script
alembic upgrade head                                # applies it
alembic check                                       # models vs live DB: any drift?
```

The container runs `alembic upgrade head` before uvicorn, so a failed migration
stops the container rather than letting it serve against a stale schema.

Tests take a shortcut: `db.create_all_for_tests()` builds the schema straight
from the metadata against a throwaway SQLite file, skipping the migration chain
entirely. That keeps the suite container-free and fast, at the cost of one real
risk — a model change without a matching migration passes the tests and breaks
deployment. `alembic check` against a live database is what catches that; run it
before deploying.

The URL is not configured in `alembic.ini`. `env.py` takes it from
`db.get_engine()`, so the app and the migrations cannot disagree about how to
connect — including the `postgresql://` → `postgresql+psycopg://` normalisation
that compose's `DATABASE_URL` needs.

## Replacing the frontend

The backend serves JSON and nothing else — no templates, no server-rendered
HTML, no static file mounting. The whole contract is the five functions in
`frontend/src/api.js`, and every game rule is enforced server-side, so a
replacement frontend cannot accidentally leak an outcome or double-count a
vote. Three things it does have to carry over:

- **`credentials: 'include'` on every request.** The voter cookie is httpOnly.
  Omit this and nothing errors — you get 200s and a plausible UI, but every
  page load looks like a new voter. Covered by `src/api.test.js`.
- **The status codes mean different things.** `409` is "you already voted, here
  is your reveal", not a failure. `403` is "vote first". `503` is retryable.
  Covered by `src/pages/DailyPage.test.jsx`.
- **A new origin needs adding to `CORS_ORIGINS`** (comma-separated, defaults to
  `http://localhost:5173`). Vite proxies `/api` in development so the question
  does not come up there; a framework serving from its own origin will hit it
  immediately, as an opaque browser-side CORS error.

## API

| Method | Path | |
|---|---|---|
| `GET` | `/api/questions` | All questions, outcomes stripped |
| `GET` | `/api/questions/random` | One random question, outcome stripped |
| `POST` | `/api/questions/{id}/vote` | Quick-play vote → reveal |
| `GET` | `/api/daily` | Today's question, plus whether you have voted |
| `POST` | `/api/daily/vote` | Cast today's vote → reveal |
| `GET` | `/api/daily/results` | Your reveal, and the split once the day closes |
| `GET` | `/health` | Liveness |

Interactive docs at `/docs` while the backend is running.
