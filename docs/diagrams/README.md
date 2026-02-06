# 📊 Architecture Diagrams

This directory contains architecture diagrams for Dynamic Labyrinth.

## Diagram Files

| File | Description | Format |
|------|-------------|--------|
| [system-overview.mmd](system-overview.mmd) | High-level system architecture | Mermaid |
| [data-flow.mmd](data-flow.mmd) | Data and event flow | Mermaid |
| [network-topology.mmd](network-topology.mmd) | Network segmentation | Mermaid |
| [escalation-sequence.mmd](escalation-sequence.mmd) | Session escalation flow | Mermaid |

## Viewing Diagrams

### VS Code
Install the "Markdown Preview Mermaid Support" extension.

### GitHub
Mermaid diagrams render automatically in GitHub markdown.

### CLI
```bash
# Install mermaid-cli
npm install -g @mermaid-js/mermaid-cli

# Generate PNG
mmdc -i system-overview.mmd -o system-overview.png

# Generate SVG
mmdc -i system-overview.mmd -o system-overview.svg
```

## Updating Diagrams

1. Edit the `.mmd` file
2. Preview changes in VS Code or GitHub
3. Regenerate static images if needed
4. Commit both source and generated files
