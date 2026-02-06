#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Pre-warm Containers Script
# =============================================================================
# Starts and validates all honeytrap containers before accepting traffic.
# Ensures all containers are healthy and ready to receive connections.
#
# Usage: ./prewarm.sh [--timeout SECONDS] [--parallel]
# =============================================================================

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"
TIMEOUT="${TIMEOUT:-120}"
PARALLEL="${PARALLEL:-false}"
HEALTH_CHECK_INTERVAL=5
MAX_RETRIES=24

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Container lists by level
LEVEL1_CONTAINERS=(
    "honeytrap-level1-1"
    "honeytrap-level1-2"
    "honeytrap-level1-3"
    "honeytrap-level1-4"
    "honeytrap-level1-5"
)

LEVEL2_CONTAINERS=(
    "honeytrap-level2-1"
    "honeytrap-level2-2"
    "honeytrap-level2-3"
)

LEVEL3_CONTAINERS=(
    "honeytrap-level3-1"
)

ALL_CONTAINERS=("${LEVEL1_CONTAINERS[@]}" "${LEVEL2_CONTAINERS[@]}" "${LEVEL3_CONTAINERS[@]}")

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_debug() {
    if [[ "${DEBUG:-false}" == "true" ]]; then
        echo -e "${BLUE}[DEBUG]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
    fi
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --timeout)
                TIMEOUT="$2"
                shift 2
                ;;
            --parallel)
                PARALLEL=true
                shift
                ;;
            --debug)
                DEBUG=true
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                exit 1
                ;;
        esac
    done
}

show_help() {
    cat << EOF
Dynamic Labyrinth - Pre-warm Containers Script

Usage: ./prewarm.sh [OPTIONS]

Options:
    --timeout SECONDS    Maximum time to wait for containers (default: 120)
    --parallel           Start containers in parallel (faster but more resource intensive)
    --debug              Enable debug output
    --help               Show this help message

Examples:
    ./prewarm.sh                    # Default sequential startup
    ./prewarm.sh --timeout 180      # Wait up to 3 minutes
    ./prewarm.sh --parallel         # Parallel startup

EOF
}

# Check if Docker is running
check_docker() {
    if ! docker info &>/dev/null; then
        log_error "Docker is not running or not accessible"
        exit 1
    fi
    log_info "Docker is running"
}

# Start containers
start_containers() {
    log_info "Starting containers..."
    
    cd "$PROJECT_ROOT"
    
    if [[ "$PARALLEL" == "true" ]]; then
        log_info "Starting all containers in parallel..."
        docker-compose -f "$COMPOSE_FILE" up -d
    else
        # Start in order: Level 1 -> Level 2 -> Level 3
        log_info "Starting Level 1 containers..."
        for container in "${LEVEL1_CONTAINERS[@]}"; do
            docker-compose -f "$COMPOSE_FILE" up -d "$container"
        done
        
        log_info "Starting Level 2 containers..."
        for container in "${LEVEL2_CONTAINERS[@]}"; do
            docker-compose -f "$COMPOSE_FILE" up -d "$container"
        done
        
        log_info "Starting Level 3 containers..."
        for container in "${LEVEL3_CONTAINERS[@]}"; do
            docker-compose -f "$COMPOSE_FILE" up -d "$container"
        done
    fi
    
    log_info "Container startup initiated"
}

# Wait for container to be healthy
wait_for_container() {
    local container=$1
    local retries=0
    
    log_debug "Waiting for $container to be healthy..."
    
    while [[ $retries -lt $MAX_RETRIES ]]; do
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "not_found")
        
        case $status in
            healthy)
                log_info "✓ $container is healthy"
                return 0
                ;;
            unhealthy)
                log_error "✗ $container is unhealthy"
                docker logs --tail 20 "$container" 2>&1 | head -10
                return 1
                ;;
            starting)
                log_debug "$container is still starting..."
                ;;
            not_found)
                log_warn "$container not found, may not be started yet"
                ;;
            *)
                log_debug "$container status: $status"
                ;;
        esac
        
        retries=$((retries + 1))
        sleep "$HEALTH_CHECK_INTERVAL"
    done
    
    log_error "✗ $container did not become healthy within timeout"
    return 1
}

