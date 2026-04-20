"""
ingest-adapters/__init__.py — Adapter registry.

Usage:
    from ingest_adapters import get_adapter

    adapter = get_adapter("file", {"path": "/var/log/honeytrap/events.jsonl"})
    for event in adapter.iter_events():
        process(event)
"""

from __future__ import annotations

from typing import Any, Dict

from . import elasticsearch, file, kafka

_REGISTRY = {
    "file": file,
    "elasticsearch": elasticsearch,
    "kafka": kafka,
}


def get_adapter(name: str, config: Dict[str, Any] = None):
    """
    Return an initialised adapter instance.

    Args:
        name:   One of 'file', 'elasticsearch', 'kafka'.
        config: Dict of adapter-specific config (overrides env vars).

    Raises:
        ValueError: Unknown adapter name.
    """
    config = config or {}
    module = _REGISTRY.get(name)
    if module is None:
        raise ValueError(
            f"Unknown adapter {name!r}. Available: {list(_REGISTRY)}"
        )
    return module.create(config)


__all__ = ["get_adapter", "file", "elasticsearch", "kafka"]
