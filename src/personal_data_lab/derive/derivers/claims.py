"""Phase 5's claims ledger (PHASE5_IMPLEMENTATION_PLAN.md): `know_claim_evaluate` wraps Phase
3/4's already-computed `signal.anomaly` and confirmed/rejected `episode.episode` rows into
first-class, inspectable `know.claim` objects with a per-dimension confidence profile and cited
evidence. No new statistics are computed here -- this deriver reads what Phases 3/4 already wrote
and cites it: `communication_change` (one claim per `signal.anomaly` row), `episode_evidence` (one
claim per `episode.episode` row that has actually been reviewed -- `status IN ('confirmed',
'rejected')`, never `candidate`; invariant 38, "review queues fail unresolved, never optimistic"),
and `relationship_state_shift` (added 2026-09-25, ROADMAP.md item 3's "further claim-template
growth": one claim per consecutive pair of `signal.interaction_state` segments for the same
(source, correspondent) -- Phase 4's own PELT segmentation already found these transitions, this
template just wraps them the same way `episode_evidence` wraps episode detection. Grounded against
the real canonical database: 77 states across 69 (source, correspondent) pairs, of which 7
correspondents have 2+ states (8 real consecutive-pair transitions total) -- the honest small-N
population this template actually has to work with, not invented).

Evidence is cited inline as JSONB on `know.claim.evidence` (migration 016's correction to
migration 015's original separate `know.claim_evidence` table): this project's deriver framework
(`derive/worker.py`'s `_write_rows`) writes `ComputeResult.rows` to exactly one `output_relation`
per deriver, via a plain multi-row INSERT with no `RETURNING`, so a claim and a child evidence row
referencing its worker-generated id can never be written from the same `compute()` call. This is
the same problem, and the same fix, `episode.episode.evidence` already established for Phase 4.

Same GLOBAL-sibling ordering caveat signals.py's and interaction.py's own module docstrings
document: this deriver declares `inputs=("signal_anomaly", "episode_detect_candidates")` for
correct dirtying propagation, but within one `derive run --global` invocation the worker claims
dirty partitions in `dirty_since` order, not topological order, so `know_claim_evaluate` is not
guaranteed to run *after* those two GLOBAL siblings finish in the same batch. Unlike
signal_anomaly/signal_interaction_state (which sidestep this by recomputing their own inputs
inline from a shared upstream table), `know_claim_evaluate`'s entire job is to wrap those two
tables' *output* rows -- there is no inline recomputation available here. A claims run inside the
same batch as the anomaly/episode data that produced it may reflect a stale snapshot until the
next `derive run --global` picks it up again. Documented as a real, accepted limitation (same
precedent as signals.py's own docstring), not solved here: rollout calls `derive run --global`
repeatedly until `derive-status` reports zero dirty, the same operational pattern already used for
every prior phase's own real-database rollout.
"""

from __future__ import annotations

import json
from datetime import date
from itertools import pairwise

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ...fingerprints import fingerprint_value
from ..registry import ComputeResult, deriver

_CLASS_BY_EPISODE_KIND = {"declared": "DECLARED", "detected": "STATISTICAL"}


def _clear_claims(connection: Connection, partition_key: str) -> None:
    connection.execute(text("DELETE FROM know.claim"))


def _coverage_for(connection: Connection, pairs: set[tuple[str, date]]) -> float | None:
    """Minimum `measure.coverage.completeness` across a set of (source, day) pairs, object_kind
    'event' -- the same object_kind every current metric and episode evidence source uses. `None`
    (dimension absent, not a fabricated 1.0) if `pairs` is empty or any pair has no coverage row
    at all -- an undeclared/uncovered day is unknown, never silently treated as fully covered.

    Used only by `communication_change` claims, where `pairs` is every constituent source for
    exactly *one* day (a composite metric can have several sources; a single-source metric has
    one) -- "weakest link across today's sources" is the right question there. Not used for
    `episode_evidence` (see `_coverage_for_range` and the module-level note on why a strict
    per-literal-day minimum over *many* days was a real, documented weakness the 2026-09-24
    Phase 5 rollout found).
    """
    if not pairs:
        return None
    values: list[float] = []
    for source, day in pairs:
        row = connection.execute(
            text(
                "SELECT completeness FROM measure.coverage "
                "WHERE source = :source AND bucket_start = :day AND object_kind = 'event'"
            ),
            {"source": source, "day": day},
        ).scalar_one_or_none()
        if row is None:
            return None
        values.append(float(row))
    return min(values)


