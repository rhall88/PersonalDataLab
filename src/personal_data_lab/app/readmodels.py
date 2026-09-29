"""Read models behind the product API. Every function here takes validated arguments and runs
fixed, parameterized SQL -- nothing is built from client-supplied text except bound parameters.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections import defaultdict
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..derive.derivers.interaction import _facebook_owner_identity
from ..identity_contacts import contact_proposals
from ..identity_normalize import ROLE_LABELS, normalize_name

RELATIONSHIP_SOURCES = ("facebook", "imessages")


class NotFound(ValueError):
    pass


# --- time windows ---------------------------------------------------------------------------


def month_floor(d: date) -> date:
    return date(d.year, d.month, 1)


def add_months(d: date, months: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def default_window(today: date | None = None, *, years: int = 3) -> tuple[date, date]:
    """The trailing window of complete months: the current month is still in progress, so its
    counts would read as a false decline."""
    end = month_floor(today or datetime.now(UTC).date())
    return add_months(end, -12 * years), end


def months_between(start: date, end: date) -> list[date]:
    out, cursor = [], month_floor(start)
    while cursor < end:
        out.append(cursor)
        cursor = add_months(cursor, 1)
    return out


# --- relationship keys ----------------------------------------------------------------------


def person_key(person_id: int) -> str:
    return f"p.{person_id}"


def identity_key(source: str, value: str) -> str:
    raw = json.dumps([source, value], ensure_ascii=False).encode()
    return "i." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_key(key: str) -> tuple[str, int | tuple[str, str]]:
    try:
        if key.startswith("p."):
            return "person", int(key[2:])
        if key.startswith("i."):
            payload = key[2:] + "=" * (-len(key[2:]) % 4)
            source, value = json.loads(base64.urlsafe_b64decode(payload))
            if source not in RELATIONSHIP_SOURCES or not isinstance(value, str):
                raise ValueError
            return "identity", (source, value)
    except (ValueError, json.JSONDecodeError, TypeError) as error:
        raise NotFound(f"unknown relationship key {key!r}") from error
    raise NotFound(f"unknown relationship key {key!r}")


def key_identities(engine: Engine, key: str) -> tuple[str, list[tuple[str, str]], str | None]:
    """(label, [(source, correspondent)], person_id-or-None) for a relationship key."""
    kind, ref = parse_key(key)
    with engine.connect() as connection:
        if kind == "person":
            person = connection.execute(
                text("SELECT display_name FROM people WHERE id = :id"), {"id": ref}
            ).scalar_one_or_none()
            if person is None:
                raise NotFound(f"no person {ref}")
            idents = [
                (s, v)
                for s, v in connection.execute(
                    text(
                        "SELECT source, identity_value FROM identity_links WHERE person_id = :id "
                        "AND lifecycle_state = 'active' ORDER BY source, identity_value"
                    ),
                    {"id": ref},
                )
                if s in RELATIONSHIP_SOURCES
            ]
            return person, idents, ref
    source, value = ref
    return value, [(source, value)], None


# --- contact hints (cached: parsing every contact card costs ~1s) ---------------------------

_hint_lock = threading.Lock()
_hint_cache: dict = {"at": 0.0, "hints": {}}


def contact_hints(engine: Engine, *, max_age: float = 300.0) -> dict[tuple[str, str], str]:
    with _hint_lock:
        if time.monotonic() - _hint_cache["at"] > max_age:
            hints: dict[tuple[str, str], str] = {}
            for proposal in contact_proposals(engine):
                for match in proposal.matches:
                    hints[(match.source, match.identity_value)] = proposal.display_name
            _hint_cache.update(at=time.monotonic(), hints=hints)
        return _hint_cache["hints"]


def invalidate_contact_hints() -> None:
    with _hint_lock:
        _hint_cache["at"] = 0.0


# --- status / coverage / extraction health --------------------------------------------------


_status_cache: dict = {"at": 0.0, "value": None}


def status(engine: Engine, *, max_age: float = 30.0) -> dict:
    """Corpus status, cached briefly: the counts scan the largest tables."""
    with _hint_lock:
        if _status_cache["value"] is not None and time.monotonic() - _status_cache["at"] < max_age:
            return _status_cache["value"]
    value = _status(engine)
    with _hint_lock:
        _status_cache.update(at=time.monotonic(), value=value)
    return value


def invalidate_status() -> None:
    with _hint_lock:
        _status_cache["value"] = None


def _status(engine: Engine) -> dict:
    with engine.connect() as c:
        scalar = lambda sql: c.execute(text(sql)).scalar_one()
        counts = {
            "events": scalar("SELECT count(*) FROM events"),
            "records": scalar("SELECT count(*) FROM records"),
            "conversations": scalar("SELECT count(*) FROM conversations"),
            "assets": scalar("SELECT count(*) FROM assets"),
            "text_units": scalar("SELECT count(*) FROM semantic.text_unit"),
            "document_text_units": scalar("SELECT count(*) FROM semantic.text_unit_document"),
            "claims_active": scalar("SELECT count(*) FROM know.claim WHERE status = 'active'"),
            "people": scalar("SELECT count(*) FROM people"),
            "identity_links": scalar(
                "SELECT count(*) FROM identity_links WHERE lifecycle_state = 'active'"
            ),
            "aliases": scalar("SELECT count(*) FROM identity.alias WHERE lifecycle_state = 'active'"),
            "topic_clusters": scalar(
                "SELECT count(DISTINCT a.cluster_index) FROM semantic.topic_cluster_assignment a "
                "JOIN derive.deriver_version v ON v.id = a.deriver_version_id AND v.status = 'active' "
                "WHERE a.cluster_index >= 0"
            ),
            "person_link_candidates": scalar(
                "SELECT count(*) FROM entity.link_candidate WHERE status = 'candidate' "
                "AND candidate_person_id IS NOT NULL"
            ),
            "person_links_confirmed": scalar(
                "SELECT count(*) FROM entity.person_link WHERE lifecycle_state = 'active'"
            ),
        }
        extraction = dict(
            c.execute(
                text("SELECT extraction_status, count(*) FROM generative.extraction GROUP BY 1")
            ).all()
        )
        sources = [
            dict(r)
            for r in c.execute(
                text(
                    "SELECT source, count(*) AS events, min(occurred_at) AS first_at, "
                    "max(occurred_at) AS last_at, count(*) FILTER (WHERE occurred_at IS NULL) "
                    "AS undated FROM events GROUP BY source ORDER BY count(*) DESC"
                )
            ).mappings()
        ]
        epoch = c.execute(
            text("SELECT id, closed_at FROM derive.evidence_epoch ORDER BY id DESC LIMIT 1")
        ).mappings().first()
        derive_health = dict(
            c.execute(
                text(
                    "SELECT ps.state, count(*) FROM derive.partition_state ps "
                    "JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id "
                    "AND dv.status = 'active' GROUP BY 1"
                )
            ).all()
        )
        schema_version = scalar("SELECT max(version) FROM schema_migrations")
    warnings = []
    if extraction.get("truncated"):
        warnings.append(
            f"{extraction['truncated']} text units have an incomplete (truncated) life-graph "
            "extraction; they are excluded from entity/event/intent results."
        )
    if counts["person_link_candidates"]:
        warnings.append(
            f"{counts['person_link_candidates']} name mentions are proposed but not yet confirmed "
            "as people; event and decision threads need confirmed people (People view)."
        )
    not_clean = {k: v for k, v in derive_health.items() if k != "clean"}
    if not_clean:
        warnings.append(f"Derived data is not fully up to date: {not_clean} partitions.")
    return {
        "schema_version": schema_version,
        "counts": counts,
        "extraction": extraction,
        "sources": sources,
        "epoch": dict(epoch) if epoch else None,
        "derive_health": derive_health,
        "warnings": warnings,
    }


def coverage(engine: Engine, *, start: date | None = None, end: date | None = None) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT source, object_kind, count(*) AS days,
                       avg(completeness) AS mean_completeness,
                       count(*) FILTER (WHERE completeness IS NOT NULL AND completeness < 0.5)
                         AS low_days,
                       min(bucket_start) AS first_day, max(bucket_start) AS last_day
                FROM measure.coverage
                WHERE (CAST(:start AS date) IS NULL OR bucket_start >= :start)
                  AND (CAST(:end AS date) IS NULL OR bucket_start < :end)
                GROUP BY 1, 2 ORDER BY 1, 2
                """
            ),
            {"start": start, "end": end},
        ).mappings()
        return [
            {**dict(r), "mean_completeness": float(r["mean_completeness"]) if r["mean_completeness"] is not None else None}
            for r in rows
        ]


