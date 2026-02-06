# 📋 Dynamic Labyrinth Operations Runbook

> This runbook provides operational procedures for the Dynamic Labyrinth honeypot orchestration system.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Daily Operations](#daily-operations)
3. [Deployment Procedures](#deployment-procedures)
4. [Incident Response](#incident-response)
5. [Troubleshooting](#troubleshooting)
6. [Maintenance](#maintenance)
7. [Disaster Recovery](#disaster-recovery)
8. [Contacts](#contacts)

---

## System Overview

### Architecture Summary

```
Internet → Nginx (Reverse Proxy) → Honeytrap Containers (L1/L2/L3)
                                          ↓
                                   Orchestrator (FastAPI)
                                          ↓
                              Cerebrum (ML) + Dashboard
```

### Critical Services

| Service | Port | Health Endpoint | Restart Command |
|---------|------|-----------------|-----------------|
| Nginx | 80, 443 | `curl -s http://localhost/health` | `docker-compose restart nginx` |
| Orchestrator | 8080 | `curl -s http://localhost:8080/healthz` | `docker-compose restart orchestrator` |
| Cerebrum | 8000 | `curl -s http://localhost:8000/health` | `docker-compose restart cerebrum` |
| Dashboard | 3000 | `curl -s http://localhost:3000/api/health` | `docker-compose restart dashboard` |

### Container Pool Sizes

| Level | Min | Target | Max | Purpose |
|-------|-----|--------|-----|---------|
| L1 | 3 | 5 | 10 | Initial detection |
| L2 | 2 | 3 | 6 | Extended analysis |
| L3 | 1 | 1 | 2 | Full engagement |

---

## Daily Operations

### Morning Health Check (09:00)

```bash
#!/bin/bash
# Run daily health check

echo "=== Dynamic Labyrinth Daily Health Check ==="
echo "Date: $(date)"
echo ""

# 1. Check all services
echo "1. Service Status:"
docker-compose ps

# 2. Check pool status
echo ""
echo "2. Pool Status:"
python infra/scripts/pool_status.py

# 3. Check disk usage
echo ""
echo "3. Disk Usage:"
df -h /var/lib/docker

# 4. Check recent errors
echo ""
echo "4. Recent Errors (last hour):"
docker-compose logs --since 1h 2>&1 | grep -i error | tail -20

# 5. Check session count
echo ""
echo "5. Active Sessions:"
curl -s http://localhost:8080/pools | jq '.pools[] | .assigned' | awk '{sum+=$1} END {print "Total: " sum}'
```

### Metrics to Monitor

| Metric | Warning Threshold | Critical Threshold |
|--------|-------------------|-------------------|
| Pool availability (L1) | < 2 available | < 1 available |
| Pool availability (L2) | < 1 available | 0 available |
| Escalation latency | > 500ms | > 2000ms |
| Container health | < 80% healthy | < 50% healthy |
| Disk usage | > 70% | > 85% |
| Memory usage | > 80% | > 90% |

---

## Deployment Procedures

### Standard Deployment

**Pre-deployment Checklist:**
- [ ] All CI checks passing
- [ ] Changelog reviewed
- [ ] Backup completed
- [ ] Team notified in #ops channel

**Deployment Steps:**

```bash
# 1. Create backup
./infra/scripts/backup.sh

# 2. Pull latest code
git pull origin main

# 3. Deploy with zero downtime
./infra/scripts/deploy.sh

# 4. Verify deployment
curl -s http://localhost:8080/healthz | jq .

# 5. Check pool status
python infra/scripts/pool_status.py

# 6. Monitor logs for 5 minutes
docker-compose logs -f --tail=100
```

### Rollback Procedure

**When to rollback:**
- Health check fails after deployment
- Error rate exceeds 5%
- Pool containers failing to start
- Critical functionality broken

**Rollback Steps:**

```bash
# 1. Execute rollback
./infra/scripts/rollback.sh

# 2. Verify services restored
curl -s http://localhost:8080/healthz

# 3. Check pool status
python infra/scripts/pool_status.py

# 4. Notify team
echo "Rollback completed at $(date)" | slack-notify #ops
```

### Emergency Deployment

For critical security patches:

```bash
# Skip pre-warm, deploy immediately
SKIP_PREWARM=1 ./infra/scripts/deploy.sh --force

# Monitor closely
watch -n 5 'curl -s http://localhost:8080/pools | jq .'
```

---

## Incident Response

### Severity Levels

| Level | Description | Response Time | Examples |
|-------|-------------|---------------|----------|
| **SEV1** | Complete outage | 15 min | All honeypots down, no data collection |
| **SEV2** | Major degradation | 30 min | One pool exhausted, escalation failing |
| **SEV3** | Minor issue | 2 hours | Single container unhealthy, slow response |
| **SEV4** | Low priority | 24 hours | Log verbosity, cosmetic issues |

### SEV1: Complete Outage

```bash
# 1. Assess the situation
docker-compose ps
docker-compose logs --tail=50

# 2. Check infrastructure
ping -c 3 google.com  # Network
df -h                  # Disk
free -m                # Memory

# 3. Attempt restart
docker-compose down
docker-compose up -d

# 4. If restart fails, restore from backup
./infra/scripts/rollback.sh

# 5. Escalate if unresolved after 15 minutes
```

### SEV2: Pool Exhaustion

```bash
# 1. Identify exhausted pool
curl -s http://localhost:8080/pools | jq .

# 2. Check for stuck sessions
docker ps --filter "name=honeytrap" --format "{{.Names}} {{.Status}}"

# 3. Force cleanup of stale containers
./infra/scripts/lifecycle.sh cleanup --force

# 4. Scale up if needed
curl -X POST http://localhost:8080/pools/level2/scale \
  -H "Content-Type: application/json" \
  -d '{"target_size": 5}'

# 5. Monitor recovery
watch -n 10 'curl -s http://localhost:8080/pools | jq .'
```

### SEV2: Escalation Failures

```bash
# 1. Check Cerebrum service
curl -s http://cerebrum:8000/health

# 2. Check orchestrator logs
docker-compose logs orchestrator --tail=100 | grep -i error

# 3. Verify HMAC authentication
# Check timestamp sync
date && docker exec orchestrator date

# 4. Restart affected services
docker-compose restart cerebrum orchestrator

# 5. Test escalation manually
curl -X POST http://localhost:8080/escalate \
  -H "Content-Type: application/json" \
  -H "X-Signature: test" \
  -H "X-Timestamp: $(date +%s)" \
  -d '{"session_id":"test","source_ip":"1.2.3.4","current_level":1}'
```

---

## Troubleshooting

### Common Issues

#### Container Won't Start

```bash
# Check Docker daemon
systemctl status docker

# Check image exists
docker images | grep honeytrap

# Check container logs
docker logs <container_id>

# Check resource limits
docker stats --no-stream

# Rebuild if image corrupted
docker-compose build --no-cache honeytrap-level1
```

#### Nginx 502 Bad Gateway

```bash
# 1. Check upstream containers
docker ps --filter "name=honeytrap"

# 2. Verify nginx can reach containers
docker exec nginx curl -s http://honeytrap-l1-001:8080/health

# 3. Check nginx configuration
docker exec nginx nginx -t

# 4. Reload nginx
docker exec nginx nginx -s reload

# 5. Check session map
docker exec nginx cat /etc/nginx/maps/session_map.conf
```

#### Database Connection Issues

```bash
# Check database file
ls -la orchestrator/data/orchestrator.db

# Check permissions
chmod 644 orchestrator/data/orchestrator.db

# Test connection
docker exec orchestrator python -c "
from database import engine
print(engine.url)
"

# Reset database (CAUTION: loses data)
rm orchestrator/data/orchestrator.db
docker-compose restart orchestrator
```

#### High Memory Usage

```bash
# Check container memory
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}"

# Identify memory hogs
docker ps -q | xargs docker inspect --format '{{.Name}} {{.HostConfig.Memory}}' | sort -k2 -n

# Restart memory-heavy containers
docker-compose restart honeytrap-l3

# Force garbage collection
docker system prune -f
```

#### Session Routing Failures

```bash
# 1. Check cookie is being set
curl -v http://localhost/ 2>&1 | grep -i set-cookie

# 2. Verify session map
cat /etc/nginx/maps/session_map.conf

# 3. Check orchestrator is updating map
docker-compose logs orchestrator | grep nginx

# 4. Manually regenerate map
curl -X POST http://localhost:8080/internal/regenerate-map

# 5. Reload nginx
docker exec nginx nginx -s reload
```

### Log Analysis

```bash
# Orchestrator errors
docker-compose logs orchestrator 2>&1 | grep -E "(ERROR|CRITICAL)" | tail -50

# Escalation events
docker-compose logs orchestrator 2>&1 | grep "escalate" | tail -20

# Container lifecycle events
docker events --filter 'type=container' --since 1h

# Nginx access patterns
docker exec nginx tail -100 /var/log/nginx/access.log | awk '{print $1}' | sort | uniq -c | sort -rn
```

---

## Maintenance

### Weekly Maintenance (Sundays 02:00)

```bash
#!/bin/bash
# Weekly maintenance script

echo "=== Weekly Maintenance Started ==="

# 1. Create backup
./infra/scripts/backup.sh

# 2. Rotate logs
docker-compose logs --no-log-prefix > /var/log/labyrinth/weekly-$(date +%Y%m%d).log
docker-compose down
rm -rf /var/lib/docker/containers/*/*.log
docker-compose up -d

# 3. Clean unused images
docker image prune -a --filter "until=168h" -f

# 4. Clean unused volumes
docker volume prune -f

# 5. Rebuild containers (optional, for security patches)
docker-compose build --pull --no-cache
docker-compose up -d

# 6. Pre-warm pools
./infra/scripts/prewarm.sh

# 7. Verify health
sleep 30
./infra/scripts/healthcheck.sh

echo "=== Weekly Maintenance Complete ==="
```

### Monthly Maintenance

- [ ] Review and rotate secrets
- [ ] Update base images
- [ ] Review access logs for anomalies
- [ ] Test disaster recovery procedures
- [ ] Update documentation
- [ ] Performance baseline comparison

### Certificate Renewal

```bash
# Check certificate expiry
openssl x509 -in /etc/ssl/certs/labyrinth.crt -noout -enddate

# Renew with certbot (if using Let's Encrypt)
certbot renew --dry-run
certbot renew

# Reload nginx
docker exec nginx nginx -s reload
```

---

## Disaster Recovery

### Backup Locations

| Type | Location | Retention | Frequency |
|------|----------|-----------|-----------|
| Database | `./backups/db/` | 30 days | Daily |
| Config | `./backups/config/` | 90 days | Weekly |
| Docker volumes | `./backups/volumes/` | 14 days | Daily |
| Full system | Off-site S3 | 1 year | Weekly |

### Recovery Procedures

#### Recover from Backup

```bash
# 1. Stop all services
docker-compose down

# 2. List available backups
ls -la ./backups/

# 3. Restore latest backup
BACKUP_DIR="./backups/2026-02-06_02-00-00"

# Restore database
cp ${BACKUP_DIR}/orchestrator.db orchestrator/data/

# Restore configs
cp ${BACKUP_DIR}/pools.yaml orchestrator/
cp -r ${BACKUP_DIR}/nginx/ docker/nginx/

# Restore volumes (if needed)
docker volume rm labyrinth_data
docker run --rm -v labyrinth_data:/data -v ${BACKUP_DIR}/volumes:/backup alpine \
  tar xzf /backup/data.tar.gz -C /data

# 4. Start services
docker-compose up -d

# 5. Pre-warm pools
./infra/scripts/prewarm.sh

# 6. Verify recovery
./infra/scripts/healthcheck.sh
```

#### Full System Recovery

```bash
# 1. Provision new server
# 2. Install Docker and docker-compose
# 3. Clone repository
git clone https://github.com/YOUR_ORG/dynamic-labyrinth.git
cd dynamic-labyrinth

# 4. Download backup from S3
aws s3 cp s3://labyrinth-backups/weekly/latest.tar.gz .
tar xzf latest.tar.gz

# 5. Restore configuration
cp backup/.env .
cp backup/pools.yaml orchestrator/

# 6. Start services
docker-compose up -d

# 7. Restore data
./infra/scripts/restore.sh backup/

# 8. Update DNS/Load balancer
```

---

## Contacts

### On-Call Rotation

| Week | Primary | Secondary |
|------|---------|-----------|
| 1 | Omar | Ahmed |
| 2 | Ahmed | Yara |
| 3 | Yara | Salma |
| 4 | Salma | Omar |

### Escalation Path

1. **Primary On-Call** (15 min response)
   - Slack: @oncall-primary
   - Phone: Listed in PagerDuty

2. **Secondary On-Call** (30 min response)
   - Slack: @oncall-secondary
   - Phone: Listed in PagerDuty

3. **Team Lead** (1 hour response)
   - Slack: @team-lead
   - Email: lead@example.com

### External Contacts

| Service | Contact | Purpose |
|---------|---------|---------|
| Cloud Provider | support@cloud.example.com | Infrastructure issues |
| Security Team | security@example.com | Threat escalation |
| Compliance | compliance@example.com | Data handling questions |

---

## Appendix

### Useful Commands

```bash
# Quick status check
alias dlstatus='docker-compose ps && echo "---" && curl -s localhost:8080/healthz | jq .'

# Follow all logs
alias dllogs='docker-compose logs -f --tail=50'

# Pool overview
alias dlpools='curl -s localhost:8080/pools | jq .'

# Container count by level
alias dlcount='docker ps --filter "name=honeytrap" --format "{{.Names}}" | cut -d- -f2 | sort | uniq -c'
```

### Monitoring Dashboards

- **Grafana**: http://localhost:3000/d/labyrinth
- **Prometheus**: http://localhost:9090
- **Alertmanager**: http://localhost:9093

### Related Documentation

- [README.md](../README.md) - Project overview
- [API Specification](api/openapi.yaml) - Full API docs
- [Architecture Guide](architecture.md) - System design
- [Security Guide](security.md) - Security procedures