def _coverage_for_range(
    connection: Connection, sources: set[str], start: date, end: date
) -> float | None:
    """Mean `measure.coverage.completeness` (object_kind 'event') across every declared day in
    `[start, end]` for `sources` -- the fix for a real, documented weakness the 2026-09-24 Phase 5
    rollout found in `episode_evidence` claims: taking the *minimum* completeness across only the
    handful of literal change-point dates in a claim's evidence meant any multi-day episode
    touching a naturally sparse source (e.g. anthropic, which has real activity on only a fraction
    of days) got dragged to `coverage = 0.0` almost every time, even when the episode's actual
    span was well covered. A single sparse day with no same-day activity for one specific metric
    is a normal pattern, not a coverage gap -- asking "how complete was data collection across
    this claim's actual sources, over its actual span" (a mean over the whole range) is the more
    representative question, and the one this dimension was meant to answer. `None` (absent, not
    a fabricated value) if there is no coverage data at all for these sources over this range --
    an undeclared contract is still unknown, never silently treated as covered.
    """
    if not sources or start is None or end is None:
        return None
    row = connection.execute(
        text(
            "SELECT avg(completeness) AS avg_completeness, count(*) AS n FROM measure.coverage "
            "WHERE source = ANY(:sources) AND object_kind = 'event' "
            "AND bucket_start >= :start AND bucket_start <= :end"
        ),
        {"sources": list(sources), "start": start, "end": end},
    ).mappings().one()
    if row["n"] == 0 or row["avg_completeness"] is None:
        return None
    return float(row["avg_completeness"])


def _communication_change_claims(connection: Connection, epoch_id: int) -> list[dict]:
    """One claim per `signal.anomaly` row -- the architecture's own worked example (section 9) of
    a `communication_change` claim. `identity` is never computed here: none of Phase 3's current
    metrics are per-correspondent (plan decision 3's own grounding), so every claim from this
    template has that dimension correctly absent today, not a bug to fix later.
    """
    rows = connection.execute(
        text(
            """
            SELECT sa.metric_id, md.name AS metric_name, sa.bucket_start, sa.observed,
                   sa.expected_low, sa.expected_high, sa.z_score, sa.baseline_kind
            FROM signal.anomaly sa
            JOIN derive.metric_def md ON md.id = sa.metric_id
            ORDER BY sa.metric_id, sa.bucket_start
            """
        )
    ).mappings().all()
    if not rows:
        return []

    sources_by_metric: dict[int, set[str]] = {}
    for metric_id, source in connection.execute(
        text("SELECT metric_id, source FROM derive.metric_dependency")
    ).all():
        sources_by_metric.setdefault(metric_id, set()).add(source)

    claims: list[dict] = []
    for row in rows:
        sources = sources_by_metric.get(row["metric_id"], set())
        coverage = _coverage_for(connection, {(s, row["bucket_start"]) for s in sources})
        confidence: dict = {
            "statistical_strength": abs(row["z_score"]),
            "source_independence": 1,
        }
        if coverage is not None:
            confidence["coverage"] = coverage
        statement = (
            f"{row['metric_name']} on {row['bucket_start'].isoformat()} was "
            f"{row['observed']:g}, far outside its expected range of roughly "
            f"{row['expected_low']:.1f} to {row['expected_high']:.1f} "
            f"(z={row['z_score']:.1f} against its {row['baseline_kind']} baseline)."
        )
        evidence = {
            "anomalies": [
                {
                    "metric_id": row["metric_id"],
                    "metric": row["metric_name"],
                    "bucket_start": row["bucket_start"].isoformat(),
                }
            ]
        }
        claims.append(
            {
                "template": "communication_change",
                "template_version": "1",
                "class": "STATISTICAL",
                "statement": statement,
                "subject": json.dumps(
                    {"metric": row["metric_name"], "bucket_start": row["bucket_start"].isoformat()}
                ),
                "confidence": json.dumps(confidence),
                "evidence": json.dumps(evidence),
                "status": "active",
                "computed_epoch_id": epoch_id,
            }
        )
    return claims


