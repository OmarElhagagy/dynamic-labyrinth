# Changelog

All notable changes to Dynamic Labyrinth will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project structure and architecture
- Orchestrator service with FastAPI
  - Pool management for L1/L2/L3 containers
  - Session-to-container mapping
  - Nginx map file generation
  - HMAC authentication middleware
  - Prometheus metrics endpoint
- Docker infrastructure
  - Multi-stage Dockerfiles for honeytrap levels
  - Nginx reverse proxy with cookie-based routing
  - Docker Compose configurations (dev/prod)
- Container lifecycle management scripts
  - Health checks
  - Pre-warm scripts
  - Deploy/rollback automation
  - Backup utilities
- CI/CD pipelines
  - GitHub Actions workflows
  - Security scanning (Trivy, CodeQL, Bandit)
  - Multi-arch Docker builds
  - Automated deployment
- Testing infrastructure
  - pytest unit tests
  - Integration tests
  - Locust load tests
- Documentation
  - README with quick start guide
  - OpenAPI specification
  - Operations runbook
  - Architecture documentation

### Changed
- N/A

### Deprecated
- N/A

### Removed
- N/A

### Fixed
- N/A

### Security
- HMAC-SHA256 authentication for internal APIs
- Rate limiting on sensitive endpoints
- Container security hardening (no-new-privileges, read-only rootfs)
- Automated vulnerability scanning in CI

## [0.1.0] - 2026-02-06

### Added
- Initial release
- Core orchestration functionality
- Basic honeytrap integration
- Development environment setup

---

## Release Notes Format

When adding entries, use the following format:

```markdown
### Added
- New feature description ([#PR](link)) - @author

### Changed
- Change description ([#PR](link)) - @author

### Fixed
- Bug fix description ([#PR](link)) - @author
```

## Version Guidelines

- **MAJOR**: Breaking changes to API or configuration
- **MINOR**: New features, backward compatible
- **PATCH**: Bug fixes, security updates
