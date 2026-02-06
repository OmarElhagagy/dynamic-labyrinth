#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Health Check Script
# =============================================================================
# Performs health checks on all honeytrap containers and services.
# Can be used for monitoring, alerting, or CI/CD validation.
#
# Usage: ./healthcheck.sh [--json] [--verbose] [--container NAME]
# =============================================================================

set -uo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
OUTPUT_FORMAT="text"
VERBOSE=false
SPECIFIC_CONTAINER=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Container lists
LEVEL1_CONTAINERS=("honeytrap-level1-1" "honeytrap-level1-2" "honeytrap-level1-3" "honeytrap-level1-4" "honeytrap-level1-5")
LEVEL2_CONTAINERS=("honeytrap-level2-1" "honeytrap-level2-2" "honeytrap-level2-3")
LEVEL3_CONTAINERS=("honeytrap-level3-1")
INFRA_CONTAINERS=("orchestrator" "nginx")

ALL_CONTAINERS=("${INFRA_CONTAINERS[@]}" "${LEVEL1_CONTAINERS[@]}" "${LEVEL2_CONTAINERS[@]}" "${LEVEL3_CONTAINERS[@]}")

# Results storage
declare -A RESULTS

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --json)
                OUTPUT_FORMAT="json"
                shift
                ;;
            --verbose|-v)
                VERBOSE=true
                shift
                ;;
            --container|-c)
                SPECIFIC_CONTAINER="$2"
                shift 2
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                echo "Unknown argument: $1"
                exit 1
                ;;
        esac
    done
}

show_help() {
    cat << EOF
Dynamic Labyrinth - Health Check Script

Usage: ./healthcheck.sh [OPTIONS]

Options:
    --json              Output results in JSON format
    --verbose, -v       Show detailed health information
    --container, -c     Check specific container only
    --help              Show this help message

Examples:
    ./healthcheck.sh                          # Check all containers
    ./healthcheck.sh --json                   # JSON output
    ./healthcheck.sh -c honeytrap-level1-1    # Check specific container

Exit Codes:
    0 - All containers healthy
    1 - One or more containers unhealthy
    2 - Script error

EOF
}

# Check if container is running
check_container_running() {
    local container=$1
    docker inspect --format='{{.State.Running}}' "$container" 2>/dev/null || echo "false"
}

# Get container health status
get_container_health() {
    local container=$1
    docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "unknown"
}

# Check container's health endpoint
check_health_endpoint() {
    local container=$1
    local port=$2
    
    # Try internal health check first
    if docker exec "$container" curl -sf "http://localhost:${port}/health" &>/dev/null; then
        echo "ok"
    else
        echo "fail"
    fi
}

# Get container stats
get_container_stats() {
    local container=$1
    docker stats --no-stream --format "{{.CPUPerc}},{{.MemUsage}}" "$container" 2>/dev/null || echo "N/A,N/A"
}

