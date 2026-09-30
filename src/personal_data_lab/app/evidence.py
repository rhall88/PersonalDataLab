"""Evidence query handles: an opaque, URL-safe token naming one allow-listed resolver plus its
validated parameters. A handle never carries SQL; resolving an unknown or malformed one fails.
Aggregates (a relationship, a topic, a search) carry a handle plus a count instead of thousands
of ids.
"""

from __future__ import annotations

import base64
import json
from datetime import date

from sqlalchemy.engine import Engine

from ..derive.derivers.claims import claim_why
from ..derive.derivers.semantic import search as semantic_search
from ..search import ALL_KINDS, search_canonical
from . import readmodels as rm


class BadHandle(ValueError):
    pass


def make_handle(resolver: str, params: dict) -> str:
    raw = json.dumps({"r": resolver, "p": params}, sort_keys=True, default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_handle(handle: str) -> tuple[str, dict]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(handle + "=" * (-len(handle) % 4)))
        resolver, params = payload["r"], payload["p"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise BadHandle("malformed evidence handle") from error
    if resolver not in RESOLVERS or not isinstance(params, dict):
        raise BadHandle(f"unknown evidence resolver {resolver!r}")
    return resolver, params


def evidence_ref(resolver: str, params: dict, count: int) -> dict:
    return {"type": "query", "query_handle": make_handle(resolver, params), "count": count}


def _d(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _relationship_messages(engine, p, cursor, limit, include_text):
    page = rm.relationship_messages(
        engine, p["key"], start=_d(p["from"]), end=_d(p["to"]), cursor=cursor, limit=limit,
        include_text=include_text,
    )
    return {
        "title": f"1:1 messages with {page['label']}",
        "epistemic_class": "OBSERVED",
        **page,
    }


def _topic_members(engine, p, cursor, limit, include_text):
    page = rm.topic_members(engine, int(p["cluster_index"]), cursor=cursor, limit=limit)
    if not include_text:
        for item in page["items"]:
            item["text"] = None
    return {"title": f"Text units in topic {p['cluster_index']}", "epistemic_class": "OBSERVED", **page}


def _claim_why(engine, p, cursor, limit, include_text):
    why = claim_why(engine, int(p["claim_id"]))
    return {
        "title": f"Why claim {p['claim_id']}",
        "epistemic_class": why["claim"]["class"],
        "total": len(why["evidence_detail"]),
        "items": [{"kind": "claim_evidence", **e} for e in why["evidence_detail"]],
        "why": why,
        "next_cursor": None,
    }


def _full_text(engine, p, cursor, limit, include_text):
    kinds = tuple(k for k in p.get("kinds") or ALL_KINDS if k in ALL_KINDS) or ALL_KINDS
    results = search_canonical(
        engine, p["query"], kinds=kinds, sources=tuple(p.get("sources") or ()), limit=100
    )
    items = [
        {
            "kind": h.kind, "id": h.id, "source": h.source, "title": h.title,
            "text": h.snippet if include_text else None, "occurred_at": h.occurred_at,
            "rank": h.rank,
        }
        for h in results.hits
    ]
    return {
        "title": f"Full-text matches for “{p['query']}”",
        "epistemic_class": "OBSERVED",
        "total": len(items),
        "items": items[cursor : cursor + limit],
        "next_cursor": cursor + limit if cursor + limit < len(items) else None,
    }


def _semantic(engine, p, cursor, limit, include_text):
    rows = semantic_search(
        engine, p["query"], limit=50, include_documents=bool(p.get("include_documents"))
    )
    items = [
        {
            "kind": "text_unit" if r["kind"] == "message" else "document_text_unit",
            "id": r["id"], "source": r["source"], "occurred_at": r.get("started_at"),
            "record_id": r.get("record_id"), "asset_id": r.get("asset_id"), "text": (r["text"] or "")[:600] if include_text else None,
            "distance": float(r["distance"]),
        }
        for r in rows
    ]
    return {
        "title": f"Semantic lookup for “{p['query']}” (lookup results, not findings)",
        "epistemic_class": "OBSERVED",
        "total": len(items),
        "items": items[cursor : cursor + limit],
        "next_cursor": cursor + limit if cursor + limit < len(items) else None,
    }


def _text_units(engine, p, cursor, limit, include_text):
    page = rm.text_units_by_ids(engine, [int(i) for i in p["ids"]][:200])
    if not include_text:
        for item in page["items"]:
            item["text"] = None
    return {"title": "Text units", "epistemic_class": "OBSERVED", **page}


def _relationship_metrics(engine, p, cursor, limit, include_text):
    timeline = rm.relationship_timeline(engine, p["key"], start=_d(p["from"]), end=_d(p["to"]))
    items = []
    for source, series in timeline["series"].items():
        for month, n_in, n_out in zip(timeline["months"], series["in"], series["out"], strict=True):
            if n_in or n_out:
                items.append(
                    {"kind": "metric", "source": source, "month": month, "messages_in": n_in,
                     "messages_out": n_out}
                )
    return {
        "title": f"Monthly interaction metrics with {timeline['label']}",
        "epistemic_class": "COMPUTED",
        "total": len(items),
        "items": items[cursor : cursor + limit],
        "next_cursor": cursor + limit if cursor + limit < len(items) else None,
    }


RESOLVERS = {
    "relationship_messages": _relationship_messages,
    "relationship_metrics": _relationship_metrics,
    "topic_members": _topic_members,
    "claim_why": _claim_why,
    "full_text": _full_text,
    "semantic": _semantic,
    "text_units": _text_units,
}


def resolve(engine: Engine, handle: str, *, cursor: int = 0, limit: int = 50, include_text: bool = True) -> dict:
    resolver, params = parse_handle(handle)
    if cursor < 0 or not 1 <= limit <= 200:
        raise BadHandle("cursor must be >= 0 and limit between 1 and 200")
    try:
        page = RESOLVERS[resolver](engine, params, cursor, limit, include_text)
    except (KeyError, TypeError) as error:
        raise BadHandle(f"evidence handle is missing a parameter: {error}") from error
    return {"handle": handle, "resolver": resolver, **page}