def extraction_health(engine: Engine) -> dict:
    fields = ("interaction_primary", "salience", "event_type", "decision_state", "open_loop_state")
    with engine.connect() as c:
        by_status = dict(
            c.execute(text("SELECT extraction_status, count(*) FROM generative.extraction GROUP BY 1")).all()
        )
        distributions = {
            f: [
                {"value": v, "count": n}
                for v, n in c.execute(
                    text(
                        f"SELECT coalesce({f}, '(none)'), count(*) FROM generative.extraction "
                        "WHERE extraction_status = 'complete' GROUP BY 1 ORDER BY 2 DESC"
                    )
                )
            ]
            for f in fields
        }
        mentions = dict(c.execute(text("SELECT kind, count(*) FROM entity.mention GROUP BY 1")).all())
    return {
        "by_status": by_status,
        "distributions": distributions,
        "mentions_by_kind": mentions,
        "epistemic_class": "SEMANTIC",
        "notes": [
            "Extraction labels are model output (SEMANTIC), not observed facts.",
            ("Event, decision, and open-loop labels are noisy: most units get some event type "
            "(largely 'other'), about half are marked as an 'opened' loop, and ~40 percent as a "
            "decision 'considering'. Treat them as candidates for review."),
            "Truncated units hit the generation cap; every model field is empty for them.",
        ],
    }


