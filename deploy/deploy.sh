#!/usr/bin/env bash
# Run ON the VM (Ubuntu/Debian) after copying the project there.
#   1) cp .env.prod.example .env.prod && edit it
#   2) bash deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env.prod ] || { echo "Create .env.prod first (see .env.prod.example)"; exit 1; }
grep -qE '^(POSTGRES_PASSWORD|ID_SECRET|JWT_SECRET)=$' .env.prod && { echo "Fill in all secrets in .env.prod"; exit 1; }

if ! command -v docker >/dev/null; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
fi

docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
echo "Waiting for the API..."
for i in $(seq 1 40); do
  docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T api python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/ready').status==200 else 1)" \
    2>/dev/null && { echo "API ready."; break; }
  sleep 3
done
DOMAIN=$(grep '^DOMAIN=' .env.prod | cut -d= -f2)
echo "Live at https://$DOMAIN  (first HTTPS request may take ~10s while the certificate is issued)"
