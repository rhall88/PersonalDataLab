"""The local product API (`personal-data-lab serve`). Sync routes run in FastAPI's threadpool;
agent runs are asyncio tasks streamed over SSE."""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.engine import Engine

from .. import identity as identity_api
from ..derive import rebuild as derive_rebuild
from ..derive import run as derive_run
from ..derive.derivers.claims import claim_why, list_claims
from ..derive.derivers.entity import confirm_person_mentions
from ..identity_contacts import add_alias, confirm_contact_proposal
from . import agent, investigations
from . import readmodels as rm
from .evidence import BadHandle, resolve
from .security import LocalGuard

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def _error(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": {}, "retryable": retryable}},
        status_code=status,
    )


class RunIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    context: dict = Field(default_factory=dict)
    include_message_text: bool = False


class FullTextSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    kinds: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)


class SemanticSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    include_documents: bool = True
    limit: int = Field(default=25, ge=1, le=50)


class PersonIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class IdentityIn(BaseModel):
    source: str = Field(min_length=1, max_length=64)
    identity_value: str = Field(min_length=1, max_length=500)


class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=200)


class NotSameAsIn(IdentityIn):
    person_id: int | None = None
    note: str | None = Field(default=None, max_length=500)


class ContactConfirmIn(BaseModel):
    person_id: int | None = None
    exclude: list[IdentityIn] = Field(default_factory=list)


class MentionConfirmIn(BaseModel):
    surface_texts: list[str] | None = None
    exclude_sources: list[str] | None = None


class InvestigationIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    insight: dict


class RevisionIn(BaseModel):
    insight: dict


def _window(from_: date | None, to: date | None) -> tuple[date, date]:
    start, end = rm.default_window()
    start, end = from_ or start, to or end
    if start >= end:
        raise ValueError("'from' must be before 'to'")
    return start, end