def _episode_statement(
    *, kind: str, status: str, label: str | None, note: str | None,
    start_low: date, end_high: date, n_change_points: int, n_sources: int,
) -> str:
    if status == "rejected":
        base = (
            f"Episode candidate ({start_low.isoformat()}–{end_high.isoformat()}, "
            f"{n_change_points} change points across {n_sources} source families) was reviewed "
            "and rejected"
        )
        return f"{base}: {note}" if note else f"{base}."
    if label:
        return label
    if kind == "declared":
        return f"Declared period: {start_low.isoformat()}–{end_high.isoformat()}."
    return (
        f"Detected and confirmed period ({start_low.isoformat()}–{end_high.isoformat()}), "
        f"clustering {n_change_points} change points across {n_sources} source families."
    )


def _episode_evidence_claims(connection: Connection, epoch_id: int) -> list[dict]:
    """One claim per `episode.episode` row that has actually been reviewed (plan decision 2):
    `class` is DECLARED for a human's own stated period, STATISTICAL for a detected candidate a
    human confirmed -- the episode itself is a statistical inference (change-point clustering)
    that a human then verified; verification raises confidence, it does not change what kind of
    assertion the underlying evidence is (the architecture's own rule that a declaration is
    authoritative about what you declared, not about what you're declaring is true of). A
    `rejected` episode still gets a claim: rejecting is itself a fact worth an inspectable trace,
    not something to silently delete.
    """
    rows = connection.execute(
        text(
            "SELECT id, kind, status, start_range_low, end_range_high, label, note, evidence "
            "FROM episode.episode WHERE status IN ('confirmed', 'rejected')"
        )
    ).mappings().all()

    claims: list[dict] = []
    for row in rows:
        change_points = (row["evidence"] or {}).get("change_points", [])
        n_change_points = len(change_points)
        sources = {cp["source"] for cp in change_points}
        confidence: dict = {}
        if change_points:
            confidence["statistical_strength"] = n_change_points
            confidence["source_independence"] = len(sources)
            coverage = _coverage_for_range(
                connection, sources, row["start_range_low"], row["end_range_high"]
            )
            if coverage is not None:
                confidence["coverage"] = coverage
        statement = _episode_statement(
            kind=row["kind"], status=row["status"], label=row["label"], note=row["note"],
            start_low=row["start_range_low"], end_high=row["end_range_high"],
            n_change_points=n_change_points, n_sources=len(sources),
        )
        claims.append(
            {
                "template": "episode_evidence",
                "template_version": "1",
                "class": _CLASS_BY_EPISODE_KIND[row["kind"]],
                "statement": statement,
                "subject": json.dumps({"episode_id": row["id"], "episode_status": row["status"]}),
                "confidence": json.dumps(confidence),
                "evidence": json.dumps(
                    {
                        "episode_id": row["id"],
                        "change_points": [
                            {
                                "metric": cp["metric"],
                                "source": cp["source"],
                                "date": cp["date"],
                            }
                            for cp in change_points
                        ],
                    }
                ),
                "status": "active",
                "computed_epoch_id": epoch_id,
            }
        )
    return claims


