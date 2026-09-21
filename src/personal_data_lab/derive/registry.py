from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Connection

EPISTEMIC_CLASSES = frozenset(
    {"OBSERVED", "COMPUTED", "STATISTICAL", "SEMANTIC", "DECLARED", "HYPOTHESIS", "NARRATIVE"}
)
PARTITION_CLASSES = frozenset({"LOCAL", "HALO", "GLOBAL"})


class DeriverRegistrationError(ValueError):
    """Raised when a deriver is misconfigured or its dependencies would form a cycle."""


@dataclass(frozen=True)
class ComputeResult:
    """What a deriver's compute() returns for one partition.

    input_fingerprint is compared against the previous run's fingerprint for this partition;
    an unchanged fingerprint means the worker records `skipped_unchanged` and writes nothing.
    rows is the complete replacement content for this partition in the deriver's output_relation
    -- the worker deletes whatever was there before and inserts exactly these rows, so a
    deriver's compute() must be a pure function of (partition_key, the data currently visible
    to it), not an incremental patch.
    """

    input_fingerprint: bytes
    rows: list[dict]


ComputeFn = Callable[[Connection, str, int], ComputeResult]
ClearFn = Callable[[Connection, str], None]


@dataclass(frozen=True)
class Deriver:
    name: str
    compute: ComputeFn
    clear_partition: ClearFn
    partition_class: str
    final_inference: str
    description: str
    output_relation: str
    inputs: tuple[str, ...] = ()
    lookback_halo: str = "0"
    lookahead_halo: str = "0"
    code_version: str = "1"
    params: dict = field(default_factory=dict)
    model_ref: dict | None = None


class DeriverRegistry:
    """In-process registry populated by @deriver decorators at import time.

    A fresh instance avoids cross-test leakage; production code uses the module-level REGISTRY,
    populated by importing personal_data_lab.derive.derivers (see derivers/__init__.py).
    """

    def __init__(self) -> None:
        self._derivers: dict[str, Deriver] = {}

    def register(self, deriver: Deriver) -> None:
        if deriver.name in self._derivers:
            raise DeriverRegistrationError(f"deriver {deriver.name!r} is already registered")
        if deriver.partition_class not in PARTITION_CLASSES:
            raise DeriverRegistrationError(
                f"{deriver.name}: partition_class must be one of {sorted(PARTITION_CLASSES)}"
            )
        if deriver.final_inference not in EPISTEMIC_CLASSES:
            raise DeriverRegistrationError(
                f"{deriver.name}: final_inference must be one of {sorted(EPISTEMIC_CLASSES)}"
            )
        if deriver.partition_class != "HALO" and (
            deriver.lookback_halo != "0" or deriver.lookahead_halo != "0"
        ):
            raise DeriverRegistrationError(
                f"{deriver.name}: a halo was set but partition_class is not 'HALO'"
            )
        for upstream in deriver.inputs:
            if upstream not in self._derivers:
                raise DeriverRegistrationError(
                    f"{deriver.name}: unknown upstream dependency {upstream!r} -- derivers must "
                    f"be registered in dependency order"
                )
        provisional = {**self._derivers, deriver.name: deriver}
        _assert_acyclic(provisional)
        self._derivers[deriver.name] = deriver

    def get(self, name: str) -> Deriver:
        try:
            return self._derivers[name]
        except KeyError:
            raise DeriverRegistrationError(f"no such deriver: {name!r}") from None

    def __iter__(self) -> Iterator[Deriver]:
        return iter(self._derivers.values())

    def __len__(self) -> int:
        return len(self._derivers)

    def sync(self, connection: Connection) -> None:
        """Upsert derive.deriver / derive.deriver_dependency rows for every registered deriver.

        Deriver definitions are code, not schema (see migration 010's comment on this) -- this
        is where that code becomes visible to the database, idempotently, on every `derive sync`.
        """
        for deriver in self._derivers.values():
            connection.execute(
                text(
                    """
                    INSERT INTO derive.deriver
                        (name, output_relation, partition_class, lookback_halo, lookahead_halo,
                         final_inference, description)
                    VALUES
                        (:name, :output_relation, :partition_class,
                         CAST(:lookback_halo AS interval), CAST(:lookahead_halo AS interval),
                         :final_inference, :description)
                    ON CONFLICT (name) DO UPDATE SET
                        output_relation = EXCLUDED.output_relation,
                        partition_class = EXCLUDED.partition_class,
                        lookback_halo = EXCLUDED.lookback_halo,
                        lookahead_halo = EXCLUDED.lookahead_halo,
                        final_inference = EXCLUDED.final_inference,
                        description = EXCLUDED.description
                    """
                ),
                {
                    "name": deriver.name,
                    "output_relation": deriver.output_relation,
                    "partition_class": deriver.partition_class,
                    "lookback_halo": deriver.lookback_halo,
                    "lookahead_halo": deriver.lookahead_halo,
                    "final_inference": deriver.final_inference,
                    "description": deriver.description,
                },
            )
        for deriver in self._derivers.values():
            for upstream in deriver.inputs:
                connection.execute(
                    text(
                        """
                        INSERT INTO derive.deriver_dependency (upstream, downstream)
                        VALUES (:upstream, :downstream)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"upstream": upstream, "downstream": deriver.name},
                )

    def downstream_of(self, name: str) -> Sequence[str]:
        return tuple(d.name for d in self._derivers.values() if name in d.inputs)


def _assert_acyclic(derivers: dict[str, Deriver]) -> None:
    """DFS cycle check over the dependency graph implied by `inputs` (architecture inv. 35:
    a claim, and here a deriver, can never bootstrap its own evidentiary strength through its
    own downstream output)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(derivers, WHITE)

    def visit(name: str, path: list[str]) -> None:
        color[name] = GRAY
        for upstream in derivers[name].inputs:
            if upstream not in derivers:
                continue
            if color[upstream] == GRAY:
                cycle = " -> ".join([*path, upstream])
                raise DeriverRegistrationError(f"deriver dependency cycle detected: {cycle}")
            if color[upstream] == WHITE:
                visit(upstream, [*path, upstream])
        color[name] = BLACK

    for name in derivers:
        if color[name] == WHITE:
            visit(name, [name])


REGISTRY = DeriverRegistry()


def deriver(
    *,
    name: str,
    partition_class: str,
    final_inference: str,
    description: str,
    output_relation: str | None = None,
    inputs: Iterable[str] = (),
    lookback_halo: str = "0",
    lookahead_halo: str = "0",
    code_version: str = "1",
    params: dict | None = None,
    model_ref: dict | None = None,
    clear_partition: ClearFn,
    registry: DeriverRegistry = REGISTRY,
) -> Callable[[ComputeFn], ComputeFn]:
    """Decorator registering a compute function as a named deriver.

    clear_partition is required, not optional with a default -- a deriver that doesn't know how
    to delete its own partition's prior output can't safely be rerun, and that's a bug worth
    surfacing at registration time rather than at first rebuild.
    """

    def decorate(fn: ComputeFn) -> ComputeFn:
        registry.register(
            Deriver(
                name=name,
                compute=fn,
                clear_partition=clear_partition,
                partition_class=partition_class,
                final_inference=final_inference,
                description=description,
                output_relation=output_relation or f"measure.{name}",
                inputs=tuple(inputs),
                lookback_halo=lookback_halo,
                lookahead_halo=lookahead_halo,
                code_version=code_version,
                params=params or {},
                model_ref=model_ref,
            )
        )
        return fn

    return decorate
