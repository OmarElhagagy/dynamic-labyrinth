#!/bin/bash
# =============================================================================
# Dynamic Labyrinth - Monitoring Setup Script
# =============================================================================
# Sets up monitoring dashboards and alerting for the labyrinth infrastructure.
#
# Usage: ./monitoring.sh setup
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Colors
GREEN='\033[0;32m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }

# Create Prometheus configuration
setup_prometheus() {
    log_info "Setting up Prometheus configuration..."
    
    mkdir -p "${PROJECT_ROOT}/monitoring/prometheus"
    
    cat > "${PROJECT_ROOT}/monitoring/prometheus/prometheus.yml" << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets: []

rule_files: []

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'orchestrator'
    static_configs:
      - targets: ['orchestrator:8000']
    metrics_path: '/metrics'

  - job_name: 'nginx'
    static_configs:
      - targets: ['nginx:9113']
    metrics_path: '/metrics'

  - job_name: 'honeytrap'
    static_configs:
      - targets:
          - 'honeytrap-level1-1:8080'
          - 'honeytrap-level1-2:8080'
          - 'honeytrap-level1-3:8080'
          - 'honeytrap-level1-4:8080'
          - 'honeytrap-level1-5:8080'
          - 'honeytrap-level2-1:8080'
          - 'honeytrap-level2-2:8080'
          - 'honeytrap-level2-3:8080'
          - 'honeytrap-level3-1:8080'
    metrics_path: '/metrics'
    relabel_configs:
      - source_labels: [__address__]
        regex: 'honeytrap-level(\d+)-(\d+):.*'
        target_label: 'level'
        replacement: '$1'
      - source_labels: [__address__]
        regex: 'honeytrap-level(\d+)-(\d+):.*'
        target_label: 'instance_num'
        replacement: '$2'
EOF
    
    log_info "Prometheus configuration created"
}

# Create Grafana dashboards
setup_grafana() {
    log_info "Setting up Grafana dashboards..."
    
    mkdir -p "${PROJECT_ROOT}/monitoring/grafana/dashboards"
    mkdir -p "${PROJECT_ROOT}/monitoring/grafana/provisioning/dashboards"
    mkdir -p "${PROJECT_ROOT}/monitoring/grafana/provisioning/datasources"
    
    # Datasource configuration
    cat > "${PROJECT_ROOT}/monitoring/grafana/provisioning/datasources/prometheus.yml" << 'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
EOF
    
    # Dashboard provisioning
    cat > "${PROJECT_ROOT}/monitoring/grafana/provisioning/dashboards/dashboards.yml" << 'EOF'
apiVersion: 1

providers:
  - name: 'Dynamic Labyrinth'
    orgId: 1
    folder: 'Dynamic Labyrinth'
    folderUid: 'dynamic-labyrinth'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
EOF
    
    # Main overview dashboard
    cat > "${PROJECT_ROOT}/monitoring/grafana/dashboards/overview.json" << 'EOF'
{
  "annotations": {
    "list": []
  },
  "editable": true,
  "fiscalYearStartMonth": 0,
  "graphTooltip": 0,
  "id": null,
  "links": [],
  "liveNow": false,
  "panels": [
    {
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "fieldConfig": {
        "defaults": {
          "color": {
            "mode": "palette-classic"
          },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {"color": "green", "value": null},
              {"color": "red", "value": 80}
            ]
          }
        },
        "overrides": []
      },
      "gridPos": {"h": 8, "w": 6, "x": 0, "y": 0},
      "id": 1,
      "options": {
        "orientation": "auto",
        "reduceOptions": {
          "calcs": ["lastNotNull"],
          "fields": "",
          "values": false
        },
        "showThresholdLabels": false,
        "showThresholdMarkers": true
      },
      "pluginVersion": "9.0.0",
      "targets": [
        {
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "labyrinth_pool_available_total",
          "legendFormat": "{{level}}",
          "refId": "A"
        }
      ],
      "title": "Available Containers",
      "type": "gauge"
    },
    {
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "fieldConfig": {
        "defaults": {
          "color": {"mode": "palette-classic"},
          "custom": {
            "axisCenteredZero": false,
            "axisColorMode": "text",
            "axisLabel": "",
            "axisPlacement": "auto",
            "barAlignment": 0,
            "drawStyle": "line",
            "fillOpacity": 10,
            "gradientMode": "none",
            "hideFrom": {"legend": false, "tooltip": false, "viz": false},
            "lineInterpolation": "linear",
            "lineWidth": 1,
            "pointSize": 5,
            "scaleDistribution": {"type": "linear"},
            "showPoints": "auto",
            "spanNulls": false,
            "stacking": {"group": "A", "mode": "none"},
            "thresholdsStyle": {"mode": "off"}
          },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [{"color": "green", "value": null}]
          }
        },
        "overrides": []
      },
      "gridPos": {"h": 8, "w": 12, "x": 6, "y": 0},
      "id": 2,
      "options": {
        "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": true},
        "tooltip": {"mode": "single", "sort": "none"}
      },
      "targets": [
        {
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "rate(labyrinth_sessions_total[5m])",
          "legendFormat": "Sessions/min",
          "refId": "A"
        }
      ],
      "title": "Session Rate",
      "type": "timeseries"
    },
    {
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "fieldConfig": {
        "defaults": {
          "color": {"mode": "thresholds"},
          "mappings": [
            {"options": {"0": {"color": "red", "index": 0, "text": "Down"}}, "type": "value"},
            {"options": {"1": {"color": "green", "index": 1, "text": "Up"}}, "type": "value"}
          ],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {"color": "red", "value": null},
              {"color": "green", "value": 1}
            ]
          }
        },
        "overrides": []
      },
      "gridPos": {"h": 8, "w": 6, "x": 18, "y": 0},
      "id": 3,
      "options": {
        "colorMode": "value",
        "graphMode": "none",
        "justifyMode": "auto",
        "orientation": "auto",
        "reduceOptions": {
          "calcs": ["lastNotNull"],
          "fields": "",
          "values": false
        },
        "textMode": "auto"
      },
      "targets": [
        {
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "up{job=\"honeytrap\"}",
          "legendFormat": "{{instance}}",
          "refId": "A"
        }
      ],
      "title": "Container Status",
      "type": "stat"
    }
  ],
  "refresh": "10s",
  "schemaVersion": 38,
  "style": "dark",
  "tags": ["dynamic-labyrinth"],
  "templating": {"list": []},
  "time": {"from": "now-1h", "to": "now"},
  "timepicker": {},
  "timezone": "",
  "title": "Dynamic Labyrinth Overview",
  "uid": "labyrinth-overview",
  "version": 1,
  "weekStart": ""
}
EOF
    
    log_info "Grafana dashboards created"
}