def create_app(engine: Engine, *, allowed_hosts: set[str], static_dir: Path | None = WEB_DIST) -> FastAPI:
    app = FastAPI(title="PersonalDataLab", docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json")
    app.add_middleware(LocalGuard, allowed_hosts=allowed_hosts)
    runs = agent.RunManager(engine)
    maintenance = {"state": "idle", "detail": None}

    @app.exception_handler(rm.NotFound)
    async def _not_found(_: Request, error: rm.NotFound):
        return _error(404, "not_found", str(error))

    @app.exception_handler(BadHandle)
    async def _bad_handle(_: Request, error: BadHandle):
        return _error(400, "bad_evidence_handle", str(error))

    @app.exception_handler(ValidationError)
    async def _invalid(_: Request, error: ValidationError):
        return _error(400, "invalid_input", str(error)[:500])

    @app.exception_handler(ValueError)
    async def _value(_: Request, error: ValueError):
        return _error(400, "invalid_request", str(error)[:500])

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, error: Exception):
        return _error(500, "internal_error", f"{type(error).__name__}", retryable=True)

    api = "/api/v1"

    # --- system ---
    @app.get(f"{api}/status")
    def status():
        out = rm.status(engine)
        cfg = agent.model_config()
        out["agent"] = {"mode": "agent" if cfg else "guided", "model": cfg["model"] if cfg else None}
        out["maintenance"] = maintenance
        return out

    @app.get(f"{api}/coverage")
    def coverage(from_: date | None = Query(None, alias="from"), to: date | None = None):
        return rm.coverage(engine, start=from_, end=to)

    @app.get(f"{api}/extraction-health")
    def extraction_health():
        return rm.extraction_health(engine)

    # --- agent ---
    @app.post(f"{api}/agent/runs")
    async def start_run(body: RunIn):
        run = await runs.start(body.question, body.context, body.include_message_text)
        return run.summary()

    @app.get(f"{api}/agent/runs/{{run_id}}")
    async def get_run(run_id: str):
        try:
            return runs.get(run_id).summary()
        except KeyError:
            raise rm.NotFound(f"no run {run_id}") from None

    @app.get(f"{api}/agent/runs/{{run_id}}/events", response_class=EventSourceResponse)
    async def run_events(run_id: str, request: Request):
        try:
            runs.get(run_id)
        except KeyError:
            raise rm.NotFound(f"no run {run_id}") from None
        after = int(request.headers.get("last-event-id", "-1") or -1)
        async for event in runs.stream(run_id, after):
            if event is None:
                yield ServerSentEvent(comment="keep-alive")
            else:
                yield ServerSentEvent(data=event, event=event["type"], id=str(event["seq"]))

    @app.post(f"{api}/agent/runs/{{run_id}}/cancel")
    async def cancel_run(run_id: str):
        try:
            return (await runs.cancel(run_id)).summary()
        except KeyError:
            raise rm.NotFound(f"no run {run_id}") from None

    # --- relationships ---
    @app.get(f"{api}/relationships")
    def relationships(
        from_: date | None = Query(None, alias="from"), to: date | None = None,
        source: str | None = Query(None, pattern="^(facebook|imessages)$"), limit: int = Query(200, ge=1, le=1000),
    ):
        start, end = _window(from_, to)
        result = agent.run_tool(engine, "relationship_change_overview",
                                {"from": start, "to": end, "source": source, "limit": min(limit, 200)})
        return result["data"]

    @app.get(f"{api}/relationships/{{key}}")
    def relationship(key: str, from_: date | None = Query(None, alias="from"), to: date | None = None):
        start, end = _window(from_, to)
        return agent.run_tool(engine, "relationship_detail", {"key": key, "from": start, "to": end})

    @app.get(f"{api}/relationships/{{key}}/timeline")
    def relationship_timeline(key: str, from_: date | None = Query(None, alias="from"), to: date | None = None):
        start, end = _window(from_, to)
        return rm.relationship_timeline(engine, key, start=start, end=end)

    @app.get(f"{api}/relationships/{{key}}/evidence")
    def relationship_evidence(
        key: str, from_: date | None = Query(None, alias="from"), to: date | None = None,
        cursor: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    ):
        start, end = _window(from_, to)
        return rm.relationship_messages(engine, key, start=start, end=end, cursor=cursor, limit=limit)

    # --- topics ---
    @app.get(f"{api}/topics")
    def topics(min_size: int = Query(0, ge=0), include_noise: bool = False):
        return agent.run_tool(engine, "list_topics",
                              {"min_size": min_size, "include_noise": include_noise, "limit": 400})["data"]

    @app.get(f"{api}/topics/{{cluster_index}}")
    def topic(cluster_index: int, sample_size: int = Query(12, ge=1, le=50)):
        detail = rm.topic_detail(engine, cluster_index, sample_size=sample_size)
        detail["evidence"] = agent.evidence_ref("topic_members", {"cluster_index": cluster_index}, detail["size"])
        return detail

    # --- claims ---
    @app.get(f"{api}/claims")
    def claims(
        template: str | None = None, claim_class: str | None = Query(None, alias="class"),
        status: str | None = "active", limit: int = Query(100, ge=1, le=1000), cursor: int = Query(0, ge=0),
    ):
        rows = list_claims(engine, template=template, claim_class=claim_class, status=status or None)
        page = rows[cursor : cursor + limit]
        for r in page:
            r["evidence"] = agent.evidence_ref("claim_why", {"claim_id": r["id"]}, 1)
        templates = sorted({r["template"] for r in rows})
        return {"total": len(rows), "items": page, "templates": templates,
                "next_cursor": cursor + limit if cursor + limit < len(rows) else None}

    @app.get(f"{api}/claims/{{claim_id}}")
    def claim(claim_id: int):
        return claim_why(engine, claim_id)["claim"]

    @app.get(f"{api}/claims/{{claim_id}}/why")
    def claim_why_route(claim_id: int):
        return claim_why(engine, claim_id)

    # --- search & evidence ---
    @app.post(f"{api}/search/full-text")
    def full_text(body: FullTextSearchIn):
        result = agent.run_tool(engine, "full_text_search",
                                {"query": body.query, "sources": body.sources, "limit": min(body.limit, 100)})
        hits = [h for h in result["data"]["hits"] if not body.kinds or h["kind"] in body.kinds]
        return {"query": body.query, "hits": hits, "evidence": result["evidence"], "epistemic_class": "OBSERVED"}

    @app.post(f"{api}/search/semantic")
    def semantic(body: SemanticSearchIn):
        result = agent.run_tool(engine, "semantic_search", body.model_dump())
        return {**result["data"], "evidence": result["evidence"], "epistemic_class": "OBSERVED"}

    @app.get(f"{api}/evidence/{{handle}}")
    def evidence(handle: str, cursor: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        return resolve(engine, handle, cursor=cursor, limit=limit)

    @app.get(f"{api}/canonical/{{kind}}/{{object_id}}")
    def canonical(kind: str, object_id: int):
        return rm.canonical(engine, kind, object_id)

    @app.get(f"{api}/canonical/conversation/{{object_id}}/events")
    def conversation_events(object_id: int, cursor: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return rm.conversation_events(engine, object_id, cursor=cursor, limit=limit)

    @app.get(f"{api}/text-units/{{text_unit_id}}")
    def text_unit(text_unit_id: int):
        return rm.text_unit(engine, text_unit_id)

    # --- people & identity ---
    @app.get(f"{api}/people")
    def people():
        return rm.people(engine)

    @app.get(f"{api}/identity/candidates")
    def identity_candidates(limit: int = Query(100, ge=1, le=500)):
        return rm.identity_review_queue(engine, limit=limit)

    @app.post(f"{api}/people")
    def create_person(body: PersonIn):
        person = identity_api.create_person(engine, body.display_name.strip())
        rm.invalidate_status()
        return person.model_dump(mode="json")

    @app.post(f"{api}/people/{{person_id}}/identities")
    def link_identity(person_id: int, body: IdentityIn):
        link = identity_api.merge_identity(engine, person_id, body.source, body.identity_value,
                                           note="linked from the PDL app")
        rm.invalidate_contact_hints()
        rm.invalidate_status()
        return link.model_dump(mode="json")

    @app.post(f"{api}/identity/split")
    def split_identity(body: IdentityIn):
        link = identity_api.split_identity(engine, body.source, body.identity_value)
        rm.invalidate_contact_hints()
        rm.invalidate_status()
        return link.model_dump(mode="json")

    @app.post(f"{api}/people/{{person_id}}/aliases")
    def alias(person_id: int, body: AliasIn):
        return {"person_id": person_id, "alias": body.alias,
                "added": add_alias(engine, person_id, body.alias, evidence={"via": "app"})}

    @app.post(f"{api}/identity/not-same-as")
    def not_same_as(body: NotSameAsIn):
        return identity_api.not_same_as_identity(
            engine, body.source, body.identity_value, person_id=body.person_id, note=body.note
        ).model_dump(mode="json")

    @app.post(f"{api}/identity/contacts/{{key}}/confirm")
    def confirm_contact(key: str, body: ContactConfirmIn = Body(default_factory=ContactConfirmIn)):
        result = confirm_contact_proposal(
            engine, key, person_id=body.person_id,
            exclude=frozenset((e.source, e.identity_value) for e in body.exclude),
        )
        rm.invalidate_contact_hints()
        rm.invalidate_status()
        return result.__dict__

    @app.get(f"{api}/people/{{person_id}}/mentions")
    def person_mentions(person_id: int):
        return rm.person_mention_summary(engine, person_id)

    @app.get(f"{api}/people/{{person_id}}/mentions/samples")
    def person_mention_samples(person_id: int, surface: str = Query(min_length=1, max_length=200)):
        return rm.person_mention_samples(engine, person_id, surface)

    @app.post(f"{api}/people/{{person_id}}/mentions/confirm")
    def confirm_mentions(person_id: int, body: MentionConfirmIn):
        result = confirm_person_mentions(engine, person_id, surface_texts=body.surface_texts,
                                         exclude_sources=body.exclude_sources)
        rm.invalidate_status()
        return result

    @app.post(f"{api}/maintenance/refresh-links")
    def refresh_links():
        """Re-run the name matcher and the event/intent/claim resolvers after identity changes
        (background; seconds). The worker doesn't propagate, so each is rebuilt explicitly."""
        if maintenance["state"] == "running":
            return maintenance

        def work():
            try:
                for name in ("entity_resolve_mentions", "event_resolve_candidates",
                             "intent_resolve_candidates", "know_claim_evaluate", "claim_contradiction_detect"):
                    derive_rebuild(engine, deriver_name=name)
                outcomes = derive_run(engine, include_global=True)
                failed = [o for o in outcomes if o.status == "failed"]
                maintenance.update(state="idle", detail=f"{len(outcomes)} partitions, {len(failed)} failed")
                rm.invalidate_status()
            except Exception as error:  # noqa: BLE001
                maintenance.update(state="failed", detail=str(error)[:300])

        maintenance.update(state="running", detail=None)
        threading.Thread(target=work, daemon=True).start()
        return maintenance

    # --- investigations ---
    @app.get(f"{api}/investigations")
    def list_investigations():
        return investigations.list_investigations(engine)

    @app.post(f"{api}/investigations")
    def create_investigation(body: InvestigationIn):
        return investigations.create(engine, body.title, body.insight)

    @app.get(f"{api}/investigations/{{investigation_id}}")
    def get_investigation(investigation_id: int):
        return investigations.get(engine, investigation_id)

    @app.post(f"{api}/investigations/{{investigation_id}}/revisions")
    def add_revision(investigation_id: int, body: RevisionIn):
        return investigations.save_revision(engine, investigation_id, body.insight)

    @app.post(f"{api}/investigations/{{investigation_id}}/refresh")
    def refresh_investigation(investigation_id: int):
        return investigations.refresh(engine, investigation_id)

    # --- frontend ---
    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return _error(404, "not_found", "no such API route")
            return FileResponse(static_dir / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return JSONResponse({"message": "API is running; build the frontend with "
                                 "`npm --prefix web install && npm --prefix web run build`."})

    return app
