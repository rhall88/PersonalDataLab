"""Saved investigations. A revision stores the whole answer plus an evidence snapshot, because
derived tables are rewritten on recompute; refreshing re-runs the saved plan and diffs it against
the latest revision instead of silently replacing it."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from . import agent
from .evidence import resolve
from .readmodels import NotFound

_VERSIONED = (
    "measure_interaction_dynamics", "signal_interaction_state", "know_claim_evaluate",
    "semantic_topic_cluster", "generative_extract_text_unit", "entity_resolve_mentions",
)


def _versions(engine: Engine, insight: dict) -> dict:
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT deriver_name, encode(version_hash, 'hex') FROM derive.deriver_version "
                "WHERE status = 'active' AND deriver_name = ANY(:names)"
            ),
            {"names": list(_VERSIONED)},
        ).all()
    return {
        "derivers": dict(rows), "tool_version": insight.get("tool_version"),
        "mode": insight.get("mode"), "model": insight.get("model"),
    }


def _snapshot(engine: Engine, insight: dict) -> dict:
    objects = {}
    for view in insight.get("views", []):
        data = view.get("data") or {}
        for row in data.get("rows", []) if isinstance(data, dict) else []:
            objects[row["key"]] = {
                "label": row["label"], "messages": row["messages"], "direction": row["direction"],
                "identity_status": row["identity_status"],
            }
        for t in data.get("topics", []) if isinstance(data, dict) else []:
            objects[f"topic.{t['cluster_index']}"] = {"label": t["cluster_label"], "size": t["size"]}
        for cl in data.get("claims", []) if isinstance(data, dict) else []:
            objects[f"claim.{cl['id']}"] = {"statement": cl["statement"]}
    evidence = []
    for f in insight.get("findings", []):
        ev = f.get("evidence") or {}
        # The evidence handle is deterministic (resolver + params), so it identifies "the same
        # evidence" across a refresh even when two findings share an object.
        entry = {
            "key": ev.get("query_handle") or f["text"][:200], "finding": f["text"][:200],
            "object_key": f.get("object_key"), "count": ev.get("count"),
        }
        if ev.get("query_handle"):
            try:
                page = resolve(engine, ev["query_handle"], limit=25, include_text=False)
                entry["first_ids"] = [
                    [i.get("kind"), i.get("id")] for i in page["items"] if i.get("id") is not None
                ]
                entry["total"] = page.get("total")
            except Exception as error:  # noqa: BLE001 -- a snapshot records what it can
                entry["error"] = str(error)[:200]
        evidence.append(entry)
    return {"objects": objects, "evidence": evidence}


def list_investigations(engine: Engine) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT i.id, i.title, i.question, i.created_at, i.updated_at, max(r.revision) AS revisions "
                "FROM app.investigation i LEFT JOIN app.investigation_revision r ON r.investigation_id = i.id "
                "GROUP BY i.id ORDER BY i.updated_at DESC"
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def _insert_revision(c, investigation_id: int, revision: int, insight: dict, snapshot: dict, versions: dict) -> None:
    c.execute(
        text(
            "INSERT INTO app.investigation_revision (investigation_id, revision, plan, insight, "
            "evidence_snapshot, versions, epoch_id) VALUES (:iid, :rev, CAST(:plan AS jsonb), "
            "CAST(:insight AS jsonb), CAST(:snap AS jsonb), CAST(:ver AS jsonb), :epoch)"
        ),
        {
            "iid": investigation_id, "rev": revision, "plan": json.dumps(insight.get("plan", [])),
            "insight": json.dumps(insight, default=str), "snap": json.dumps(snapshot, default=str),
            "ver": json.dumps(versions), "epoch": insight.get("computed_epoch_id"),
        },
    )


def create(engine: Engine, title: str, insight: dict) -> dict:
    if not title.strip():
        raise ValueError("title is required")
    snapshot, versions = _snapshot(engine, insight), _versions(engine, insight)
    with engine.begin() as c:
        iid = c.execute(
            text("INSERT INTO app.investigation (title, question) VALUES (:t, :q) RETURNING id"),
            {"t": title.strip()[:200], "q": insight.get("question", "")},
        ).scalar_one()
        _insert_revision(c, iid, 1, insight, snapshot, versions)
    return get(engine, iid)


def get(engine: Engine, investigation_id: int) -> dict:
    with engine.connect() as c:
        inv = c.execute(
            text("SELECT id, title, question, created_at, updated_at FROM app.investigation WHERE id = :id"),
            {"id": investigation_id},
        ).mappings().first()
        if inv is None:
            raise NotFound(f"no investigation {investigation_id}")
        revs = c.execute(
            text(
                "SELECT revision, insight, evidence_snapshot, versions, epoch_id, created_at "
                "FROM app.investigation_revision WHERE investigation_id = :id ORDER BY revision"
            ),
            {"id": investigation_id},
        ).mappings().all()
    return {**dict(inv), "revisions": [dict(r) for r in revs]}


def refresh(engine: Engine, investigation_id: int) -> dict:
    inv = get(engine, investigation_id)
    latest = inv["revisions"][-1]
    saved = latest["insight"]
    plan = agent.guided_plan(saved["question"]) if not saved.get("plan") else agent.Plan(
        "guided", saved.get("answer_mode", ""), saved["plan"]
    )
    if plan.unsupported:
        current = agent.build_insight(saved["question"], plan, {}, engine=engine, mode="guided", model=None,
                                      run_id=f"refresh-{investigation_id}")
    else:
        results = agent.execute_calls(engine, plan.calls)
        current = agent.build_insight(saved["question"], plan, results, engine=engine, mode="guided",
                                      model=None, run_id=f"refresh-{investigation_id}")
    current["revision"] = latest["revision"] + 1
    snapshot, versions = _snapshot(engine, current), _versions(engine, current)
    return {
        "investigation_id": investigation_id,
        "based_on_revision": latest["revision"],
        "current": current,
        "current_snapshot": snapshot,
        "current_versions": versions,
        "diff": diff(latest["evidence_snapshot"], latest["versions"], saved, snapshot, versions, current),
    }


def save_revision(engine: Engine, investigation_id: int, insight: dict) -> dict:
    inv = get(engine, investigation_id)
    revision = inv["revisions"][-1]["revision"] + 1
    insight = {**insight, "revision": revision}
    with engine.begin() as c:
        _insert_revision(c, investigation_id, revision, insight, _snapshot(engine, insight), _versions(engine, insight))
        c.execute(text("UPDATE app.investigation SET updated_at = :now WHERE id = :id"),
                  {"now": datetime.now(UTC), "id": investigation_id})
    return get(engine, investigation_id)


def diff(old_snap: dict, old_versions: dict, old_insight: dict, new_snap: dict, new_versions: dict,
         new_insight: dict) -> dict:
    old_objs, new_objs = old_snap.get("objects", {}), new_snap.get("objects", {})
    added = sorted(set(new_objs) - set(old_objs))
    removed = sorted(set(old_objs) - set(new_objs))
    changed = [
        {"key": k, "before": old_objs[k], "after": new_objs[k]}
        for k in sorted(set(old_objs) & set(new_objs)) if old_objs[k] != new_objs[k]
    ]
    old_ev = {e.get("key") or e["finding"]: e for e in old_snap.get("evidence", [])}
    evidence_changes = []
    for e in new_snap.get("evidence", []):
        k = e.get("key") or e["finding"]
        before = old_ev.get(k)
        if before is None:
            evidence_changes.append({"key": e["finding"], "change": "new_finding", "total": e.get("total")})
            continue
        b_ids = {tuple(x) for x in before.get("first_ids", [])}
        a_ids = {tuple(x) for x in e.get("first_ids", [])}
        if before.get("total") != e.get("total") or b_ids != a_ids:
            evidence_changes.append({
                "key": e["finding"], "change": "evidence_changed", "total_before": before.get("total"),
                "total_after": e.get("total"), "ids_added": len(a_ids - b_ids), "ids_removed": len(b_ids - a_ids),
            })
    derivation_changed = {
        name: {"before": old_versions.get("derivers", {}).get(name), "after": h}
        for name, h in new_versions.get("derivers", {}).items()
        if old_versions.get("derivers", {}).get(name) != h
    }
    shape_changed = (old_insight.get("answer_mode") != new_insight.get("answer_mode")
                     or [v["type"] for v in old_insight.get("views", [])] != [v["type"] for v in new_insight.get("views", [])])
    unchanged = not (added or removed or changed or evidence_changes or derivation_changed or shape_changed)
    return {
        "unchanged": unchanged, "objects_added": added, "objects_removed": removed, "objects_changed": changed,
        "evidence_changes": evidence_changes, "derivation_changed": derivation_changed,
        "answer_shape_changed": shape_changed,
    }