# Create docker-compose monitoring extension
setup_compose_extension() {
    log_info "Creating monitoring docker-compose extension..."
    
    cat > "${PROJECT_ROOT}/docker-compose.monitoring.yml" << 'EOF'
# Dynamic Labyrinth - Monitoring Stack
# Use with: docker-compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d

version: "3.8"

services:
  prometheus:
    image: prom/prometheus:v2.45.0
    container_name: prometheus
    restart: unless-stopped
    volumes:
      - ./monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/usr/share/prometheus/console_libraries'
      - '--web.console.templates=/usr/share/prometheus/consoles'
      - '--web.enable-lifecycle'
    ports:
      - "9090:9090"
    networks:
      - management

  grafana:
    image: grafana/grafana:10.0.0
    container_name: grafana
    restart: unless-stopped
    environment:
      - GF_SECURITY_ADMIN_USER=${GRAFANA_ADMIN_USER:-admin}
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro
    ports:
      - "3001:3000"
    depends_on:
      - prometheus
    networks:
      - management

  nginx-exporter:
    image: nginx/nginx-prometheus-exporter:0.11.0
    container_name: nginx-exporter
    restart: unless-stopped
    command:
      - '-nginx.scrape-uri=http://nginx:8080/nginx_status'
    depends_on:
      - nginx
    networks:
      - management

volumes:
  prometheus_data:
  grafana_data:
EOF
    
    log_info "Monitoring compose file created"
}

# Main
main() {
    local command="${1:-setup}"
    
    log_info "=========================================="
    log_info "Dynamic Labyrinth - Monitoring Setup"
    log_info "=========================================="
    
    case $command in
        setup)
            setup_prometheus
            setup_grafana
            setup_compose_extension
            ;;
        *)
            echo "Unknown command: $command"
            echo "Usage: ./monitoring.sh setup"
            exit 1
            ;;
    esac
    
    echo ""
    log_info "Monitoring setup complete!"
    log_info ""
    log_info "To enable monitoring, run:"
    log_info "  docker-compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d"
    log_info ""
    log_info "Access:"
    log_info "  Prometheus: http://localhost:9090"
    log_info "  Grafana:    http://localhost:3001 (admin/admin)"
}

main "$@"