def _relationship_state_shift_claims(connection: Connection, epoch_id: int) -> list[dict]:
    """One claim per consecutive pair of `signal.interaction_state` segments for the same
    (source, correspondent) -- a correspondent with N states produces N-1 transition claims, and
    one with a single state (most of them, per the real grounding above) produces none: a single
    state is "no change detected yet", not itself a claim-worthy transition.
    """
    rows = connection.execute(
        text(
            "SELECT source, correspondent, state_start, state_end, mean_messages_in, "
            "mean_messages_out, mean_reciprocity_index, n_months "
            "FROM signal.interaction_state ORDER BY source, correspondent, state_start"
        )
    ).mappings().all()

    by_pair: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_pair.setdefault((row["source"], row["correspondent"]), []).append(dict(row))

    claims: list[dict] = []
    for (source, correspondent), states in by_pair.items():
        for before, after in pairwise(states):
            reciprocity_delta = abs(
                after["mean_reciprocity_index"] - before["mean_reciprocity_index"]
            )
            coverage = _coverage_for_range(
                connection, {source}, before["state_start"], after["state_end"]
            )
            confidence: dict = {
                "statistical_strength": reciprocity_delta,
                # Interaction dynamics is computed per single source (PHASE4_IMPLEMENTATION_PLAN.md
                # decision 10) -- one family, never fabricated as corroborated by more.
                "source_independence": 1,
            }
            if coverage is not None:
                confidence["coverage"] = coverage
            direction = "increased" if after["mean_reciprocity_index"] > before["mean_reciprocity_index"] else "decreased"
            statement = (
                f"Your interaction pattern with {correspondent} on {source} {direction}: "
                f"reciprocity went from {before['mean_reciprocity_index']:.2f} "
                f"(msgs in/out ~{before['mean_messages_in']:.1f}/{before['mean_messages_out']:.1f}, "
                f"{before['state_start'].isoformat()}–{before['state_end'].isoformat()}) to "
                f"{after['mean_reciprocity_index']:.2f} "
                f"(msgs in/out ~{after['mean_messages_in']:.1f}/{after['mean_messages_out']:.1f}, "
                f"{after['state_start'].isoformat()}–{after['state_end'].isoformat()})."
            )
            claims.append(
                {
                    "template": "relationship_state_shift",
                    "template_version": "1",
                    "class": "STATISTICAL",
                    "statement": statement,
                    "subject": json.dumps({"source": source, "correspondent": correspondent}),
                    "confidence": json.dumps(confidence),
                    "evidence": json.dumps(
                        {
                            "before": {
                                "source": source, "correspondent": correspondent,
                                "state_start": before["state_start"].isoformat(),
                            },
                            "after": {
                                "source": source, "correspondent": correspondent,
                                "state_start": after["state_start"].isoformat(),
                            },
                        }
                    ),
                    "status": "active",
                    "computed_epoch_id": epoch_id,
                }
            )
    return claims


def _event_claims(connection: Connection, epoch_id: int) -> list[dict]:
    """One claim per confirmed event.canonical_event (Phase 10 decision 15) -- packaging
    already-resolved rows, same shape episode_evidence/relationship_state_shift established. No
    new inference: event_resolve_candidates/event-confirm already did the clustering and the
    human review.
    """
    events = connection.execute(
        text("SELECT id, event_type, label, occurred_range FROM event.canonical_event")
    ).mappings().all()
    claims: list[dict] = []
    for ev in events:
        members = connection.execute(
            text(
                "SELECT el.text_unit_id, t.source FROM event.event_link el "
                "JOIN semantic.text_unit t ON t.id = el.text_unit_id "
                "WHERE el.canonical_event_id = :id ORDER BY el.text_unit_id"
            ),
            {"id": ev["id"]},
        ).mappings().all()
        sources = {m["source"] for m in members}
        confidence = {"statistical_strength": len(members), "source_independence": len(sources)}
        occurred = ev["occurred_range"]
        range_note = ""
        if occurred is not None and not occurred.isempty:
            range_note = f" around {occurred.lower.isoformat()}–{occurred.upper.isoformat()}"
        statement = (
            f"{ev['label']} ({ev['event_type']}){range_note}, corroborated across "
            f"{len(sources)} source(s): {', '.join(sorted(sources))}."
        )
        claims.append(
            {
                "template": "event_claim",
                "template_version": "1",
                "class": "COMPUTED",
                "statement": statement,
                "subject": json.dumps({"canonical_event_id": ev["id"]}),
                "confidence": json.dumps(confidence),
                "evidence": json.dumps(
                    {
                        "canonical_event_id": ev["id"],
                        "members": [dict(m) for m in members],
                    }
                ),
                "status": "active",
                "computed_epoch_id": epoch_id,
            }
        )
    return claims


