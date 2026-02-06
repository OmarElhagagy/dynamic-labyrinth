#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Container Lifecycle Manager
# =============================================================================
# Manages the complete lifecycle of honeytrap containers.
# Handles creation, monitoring, recycling, and cleanup.
#
# Usage: ./lifecycle.sh <command> [options]
# =============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Show help
show_help() {
    cat << EOF
Dynamic Labyrinth - Container Lifecycle Manager

Usage: ./lifecycle.sh <command> [options]

Commands:
    recycle <container>     Recycle a specific container
    recycle-pool <level>    Recycle all containers in a pool
    cleanup                 Clean up orphaned/unhealthy containers
    gc                      Run garbage collection
    logs <container>        View container logs
    stats                   Show container statistics
    restart <container>     Restart a specific container
    restart-pool <level>    Restart all containers in a pool

Options:
    --force, -f             Force operation without confirmation
    --dry-run               Show what would be done
    --help                  Show this help

Examples:
    ./lifecycle.sh recycle honeytrap-level1-1
    ./lifecycle.sh recycle-pool 2
    ./lifecycle.sh cleanup --dry-run
    ./lifecycle.sh gc

EOF
}

# Recycle a single container
cmd_recycle() {
    local container="$1"
    local force="${2:-false}"
    
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${container}$"; then
        log_error "Container '$container' not found"
        return 1
    fi
    
    log_info "Recycling container: $container"
    
    # Stop container gracefully
    log_info "Stopping container..."
    docker stop --time 30 "$container" 2>/dev/null || true
    
    # Remove container
    log_info "Removing container..."
    docker rm "$container" 2>/dev/null || true
    
    # Start fresh container
    log_info "Starting fresh container..."
    cd "$PROJECT_ROOT"
    docker-compose -f "$COMPOSE_FILE" up -d "$container"
    
    # Wait for health
    log_info "Waiting for container to be healthy..."
    local retries=0
    while [[ $retries -lt 30 ]]; do
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "starting")
        
        if [[ "$status" == "healthy" ]]; then
            log_info "Container $container is healthy"
            return 0
        fi
        
        retries=$((retries + 1))
        sleep 2
    done
    
    log_warn "Container did not become healthy within timeout"
    return 1
}

# Recycle all containers in a pool
cmd_recycle_pool() {
    local level="$1"
    local containers=()
    
    case $level in
        1)
            containers=("honeytrap-level1-1" "honeytrap-level1-2" "honeytrap-level1-3" "honeytrap-level1-4" "honeytrap-level1-5")
            ;;
        2)
            containers=("honeytrap-level2-1" "honeytrap-level2-2" "honeytrap-level2-3")
            ;;
        3)
            containers=("honeytrap-level3-1")
            ;;
        *)
            log_error "Invalid level: $level (must be 1, 2, or 3)"
            return 1
            ;;
    esac
    
    log_info "Recycling pool level $level (${#containers[@]} containers)"
    
    local failed=0
    for container in "${containers[@]}"; do
        if ! cmd_recycle "$container"; then
            failed=$((failed + 1))
        fi
    done
    
    if [[ $failed -gt 0 ]]; then
        log_warn "$failed container(s) failed to recycle"
        return 1
    fi
    
    log_info "Pool level $level recycled successfully"
    return 0
}

# Clean up orphaned/unhealthy containers
cmd_cleanup() {
    local dry_run="${1:-false}"
    
    log_info "Scanning for unhealthy containers..."
    
    local unhealthy_containers
    unhealthy_containers=$(docker ps --filter "health=unhealthy" --format '{{.Names}}' | grep "honeytrap" || true)
    
    if [[ -z "$unhealthy_containers" ]]; then
        log_info "No unhealthy containers found"
        return 0
    fi
    
    echo "Unhealthy containers:"
    echo "$unhealthy_containers" | while read -r container; do
        echo "  - $container"
    done
    
    if [[ "$dry_run" == "true" ]]; then
        log_info "(Dry run - no changes made)"
        return 0
    fi
    
    # Recycle each unhealthy container
    echo "$unhealthy_containers" | while read -r container; do
        cmd_recycle "$container"
    done
    
    log_info "Cleanup complete"
}

# Garbage collection
cmd_gc() {
    log_info "Running garbage collection..."
    
    # Remove stopped containers
    log_info "Removing stopped honeytrap containers..."
    docker container prune -f --filter "label=app=honeytrap" 2>/dev/null || true
    
    # Remove dangling images
    log_info "Removing dangling images..."
    docker image prune -f 2>/dev/null || true
    
    # Remove unused volumes
    log_info "Removing unused volumes..."
    docker volume prune -f 2>/dev/null || true
    
    # Clean up old logs
    log_info "Cleaning up old logs..."
    find "${PROJECT_ROOT}/logs" -type f -name "*.log" -mtime +7 -delete 2>/dev/null || true
    
    log_info "Garbage collection complete"
}

# View container logs
cmd_logs() {
    local container="$1"
    local lines="${2:-100}"
    
    docker logs --tail "$lines" -f "$container"
}

# Show container statistics
cmd_stats() {
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}" \
        $(docker ps --filter "name=honeytrap" --format "{{.Names}}")
}

# Restart a container
cmd_restart() {
    local container="$1"
    
    log_info "Restarting container: $container"
    docker restart --time 30 "$container"
    
    log_info "Waiting for container to be healthy..."
    local retries=0
    while [[ $retries -lt 30 ]]; do
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "starting")
        
        if [[ "$status" == "healthy" ]]; then
            log_info "Container $container is healthy"
            return 0
        fi
        
        retries=$((retries + 1))
        sleep 2
    done
    
    log_warn "Container did not become healthy within timeout"
    return 1
}

# Restart all containers in a pool
cmd_restart_pool() {
    local level="$1"
    local containers=()
    
    case $level in
        1)
            containers=("honeytrap-level1-1" "honeytrap-level1-2" "honeytrap-level1-3" "honeytrap-level1-4" "honeytrap-level1-5")
            ;;
        2)
            containers=("honeytrap-level2-1" "honeytrap-level2-2" "honeytrap-level2-3")
            ;;
        3)
            containers=("honeytrap-level3-1")
            ;;
        *)
            log_error "Invalid level: $level"
            return 1
            ;;
    esac
    
    log_info "Restarting pool level $level"
    
    for container in "${containers[@]}"; do
        cmd_restart "$container"
    done
    
    log_info "Pool level $level restarted"
}

# Main
main() {
    local command="${1:-}"
    shift || true
    
    case "$command" in
        recycle)
            cmd_recycle "${1:-}"
            ;;
        recycle-pool)
            cmd_recycle_pool "${1:-}"
            ;;
        cleanup)
            local dry_run=false
            [[ "${1:-}" == "--dry-run" ]] && dry_run=true
            cmd_cleanup "$dry_run"
            ;;
        gc)
            cmd_gc
            ;;
        logs)
            cmd_logs "${1:-}" "${2:-100}"
            ;;
        stats)
            cmd_stats
            ;;
        restart)
            cmd_restart "${1:-}"
            ;;
        restart-pool)
            cmd_restart_pool "${1:-}"
            ;;
        --help|help)
            show_help
            ;;
        *)
            log_error "Unknown command: $command"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