# Get container uptime
get_container_uptime() {
    local container=$1
    local started
    started=$(docker inspect --format='{{.State.StartedAt}}' "$container" 2>/dev/null)
    
    if [[ -n "$started" && "$started" != "0001-01-01T00:00:00Z" ]]; then
        # Calculate uptime
        local start_ts
        start_ts=$(date -d "$started" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%S" "${started:0:19}" +%s 2>/dev/null)
        local now_ts
        now_ts=$(date +%s)
        local uptime_secs=$((now_ts - start_ts))
        
        # Format uptime
        local days=$((uptime_secs / 86400))
        local hours=$(((uptime_secs % 86400) / 3600))
        local mins=$(((uptime_secs % 3600) / 60))
        
        if [[ $days -gt 0 ]]; then
            echo "${days}d ${hours}h ${mins}m"
        elif [[ $hours -gt 0 ]]; then
            echo "${hours}h ${mins}m"
        else
            echo "${mins}m"
        fi
    else
        echo "unknown"
    fi
}

# Check a single container
check_container() {
    local container=$1
    local level=""
    local health_port="8080"
    
    # Determine level and port
    if [[ "$container" == *"level1"* ]]; then
        level="1"
    elif [[ "$container" == *"level2"* ]]; then
        level="2"
    elif [[ "$container" == *"level3"* ]]; then
        level="3"
    elif [[ "$container" == "orchestrator" ]]; then
        level="infra"
        health_port="8000"
    elif [[ "$container" == "nginx" ]]; then
        level="infra"
        health_port="80"
    fi
    
    # Check running status
    local running
    running=$(check_container_running "$container")
    
    if [[ "$running" != "true" ]]; then
        RESULTS["$container"]="not_running"
        return 1
    fi
    
    # Check Docker health status
    local health
    health=$(get_container_health "$container")
    
    # Check health endpoint
    local endpoint
    endpoint=$(check_health_endpoint "$container" "$health_port")
    
    # Determine overall status
    if [[ "$health" == "healthy" && "$endpoint" == "ok" ]]; then
        RESULTS["$container"]="healthy"
        return 0
    elif [[ "$health" == "starting" ]]; then
        RESULTS["$container"]="starting"
        return 0
    else
        RESULTS["$container"]="unhealthy"
        return 1
    fi
}

# Print text output
print_text_output() {
    echo ""
    echo "=========================================="
    echo "Dynamic Labyrinth - Health Check Report"
    echo "=========================================="
    echo "Timestamp: $(date -Iseconds)"
    echo ""
    
    local healthy_count=0
    local unhealthy_count=0
    local total_count=0
    
    # Infrastructure
    echo "Infrastructure:"
    for container in "${INFRA_CONTAINERS[@]}"; do
        total_count=$((total_count + 1))
        local status="${RESULTS[$container]:-unknown}"
        
        if [[ "$status" == "healthy" ]]; then
            echo -e "  ${GREEN}✓${NC} $container: $status"
            healthy_count=$((healthy_count + 1))
        else
            echo -e "  ${RED}✗${NC} $container: $status"
            unhealthy_count=$((unhealthy_count + 1))
        fi
        
        if [[ "$VERBOSE" == "true" ]]; then
            local stats
            stats=$(get_container_stats "$container")
            local uptime
            uptime=$(get_container_uptime "$container")
            echo "      CPU/Mem: $stats | Uptime: $uptime"
        fi
    done
    
    echo ""
    echo "Level 1 (Low Interaction):"
    for container in "${LEVEL1_CONTAINERS[@]}"; do
        total_count=$((total_count + 1))
        local status="${RESULTS[$container]:-unknown}"
        
        if [[ "$status" == "healthy" ]]; then
            echo -e "  ${GREEN}✓${NC} $container: $status"
            healthy_count=$((healthy_count + 1))
        else
            echo -e "  ${RED}✗${NC} $container: $status"
            unhealthy_count=$((unhealthy_count + 1))
        fi
    done
    
    echo ""
    echo "Level 2 (Medium Interaction):"
    for container in "${LEVEL2_CONTAINERS[@]}"; do
        total_count=$((total_count + 1))
        local status="${RESULTS[$container]:-unknown}"
        
        if [[ "$status" == "healthy" ]]; then
            echo -e "  ${GREEN}✓${NC} $container: $status"
            healthy_count=$((healthy_count + 1))
        else
            echo -e "  ${RED}✗${NC} $container: $status"
            unhealthy_count=$((unhealthy_count + 1))
        fi
    done
    
    echo ""
    echo "Level 3 (High Interaction):"
    for container in "${LEVEL3_CONTAINERS[@]}"; do
        total_count=$((total_count + 1))
        local status="${RESULTS[$container]:-unknown}"
        
        if [[ "$status" == "healthy" ]]; then
            echo -e "  ${GREEN}✓${NC} $container: $status"
            healthy_count=$((healthy_count + 1))
        else
            echo -e "  ${RED}✗${NC} $container: $status"
            unhealthy_count=$((unhealthy_count + 1))
        fi
    done
    
    echo ""
    echo "=========================================="
    echo "Summary: ${healthy_count}/${total_count} healthy"
    
    if [[ $unhealthy_count -gt 0 ]]; then
        echo -e "${RED}Status: UNHEALTHY${NC}"
        return 1
    else
        echo -e "${GREEN}Status: HEALTHY${NC}"
        return 0
    fi
}

# Print JSON output
print_json_output() {
    local timestamp
    timestamp=$(date -Iseconds)
    
    echo "{"
    echo "  \"timestamp\": \"$timestamp\","
    echo "  \"containers\": {"
    
    local first=true
    for container in "${ALL_CONTAINERS[@]}"; do
        if [[ "$first" == "true" ]]; then
            first=false
        else
            echo ","
        fi
        
        local status="${RESULTS[$container]:-unknown}"
        local stats
        stats=$(get_container_stats "$container")
        local uptime
        uptime=$(get_container_uptime "$container")
        
        echo -n "    \"$container\": {"
        echo -n "\"status\": \"$status\""
        
        if [[ "$VERBOSE" == "true" ]]; then
            IFS=',' read -r cpu mem <<< "$stats"
            echo -n ", \"cpu\": \"$cpu\", \"memory\": \"$mem\", \"uptime\": \"$uptime\""
        fi
        
        echo -n "}"
    done
    
    echo ""
    echo "  },"
    
    # Count healthy/unhealthy
    local healthy=0
    local unhealthy=0
    for status in "${RESULTS[@]}"; do
        if [[ "$status" == "healthy" ]]; then
            healthy=$((healthy + 1))
        else
            unhealthy=$((unhealthy + 1))
        fi
    done
    
    echo "  \"summary\": {"
    echo "    \"total\": ${#RESULTS[@]},"
    echo "    \"healthy\": $healthy,"
    echo "    \"unhealthy\": $unhealthy"
    echo "  },"
    
    if [[ $unhealthy -gt 0 ]]; then
        echo "  \"overall_status\": \"unhealthy\""
    else
        echo "  \"overall_status\": \"healthy\""
    fi
    
    echo "}"
    
    [[ $unhealthy -eq 0 ]]
}

# Main execution
main() {
    parse_args "$@"
    
    # Check specific container or all
    if [[ -n "$SPECIFIC_CONTAINER" ]]; then
        check_container "$SPECIFIC_CONTAINER"
        local status="${RESULTS[$SPECIFIC_CONTAINER]:-unknown}"
        
        if [[ "$OUTPUT_FORMAT" == "json" ]]; then
            echo "{\"container\": \"$SPECIFIC_CONTAINER\", \"status\": \"$status\"}"
        else
            if [[ "$status" == "healthy" ]]; then
                echo -e "${GREEN}✓${NC} $SPECIFIC_CONTAINER: $status"
            else
                echo -e "${RED}✗${NC} $SPECIFIC_CONTAINER: $status"
            fi
        fi
        
        [[ "$status" == "healthy" ]]
        exit $?
    fi
    
    # Check all containers
    for container in "${ALL_CONTAINERS[@]}"; do
        check_container "$container"
    done
    
    # Output results
    if [[ "$OUTPUT_FORMAT" == "json" ]]; then
        print_json_output
    else
        print_text_output
    fi
}

main "$@"