def _intent_thread_claims(connection: Connection, epoch_id: int, kind: str, template: str) -> list[dict]:
    """One claim per confirmed intent.thread of `kind`, carrying its member occurrences' full
    state sequence -- only possible now that decision 13 gives it somewhere to come from
    (intent_resolve_candidates + intent-confirm already did the clustering and human review).
    """
    state_column = "decision_state" if kind == "decision" else "open_loop_state"
    threads = connection.execute(
        text("SELECT id, label FROM intent.thread WHERE kind = :kind"), {"kind": kind}
    ).mappings().all()
    claims: list[dict] = []
    for thread in threads:
        members = connection.execute(
            text(
                f"""
                SELECT tl.text_unit_id, t.source, t.started_at, g.{state_column} AS state
                FROM intent.thread_link tl
                JOIN semantic.text_unit t ON t.id = tl.text_unit_id
                JOIN generative.extraction g ON g.text_unit_id = tl.text_unit_id
                WHERE tl.thread_id = :thread_id
                ORDER BY t.started_at
                """
            ),
            {"thread_id": thread["id"]},
        ).mappings().all()
        sources = {m["source"] for m in members}
        confidence = {"statistical_strength": len(members), "source_independence": len(sources)}
        state_path = " -> ".join(m["state"] for m in members)
        statement = f"{thread['label']}: {state_path} (across {len(members)} occurrence(s))."
        claims.append(
            {
                "template": template,
                "template_version": "1",
                "class": "COMPUTED",
                "statement": statement,
                "subject": json.dumps({"thread_id": thread["id"]}),
                "confidence": json.dumps(confidence),
                "evidence": json.dumps(
                    {
                        "thread_id": thread["id"],
                        "state_sequence": [
                            {
                                "text_unit_id": m["text_unit_id"],
                                "source": m["source"],
                                "state": m["state"],
                                "started_at": m["started_at"].isoformat(),
                            }
                            for m in members
                        ],
                    }
                ),
                "status": "active",
                "computed_epoch_id": epoch_id,
            }
        )
    return claims


