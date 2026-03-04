#!/usr/bin/env bash
# Self-contained integration test runner.
#
# Runs inside Docker with GCP credentials mounted from host.
# Fetches all secrets from Secret Manager automatically.
# Starts Cloud SQL Proxy for DB access (requires public IP on instance).
#
# Usage (from repo root):
#   docker build -t arcoa-integration demos/
#   docker run --rm \
#     -v ~/.config/gcloud:/root/.config/gcloud:ro \
#     arcoa-integration

set -euo pipefail

# ─── Defaults (override via env) ───
export GCP_PROJECT="${GCP_PROJECT:-agent-registry-488317}"
export DB_CONNECTION="${DB_CONNECTION:-agent-registry-488317:us-west1:agent-registry-staging}"
export DEMO_DB_NAME="${DEMO_DB_NAME:-agent_registry}"
export DEMO_DB_USER="${DEMO_DB_USER:-api_user}"
export DEMO_DB_HOST="127.0.0.1"
export DEMO_DB_PORT="5433"
export AGENT_REGISTRY_URL="${AGENT_REGISTRY_URL:-https://api.staging.arcoa.ai}"
export BLOCKCHAIN_NETWORK="${BLOCKCHAIN_NETWORK:-base_sepolia}"

# Demo amounts (small for testnet)
export DEMO_DEPOSIT_AMOUNT="${DEMO_DEPOSIT_AMOUNT:-3.00}"
export DEMO_LISTING_PRICE="${DEMO_LISTING_PRICE:-0.05}"
export DEMO_MAX_BUDGET="${DEMO_MAX_BUDGET:-1.50}"
export DEMO_COUNTER_PRICE_1="${DEMO_COUNTER_PRICE_1:-2.00}"
export DEMO_COUNTER_PRICE_2="${DEMO_COUNTER_PRICE_2:-1.80}"
export DEMO_ALICE_DEPOSIT="${DEMO_ALICE_DEPOSIT:-0.50}"

# ─── Colors ───
GREEN="\033[92m"
RED="\033[91m"
BOLD="\033[1m"
DIM="\033[2m"
RESET="\033[0m"

echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Arcoa Integration Test${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${DIM}  API:      ${AGENT_REGISTRY_URL}${RESET}"
echo -e "${DIM}  DB:       ${DB_CONNECTION}${RESET}"
echo -e "${DIM}  Network:  ${BLOCKCHAIN_NETWORK}${RESET}"
echo ""

# ─── Fetch secrets from Secret Manager ───
echo -e "${DIM}Fetching secrets from Secret Manager...${RESET}"
fetch_secret() {
    python3 -c "
from google.cloud import secretmanager
client = secretmanager.SecretManagerServiceClient()
name = 'projects/${GCP_PROJECT}/secrets/$1/versions/latest'
response = client.access_secret_version(request={'name': name})
print(response.payload.data.decode('UTF-8'), end='')
"
}

export DEMO_DB_PASSWORD=$(fetch_secret "db-password-staging")
export DEMO_WALLET_PRIVATE_KEY="${DEMO_WALLET_PRIVATE_KEY:-$(fetch_secret "treasury_wallet_private_key")}"
echo -e "${GREEN}✓ Secrets retrieved${RESET}"

# ─── Start Cloud SQL Proxy ───
echo -e "${DIM}Starting Cloud SQL Proxy...${RESET}"
cloud-sql-proxy "${DB_CONNECTION}" \
    --port=5433 \
    --quiet &
PROXY_PID=$!

# Wait for proxy to be ready
for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1', 5433)); s.close()" 2>/dev/null; then
        echo -e "${GREEN}✓ Cloud SQL Proxy ready${RESET}"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo -e "${RED}✗ Cloud SQL Proxy failed to start${RESET}"
        kill $PROXY_PID 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

# ─── Cleanup trap ───
cleanup() {
    local exit_code=$?
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}  Teardown${RESET}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
    python3 demo_teardown.py || true
    kill $PROXY_PID 2>/dev/null || true
    wait $PROXY_PID 2>/dev/null || true
    echo ""
    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}${BOLD}═══ INTEGRATION TEST PASSED ═══${RESET}"
    else
        echo -e "${RED}${BOLD}═══ INTEGRATION TEST FAILED ═══${RESET}"
    fi
    exit $exit_code
}
trap cleanup EXIT

# ─── Setup ───
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Setup${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
python3 demo_setup.py

# Load generated tokens
set -a
source .env.demo
set +a

# ─── Run Demo ───
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Integration Test${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
python3 demo_success.py

# Teardown happens in the trap
