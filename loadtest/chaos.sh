#!/usr/bin/env bash
# Chaos experiment: steady traffic while we kill and restore Redis, then Postgres.
#
#   bash loadtest/chaos.sh [name]
#
# Timeline (seconds since traffic starts):
#   t=15  stop  Redis      t=30  start Redis
#   t=40  stop  Postgres   t=55  start Postgres
#   t=75  traffic ends
# Output: loadtest/results/<name>.json  ->  python loadtest/analyze_chaos.py <name>
set -u
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1     # stop Git Bash rewriting /scripts/... paths
NAME="${1:-chaos}"
LOG="loadtest/results/$NAME.log"
mkdir -p loadtest/results

# Run with these knobs so we measure resilience, not the rate limiter.
export RATE_LIMIT_ENABLED=false CACHE_ENABLED=true

echo "[chaos] resetting stack"
docker compose up -d db redis api worker >/dev/null 2>&1
docker compose up -d --force-recreate api >/dev/null 2>&1
for i in $(seq 1 30); do curl -fs localhost:8000/ready >/dev/null && break; sleep 1; done

echo "[chaos] seeding 20,000 long-tail links directly in Postgres"
docker compose exec -T db psql -U shortener -d shortener -q -c "
  DELETE FROM links WHERE code LIKE 'tail-%';
  INSERT INTO links (id, code, original_url)
  SELECT nextval('links_id_seq'), 'tail-' || g, 'https://example.com/t/' || g FROM generate_series(1, 20000) g;"
# make sure no tail entries are cached from an earlier run
docker compose exec -T redis redis-cli --scan --pattern 'link:tail-*' | xargs -r docker compose exec -T redis redis-cli del >/dev/null 2>&1

echo "[chaos] starting traffic"
docker compose --profile load run --no-deps --rm -T k6 run -e NAME="$NAME" -e RATE="${RATE:-800}" \
  /scripts/chaos.js >"$LOG" 2>&1 &
K6PID=$!

for i in $(seq 1 60); do grep -q '^.*T0 ' "$LOG" 2>/dev/null && break; sleep 1; done
T0=$(date +%s)
at() { local target=$1 now; now=$(( $(date +%s) - T0 )); [ "$now" -lt "$target" ] && sleep $(( target - now )); }

at 15; echo "[chaos] t=15  STOP  redis";     docker compose stop redis >/dev/null 2>&1
at 30; echo "[chaos] t=30  START redis";     docker compose start redis >/dev/null 2>&1
at 40; echo "[chaos] t=40  STOP  postgres";  docker compose stop db >/dev/null 2>&1
at 55; echo "[chaos] t=55  START postgres";  docker compose start db >/dev/null 2>&1

wait $K6PID
echo "[chaos] done -> loadtest/results/$NAME.json"