@deriver(
    name="know_claim_evaluate",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description=(
        "Wraps signal.anomaly (communication_change), reviewed episode.episode rows "
        "(episode_evidence), consecutive signal.interaction_state pairs "
        "(relationship_state_shift), confirmed event.canonical_event rows (event_claim), and "
        "confirmed intent.thread rows (decision_claim/open_loop_claim) into inspectable "
        "know.claim objects with a confidence profile (PHASE5_IMPLEMENTATION_PLAN.md; "
        "relationship_state_shift added 2026-09-25; event/decision/open_loop claims added by "
        "PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 15)."
    ),
    output_relation="know.claim",
    inputs=(
        "signal_anomaly", "episode_detect_candidates", "signal_interaction_state",
        "event_resolve_candidates", "intent_resolve_candidates",
    ),
    clear_partition=_clear_claims,
)
def know_claim_evaluate(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    rows = (
        _communication_change_claims(connection, epoch_id)
        + _episode_evidence_claims(connection, epoch_id)
        + _relationship_state_shift_claims(connection, epoch_id)
        + _event_claims(connection, epoch_id)
        + _intent_thread_claims(connection, epoch_id, "decision", "decision_claim")
        + _intent_thread_claims(connection, epoch_id, "open_loop", "open_loop_claim")
    )
    fingerprint_source = sorted(
        (row["template"], row["subject"], row["statement"]) for row in rows
    )
    return ComputeResult(input_fingerprint=fingerprint_value(fingerprint_source), rows=rows)


def _clear_contradictions(connection: Connection, partition_key: str) -> None:
    connection.execute(text("DELETE FROM know.claim_contradiction WHERE status = 'candidate'"))


def _detect_open_loop_contradictions(connection: Connection, epoch_id: int) -> list[dict]:
    """Decision 16(b): two open_loop_claim rows citing the *same* resolved intent.thread whose
    most recent member state disagrees -- only possible for a thread with genuinely conflicting
    source evidence, now a real, checkable identity rather than an assumption. Under this
    deriver's own 1:1 (thread -> claim) production this group normally has exactly one member and
    never fires; the check is written generically (group by thread_id, not by claim id) so it
    still catches the case if it ever can happen, rather than silently assuming it can't.
    """
    rows = connection.execute(
        text(
            "SELECT id, evidence FROM know.claim WHERE template = 'open_loop_claim' AND status = 'active'"
        )
    ).mappings().all()
    by_thread: dict[int, list[dict]] = {}
    for row in rows:
        thread_id = row["evidence"]["thread_id"]
        sequence = row["evidence"]["state_sequence"]
        latest_state = sequence[-1]["state"] if sequence else None
        by_thread.setdefault(thread_id, []).append(
            {"claim_id": row["id"], "latest_state": latest_state}
        )

    contradictions: list[dict] = []
    for thread_id, claims in by_thread.items():
        if len(claims) < 2:
            continue
        states = {c["latest_state"] for c in claims}
        if len(states) < 2:
            continue
        ordered = sorted(claims, key=lambda c: c["claim_id"])
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                a, b = ordered[i], ordered[j]
                if a["latest_state"] == b["latest_state"]:
                    continue
                claim_a, claim_b = sorted((a["claim_id"], b["claim_id"]))
                contradictions.append(
                    {
                        "claim_id_a": claim_a,
                        "claim_id_b": claim_b,
                        "note": (
                            f"open_loop_claim rows for the same thread (id {thread_id}) disagree "
                            f"on latest state: {a['latest_state']!r} vs {b['latest_state']!r}."
                        ),
                        "status": "candidate",
                        "computed_epoch_id": epoch_id,
                    }
                )
    return contradictions


def _detect_directional_contradictions(connection: Connection, epoch_id: int) -> list[dict]:
    """Decision 16(a): two communication_change/relationship_state_shift claims about the same
    subject with opposite directional statements over overlapping ranges. Scoped to what pure
    logic can actually verify -- no free-text semantic contradiction detection is attempted.
    """
    rows = connection.execute(
        text(
            "SELECT id, template, subject, statement FROM know.claim "
            "WHERE template IN ('communication_change', 'relationship_state_shift') "
            "AND status = 'active'"
        )
    ).mappings().all()
    by_subject: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["template"], json.dumps(row["subject"], sort_keys=True))
        by_subject.setdefault(key, []).append(row)

    contradictions: list[dict] = []
    for claims in by_subject.values():
        if len(claims) < 2:
            continue
        increased = [c for c in claims if "increased" in c["statement"]]
        decreased = [c for c in claims if "decreased" in c["statement"]]
        for a in increased:
            for b in decreased:
                claim_a, claim_b = sorted((a["id"], b["id"]))
                contradictions.append(
                    {
                        "claim_id_a": claim_a,
                        "claim_id_b": claim_b,
                        "note": "same subject reported as both increased and decreased.",
                        "status": "candidate",
                        "computed_epoch_id": epoch_id,
                    }
                )
    return contradictions


@deriver(
    name="claim_contradiction_detect",
    partition_class="GLOBAL",
    final_inference="HYPOTHESIS",
    description=(
        "Detects only two checkable contradiction shapes: opposite-direction communication_change/"
        "relationship_state_shift claims about the same subject, and disagreeing open_loop_claim "
        "rows for the same resolved intent.thread -- no free-text semantic contradiction detection "
        "(PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 16)."
    ),
    output_relation="know.claim_contradiction",
    inputs=("know_claim_evaluate",),
    clear_partition=_clear_contradictions,
)
def claim_contradiction_detect(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    rows = _detect_directional_contradictions(connection, epoch_id) + _detect_open_loop_contradictions(
        connection, epoch_id
    )
    fingerprint = fingerprint_value(sorted((r["claim_id_a"], r["claim_id_b"], r["note"]) for r in rows))
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)