# Wait for all containers to be healthy
wait_for_all_containers() {
    log_info "Waiting for all containers to be healthy (timeout: ${TIMEOUT}s)..."
    
    local failed=0
    local start_time
    start_time=$(date +%s)
    
    for container in "${ALL_CONTAINERS[@]}"; do
        local current_time
        current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [[ $elapsed -ge $TIMEOUT ]]; then
            log_error "Timeout reached while waiting for containers"
            return 1
        fi
        
        if ! wait_for_container "$container"; then
            failed=$((failed + 1))
        fi
    done
    
    if [[ $failed -gt 0 ]]; then
        log_error "$failed container(s) failed to become healthy"
        return 1
    fi
    
    log_info "All containers are healthy!"
    return 0
}

# Verify container connectivity
verify_connectivity() {
    log_info "Verifying container connectivity..."
    
    local failed=0
    
    for container in "${ALL_CONTAINERS[@]}"; do
        # Get container IP
        local ip
        ip=$(docker inspect --format='{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$container" 2>/dev/null)
        
        if [[ -z "$ip" ]]; then
            log_warn "Could not get IP for $container"
            continue
        fi
        
        # Try to reach health endpoint
        if docker exec "$container" curl -sf "http://localhost:8080/health" &>/dev/null; then
            log_debug "✓ $container health endpoint responding"
        else
            log_warn "✗ $container health endpoint not responding"
            failed=$((failed + 1))
        fi
    done
    
    if [[ $failed -gt 0 ]]; then
        log_warn "$failed container(s) have connectivity issues"
    else
        log_info "All containers are reachable"
    fi
}

# Start orchestrator
start_orchestrator() {
    log_info "Starting orchestrator..."
    
    cd "$PROJECT_ROOT"
    docker-compose -f "$COMPOSE_FILE" up -d orchestrator
    
    # Wait for orchestrator
    local retries=0
    while [[ $retries -lt 20 ]]; do
        if curl -sf "http://localhost:8000/healthz" &>/dev/null; then
            log_info "✓ Orchestrator is healthy"
            return 0
        fi
        retries=$((retries + 1))
        sleep 3
    done
    
    log_error "Orchestrator did not become healthy"
    return 1
}

# Start nginx
start_nginx() {
    log_info "Starting nginx..."
    
    cd "$PROJECT_ROOT"
    docker-compose -f "$COMPOSE_FILE" up -d nginx
    
    # Wait for nginx
    local retries=0
    while [[ $retries -lt 10 ]]; do
        if curl -sf "http://localhost/health" &>/dev/null; then
            log_info "✓ Nginx is healthy"
            return 0
        fi
        retries=$((retries + 1))
        sleep 2
    done
    
    log_error "Nginx did not become healthy"
    return 1
}

# Print summary
print_summary() {
    log_info "=========================================="
    log_info "Pre-warm Summary"
    log_info "=========================================="
    
    echo ""
    echo "Container Status:"
    docker-compose -f "$COMPOSE_FILE" ps
    
    echo ""
    echo "Pool Status (from orchestrator):"
    curl -sf "http://localhost:8000/pools" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  Could not fetch pool status"
    
    echo ""
    log_info "Pre-warm complete!"
}

# Main execution
main() {
    parse_args "$@"
    
    log_info "=========================================="
    log_info "Dynamic Labyrinth - Pre-warm Containers"
    log_info "=========================================="
    log_info "Timeout: ${TIMEOUT}s"
    log_info "Parallel: ${PARALLEL}"
    
    check_docker
    start_containers
    wait_for_all_containers
    verify_connectivity
    start_orchestrator
    start_nginx
    print_summary
}

main "$@"
