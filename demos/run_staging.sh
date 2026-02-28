#!/usr/bin/env bash
# Run demo scripts against the staging environment.
#
# Usage:
#   ./run_staging.sh                 # runs demo.py (full happy path)
#   ./run_staging.sh success         # runs demo_success.py
#   ./run_staging.sh failure         # runs demo_failure.py
#   ./run_staging.sh all             # runs success then failure

set -euo pipefail
cd "$(dirname "$0")"

# Load staging env
set -a
source .env.staging
set +a

DEMO="${1:-demo}"

run_demo() {
    echo "━━━ Running $1 against staging ━━━"
    echo "    URL: $AGENT_REGISTRY_URL"
    echo ""
    python3 "$1"
}

case "$DEMO" in
    demo|full)
        run_demo demo.py
        ;;
    success)
        run_demo demo_success.py
        ;;
    failure|fail)
        run_demo demo_failure.py
        ;;
    all)
        run_demo demo_success.py
        echo ""
        echo "════════════════════════════════════════════════════════════════"
        echo ""
        run_demo demo_failure.py
        ;;
    *)
        echo "Unknown demo: $DEMO"
        echo "Usage: $0 [demo|success|failure|all]"
        exit 1
        ;;
esac