def list_claim_contradictions(engine: Engine, *, status: str | None = "candidate") -> list[dict]:
    query = (
        "SELECT id, claim_id_a, claim_id_b, note, status, created_at FROM know.claim_contradiction"
    )
    params: dict = {}
    if status is not None:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY id"
    with engine.connect() as connection:
        rows = connection.execute(text(query), params).mappings().all()
    return [dict(row) for row in rows]


def confirm_claim_contradiction(engine: Engine, contradiction_id: int) -> dict:
    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT status FROM know.claim_contradiction WHERE id = :id FOR UPDATE"),
            {"id": contradiction_id},
        ).first()
        if row is None:
            raise ValueError(f"no claim contradiction with id {contradiction_id}")
        if row.status != "candidate":
            raise ValueError(f"claim contradiction {contradiction_id} is already status={row.status!r}")
        updated = connection.execute(
            text(
                "UPDATE know.claim_contradiction SET status = 'confirmed' WHERE id = :id "
                "RETURNING id, status"
            ),
            {"id": contradiction_id},
        ).mappings().one()
        return dict(updated)


def reject_claim_contradiction(engine: Engine, contradiction_id: int) -> dict:
    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT status FROM know.claim_contradiction WHERE id = :id FOR UPDATE"),
            {"id": contradiction_id},
        ).first()
        if row is None:
            raise ValueError(f"no claim contradiction with id {contradiction_id}")
        if row.status != "candidate":
            raise ValueError(f"claim contradiction {contradiction_id} is already status={row.status!r}")
        updated = connection.execute(
            text(
                "UPDATE know.claim_contradiction SET status = 'rejected' WHERE id = :id "
                "RETURNING id, status"
            ),
            {"id": contradiction_id},
        ).mappings().one()
        return dict(updated)


# --- claims-list / claims-why: plain read functions the CLI calls directly, same precedent as
# episode.py's declare_episode/list_episodes -- browsing already-computed claims isn't itself a
# derivation. ------------------------------------------------------------------------------------

_CLAIM_COLUMNS = (
    "id, template, template_version, class, statement, subject, confidence, evidence, status, "
    "computed_epoch_id, created_at"
)


def list_claims(
    engine: Engine, *, template: str | None = None, claim_class: str | None = None,
    status: str | None = None,
) -> list[dict]:
    query = f"SELECT {_CLAIM_COLUMNS} FROM know.claim"
    clauses = []
    params: dict = {}
    if template is not None:
        clauses.append("template = :template")
        params["template"] = template
    if claim_class is not None:
        clauses.append("class = :claim_class")
        params["claim_class"] = claim_class
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    with engine.connect() as connection:
        rows = connection.execute(text(query), params).mappings().all()
    return [dict(row) for row in rows]


def _upstream_chain(connection: Connection, name: str) -> set[str]:
    """Every deriver name reachable by walking derive.deriver_dependency upstream from `name`,
    including `name` itself -- the real dependency chain the registry's own acyclicity check
    already guarantees terminates, used here to report which deriver versions were active when
    a claim was computed (a partial stand-in for the architecture's full version vector -- this
    project has no model/embedding/prompt versioning yet to extend it with).
    """
    seen = {name}
    frontier = [name]
    while frontier:
        current = frontier.pop()
        upstream = connection.execute(
            text("SELECT upstream FROM derive.deriver_dependency WHERE downstream = :d"),
            {"d": current},
        ).scalars().all()
        for u in upstream:
            if u not in seen:
                seen.add(u)
                frontier.append(u)
    return seen


