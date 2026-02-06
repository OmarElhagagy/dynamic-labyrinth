#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Deployment Script
# =============================================================================
# Full deployment automation for Dynamic Labyrinth infrastructure.
# Handles building, deploying, and validating the complete system.
#
# Usage: ./deploy.sh [--env ENV] [--build] [--no-prewarm]
# =============================================================================

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
ENVIRONMENT="${ENVIRONMENT:-development}"
BUILD_IMAGES=false
SKIP_PREWARM=false
COMPOSE_FILES="-f docker-compose.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $(date '+%H:%M:%S') $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $(date '+%H:%M:%S') $1"; }

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --env|-e)
                ENVIRONMENT="$2"
                shift 2
                ;;
            --build|-b)
                BUILD_IMAGES=true
                shift
                ;;
            --no-prewarm)
                SKIP_PREWARM=true
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
    
    # Set compose files based on environment
    case $ENVIRONMENT in
        development|dev)
            COMPOSE_FILES="-f docker-compose.yml -f docker-compose.override.yml"
            ;;
        production|prod)
            COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"
            ;;
        *)
            COMPOSE_FILES="-f docker-compose.yml"
            ;;
    esac
}

show_help() {
    cat << EOF
Dynamic Labyrinth - Deployment Script

Usage: ./deploy.sh [OPTIONS]

Options:
    --env, -e ENV       Set environment (development, production)
    --build, -b         Build Docker images before deploying
    --no-prewarm        Skip container pre-warming
    --help              Show this help message

Environments:
    development         Uses docker-compose.override.yml (fewer containers)
    production          Uses docker-compose.prod.yml (resource limits)

Examples:
    ./deploy.sh                         # Deploy development
    ./deploy.sh --env production        # Deploy production
    ./deploy.sh --build --env prod      # Build and deploy production

EOF
}

# Pre-flight checks
preflight_checks() {
    log_step "Running pre-flight checks..."
    
    # Check Docker
    if ! docker info &>/dev/null; then
        log_error "Docker is not running"
        exit 1
    fi
    log_info "Docker is running"
    
    # Check Docker Compose
    if ! docker-compose version &>/dev/null; then
        log_error "Docker Compose is not installed"
        exit 1
    fi
    log_info "Docker Compose is available"
    
    # Check .env file
    if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
        log_warn ".env file not found, copying from .env.example"
        if [[ -f "${PROJECT_ROOT}/.env.example" ]]; then
            cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
        else
            log_error ".env.example not found"
            exit 1
        fi
    fi
    
    # Validate HMAC secret
    local hmac_secret
    hmac_secret=$(grep "^HMAC_SECRET=" "${PROJECT_ROOT}/.env" | cut -d'=' -f2)
    if [[ "$hmac_secret" == "change-me-in-production"* && "$ENVIRONMENT" == "prod"* ]]; then
        log_error "HMAC_SECRET must be changed for production!"
        log_info "Generate with: openssl rand -hex 32"
        exit 1
    fi
    
    log_info "Pre-flight checks passed"
}

# Stop existing containers
stop_existing() {
    log_step "Stopping existing containers..."
    
    cd "$PROJECT_ROOT"
    docker-compose $COMPOSE_FILES down --remove-orphans 2>/dev/null || true
    
    log_info "Existing containers stopped"
}

# Build images
build_images() {
    if [[ "$BUILD_IMAGES" != "true" ]]; then
        return
    fi
    
    log_step "Building Docker images..."
    
    cd "$PROJECT_ROOT"
    
    # Build honeytrap images
    log_info "Building honeytrap-level1..."
    docker build -f docker/honeytrap-level1.Dockerfile -t honeytrap-level1:latest .
    
    log_info "Building honeytrap-level2..."
    docker build -f docker/honeytrap-level2.Dockerfile -t honeytrap-level2:latest .
    
    log_info "Building honeytrap-level3..."
    docker build -f docker/honeytrap-level3.Dockerfile -t honeytrap-level3:latest .
    
    # Build orchestrator
    log_info "Building orchestrator..."
    docker build -f orchestrator/Dockerfile -t orchestrator:latest ./orchestrator
    
    # Build nginx
    log_info "Building nginx..."
    docker build -f docker/nginx/Dockerfile -t nginx-labyrinth:latest .
    
    log_info "All images built successfully"
}

