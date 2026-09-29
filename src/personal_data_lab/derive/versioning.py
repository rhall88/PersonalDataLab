from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..fingerprints import canonical_json_bytes, fingerprint_value
from .registry import Deriver


class VersioningError(RuntimeError):
    """Raised when a version or promotion request can't be satisfied."""


def version_hash(deriver: Deriver) -> bytes:
    """sha256 over the canonical JSON of exactly the fields that define a deriver's behavior:
    its name (so two derivers with identical code/params never collide), code_version, params,
    and model_ref. Changing any of these is, by definition, a different version.
    """
    payload = {
        "name": deriver.name,
        "code_version": deriver.code_version,
        "params": deriver.params,
        "model_ref": deriver.model_ref,
    }
    return fingerprint_value(payload)


def ensure_deriver_version(connection: Connection, deriver: Deriver) -> int:
    """Idempotently ensure a derive.deriver_version row exists for this deriver's current
    version_hash, returning its id.

    A brand-new deriver's first version becomes active immediately -- there is nothing to
    shadow against yet, and requiring a manual `derive promote` before a deriver can ever run
    would just be ceremony. A deriver whose hash has changed (params/code_version/model_ref
    edited) gets a new 'shadow' version instead: it exists and can be run, but does not become
    the active version -- and therefore does not affect derive.deriver_dependency propagation
    or what `derive status` reports as current -- until an explicit `derive promote`.
    """
    h = version_hash(deriver)
    existing = connection.execute(
        text("SELECT id FROM derive.deriver_version WHERE version_hash = :hash"),
        {"hash": h},
    ).first()
    if existing is not None:
        return existing.id

    has_active = connection.execute(
        text(
            "SELECT 1 FROM derive.deriver_version WHERE deriver_name = :name AND status = 'active'"
        ),
        {"name": deriver.name},
    ).first()
    status = "shadow" if has_active else "active"
    return connection.execute(
        text(
            """
            INSERT INTO derive.deriver_version
                (deriver_name, code_version, params, model_ref, version_hash, status)
            VALUES
                (:name, :code_version, CAST(:params AS jsonb), CAST(:model_ref AS jsonb),
                 :hash, :status)
            RETURNING id
            """
        ),
        {
            "name": deriver.name,
            "code_version": deriver.code_version,
            "params": canonical_json_bytes(deriver.params).decode("utf-8"),
            "model_ref": (
                canonical_json_bytes(deriver.model_ref).decode("utf-8")
                if deriver.model_ref is not None
                else None
            ),
            "hash": h,
            "status": status,
        },
    ).scalar_one()


def active_version_id(connection: Connection, deriver_name: str) -> int:
    row = connection.execute(
        text(
            "SELECT id FROM derive.deriver_version WHERE deriver_name = :name AND status = 'active'"
        ),
        {"name": deriver_name},
    ).first()
    if row is None:
        raise VersioningError(
            f"deriver {deriver_name!r} has no active version -- run `derive sync` first"
        )
    return row.id


def promote(connection: Connection, deriver_name: str, version_hash_hex: str) -> None:
    """Flip the named version to active, retiring whichever version was active before, in one
    transaction. The retired version's rows stay queryable (nothing is deleted) so a past
    answer computed under it can still be replayed.

    The newly active version inherits the retired version's partition space, all marked dirty.
    The worker only claims partitions of the active version, so without this a promoted version
    would have nothing to compute until something happened to re-dirty each partition. Downstream
    derivers are not re-dirtied here: the worker doesn't propagate after writing, so they need a
    `derive-rebuild` once this version's partitions have actually run.
    """
    target = connection.execute(
        text(
            "SELECT id, status FROM derive.deriver_version "
            "WHERE deriver_name = :name AND version_hash = :hash"
        ),
        {"name": deriver_name, "hash": bytes.fromhex(version_hash_hex)},
    ).first()
    if target is None:
        raise VersioningError(f"no version {version_hash_hex} for deriver {deriver_name!r}")
    if target.status == "active":
        return
    if target.status == "retired":
        raise VersioningError(
            f"version {version_hash_hex} of {deriver_name!r} is retired and cannot be re-promoted"
        )
    previous = connection.execute(
        text(
            "SELECT id FROM derive.deriver_version WHERE deriver_name = :name AND status = 'active'"
        ),
        {"name": deriver_name},
    ).first()
    connection.execute(
        text(
            "UPDATE derive.deriver_version SET status = 'retired' "
            "WHERE deriver_name = :name AND status = 'active'"
        ),
        {"name": deriver_name},
    )
    connection.execute(
        text("UPDATE derive.deriver_version SET status = 'active' WHERE id = :id"),
        {"id": target.id},
    )
    if previous is not None:
        connection.execute(
            text(
                """
                INSERT INTO derive.partition_state (deriver_version_id, partition_key, state, dirty_since)
                SELECT :new_id, partition_key, 'dirty', CURRENT_TIMESTAMP
                FROM derive.partition_state WHERE deriver_version_id = :old_id
                ON CONFLICT (deriver_version_id, partition_key) DO UPDATE SET state = 'dirty'
                WHERE derive.partition_state.state <> 'running'
                """
            ),
            {"new_id": target.id, "old_id": previous.id},
        )