# --- relationships --------------------------------------------------------------------------


def _direction(recent: int, prior: int) -> str:
    if recent + prior < 12:
        return "low_volume"
    change = (recent - prior) / max(prior, 1)
    if change >= 0.5 and recent - prior >= 6:
        return "increased"
    if change <= -0.5 and prior - recent >= 6:
        return "decreased"
    return "stable"


def _comparison_windows(start: date, end: date) -> tuple[date, date]:
    """(split, prior_start): recent = [split, end), prior = [prior_start, split)."""
    months = len(months_between(start, end))
    half = min(6, max(1, months // 2))
    split = add_months(end, -half)
    return split, add_months(split, -half)


def relationships(
    engine: Engine, *, start: date, end: date, source: str | None = None, limit: int = 200
) -> dict:
    if start >= end:
        raise ValueError("'from' must be before 'to'")
    split, prior_start = _comparison_windows(start, end)
    months = months_between(start, end)
    with engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT d.source, d.correspondent, d.bucket_start, d.messages_in, d.messages_out,
                       d.initiation_share, il.person_id, p.display_name
                FROM measure.interaction_dynamics d
                LEFT JOIN identity_links il ON il.source = d.source
                     AND il.identity_value = d.correspondent AND il.lifecycle_state = 'active'
                LEFT JOIN people p ON p.id = il.person_id
                WHERE d.bucket_start >= :start AND d.bucket_start < :end
                  AND (CAST(:source AS text) IS NULL OR d.source = :source)
                """
            ),
            {"start": start, "end": end, "source": source},
        ).mappings().all()
        states = c.execute(
            text(
                """
                SELECT id, source, correspondent, state_start, state_end, mean_messages_in,
                       mean_messages_out, mean_reciprocity_index, n_months
                FROM signal.interaction_state
                WHERE state_start < :end AND (state_end IS NULL OR state_end >= :start)
                """
            ),
            {"start": start, "end": end},
        ).mappings().all()
        claims = c.execute(
            text(
                "SELECT id, statement, class, confidence, subject, evidence FROM know.claim "
                "WHERE template = 'relationship_state_shift' AND status = 'active'"
            )
        ).mappings().all()
    hints = contact_hints(engine)

    groups: dict[str, dict] = {}
    for r in rows:
        key = person_key(r["person_id"]) if r["person_id"] else identity_key(r["source"], r["correspondent"])
        g = groups.setdefault(
            key,
            {
                "key": key,
                "label": r["display_name"] or r["correspondent"],
                "person_id": r["person_id"],
                "identities": set(),
                "sources": set(),
                "in": 0,
                "out": 0,
                "by_month": defaultdict(int),
                "init_weighted": 0.0,
                "init_weight": 0,
            },
        )
        g["identities"].add((r["source"], r["correspondent"]))
        g["sources"].add(r["source"])
        n = r["messages_in"] + r["messages_out"]
        g["in"] += r["messages_in"]
        g["out"] += r["messages_out"]
        g["by_month"][r["bucket_start"]] += n
        if r["initiation_share"] is not None and n:
            g["init_weighted"] += float(r["initiation_share"]) * n
            g["init_weight"] += n

    states_by_ident = defaultdict(list)
    for s in states:
        states_by_ident[(s["source"], s["correspondent"])].append(dict(s))
    claims_by_ident = defaultdict(list)
    for cl in claims:
        subject = cl["subject"] or {}
        # A state-shift claim belongs to the window only if its transition happened in it.
        shifted = ((cl["evidence"] or {}).get("after") or {}).get("state_start")
        if not shifted or not (start <= date.fromisoformat(str(shifted)[:10]) < end):
            continue
        claims_by_ident[(subject.get("source"), subject.get("correspondent"))].append(
            {"id": cl["id"], "statement": cl["statement"], "class": cl["class"]}
        )

    names_by_source: dict[str, set[str]] = defaultdict(set)
    items = []
    for g in groups.values():
        total = g["in"] + g["out"]
        recent = sum(v for m, v in g["by_month"].items() if m >= split)
        prior = sum(v for m, v in g["by_month"].items() if prior_start <= m < split)
        hint = None
        if g["person_id"] is None:
            (ident,) = g["identities"]
            hint = hints.get(ident)
        name = normalize_name(g["label"] if g["person_id"] else (hint or g["label"]))
        for s in g["sources"]:
            names_by_source[name].add(s)
        items.append(
            {
                "key": g["key"],
                "label": g["label"],
                "person_id": g["person_id"],
                "identity_status": "resolved" if g["person_id"] else "unresolved",
                "contact_hint": hint,
                "sources": sorted(g["sources"]),
                "identities": [{"source": s, "value": v} for s, v in sorted(g["identities"])],
                "messages": total,
                "messages_in": g["in"],
                "messages_out": g["out"],
                "reciprocity": round(min(g["in"], g["out"]) / max(g["in"], g["out"]), 3)
                if max(g["in"], g["out"])
                else None,
                "initiation_share": round(g["init_weighted"] / g["init_weight"], 3)
                if g["init_weight"]
                else None,
                "recent_messages": recent,
                "prior_messages": prior,
                "direction": _direction(recent, prior),
                "monthly": [g["by_month"].get(m, 0) for m in months],
                "states": [st for ident in g["identities"] for st in states_by_ident.get(ident, [])],
                "claims": [cl for ident in g["identities"] for cl in claims_by_ident.get(ident, [])],
                "_name": name,
            }
        )
    for item in items:
        other_sources = names_by_source[item.pop("_name")] - set(item["sources"])
        item["possible_other_source"] = sorted(other_sources) if item["person_id"] is None and other_sources else []
    items.sort(key=lambda i: -i["messages"])
    totals = {
        "active_correspondents": len(items),
        "measurable_shifts": sum(i["direction"] in ("increased", "decreased") for i in items),
        "by_direction": {
            d: sum(i["direction"] == d for i in items)
            for d in ("increased", "decreased", "stable", "low_volume")
        },
        "state_transitions": sum(len(i["states"]) for i in items),
        "unresolved_identities": sum(i["identity_status"] == "unresolved" for i in items),
        "messages": sum(i["messages"] for i in items),
        "messages_resolved": sum(i["messages"] for i in items if i["person_id"]),
    }
    return {
        "window": {"from": start, "to": end, "recent_from": split, "prior_from": prior_start},
        "months": months,
        "totals": totals,
        "rows": items[:limit],
        "truncated_rows": max(0, len(items) - limit),
    }


def relationship_timeline(engine: Engine, key: str, *, start: date, end: date) -> dict:
    label, idents, person_id = key_identities(engine, key)
    months = months_between(start, end)
    series: dict[str, dict] = {}
    with engine.connect() as c:
        for source, value in idents:
            rows = c.execute(
                text(
                    "SELECT bucket_start, messages_in, messages_out, reciprocity_index, "
                    "initiation_share, median_response_latency_seconds "
                    "FROM measure.interaction_dynamics WHERE source = :s AND correspondent = :v "
                    "AND bucket_start >= :start AND bucket_start < :end"
                ),
                {"s": source, "v": value, "start": start, "end": end},
            ).mappings().all()
            by_month = {r["bucket_start"]: r for r in rows}
            series[source] = {
                "in": [by_month[m]["messages_in"] if m in by_month else 0 for m in months],
                "out": [by_month[m]["messages_out"] if m in by_month else 0 for m in months],
            }
    return {"key": key, "label": label, "person_id": person_id, "months": months, "series": series}


def _owner_for(source: str, connection) -> str | None:
    return _facebook_owner_identity(connection) if source == "facebook" else None


def relationship_messages(
    engine: Engine, key: str, *, start: date, end: date, cursor: int = 0, limit: int = 50,
    include_text: bool = True,
) -> dict:
    """Messages in the 1:1 threads behind a relationship -- the same thread rule the interaction
    metrics use (exactly one non-owner correspondent), newest first."""
    label, idents, _ = key_identities(engine, key)
    collected = []
    total = 0
    with engine.connect() as c:
        for source, value in idents:
            owner = _owner_for(source, c)
            params = {
                "source": source, "value": value, "owner": owner, "start": start, "end": end,
                "roles": list(ROLE_LABELS), "take": cursor + limit,
                "message_types": ["message.incoming", "message.outgoing"]
                if source == "imessages" else ["message"],
            }
            conv_sql = """
                WITH conv AS (
                    SELECT conversation_id FROM events
                    WHERE source = :source AND conversation_id IS NOT NULL
                      AND lifecycle_state = 'active'
                      AND ((:source = 'imessages' AND event_type = 'message.incoming')
                        OR (:source = 'facebook' AND event_type = 'message'
                            AND actor_identity IS DISTINCT FROM :owner
                            AND lower(coalesce(actor_identity, '')) <> ALL(:roles)))
                    GROUP BY conversation_id
                    HAVING count(DISTINCT actor_identity) = 1 AND min(actor_identity) = :value
                )
            """
            total += c.execute(
                text(
                    conv_sql
                    + "SELECT count(*) FROM events e JOIN conv USING (conversation_id) "
                    "WHERE e.source = :source AND e.lifecycle_state = 'active' "
                    "AND e.event_type = ANY(:message_types) "
                    "AND e.occurred_at >= :start AND e.occurred_at < :end"
                ),
                params,
            ).scalar_one()
            rows = c.execute(
                text(
                    conv_sql
                    + """
                    SELECT e.id, e.source, e.occurred_at, e.event_type, e.actor_identity,
                           e.conversation_id, left(e.text, 2000) AS text
                    FROM events e JOIN conv USING (conversation_id)
                    WHERE e.source = :source AND e.lifecycle_state = 'active'
                      AND e.event_type = ANY(:message_types)
                      AND e.occurred_at >= :start AND e.occurred_at < :end
                    ORDER BY e.occurred_at DESC, e.id DESC
                    LIMIT :take
                    """
                ),
                params,
            ).mappings().all()
            for r in rows:
                if source == "imessages":
                    direction = "in" if r["event_type"] == "message.incoming" else "out"
                else:
                    direction = "out" if r["actor_identity"] == owner else "in"
                collected.append({**dict(r), "direction": direction, "kind": "event"})
    collected.sort(key=lambda r: (r["occurred_at"], r["id"]), reverse=True)
    page = collected[cursor : cursor + limit]
    if not include_text:
        for r in page:
            r["text"] = None
    next_cursor = cursor + limit if cursor + limit < total else None
    return {"label": label, "total": total, "items": page, "next_cursor": next_cursor}


# --- canonical evidence ---------------------------------------------------------------------

_CANONICAL = {
    "event": (
        "SELECT id, source, event_type, occurred_at, actor_identity, actor_person_id, "
        "conversation_id, text, metadata, source_record_namespace, source_record_id, "
        "canonical_run_id, lifecycle_state FROM events WHERE id = :id"
    ),
    "record": (
        "SELECT id, source, record_type, title, text, occurred_at, created_at, actor_identity, "
        "metadata, source_record_namespace, source_record_id, canonical_run_id, lifecycle_state "
        "FROM records WHERE id = :id"
    ),
    "conversation": (
        "SELECT id, source, title, started_at, ended_at, metadata, source_record_namespace, "
        "source_conversation_id, canonical_run_id, lifecycle_state FROM conversations WHERE id = :id"
    ),
    "asset": (
        "SELECT id, source, filename, mime_type, original_path, size_bytes, captured_at, "
        "created_at, metadata, asset_namespace, source_asset_id, canonical_run_id, "
        "lifecycle_state FROM assets WHERE id = :id"
    ),
}


def canonical(engine: Engine, kind: str, object_id: int) -> dict:
    if kind not in _CANONICAL:
        raise NotFound(f"unknown kind {kind!r}")
    with engine.connect() as c:
        row = c.execute(text(_CANONICAL[kind]), {"id": object_id}).mappings().first()
        if row is None:
            raise NotFound(f"no {kind} {object_id}")
        out = dict(row)
        meta = json.dumps(out.get("metadata"), default=str, ensure_ascii=False)
        out["metadata"] = json.loads(meta) if len(meta) <= 20000 else {"_truncated": meta[:20000]}
        if kind == "conversation":
            out["event_count"] = c.execute(
                text("SELECT count(*) FROM events WHERE conversation_id = :id"), {"id": object_id}
            ).scalar_one()
        out["kind"] = kind
        out["epistemic_class"] = "OBSERVED"
    return out


def conversation_events(engine: Engine, conversation_id: int, *, cursor: int = 0, limit: int = 100) -> dict:
    with engine.connect() as c:
        total = c.execute(
            text("SELECT count(*) FROM events WHERE conversation_id = :id"), {"id": conversation_id}
        ).scalar_one()
        rows = c.execute(
            text(
                "SELECT id, occurred_at, event_type, actor_identity, left(text, 2000) AS text "
                "FROM events WHERE conversation_id = :id ORDER BY occurred_at NULLS LAST, id "
                "OFFSET :cursor LIMIT :limit"
            ),
            {"id": conversation_id, "cursor": cursor, "limit": limit},
        ).mappings().all()
    return {
        "total": total,
        "items": [{**dict(r), "kind": "event"} for r in rows],
        "next_cursor": cursor + limit if cursor + limit < total else None,
    }


def text_unit(engine: Engine, text_unit_id: int) -> dict:
    with engine.connect() as c:
        unit = c.execute(
            text(
                "SELECT id, source, conversation_id, event_ids, started_at, ended_at, text, "
                "truncated FROM semantic.text_unit WHERE id = :id"
            ),
            {"id": text_unit_id},
        ).mappings().first()
        if unit is None:
            raise NotFound(f"no text unit {text_unit_id}")
        extraction = c.execute(
            text("SELECT * FROM generative.extraction WHERE text_unit_id = :id"), {"id": text_unit_id}
        ).mappings().first()
        topic = c.execute(
            text(
                "SELECT a.cluster_index, a.cluster_label FROM semantic.topic_cluster_assignment a "
                "JOIN derive.deriver_version v ON v.id = a.deriver_version_id AND v.status = 'active' "
                "WHERE a.text_unit_id = :id"
            ),
            {"id": text_unit_id},
        ).mappings().first()
        mentions = c.execute(
            text(
                "SELECT m.id, m.surface_text, m.kind, rm.resolved_kind, rm.resolved_id "
                "FROM entity.mention m LEFT JOIN entity.resolved_mention rm ON rm.mention_id = m.id "
                "WHERE m.text_unit_id = :id ORDER BY m.slot_index"
            ),
            {"id": text_unit_id},
        ).mappings().all()
    ext = dict(extraction) if extraction else None
    if ext:
        ext.pop("computed_epoch_id", None)
        ext.pop("created_at", None)
    return {
        **dict(unit),
        "kind": "text_unit",
        "epistemic_class": "OBSERVED",
        "extraction": ext,
        "extraction_class": "SEMANTIC",
        "topic": dict(topic) if topic else None,
        "mentions": [dict(m) for m in mentions],
    }


# --- people / identity ----------------------------------------------------------------------


def people(engine: Engine) -> list[dict]:
    with engine.connect() as c:
        persons = c.execute(text("SELECT id, display_name, created_at FROM people ORDER BY id")).mappings().all()
        links = c.execute(
            text(
                "SELECT il.person_id, il.source, il.identity_value, il.tier, count(e.id) AS events "
                "FROM identity_links il LEFT JOIN events e ON e.source = il.source "
                "AND e.actor_identity = il.identity_value WHERE il.lifecycle_state = 'active' "
                "GROUP BY 1, 2, 3, 4"
            )
        ).mappings().all()
        aliases = c.execute(
            text("SELECT person_id, alias FROM identity.alias WHERE lifecycle_state = 'active' ORDER BY alias")
        ).mappings().all()
        mention_counts = dict(
            c.execute(
                text(
                    "SELECT candidate_person_id, count(*) FROM entity.link_candidate "
                    "WHERE status = 'candidate' AND candidate_person_id IS NOT NULL GROUP BY 1"
                )
            ).all()
        )
        confirmed = dict(
            c.execute(
                text("SELECT person_id, count(*) FROM entity.person_link WHERE lifecycle_state = 'active' GROUP BY 1")
            ).all()
        )
    by_person_links = defaultdict(list)
    for link in links:
        by_person_links[link["person_id"]].append(
            {k: link[k] for k in ("source", "identity_value", "tier", "events")}
        )
    by_person_aliases = defaultdict(list)
    for a in aliases:
        by_person_aliases[a["person_id"]].append(a["alias"])
    out = []
    for p in persons:
        idents = by_person_links[p["id"]]
        out.append(
            {
                "id": p["id"],
                "display_name": p["display_name"],
                "key": person_key(p["id"]),
                "identities": sorted(idents, key=lambda i: -i["events"]),
                "aliases": by_person_aliases[p["id"]],
                "events": sum(i["events"] for i in idents),
                "mention_candidates": mention_counts.get(p["id"], 0),
                "mentions_confirmed": confirmed.get(p["id"], 0),
                "epistemic_class": "DECLARED",
            }
        )
    out.sort(key=lambda p: -p["events"])
    return out


def identity_review_queue(engine: Engine, *, limit: int = 100) -> dict:
    """Contact proposals with anything still unlinked, then active unresolved correspondents with
    no contact card at all -- both ranked by message volume."""
    proposals = []
    for p in contact_proposals(engine):
        unlinked = [m for m in p.matches if m.linked_person_id is None]
        if not unlinked:
            continue
        proposals.append(
            {
                "key": p.key,
                "display_name": p.display_name,
                "aliases": list(p.aliases),
                "total_events": p.total_events,
                "existing_person_ids": list(p.existing_person_ids),
                "matches": [
                    {
                        "source": m.source, "identity_value": m.identity_value, "match": m.match,
                        "events": m.events, "linked_person_id": m.linked_person_id,
                    }
                    for m in p.matches
                ],
            }
        )
    hinted = {(m["source"], m["identity_value"]) for p in proposals for m in p["matches"]}
    with engine.connect() as c:
        raw = c.execute(
            text(
                """
                SELECT d.source, d.correspondent, sum(d.messages_in + d.messages_out) AS messages,
                       max(d.bucket_start) AS last_month
                FROM measure.interaction_dynamics d
                WHERE NOT EXISTS (SELECT 1 FROM identity_links il WHERE il.source = d.source
                      AND il.identity_value = d.correspondent AND il.lifecycle_state = 'active')
                GROUP BY 1, 2 ORDER BY 3 DESC
                """
            )
        ).mappings().all()
    unresolved = [
        {**dict(r), "key": identity_key(r["source"], r["correspondent"])}
        for r in raw
        if (r["source"], r["correspondent"]) not in hinted
    ]
    return {"contact_proposals": proposals[:limit], "unresolved": unresolved[:limit]}


def person_mention_summary(engine: Engine, person_id: int) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT m.surface_text, t.source, count(*) AS mentions
                FROM entity.link_candidate lc
                JOIN entity.mention m ON m.id = lc.mention_id
                JOIN semantic.text_unit t ON t.id = m.text_unit_id
                WHERE lc.status = 'candidate' AND lc.candidate_person_id = :pid
                GROUP BY 1, 2 ORDER BY 3 DESC
                """
            ),
            {"pid": person_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def person_mention_samples(engine: Engine, person_id: int, surface_text: str, *, limit: int = 8) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT t.id AS text_unit_id, t.source, t.started_at, left(t.text, 400) AS text
                FROM entity.link_candidate lc
                JOIN entity.mention m ON m.id = lc.mention_id
                JOIN semantic.text_unit t ON t.id = m.text_unit_id
                WHERE lc.status = 'candidate' AND lc.candidate_person_id = :pid
                  AND m.surface_text = :surface
                ORDER BY t.started_at DESC NULLS LAST LIMIT :limit
                """
            ),
            {"pid": person_id, "surface": surface_text, "limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


# --- topics ---------------------------------------------------------------------------------

_ACTIVE_TOPIC_VERSION = (
    "(SELECT id FROM derive.deriver_version WHERE deriver_name = 'semantic_topic_cluster' "
    "AND status = 'active')"
)


def topics(engine: Engine, *, min_size: int = 0, include_noise: bool = False) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                f"""
                SELECT a.cluster_index, a.cluster_label, count(*) AS size,
                       min(t.started_at) AS first_at, max(t.started_at) AS last_at,
                       array_agg(DISTINCT t.source) AS sources
                FROM semantic.topic_cluster_assignment a
                JOIN semantic.text_unit t ON t.id = a.text_unit_id
                WHERE a.deriver_version_id = {_ACTIVE_TOPIC_VERSION}
                  AND (:include_noise OR a.cluster_index >= 0)
                GROUP BY 1, 2 HAVING count(*) >= :min_size ORDER BY count(*) DESC
                """
            ),
            {"min_size": min_size, "include_noise": include_noise},
        ).mappings().all()
    return [{**dict(r), "epistemic_class": "STATISTICAL"} for r in rows]


def topic_detail(engine: Engine, cluster_index: int, *, sample_size: int = 12) -> dict:
    with engine.connect() as c:
        summary = c.execute(
            text(
                f"SELECT cluster_label, count(*) AS size FROM semantic.topic_cluster_assignment "
                f"WHERE deriver_version_id = {_ACTIVE_TOPIC_VERSION} AND cluster_index = :ci "
                "GROUP BY 1"
            ),
            {"ci": cluster_index},
        ).mappings().first()
        if summary is None:
            raise NotFound(f"no topic {cluster_index}")
        sources = dict(
            c.execute(
                text(
                    f"SELECT t.source, count(*) FROM semantic.topic_cluster_assignment a "
                    "JOIN semantic.text_unit t ON t.id = a.text_unit_id "
                    f"WHERE a.deriver_version_id = {_ACTIVE_TOPIC_VERSION} AND a.cluster_index = :ci "
                    "GROUP BY 1 ORDER BY 2 DESC"
                ),
                {"ci": cluster_index},
            ).all()
        )
        by_month = [
            {"month": m, "count": n}
            for m, n in c.execute(
                text(
                    "SELECT date_trunc('month', t.started_at)::date, count(*) "
                    "FROM semantic.topic_cluster_assignment a "
                    "JOIN semantic.text_unit t ON t.id = a.text_unit_id "
                    f"WHERE a.deriver_version_id = {_ACTIVE_TOPIC_VERSION} AND a.cluster_index = :ci "
                    "AND t.started_at IS NOT NULL GROUP BY 1 ORDER BY 1"
                ),
                {"ci": cluster_index},
            )
        ]
    samples = topic_members(engine, cluster_index, limit=sample_size)["items"]
    return {
        "cluster_index": cluster_index,
        "cluster_label": summary["cluster_label"],
        "size": summary["size"],
        "sources": sources,
        "by_month": by_month,
        "samples": samples,
        "epistemic_class": "STATISTICAL",
        "note": "Labels are machine-generated keywords, not topic names. Samples are the units "
        "nearest the cluster centre.",
    }


def topic_members(engine: Engine, cluster_index: int, *, cursor: int = 0, limit: int = 50) -> dict:
    with engine.connect() as c:
        total = c.execute(
            text(
                f"SELECT count(*) FROM semantic.topic_cluster_assignment "
                f"WHERE deriver_version_id = {_ACTIVE_TOPIC_VERSION} AND cluster_index = :ci"
            ),
            {"ci": cluster_index},
        ).scalar_one()
        rows = c.execute(
            text(
                f"""
                SELECT t.id, t.source, t.started_at, left(t.text, 600) AS text,
                       a.distance_to_centroid
                FROM semantic.topic_cluster_assignment a
                JOIN semantic.text_unit t ON t.id = a.text_unit_id
                WHERE a.deriver_version_id = {_ACTIVE_TOPIC_VERSION} AND a.cluster_index = :ci
                ORDER BY a.distance_to_centroid NULLS LAST, t.id OFFSET :cursor LIMIT :limit
                """
            ),
            {"ci": cluster_index, "cursor": cursor, "limit": limit},
        ).mappings().all()
    return {
        "total": total,
        "items": [{**dict(r), "kind": "text_unit"} for r in rows],
        "next_cursor": cursor + limit if cursor + limit < total else None,
    }


def text_units_by_ids(engine: Engine, ids: list[int]) -> dict:
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, source, started_at, left(text, 600) AS text FROM semantic.text_unit "
                "WHERE id = ANY(:ids) ORDER BY started_at DESC NULLS LAST"
            ),
            {"ids": ids},
        ).mappings().all()
    return {"total": len(rows), "items": [{**dict(r), "kind": "text_unit"} for r in rows], "next_cursor": None}