# Deploy containers
deploy_containers() {
    log_step "Deploying containers..."
    
    cd "$PROJECT_ROOT"
    
    # Pull or use local images
    if [[ "$BUILD_IMAGES" != "true" ]]; then
        log_info "Using existing images (use --build to rebuild)"
    fi
    
    # Start all services
    docker-compose $COMPOSE_FILES up -d
    
    log_info "Containers deployed"
}

# Wait for services
wait_for_services() {
    log_step "Waiting for services to be ready..."
    
    local max_wait=120
    local waited=0
    
    # Wait for orchestrator
    log_info "Waiting for orchestrator..."
    while [[ $waited -lt $max_wait ]]; do
        if curl -sf "http://localhost:8000/healthz" &>/dev/null; then
            log_info "Orchestrator is ready"
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done
    
    if [[ $waited -ge $max_wait ]]; then
        log_error "Orchestrator did not become ready"
        docker-compose $COMPOSE_FILES logs orchestrator | tail -20
        exit 1
    fi
    
    # Wait for nginx
    log_info "Waiting for nginx..."
    waited=0
    while [[ $waited -lt $max_wait ]]; do
        if curl -sf "http://localhost/health" &>/dev/null; then
            log_info "Nginx is ready"
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done
    
    if [[ $waited -ge $max_wait ]]; then
        log_error "Nginx did not become ready"
        docker-compose $COMPOSE_FILES logs nginx | tail -20
        exit 1
    fi
}

# Validate deployment
validate_deployment() {
    log_step "Validating deployment..."
    
    # Run health check
    if "${SCRIPT_DIR}/healthcheck.sh" --json > /tmp/healthcheck.json 2>/dev/null; then
        log_info "All containers are healthy"
    else
        log_warn "Some containers may not be healthy"
        cat /tmp/healthcheck.json
    fi
    
    # Check pool status
    log_info "Pool status:"
    curl -sf "http://localhost:8000/pools" | python3 -m json.tool 2>/dev/null || echo "  Could not fetch pool status"
}

# Print summary
print_summary() {
    echo ""
    log_step "=========================================="
    log_step "Deployment Complete!"
    log_step "=========================================="
    echo ""
    echo "Environment: $ENVIRONMENT"
    echo ""
    echo "Services:"
    echo "  - Nginx (HTTP):      http://localhost:80"
    echo "  - Nginx (Internal):  http://localhost:8080"
    echo "  - Orchestrator:      http://localhost:8000"
    echo "  - Dashboard:         http://localhost:3000"
    echo ""
    echo "Honeytrap Ports:"
    echo "  - SSH:    2222"
    echo "  - Telnet: 2323"
    echo "  - FTP:    2121"
    echo "  - SMTP:   2525"
    echo ""
    echo "Useful commands:"
    echo "  docker-compose $COMPOSE_FILES logs -f         # View logs"
    echo "  docker-compose $COMPOSE_FILES ps              # List containers"
    echo "  ${SCRIPT_DIR}/healthcheck.sh                  # Check health"
    echo ""
}

# Main execution
main() {
    parse_args "$@"
    
    echo ""
    log_step "=========================================="
    log_step "Dynamic Labyrinth - Deployment"
    log_step "=========================================="
    log_info "Environment: $ENVIRONMENT"
    log_info "Build: $BUILD_IMAGES"
    echo ""
    
    preflight_checks
    stop_existing
    build_images
    deploy_containers
    wait_for_services
    validate_deployment
    print_summary
}

main "$@"
