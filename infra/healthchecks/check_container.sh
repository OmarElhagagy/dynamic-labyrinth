#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Container Health Check
# =============================================================================
# Individual container health check script for Docker HEALTHCHECK directive.
# This script runs inside containers to verify service health.
#
# Exit codes:
#   0 - Healthy
#   1 - Unhealthy
# =============================================================================

set -uo pipefail

# Configuration
HEALTH_ENDPOINT="${HEALTH_ENDPOINT:-http://localhost:8080/health}"
TIMEOUT="${TIMEOUT:-5}"
MAX_RETRIES="${MAX_RETRIES:-1}"

# Check health endpoint
check_health() {
    local retries=0
    
    while [[ $retries -lt $MAX_RETRIES ]]; do
        if curl -sf --max-time "$TIMEOUT" "$HEALTH_ENDPOINT" > /dev/null 2>&1; then
            return 0
        fi
        retries=$((retries + 1))
        sleep 1
    done
    
    return 1
}

# Check if process is running
check_process() {
    local process_name="${1:-honeytrap}"
    
    if pgrep -x "$process_name" > /dev/null 2>&1; then
        return 0
    fi
    
    return 1
}

# Check memory usage
check_memory() {
    local max_percent="${1:-90}"
    
    local used
    local total
    
    if [[ -f /proc/meminfo ]]; then
        total=$(grep MemTotal /proc/meminfo | awk '{print $2}')
        used=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
        
        if [[ -n "$total" && -n "$used" && "$total" -gt 0 ]]; then
            local percent=$(( (total - used) * 100 / total ))
            
            if [[ $percent -ge $max_percent ]]; then
                echo "Memory usage too high: ${percent}%"
                return 1
            fi
        fi
    fi
    
    return 0
}

# Check file descriptors
check_file_descriptors() {
    local max_fds="${1:-10000}"
    
    local current_fds
    current_fds=$(ls -1 /proc/self/fd 2>/dev/null | wc -l)
    
    if [[ $current_fds -ge $max_fds ]]; then
        echo "Too many open file descriptors: $current_fds"
        return 1
    fi
    
    return 0
}

# Main health check
main() {
    # Check health endpoint
    if ! check_health; then
        echo "Health endpoint check failed"
        exit 1
    fi
    
    # Check process
    if ! check_process; then
        echo "Process check failed"
        exit 1
    fi
    
    # Check memory
    if ! check_memory 90; then
        echo "Memory check failed"
        exit 1
    fi
    
    # All checks passed
    exit 0
}

main "$@"
