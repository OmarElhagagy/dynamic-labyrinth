#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Load Test Runner
# =============================================================================
# Convenience script to run load tests with common configurations.
#
# Usage: ./run_load_test.sh [scenario] [options]
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCUST_FILE="${SCRIPT_DIR}/locustfile.py"

# Default configuration
USERS="${USERS:-50}"
SPAWN_RATE="${SPAWN_RATE:-5}"
DURATION="${DURATION:-5m}"
HOST="${HOST:-http://localhost:8000}"
SCENARIO="${1:-orchestrator}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

show_help() {
    cat << EOF
Dynamic Labyrinth - Load Test Runner

Usage: ./run_load_test.sh [scenario] [options]

Scenarios:
    orchestrator    Test orchestrator API (default)
    attacker        Simulate attacker traffic on nginx
    pool            Focused pool management load test
    all             Run all user types

Options (via environment variables):
    USERS           Number of concurrent users (default: 50)
    SPAWN_RATE      Users spawned per second (default: 5)
    DURATION        Test duration (default: 5m)
    HOST            Target host (default: http://localhost:8000)

Examples:
    ./run_load_test.sh orchestrator
    USERS=100 DURATION=10m ./run_load_test.sh pool
    HOST=http://staging:8000 ./run_load_test.sh all

EOF
}

run_load_test() {
    local user_class="$1"
    
    echo -e "${GREEN}Starting load test...${NC}"
    echo "Scenario: $SCENARIO"
    echo "User class: $user_class"
    echo "Users: $USERS"
    echo "Spawn rate: $SPAWN_RATE/s"
    echo "Duration: $DURATION"
    echo "Host: $HOST"
    echo ""
    
    locust -f "$LOCUST_FILE" \
        --host="$HOST" \
        --headless \
        --users "$USERS" \
        --spawn-rate "$SPAWN_RATE" \
        --run-time "$DURATION" \
        --only-summary \
        ${user_class:+--class-picker}
}

case "${SCENARIO}" in
    orchestrator)
        echo -e "${GREEN}Running Orchestrator API load test${NC}"
        locust -f "$LOCUST_FILE" \
            --host="$HOST" \
            --headless \
            --users "$USERS" \
            --spawn-rate "$SPAWN_RATE" \
            --run-time "$DURATION" \
            --only-summary \
            OrchestratorUser
        ;;
    
    attacker)
        echo -e "${GREEN}Running Attacker simulation load test${NC}"
        locust -f "$LOCUST_FILE" \
            --host="http://localhost" \
            --headless \
            --users "$USERS" \
            --spawn-rate "$SPAWN_RATE" \
            --run-time "$DURATION" \
            --only-summary \
            AttackerSimulator
        ;;
    
    pool)
        echo -e "${GREEN}Running Pool Manager load test${NC}"
        locust -f "$LOCUST_FILE" \
            --host="$HOST" \
            --headless \
            --users "$USERS" \
            --spawn-rate "$SPAWN_RATE" \
            --run-time "$DURATION" \
            --only-summary \
            PoolManagerLoad
        ;;
    
    all)
        echo -e "${GREEN}Running all load test scenarios${NC}"
        locust -f "$LOCUST_FILE" \
            --host="$HOST" \
            --headless \
            --users "$USERS" \
            --spawn-rate "$SPAWN_RATE" \
            --run-time "$DURATION" \
            --only-summary
        ;;
    
    web)
        echo -e "${GREEN}Starting Locust web UI${NC}"
        echo "Open http://localhost:8089 in your browser"
        locust -f "$LOCUST_FILE" --host="$HOST"
        ;;
    
    help|--help|-h)
        show_help
        ;;
    
    *)
        echo -e "${YELLOW}Unknown scenario: $SCENARIO${NC}"
        show_help
        exit 1
        ;;
esac