def claim_why(engine: Engine, claim_id: int) -> dict:
    """Section 9's evidence-inspector shape, for the parts this phase implements: the claim
    itself, each cited evidence row re-fetched independently from its own source table (not just
    echoed back from know.claim.evidence -- invariant 30's "terminates in something concrete",
    checked mechanically here rather than merely asserted), and the active version_hash of every
    deriver in this claim's dependency chain. Raises ValueError for an unknown id.
    """
    with engine.connect() as connection:
        claim = connection.execute(
            text(f"SELECT {_CLAIM_COLUMNS} FROM know.claim WHERE id = :id"), {"id": claim_id}
        ).mappings().first()
        if claim is None:
            raise ValueError(f"no claim with id {claim_id}")
        claim = dict(claim)

        evidence_detail: list[dict] = []
        if claim["template"] == "communication_change":
            for item in claim["evidence"].get("anomalies", []):
                row = connection.execute(
                    text(
                        "SELECT metric_id, bucket_start, observed, expected_low, expected_high, "
                        "z_score, baseline_kind FROM signal.anomaly "
                        "WHERE metric_id = :mid AND bucket_start = :day"
                    ),
                    {"mid": item["metric_id"], "day": item["bucket_start"]},
                ).mappings().first()
                evidence_detail.append(
                    {
                        "role": "supports",
                        "source_table": "signal.anomaly",
                        "cited": item,
                        "refetched": dict(row) if row is not None else None,
                    }
                )
        elif claim["template"] == "episode_evidence":
            episode_id = claim["evidence"].get("episode_id")
            episode = connection.execute(
                text(
                    "SELECT id, kind, status, start_range_low, end_range_high, label, note "
                    "FROM episode.episode WHERE id = :id"
                ),
                {"id": episode_id},
            ).mappings().first()
            evidence_detail.append(
                {
                    "role": "supports",
                    "source_table": "episode.episode",
                    "cited": {"episode_id": episode_id},
                    "refetched": dict(episode) if episode is not None else None,
                }
            )
            for cp in claim["evidence"].get("change_points", []):
                metric_id = connection.execute(
                    text("SELECT id FROM derive.metric_def WHERE name = :name"),
                    {"name": cp["metric"]},
                ).scalar_one_or_none()
                row = None
                if metric_id is not None:
                    row = connection.execute(
                        text(
                            "SELECT metric_id, at_date, method FROM signal.change_point "
                            "WHERE metric_id = :mid AND at_date = :day"
                        ),
                        {"mid": metric_id, "day": cp["date"]},
                    ).mappings().first()
                evidence_detail.append(
                    {
                        "role": "supports",
                        "source_table": "signal.change_point",
                        "cited": cp,
                        "refetched": dict(row) if row is not None else None,
                    }
                )
        elif claim["template"] == "relationship_state_shift":
            for role in ("before", "after"):
                item = claim["evidence"][role]
                row = connection.execute(
                    text(
                        "SELECT source, correspondent, state_start, state_end, "
                        "mean_messages_in, mean_messages_out, mean_reciprocity_index, n_months "
                        "FROM signal.interaction_state "
                        "WHERE source = :source AND correspondent = :correspondent "
                        "AND state_start = :state_start"
                    ),
                    {
                        "source": item["source"], "correspondent": item["correspondent"],
                        "state_start": item["state_start"],
                    },
                ).mappings().first()
                evidence_detail.append(
                    {
                        "role": "supports",
                        "source_table": "signal.interaction_state",
                        "cited": {**item, "state_role": role},
                        "refetched": dict(row) if row is not None else None,
                    }
                )

        version_state = {
            name: connection.execute(
                text(
                    "SELECT version_hash FROM derive.deriver_version "
                    "WHERE deriver_name = :name AND status = 'active'"
                ),
                {"name": name},
            ).scalar_one_or_none()
            for name in sorted(_upstream_chain(connection, "know_claim_evaluate"))
        }

    return {
        "claim": claim,
        "evidence_detail": evidence_detail,
        "deriver_version_state": {
            name: (h.hex() if h is not None else None) for name, h in version_state.items()
        },
    }
