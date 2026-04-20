"""conftest.py — pytest configuration for ingestion tests."""
import sys
from pathlib import Path

# Ensure ingestion package is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))
