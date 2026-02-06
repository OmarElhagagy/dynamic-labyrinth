#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Rollback Script
# =============================================================================
# Quick rollback to previous deployment state.
#
# Usage: ./rollback.sh [--to VERSION] [--containers-only]
# =============================================================================

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
BACKUP_DIR="${PROJECT_ROOT}/backups"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Parse arguments
TARGET_VERSION=""
CONTAINERS_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --to)
            TARGET_VERSION="$2"
            shift 2
            ;;
        --containers-only)
            CONTAINERS_ONLY=true
            shift
            ;;
        --help)
            cat << EOF
Dynamic Labyrinth - Rollback Script

Usage: ./rollback.sh [OPTIONS]

Options:
    --to VERSION        Rollback to specific version/tag
    --containers-only   Only restart containers, don't rollback images
    --help              Show this help message

Examples:
    ./rollback.sh                       # Restart all containers
    ./rollback.sh --to v1.0.0           # Rollback to specific version
    ./rollback.sh --containers-only     # Just restart containers

EOF
            exit 0
            ;;
        *)
            log_error "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# Stop current deployment
stop_current() {
    log_info "Stopping current deployment..."
    cd "$PROJECT_ROOT"
    docker-compose down --remove-orphans 2>/dev/null || true
}

# Rollback to previous images
rollback_images() {
    if [[ "$CONTAINERS_ONLY" == "true" ]]; then
        log_info "Skipping image rollback (--containers-only)"
        return
    fi
    
    if [[ -n "$TARGET_VERSION" ]]; then
        log_info "Rolling back to version: $TARGET_VERSION"
        
        # Pull specific version tags
        docker pull "honeytrap-level1:${TARGET_VERSION}" 2>/dev/null || log_warn "Could not pull honeytrap-level1:${TARGET_VERSION}"
        docker pull "honeytrap-level2:${TARGET_VERSION}" 2>/dev/null || log_warn "Could not pull honeytrap-level2:${TARGET_VERSION}"
        docker pull "honeytrap-level3:${TARGET_VERSION}" 2>/dev/null || log_warn "Could not pull honeytrap-level3:${TARGET_VERSION}"
        docker pull "orchestrator:${TARGET_VERSION}" 2>/dev/null || log_warn "Could not pull orchestrator:${TARGET_VERSION}"
        
        # Retag as latest
        docker tag "honeytrap-level1:${TARGET_VERSION}" "honeytrap-level1:latest" 2>/dev/null || true
        docker tag "honeytrap-level2:${TARGET_VERSION}" "honeytrap-level2:latest" 2>/dev/null || true
        docker tag "honeytrap-level3:${TARGET_VERSION}" "honeytrap-level3:latest" 2>/dev/null || true
        docker tag "orchestrator:${TARGET_VERSION}" "orchestrator:latest" 2>/dev/null || true
    fi
}

# Restore database backup
restore_database() {
    if [[ "$CONTAINERS_ONLY" == "true" ]]; then
        return
    fi
    
    local latest_backup
    latest_backup=$(ls -t "${BACKUP_DIR}/orchestrator_"*.db 2>/dev/null | head -1)
    
    if [[ -n "$latest_backup" ]]; then
        log_info "Restoring database from: $latest_backup"
        cp "$latest_backup" "${PROJECT_ROOT}/orchestrator/data/orchestrator.db"
    else
        log_warn "No database backup found"
    fi
}

# Restart containers
restart_containers() {
    log_info "Starting containers..."
    cd "$PROJECT_ROOT"
    docker-compose up -d
}

# Validate rollback
validate_rollback() {
    log_info "Validating rollback..."
    
    # Wait for services
    sleep 10
    
    # Check orchestrator
    if curl -sf "http://localhost:8000/healthz" &>/dev/null; then
        log_info "Orchestrator is healthy"
    else
        log_error "Orchestrator is not responding"
        return 1
    fi
    
    # Check nginx
    if curl -sf "http://localhost/health" &>/dev/null; then
        log_info "Nginx is healthy"
    else
        log_error "Nginx is not responding"
        return 1
    fi
    
    log_info "Rollback validated successfully"
}

# Main execution
main() {
    log_info "=========================================="
    log_info "Dynamic Labyrinth - Rollback"
    log_info "=========================================="
    
    stop_current
    rollback_images
    restore_database
    restart_containers
    validate_rollback
    
    log_info "Rollback complete!"
}

main
