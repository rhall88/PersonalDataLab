from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

Kind = Literal["conversation", "event", "record", "asset"]
ALL_KINDS: tuple[Kind, ...] = ("conversation", "event", "record", "asset")


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Kind
    id: int
    source: str
    namespace: str
    identifier: str
    title: str | None
    snippet: str | None
    occurred_at: datetime | None
    lifecycle_state: str
    canonical_run_id: int | None
    rank: float


class SearchResults(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    kinds: tuple[Kind, ...]
    sources: tuple[str, ...]
    include_deleted: bool
    limit: int
    hits: tuple[SearchHit, ...]


# Each leg selects the same column shape so the outer query can UNION ALL and rank together.
# The "content" expression backing snippet/ts_headline is the same field weighted 'A' for that
# table in db/migrations/008_full_text_search.sql, so the highlighted snippet matches the primary
# search field, not an unrelated column.
_LEG_SQL: dict[Kind, str] = {
    "conversation": """
        SELECT 'conversation' AS kind, c.id, c.source,
               c.source_record_namespace AS namespace,
               c.source_conversation_id AS identifier,
               c.title AS title,
               coalesce(c.title, '') AS snippet_source,
               c.started_at AS occurred_at,
               c.lifecycle_state, c.canonical_run_id,
               ts_rank_cd(c.search_vector, q.tsq, 32) AS rank
        FROM conversations c, q
        WHERE c.search_vector @@ q.tsq
          AND (:include_deleted OR c.lifecycle_state = 'active')
          AND (CAST(:sources AS text[]) IS NULL OR c.source = ANY(CAST(:sources AS text[])))
    """,
    "event": """
        SELECT 'event' AS kind, e.id, e.source,
               e.source_record_namespace AS namespace,
               e.source_record_id AS identifier,
               e.event_type AS title,
               coalesce(e.text, '') AS snippet_source,
               e.occurred_at,
               e.lifecycle_state, e.canonical_run_id,
               ts_rank_cd(e.search_vector, q.tsq, 32) AS rank
        FROM events e, q
        WHERE e.search_vector @@ q.tsq
          AND (:include_deleted OR e.lifecycle_state = 'active')
          AND (CAST(:sources AS text[]) IS NULL OR e.source = ANY(CAST(:sources AS text[])))
    """,
    "record": """
        SELECT 'record' AS kind, r.id, r.source,
               r.source_record_namespace AS namespace,
               r.source_record_id AS identifier,
               r.title AS title,
               coalesce(r.text, coalesce(r.title, '')) AS snippet_source,
               r.occurred_at,
               r.lifecycle_state, r.canonical_run_id,
               ts_rank_cd(r.search_vector, q.tsq, 32) AS rank
        FROM records r, q
        WHERE r.search_vector @@ q.tsq
          AND (:include_deleted OR r.lifecycle_state = 'active')
          AND (CAST(:sources AS text[]) IS NULL OR r.source = ANY(CAST(:sources AS text[])))
    """,
    "asset": """
        SELECT 'asset' AS kind, a.id, a.source,
               a.asset_namespace AS namespace,
               a.source_asset_id AS identifier,
               a.filename AS title,
               filename_search_text(a.filename) AS snippet_source,
               coalesce(a.captured_at, a.created_at) AS occurred_at,
               a.lifecycle_state, a.canonical_run_id,
               ts_rank_cd(a.search_vector, q.tsq, 32) AS rank
        FROM assets a, q
        WHERE a.search_vector @@ q.tsq
          AND (:include_deleted OR a.lifecycle_state = 'active')
          AND (CAST(:sources AS text[]) IS NULL OR a.source = ANY(CAST(:sources AS text[])))
    """,
}


def search_canonical(
    engine: Engine,
    query: str,
    *,
    kinds: tuple[Kind, ...] = ALL_KINDS,
    sources: tuple[str, ...] = (),
    limit: int = 25,
    include_deleted: bool = False,
) -> SearchResults:
    """Rank-search canonical conversations/events/records/assets by full text.

    Every hit carries enough provenance (source, namespace, identifier, canonical_run_id) to be
    traced back to its evidence. A blank or all-stopword query safely matches nothing rather than
    raising, because `websearch_to_tsquery` returns an empty tsquery for such input.
    """
    if not kinds:
        raise ValueError("at least one kind must be requested")
    if limit <= 0:
        raise ValueError("limit must be positive")
    legs = " UNION ALL ".join(_LEG_SQL[kind] for kind in kinds)
    statement = text(
        f"""
        WITH q AS (SELECT websearch_to_tsquery('english', :query) AS tsq),
        top AS (
            SELECT * FROM ({legs}) combined
            ORDER BY rank DESC, occurred_at DESC NULLS LAST
            LIMIT :limit
        )
        -- Highlighting is expensive, so only the rows actually returned get a snippet.
        SELECT top.*, ts_headline('english', top.snippet_source, q.tsq) AS snippet
        FROM top, q
        ORDER BY top.rank DESC, top.occurred_at DESC NULLS LAST
        """
    )
    params = {
        "query": query,
        "sources": list(sources) or None,
        "include_deleted": include_deleted,
        "limit": limit,
    }
    with engine.connect() as connection:
        rows = connection.execute(statement, params).mappings().all()
    hits = tuple(
        SearchHit(
            kind=row["kind"],
            id=row["id"],
            source=row["source"],
            namespace=row["namespace"],
            identifier=row["identifier"],
            title=row["title"],
            snippet=row["snippet"] or None,
            occurred_at=row["occurred_at"],
            lifecycle_state=row["lifecycle_state"],
            canonical_run_id=row["canonical_run_id"],
            rank=float(row["rank"]),
        )
        for row in rows
    )
    return SearchResults(
        query=query,
        kinds=kinds,
        sources=sources,
        include_deleted=include_deleted,
        limit=limit,
        hits=hits,
    )
