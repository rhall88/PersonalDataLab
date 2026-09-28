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
# semantic and media have no dependency on any of the others (each a root LOCAL/HALO pair off
# canonical events/assets), so their position here doesn't matter -- kept last for readability,
# since they're the newest.
from . import activity  # noqa: F401 -- imported for its @deriver registration side effect
from . import coverage  # noqa: F401 -- imported for its @deriver registration side effect
from . import metrics  # noqa: F401 -- imported for its @deriver registration side effect
from . import signals  # noqa: F401 -- imported for its @deriver registration side effect
from . import interaction  # noqa: F401 -- imported for its @deriver registration side effect
from . import episode  # noqa: F401 -- imported for its @deriver registration side effect
from . import semantic  # noqa: F401 -- imported for its @deriver registration side effect
from . import semantic_documents  # noqa: F401 -- imported for its @deriver registration side effect
from . import media  # noqa: F401 -- imported for its @deriver registration side effect
from . import annotation  # noqa: F401 -- imported for its @deriver registration side effect; depends on semantic
from . import clustering  # noqa: F401 -- imported for its @deriver registration side effect; depends on semantic

# Phase 10 (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md): generative must register before entity
# (entity_mention_extract/relation_candidate_extract read generative.extraction); entity before
# event/intent (both depend on entity_resolve_mentions). claims must come last of all -- it
# already depended on signals/episode, and now also depends on event_resolve_candidates/
# intent_resolve_candidates, so it moved below this whole block (it used to sit right after
# episode, before semantic/annotation/clustering existed at all).
from . import generative  # noqa: F401 -- imported for its @deriver registration side effect; depends on semantic
from . import entity  # noqa: F401 -- imported for its @deriver registration side effect; depends on generative
from . import event  # noqa: F401 -- imported for its @deriver registration side effect; depends on generative, entity
from . import intent  # noqa: F401 -- imported for its @deriver registration side effect; depends on generative, entity
from . import claims  # noqa: F401 -- imported for its @deriver registration side effect; depends on signals, episode, event, intent

__all__: list[str] = []