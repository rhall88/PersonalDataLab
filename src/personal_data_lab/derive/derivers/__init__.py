"""Importing this package registers every Phase 0/Phase 1 deriver against derive.registry.REGISTRY.

Anything that needs the full registry populated (the CLI, the worker, tests exercising more
than one module in isolation) should import this package, not just the individual deriver
modules, so registration order (and therefore dependency-order registration, which
registry.Deriver.register enforces) is consistent.
"""

from __future__ import annotations  # noqa: I001 -- import order below is dependency order, not alphabetical

# metrics's derivers must register before signals's and episode's (both declare inputs
# referencing metrics.py's deriver names); interaction only depends on coverage. claims must
# register last: it declares inputs referencing both signals's and episode's deriver names.
# semantic has no dependency on any of the others (a root HALO/LOCAL pair off canonical events),
# so its position here doesn't matter -- kept last for readability, since it's the newest.
from . import activity  # noqa: F401 -- imported for its @deriver registration side effect
from . import coverage  # noqa: F401 -- imported for its @deriver registration side effect
from . import metrics  # noqa: F401 -- imported for its @deriver registration side effect
from . import signals  # noqa: F401 -- imported for its @deriver registration side effect
from . import interaction  # noqa: F401 -- imported for its @deriver registration side effect
from . import episode  # noqa: F401 -- imported for its @deriver registration side effect
from . import claims  # noqa: F401 -- imported for its @deriver registration side effect
from . import semantic  # noqa: F401 -- imported for its @deriver registration side effect

__all__: list[str] = []