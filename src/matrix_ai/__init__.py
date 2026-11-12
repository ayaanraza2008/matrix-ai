"""MATRIX - a local-first, JARVIS-inspired personal AI assistant.

The package is deliberately small at the top level: every subsystem lives in a
narrow module so that security review can be done per boundary:

    api/            HTTP surface (auth, validation, routing only)
    core/           orchestrator, task state machine, policy, audit
    tools/          typed, allowlisted tool adapters
    models/         model provider abstraction + hardware-aware manager
    memory/         governed memory retrieval
    storage/        SQLite source of truth
    security/       auth, pairing, redaction, untrusted-content handling
    integrations/   optional external adapters (never required)
    voice/, vision/ permission-gated session managers
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
