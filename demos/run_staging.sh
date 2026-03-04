#!/usr/bin/env bash
# Run demo scripts against the staging environment.
#
# Usage:
#   ./run_staging.sh setup            # create demo accounts + tokens
#   ./run_staging.sh success          # run happy-path demo
#   ./run_staging.sh failure          # run failure demo
#   ./run_staging.sh all              # success then failure
#   ./run_staging.sh teardown         # clean up demo data
#   ./run_staging.sh full             # setup → success → teardown

set -euo pipefail
cd "$(dirname "$0")"

# Load staging env
set -a
source .env.staging
# Load demo tokens if they exist
[ -f .env.demo ] && source .env.demo
set +a

DEMO="${1:-full}"

run_demo() {
    echo "━━━ Running $1 against staging ━━━"
    echo "    URL: $AGENT_REGISTRY_URL"
    echo ""
    python3 "$1"
}

case "$DEMO" in
    setup)
        python3 demo_setup.py
        ;;
    teardown)
        python3 demo_teardown.py
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
    full)
        python3 demo_setup.py
        echo ""
        echo "════════════════════════════════════════════════════════════════"
        echo ""
        run_demo demo_success.py
        echo ""
        echo "════════════════════════════════════════════════════════════════"
        echo ""
        python3 demo_teardown.py
        ;;
    *)
        echo "Unknown command: $DEMO"
        echo "Usage: $0 [setup|success|failure|all|teardown|full]"
        exit 1
        ;;
esac
