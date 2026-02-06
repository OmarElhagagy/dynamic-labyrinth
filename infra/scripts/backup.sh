#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Backup Script
# =============================================================================
# Creates backups of configuration, database, and logs.
#
# Usage: ./backup.sh [--output DIR] [--type TYPE]
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
OUTPUT_DIR="${PROJECT_ROOT}/backups"
BACKUP_TYPE="full"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --output|-o)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --type|-t)
            BACKUP_TYPE="$2"
            shift 2
            ;;
        --help)
            cat << EOF
Dynamic Labyrinth - Backup Script

Usage: ./backup.sh [OPTIONS]

Options:
    --output, -o DIR    Output directory (default: ./backups)
    --type, -t TYPE     Backup type: full, config, database, logs
    --help              Show this help

Examples:
    ./backup.sh                             # Full backup
    ./backup.sh --type config               # Config only
    ./backup.sh --output /mnt/backups       # Custom output dir

EOF
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# Create backup directory
mkdir -p "$OUTPUT_DIR"

backup_config() {
    log_info "Backing up configuration..."
    
    local backup_file="${OUTPUT_DIR}/config_${TIMESTAMP}.tar.gz"
    
    tar -czf "$backup_file" \
        -C "$PROJECT_ROOT" \
        .env \
        docker-compose.yml \
        docker-compose.override.yml \
        docker-compose.prod.yml \
        orchestrator/pools.yaml \
        docker/configs/ \
        docker/nginx/ \
        2>/dev/null || true
    
    log_info "Configuration backed up to: $backup_file"
}

backup_database() {
    log_info "Backing up database..."
    
    local db_file="${PROJECT_ROOT}/orchestrator/data/orchestrator.db"
    local backup_file="${OUTPUT_DIR}/orchestrator_${TIMESTAMP}.db"
    
    if [[ -f "$db_file" ]]; then
        # Use SQLite backup if database is in use
        sqlite3 "$db_file" ".backup '$backup_file'" 2>/dev/null || cp "$db_file" "$backup_file"
        log_info "Database backed up to: $backup_file"
    else
        log_warn "Database file not found, skipping"
    fi
}

backup_logs() {
    log_info "Backing up logs..."
    
    local backup_file="${OUTPUT_DIR}/logs_${TIMESTAMP}.tar.gz"
    
    # Collect container logs
    mkdir -p "/tmp/labyrinth_logs_$$"
    
    for container in $(docker ps --format '{{.Names}}' | grep -E "honeytrap|orchestrator|nginx"); do
        docker logs "$container" > "/tmp/labyrinth_logs_$$/${container}.log" 2>&1 || true
    done
    
    tar -czf "$backup_file" -C "/tmp/labyrinth_logs_$$" . 2>/dev/null || true
    rm -rf "/tmp/labyrinth_logs_$$"
    
    log_info "Logs backed up to: $backup_file"
}

backup_nginx_maps() {
    log_info "Backing up nginx session maps..."
    
    local map_file="${PROJECT_ROOT}/docker/nginx/conf.d/session_map.conf"
    local backup_file="${OUTPUT_DIR}/session_map_${TIMESTAMP}.conf"
    
    if [[ -f "$map_file" ]]; then
        cp "$map_file" "$backup_file"
        log_info "Nginx maps backed up to: $backup_file"
    else
        log_warn "Nginx map file not found, skipping"
    fi
}

# Main
main() {
    log_info "=========================================="
    log_info "Dynamic Labyrinth - Backup"
    log_info "=========================================="
    log_info "Type: $BACKUP_TYPE"
    log_info "Output: $OUTPUT_DIR"
    echo ""
    
    case $BACKUP_TYPE in
        full)
            backup_config
            backup_database
            backup_logs
            backup_nginx_maps
            ;;
        config)
            backup_config
            ;;
        database|db)
            backup_database
            ;;
        logs)
            backup_logs
            ;;
        *)
            echo "Unknown backup type: $BACKUP_TYPE"
            exit 1
            ;;
    esac
    
    echo ""
    log_info "Backup complete!"
    log_info "Files in $OUTPUT_DIR:"
    ls -lh "$OUTPUT_DIR"/*_${TIMESTAMP}* 2>/dev/null || true
}

main
