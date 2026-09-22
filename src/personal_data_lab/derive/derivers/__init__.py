"""Importing this package registers every Phase 0/Phase 1 deriver against derive.registry.REGISTRY.

Anything that needs the full registry populated (the CLI, the worker, tests exercising more
than one module in isolation) should import this package, not just the individual deriver
modules, so registration order (and therefore dependency-order registration, which
registry.Deriver.register enforces) is consistent.
"""

from __future__ import annotations

from . import (
    activity,  # noqa: F401 -- imported for its @deriver registration side effect
    coverage,  # noqa: F401 -- imported for its @deriver registration side effect
)

__all__: list[str] = []
