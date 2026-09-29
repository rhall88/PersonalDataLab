"""UI v1 product API (src/personal_data_lab/app): security guard, relationship read model, evidence
handles, guided agent, answer validation, run supersession, investigations, and `serve` binding."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from personal_data_lab import cli
from personal_data_lab.app import agent, investigations
from personal_data_lab.app import readmodels as rm
from personal_data_lab.app.evidence import BadHandle, resolve
from personal_data_lab.app.server import create_app
from personal_data_lab.database import create_database_engine
from personal_data_lab.identity import create_person

pytestmark = pytest.mark.integration

WINDOW = (date(2024, 1, 1), date(2025, 1, 1))


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_database_engine(url)
    with engine.connect() as connection:
        name = str(connection.execute(text("SELECT current_database()")).scalar_one())
    if not name.endswith("_test"):
        engine.dispose()
        pytest.fail("integration tests require a database name ending in '_test'")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as c:
        c.execute(text(
            "TRUNCATE TABLE measure.interaction_dynamics, signal.interaction_state, know.claim, "
            "app.investigation, identity_links, people, events, records, conversations "
            "RESTART IDENTITY CASCADE"
        ))
    rm.invalidate_contact_hints()
    rm.invalidate_status()
    yield


def _dynamics(engine: Engine, source: str, correspondent: str, months: dict[date, tuple[int, int]]) -> None:
    with engine.begin() as c:
        for month, (n_in, n_out) in months.items():
            c.execute(
                text(
                    "INSERT INTO measure.interaction_dynamics (source, correspondent, bucket_start, "
                    "messages_in, messages_out, n_conversations, reciprocity_index, initiation_share, "
                    "median_response_latency_seconds) VALUES (:s, :c, :m, :i, :o, 1, 0.5, 0.5, 60)"
                ),
                {"s": source, "c": correspondent, "m": month, "i": n_in, "o": n_out},
            )


def _link(engine: Engine, person_id: int, source: str, value: str) -> None:
    with engine.begin() as c:
        c.execute(
            text("INSERT INTO identity_links (person_id, source, identity_value, tier) VALUES (:p, :s, :v, 'declared')"),
            {"p": person_id, "s": source, "v": value},
        )


def _months(start: date, n: int, value: tuple[int, int]) -> dict[date, tuple[int, int]]:
    return {rm.add_months(start, i): value for i in range(n)}


# --- security -------------------------------------------------------------------------------


def test_guard_requires_host_session_and_write_header(database_engine: Engine) -> None:
    client = TestClient(create_app(database_engine, allowed_hosts={"testserver"}, static_dir=None))
    assert client.get("/api/v1/status").status_code == 401
    assert client.get("/api/v1/status", headers={"host": "evil.example"}).status_code == 400
    client.get("/api/v1/session")
    assert client.get("/api/v1/status").status_code == 200
    assert client.post("/api/v1/people", json={"display_name": "X"}).status_code == 403
    ok = client.post("/api/v1/people", json={"display_name": "X"}, headers={"X-PDL-Request": "1"})
    assert ok.status_code == 200
    assert "default-src 'self'" in ok.headers["content-security-policy"]


def test_serve_refuses_non_loopback_without_allow_network() -> None:
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "8799"]) == 2


# --- inputs ---------------------------------------------------------------------------------


def test_tool_inputs_reject_bad_ranges_and_limits(database_engine: Engine) -> None:
    with pytest.raises(ValueError):
        agent.run_tool(database_engine, "relationship_change_overview", {"from": "2025-01-01", "to": "2024-01-01"})
    with pytest.raises(ValueError):
        agent.run_tool(database_engine, "relationship_change_overview", {"limit": 100000})
    with pytest.raises(ValueError):
        agent.run_tool(database_engine, "no_such_tool", {})


# --- relationships --------------------------------------------------------------------------


def test_relationships_use_only_the_requested_window(database_engine: Engine) -> None:
    _dynamics(database_engine, "imessages", "+13175550101", {date(2023, 6, 1): (500, 500), date(2024, 3, 1): (5, 7)})
    data = rm.relationships(database_engine, start=WINDOW[0], end=WINDOW[1])
    (row,) = data["rows"]
    assert row["messages"] == 12
    assert sum(row["monthly"]) == 12
    assert data["totals"]["messages"] == 12


def test_raw_correspondents_never_merge_without_a_confirmed_link(database_engine: Engine) -> None:
    _dynamics(database_engine, "imessages", "+13175550101", _months(date(2024, 1, 1), 12, (10, 10)))
    _dynamics(database_engine, "facebook", "Jane Doe", _months(date(2024, 1, 1), 12, (3, 3)))
    rows = rm.relationships(database_engine, start=WINDOW[0], end=WINDOW[1])["rows"]
    assert len(rows) == 2 and all(r["identity_status"] == "unresolved" for r in rows)

    person = create_person(database_engine, "Jane Doe")
    _link(database_engine, person.id, "imessages", "+13175550101")
    _link(database_engine, person.id, "facebook", "Jane Doe")
    (merged,) = rm.relationships(database_engine, start=WINDOW[0], end=WINDOW[1])["rows"]
    assert merged["key"] == rm.person_key(person.id)
    assert merged["sources"] == ["facebook", "imessages"]
    assert merged["messages"] == 12 * 20 + 12 * 6


def test_direction_compares_last_six_months_with_six_before(database_engine: Engine) -> None:
    months = {**_months(date(2024, 1, 1), 6, (20, 20)), **_months(date(2024, 7, 1), 6, (2, 2))}
    _dynamics(database_engine, "imessages", "+13175550101", months)
    (row,) = rm.relationships(database_engine, start=WINDOW[0], end=WINDOW[1])["rows"]
    assert (row["prior_messages"], row["recent_messages"], row["direction"]) == (240, 24, "decreased")


# --- evidence & answers ---------------------------------------------------------------------


def test_every_guided_finding_has_resolvable_evidence(database_engine: Engine) -> None:
    _dynamics(database_engine, "imessages", "+13175550101", _months(date(2024, 1, 1), 12, (10, 10)))
    plan = agent.guided_plan("How have my relationships changed over the past 1 year?")
    assert plan.calls[0]["tool"] == "relationship_change_overview"
    plan.calls[0]["args"].update({"from": WINDOW[0].isoformat(), "to": WINDOW[1].isoformat()})
    results = agent.execute_calls(database_engine, plan.calls)
    insight = agent.build_insight("q", plan, results, engine=database_engine, mode="guided", model=None, run_id="t")
    assert insight["findings"]
    assert agent.validate_answer(insight["summary"], insight["findings"]) == []
    for finding in insight["findings"]:
        page = resolve(database_engine, finding["evidence"]["query_handle"], limit=5)
        assert page["total"] is not None
    with pytest.raises(BadHandle):
        resolve(database_engine, "not-a-handle")


def test_causal_and_invented_domain_language_is_rejected() -> None:
    assert agent.guided_plan("What led me to be where I am today?").unsupported["code"] == "unsupported_question"
    assert agent.guided_plan("Show the Social, Professional, and Love aspects of my life").unsupported["code"] == "unsupported_domain"
    ev = {"type": "query", "query_handle": "x", "count": 1}
    assert agent.validate_answer("Moving caused the drop.", [])
    assert agent.validate_answer("ok", [{"text": "Her love life domain grew.", "evidence": ev}])
    assert agent.validate_answer("ok", [{"text": "A finding with no evidence.", "evidence": None}])
    assert agent.validate_answer("Messages fell from 20 to 2.", [{"text": "Fell.", "evidence": ev}]) == []


def test_truncated_extraction_produces_a_visible_warning(database_engine: Engine) -> None:
    with database_engine.connect() as c:
        truncated = c.execute(text(
            "SELECT count(*) FROM generative.extraction WHERE extraction_status = 'truncated'")).scalar_one()
    warnings, _ = agent.status_warnings(database_engine)
    assert bool(truncated) == any("truncated" in w for w in warnings)


def test_a_newer_run_supersedes_an_older_one(database_engine: Engine) -> None:
    async def scenario():
        manager = agent.RunManager(database_engine)
        first = await manager.start("Show my stored claims", {}, False)
        second = await manager.start("What topics did I talk about most?", {}, False)
        for task in list(manager._tasks.values()):
            await task
        return first, second, manager.latest_id

    first, second, latest = asyncio.run(scenario())
    assert latest == second.id
    assert first.superseded and not second.superseded
    assert second.status == "complete"


# --- investigations -------------------------------------------------------------------------


def test_refresh_reports_drift_and_keeps_the_saved_revision(database_engine: Engine) -> None:
    _dynamics(database_engine, "imessages", "+13175550101", _months(date(2024, 1, 1), 12, (10, 10)))
    plan = agent.Plan("guided", "relationship_change", [{
        "tool": "relationship_change_overview", "args": {"from": WINDOW[0].isoformat(), "to": WINDOW[1].isoformat()},
    }])
    results = agent.execute_calls(database_engine, plan.calls)
    insight = agent.build_insight("How have my relationships changed?", plan, results, engine=database_engine,
                                  mode="guided", model=None, run_id="t")
    saved = investigations.create(database_engine, "Relationships", insight)

    unchanged = investigations.refresh(database_engine, saved["id"])
    assert unchanged["diff"]["unchanged"], unchanged["diff"]

    _dynamics(database_engine, "facebook", "New Friend", _months(date(2024, 1, 1), 12, (4, 4)))
    drifted = investigations.refresh(database_engine, saved["id"])
    assert not drifted["diff"]["unchanged"]
    assert drifted["diff"]["objects_added"]
    reopened = investigations.get(database_engine, saved["id"])
    assert len(reopened["revisions"]) == 1
    assert reopened["revisions"][0]["insight"]["summary"] == insight["summary"]

    investigations.save_revision(database_engine, saved["id"], drifted["current"])
    assert [r["revision"] for r in investigations.get(database_engine, saved["id"])["revisions"]] == [1, 2]


def test_diff_distinguishes_findings_that_share_an_object() -> None:
    snap = {
        "objects": {"p.4": {"label": "Sarah", "messages": 10}},
        "evidence": [
            {"key": "h-messages", "finding": "Sarah: 10 messages", "object_key": "p.4", "total": 10, "first_ids": [["event", 1]]},
            {"key": "h-claim", "finding": "A claim about Sarah", "object_key": "p.4", "total": 2, "first_ids": []},
        ],
    }
    versions = {"derivers": {"x": "a"}}
    insight = {"answer_mode": "relationship_change", "views": [{"type": "relationship_table"}]}
    assert investigations.diff(snap, versions, insight, snap, versions, insight)["unchanged"]
