from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Must match the declaration order of policy.egress_level exactly -- that order is the lattice.
EGRESS_ORDER: tuple[str, ...] = ("external_model_allowed", "redactable", "local_only")


class PolicyError(RuntimeError):
    """Raised when a source or object has no resolvable policy."""


def max_restriction(*levels: str) -> str:
    """The most restrictive of the given egress levels (architecture inv. 24, 29). Mirrors
    what `GREATEST()` over policy.egress_level computes in SQL, for use in Python-side derivers
    that combine multiple sources' policies before writing a row.
    """
    if not levels:
        raise ValueError("max_restriction() requires at least one level")
    return max(levels, key=EGRESS_ORDER.index)


def sensitivity_union(*sensitivity_lists: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for values in sensitivity_lists:
        for value in values:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
    return ordered


def resolve_source_policy(connection: Connection, source: str) -> tuple[list[str], str]:
    """The current (highest egress_policy_version_id) sensitivity/egress defaults for a source."""
    row = connection.execute(
        text(
            """
            SELECT sensitivity, egress
            FROM policy.source_policy
            WHERE source = :source
            ORDER BY egress_policy_version_id DESC
            LIMIT 1
            """
        ),
        {"source": source},
    ).first()
    if row is None:
        raise PolicyError(f"no policy.source_policy row for source {source!r}")
    return list(row.sensitivity), row.egress


def apply_egress_override(connection: Connection, object_ref: str, computed_level: str) -> str:
    """Loosen `computed_level` if an egress_override for `object_ref` says to.

    An override can only loosen (policy.egress_override enforces to_level < from_level at write
    time), so this never returns something more restrictive than computed_level, and never
    loosens on its own -- only when a declaration-backed override exists (inv. 24).
    """
    override = connection.execute(
        text(
            "SELECT to_level FROM policy.egress_override WHERE object_ref = :ref "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"ref": object_ref},
    ).first()
    if override is None:
        return computed_level
    if EGRESS_ORDER.index(override.to_level) < EGRESS_ORDER.index(computed_level):
        return override.to_level
    return computed_level
