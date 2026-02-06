# Contributing to Dynamic Labyrinth

Thank you for your interest in contributing to Dynamic Labyrinth! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Component Ownership](#component-ownership)

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and grow
- Report unacceptable behavior to the team lead

## Getting Started

### Prerequisites

- Docker 24.0+
- Docker Compose 2.20+
- Python 3.11+ (for orchestrator development)
- Go 1.21+ (for honeytrap development)
- Node.js 18+ (for dashboard development)

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/YOUR_ORG/dynamic-labyrinth.git
cd dynamic-labyrinth

# Copy environment configuration
cp .env.example .env

# Start all services in development mode
docker-compose up -d

# For orchestrator development
cd orchestrator
python -m venv venv
source venv/bin/activate  # or .\venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install -r ../tests/requirements.txt

# For honeytrap development
cd honeytrap
go mod download
```

## Development Workflow

### Branch Naming

Use the following prefixes:
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation changes
- `refactor/` - Code refactoring
- `test/` - Test additions/changes
- `chore/` - Maintenance tasks

Examples:
```
feature/add-redis-service
fix/session-timeout-bug
docs/update-api-reference
```

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Formatting
- `refactor`: Code restructuring
- `test`: Tests
- `chore`: Maintenance

Examples:
```
feat(orchestrator): add pool scaling endpoint
fix(nginx): correct cookie routing for stream protocols
docs(api): update escalation endpoint examples
```

### Git Workflow

```bash
# 1. Create a feature branch
git checkout -b feature/your-feature

# 2. Make changes and commit
git add .
git commit -m "feat(component): description"

# 3. Keep branch updated
git fetch origin
git rebase origin/main

# 4. Push and create PR
git push origin feature/your-feature
```

## Coding Standards

### Python (Orchestrator, Cerebrum)

```python
# Use type hints
def process_session(session_id: str, threat_score: float) -> EscalationDecision:
    ...

# Use async/await for I/O
async def get_pool_status() -> PoolStatus:
    ...

# Docstrings for public functions
def escalate_session(request: EscalationRequest) -> EscalationDecision:
    """
    Process an escalation request and return a decision.
    
    Args:
        request: The escalation request containing session info.
        
    Returns:
        EscalationDecision with target level and container assignment.
        
    Raises:
        PoolExhaustedException: If no containers available.
    """
    ...
```

Tools:
```bash
# Format code
black orchestrator/
isort orchestrator/

# Lint
ruff check orchestrator/

# Type check
mypy orchestrator/
```

### Go (Honeytrap)

```go
// Use meaningful variable names
func (s *Service) HandleConnection(conn net.Conn) error {
    // ...
}

// Document exported functions
// ProcessRequest handles incoming protocol requests and logs events.
func ProcessRequest(ctx context.Context, req *Request) (*Response, error) {
    // ...
}
```

Tools:
```bash
# Format
go fmt ./...

# Lint
golangci-lint run

# Vet
go vet ./...
```

### Configuration Files

- Use YAML for configuration (not JSON)
- Use environment variables for secrets
- Document all configuration options

## Testing

### Running Tests

```bash
# Python unit tests
cd orchestrator
pytest --cov=. -v

# Python integration tests
docker-compose up -d
pytest tests/integration/ -v

# Go tests
cd honeytrap
go test ./... -v

# Load tests
./tests/load/run_load_test.sh
```

### Writing Tests

```python
# Use descriptive test names
def test_escalation_returns_level2_when_threat_score_above_threshold():
    ...

# Use fixtures for common setup
@pytest.fixture
def mock_cerebrum_response():
    return {"threat_score": 0.85, "indicators": ["brute_force"]}

# Test edge cases
def test_escalation_handles_pool_exhaustion_gracefully():
    ...
```

### Test Coverage

- Minimum coverage: 80%
- Critical paths: 95%
- New code: Must include tests

## Pull Request Process

### Before Submitting

- [ ] Tests pass locally
- [ ] Code formatted with black/gofmt
- [ ] Linting passes
- [ ] Documentation updated
- [ ] Changelog entry added (if applicable)

### PR Template

PRs automatically use the template at `.github/PULL_REQUEST_TEMPLATE.md`.

### Review Process

1. **Automated checks** - CI runs tests, linting, security scans
2. **Code review** - At least 1 approval from component owner
3. **Merge** - Squash and merge to main

### Review Guidelines

- Review within 24 hours
- Be constructive and specific
- Approve when satisfied
- Request changes with clear guidance

## Component Ownership

| Component | Owner | Reviewers |
|-----------|-------|-----------|
| Orchestrator | Omar | Ahmed, Yara |
| Honeytrap | Salma | Omar |
| Cerebrum | Yara | Ahmed |
| Dashboard | Ahmed | Omar |
| Infrastructure | Omar | All |
| Documentation | All | All |

### Getting Help

- **Slack**: #dynamic-labyrinth-dev
- **Issues**: Use GitHub Issues for bugs/features
- **Discussions**: Use GitHub Discussions for questions

## Release Process

1. Update version in `pyproject.toml` / `go.mod`
2. Update CHANGELOG.md
3. Create PR titled "Release vX.Y.Z"
4. After merge, tag release: `git tag vX.Y.Z`
5. Push tag: `git push origin vX.Y.Z`
6. GitHub Actions builds and publishes images

---

Thank you for contributing! 🙏
