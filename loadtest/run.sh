#!/usr/bin/env bash
# Phase 2 load test: reset -> hammer -> verify the counts line up.
#
# The verification step is the point. Latency numbers are useful, but the
# question this gate answers is "did every successful vote get counted exactly
# once?" — so we compare three numbers that must be identical:
#   k6's 200-response count == Redis tally total == Postgres row count
set -euo pipefail

cd "$(dirname "$0")/.."

QUESTION_ID="${QUESTION_ID:-$(curl -sf http://localhost:8000/api/daily | python -c 'import sys,json; print(json.load(sys.stdin)["id"])')}"
NETWORK="$(docker compose ps --format json backend | python -c 'import sys,json; print(json.loads(sys.stdin.readline())["Networks"])')"

echo "question:  $QUESTION_ID"
echo "network:   $NETWORK"

echo "--- resetting vote state ---"
docker compose exec -T redis redis-cli FLUSHALL > /dev/null
docker compose exec -T postgres psql -U votecracy -d votecracy -q -c "TRUNCATE votes;"

echo "--- running k6 ---"
docker run --rm -i --network "$NETWORK" \
  -e BASE_URL=http://backend:8000 -e VUS="${VUS:-}" -e DURATION="${DURATION:-30s}" \
  -i grafana/k6 run - < loadtest/daily-vote.js

echo "--- verifying counts ---"
echo -n "redis tally:    "
docker compose exec -T redis redis-cli HGETALL "tally:$QUESTION_ID" | paste - - | tr '\t' '=' | tr '\n' ' '
echo
echo -n "redis total:    "
docker compose exec -T redis redis-cli --no-raw EVAL "local t=0 for _,v in ipairs(redis.call('HVALS', KEYS[1])) do t=t+v end return t" 1 "tally:$QUESTION_ID"
echo -n "postgres rows:  "
docker compose exec -T postgres psql -U votecracy -d votecracy -tAc "select count(*) from votes where question_id='$QUESTION_ID';"
echo -n "voter markers:  "
docker compose exec -T redis redis-cli EVAL "return #redis.call('KEYS', 'voted:*')" 0
