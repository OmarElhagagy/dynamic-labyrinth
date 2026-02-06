#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Nginx Reload Script
# =============================================================================
# Safely reloads nginx configuration with health checks.
# Usage: ./reload_nginx.sh [--force]
#
# Exit codes:
#   0 - Success
#   1 - Health check failed
#   2 - Config validation failed
#   3 - Reload failed

set -euo pipefail

# Configuration
NGINX_HEALTH_URL="${NGINX_HEALTH_URL:-http://localhost:80/health}"
NGINX_CONFIG_PATH="${NGINX_CONFIG_PATH:-/etc/nginx/nginx.conf}"
NGINX_MAP_PATH="${NGINX_MAP_PATH:-/etc/nginx/maps/honeytrap_upstream.map}"
MAX_RETRIES="${MAX_RETRIES:-3}"
RETRY_DELAY="${RETRY_DELAY:-2}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if map file exists
check_map_file() {
    if [[ ! -f "$NGINX_MAP_PATH" ]]; then
        log_error "Nginx map file not found: $NGINX_MAP_PATH"
        return 1
    fi
    log_info "Map file exists: $NGINX_MAP_PATH"
    return 0
}

# Health check nginx
health_check() {
    local retries=0
    
    while [[ $retries -lt $MAX_RETRIES ]]; do
        if curl -sf -o /dev/null "$NGINX_HEALTH_URL"; then
            log_info "Nginx health check passed"
            return 0
        fi
        
        retries=$((retries + 1))
        log_warn "Health check failed, retry $retries/$MAX_RETRIES"
        sleep "$RETRY_DELAY"
    done
    
    log_error "Nginx health check failed after $MAX_RETRIES retries"
    return 1
}

# Validate nginx configuration
validate_config() {
    log_info "Validating nginx configuration..."
    
    if nginx -t 2>&1; then
        log_info "Nginx configuration is valid"
        return 0
    else
        log_error "Nginx configuration validation failed"
        return 2
    fi
}

# Reload nginx
reload_nginx() {
    log_info "Reloading nginx..."
    
    if nginx -s reload 2>&1; then
        log_info "Nginx reloaded successfully"
        return 0
    else
        log_error "Nginx reload failed"
        return 3
    fi
}

# Post-reload verification
verify_reload() {
    log_info "Verifying nginx after reload..."
    sleep 1
    
    if health_check; then
        log_info "Post-reload verification passed"
        return 0
    else
        log_error "Post-reload verification failed"
        return 1
    fi
}

# Main execution
main() {
    local force=false
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --force)
                force=true
                shift
                ;;
            *)
                log_error "Unknown argument: $1"
                exit 1
                ;;
        esac
    done
    
    log_info "Starting nginx reload process"
    
    # Check map file
    if ! check_map_file; then
        if [[ "$force" == true ]]; then
            log_warn "Continuing despite missing map file (--force)"
        else
            exit 1
        fi
    fi
    
    # Pre-reload health check (skip if force)
    if [[ "$force" != true ]]; then
        if ! health_check; then
            log_warn "Pre-reload health check failed"
            log_warn "Use --force to skip health checks"
            exit 1
        fi
    fi
    
    # Validate configuration
    if ! validate_config; then
        exit 2
    fi
    
    # Reload nginx
    if ! reload_nginx; then
        exit 3
    fi
    
    # Post-reload verification
    if ! verify_reload; then
        log_error "Nginx may be in an unstable state"
        exit 1
    fi
    
    log_info "Nginx reload completed successfully"
    exit 0
}

main "$@"
