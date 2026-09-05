# Phase 2 load test — daily vote endpoint

**Date:** 2026-09-05
**Gate:** Phase 2 can't be marked done until the vote endpoint is proven correct
under concurrency. Latency numbers are secondary; the pass/fail condition is
that the final vote count equals the number of successful requests exactly.

## What was tested

`POST /api/daily/vote` — the full distributed path:

```
atomic dedupe + tally (Redis Lua)  ->  respond  ->  durable write (Postgres, BackgroundTask)
```

Every k6 iteration sends a **distinct** anonymous voter cookie, so every request
should be accepted and counted exactly once. That makes the correctness check
unambiguous: three independently-derived numbers must be identical.

## Setup

| | |
|---|---|
| Load generator | k6 (`grafana/k6` container, on the compose network) |
| Backend | uvicorn, **1 worker**, FastAPI sync endpoints (Starlette threadpool) |
| Redis | redis:7-alpine, single instance |
| Postgres | postgres:16-alpine, SQLAlchemy default pool (5 + 10 overflow) |
| Host | Docker Desktop on Windows 11 (WSL2 backend) |
| Duration | 30s per concurrency level, after a `FLUSHALL` + `TRUNCATE votes` reset |

Reproduce with `VUS=200 bash loadtest/run.sh`.

## Results

| Concurrency (VUs) | Requests | RPS | p50 | p95 | p99 | max | Error rate |
|---|---|---|---|---|---|---|---|
| 50 | 8,070 | 267/s | 183 ms | 239 ms | 279 ms | 376 ms | 0.00% |
| 200 | 7,890 | 258/s | 754 ms | 886 ms | 1.27 s | 2.15 s | 0.00% |
| 500 | 8,356 | 266/s | 1.87 s | 1.97 s | 3.37 s | 3.97 s | 0.00% |

Plus one ramped run (0 → 50 → 200 → 500 VUs over 80s): **19,000 requests,
237/s, p50 720 ms, p95 2.11 s, p99 2.36 s, 0.00% errors.**

## Correctness check — the actual gate

Every run, at every concurrency level:

| | 50 VUs | 200 VUs | 500 VUs | ramped |
|---|---|---|---|---|
| HTTP 200 responses | 8,070 | 7,890 | 8,356 | 19,000 |
| Redis tally total | 8,070 | 7,890 | 8,356 | 19,000 |
| Postgres rows | 8,070 | 7,890 | 8,356 | 19,000 |
| Redis voter markers | 8,070 | 7,890 | 8,356 | 19,000 |

**Zero drift across 43,316 votes.** No lost increments, no double counts, no
duplicate rows. The Lua script that combines `SET NX` (dedupe) and `HINCRBY`
(tally) into one atomic Redis call is doing what it was written to do — had
those been two separate round trips, a crash or interleave between them would
show up here as a tally lower than the 200-response count.

## What the latency numbers actually say

Throughput is flat at **~260 RPS from 50 VUs all the way to 500**, while p50
grows almost exactly 10x over the same range. That's the signature of a system
already saturated at 50 concurrent requests: added concurrency buys no extra
work, it just sits in the queue. Little's Law lines up — 500 VUs ÷ 260 RPS
≈ 1.9 s, which is the observed p50.

So the bottleneck is **not** Redis or the tally design. It's the single uvicorn
worker running sync endpoints through Starlette's threadpool. Straightforward
levers, none of them needed yet:

- run multiple uvicorn workers (or an ECS task count > 1) — the vote path holds
  no per-process state, so it scales horizontally as-is
- make the endpoint `async def` with an async Redis client, so the threadpool
  stops being the limit
- batch the Postgres writes instead of one INSERT per vote

At the target for daily mode — hundreds of concurrent players, one vote each
per day — 260 RPS on a single container is already far more headroom than the
mode needs. Optimising further before Phase 5 would be tuning against a number
nobody is asking for.

## Caveats

- Single-host test: k6, backend, Redis and Postgres all share one machine, so
  these latencies include contention the real deployment wouldn't have (and
  exclude network latency it would).
- Duplicate-vote rejection under concurrency is covered by unit tests
  (`test_concurrent_duplicate_votes_let_exactly_one_through`), not by this run —
  every request here uses a fresh voter id by design, so the 409 path is
  deliberately not exercised.
