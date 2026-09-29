from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.identity import create_person, get_person, merge_identity
from personal_data_lab.identity_contacts import (
    add_alias,
    confirm_contact_proposal,
    contact_proposals,
)
from personal_data_lab.models import NormalizedEvent, NormalizedRecord, ParsedExport

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_database_engine(test_database_url)
    with engine.connect() as connection:
        database_name = str(connection.execute(text("SELECT current_database()")).scalar_one())
    if not database_name.endswith("_test"):
        engine.dispose()
        pytest.fail("integration tests require a database name ending in '_test'")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    relationship_observations, asset_observations, record_observations,
                    event_observations, conversation_observations, attachment_observations,
                    event_versions, conversation_versions, attachment_versions,
                    asset_versions, record_versions, relationship_versions,
                    import_run_source_files, source_file_locations, attachments,
                    source_file_assets, source_file_relationships, source_file_records,
                    source_file_events, source_file_conversations, events, assets, records,
                    relationships, identity_links, identities, people, conversations,
                    source_files, import_runs
                RESTART IDENTITY CASCADE
                """
            )
        )
    yield


def _import(engine: Engine, source: str, filename: str, **kwargs: object) -> None:
    parsed = ParsedExport(
        source=source,
        parser_version="test-contacts-v1",
        filename=filename,
        original_path=filename,
        sha256=hashlib.sha256(filename.encode()).hexdigest(),
        size_bytes=1,
        **kwargs,
    )
    import_parsed_exports(engine, (parsed,))


def _events(source: str, actor: str, n: int) -> tuple[NormalizedEvent, ...]:
    return tuple(
        NormalizedEvent(
            occurred_at=None,
            source=source,
            event_type="message",
            source_record_namespace=f"{source}/message/event",
            source_record_id=f"{actor}-{i}",
            actor_identity=actor,
            text="hi",
        )
        for i in range(n)
    )


def _google_card(record_id: str, fn: str, tel: str | None = None, email: str | None = None):
    metadata: dict = {"google": {"FN": [{"value": fn, "params": {}}]}}
    if tel:
        metadata["google"]["TEL"] = [{"value": tel, "params": {"type": "cell"}}]
    if email:
        metadata["google"]["EMAIL"] = [{"value": email, "params": {"type": "internet"}}]
    return NormalizedRecord(
        source="google", record_type="contact", source_record_namespace="google/contacts/record",
        source_record_id=record_id, title=fn, metadata=metadata,
    )


def _apple_card(record_id: str, family: str, given: str, tel: str):
    return NormalizedRecord(
        source="apple", record_type="contact", source_record_namespace="apple/contacts/record",
        source_record_id=record_id, title=f"{given} {family}",
        metadata={
            "apple": {
                "properties": {
                    "N": [f"{family};{given};;;"],
                    "FN": [f"{given} {family}"],
                    "ITEM1.TEL": [tel],
                }
            }
        },
    )


def _seed(engine: Engine) -> None:
    _import(
        engine, "google", "contacts.vcf",
        records=(
            _google_card("g-jane", "Jane Doe", tel="(317) 555-0101", email="Jane@Example.com"),
            _google_card("g-alex-1", "Alex Smith", tel="317-555-0202"),
            _google_card("g-alex-2", "Alex Smith", tel="317-555-0303"),
            _google_card("g-bob", "Bob Roe", tel="317-555-0404"),
        ),
    )
    _import(engine, "apple", "contacts-apple.vcf", records=(_apple_card("a-jane", "Doe", "Jane", "+13175550101"),))
    _import(engine, "imessages", "imessages.json", events=_events("imessages", "+13175550101", 5))
    _import(engine, "facebook", "facebook.json", events=_events("facebook", "Jane Doe", 3) + _events("facebook", "Alex Smith", 7))
    _import(engine, "google", "mail.json", events=_events("google", "jane@example.com", 2))


def test_proposals_merge_duplicate_cards_and_match_phone_email_and_name(database_engine: Engine) -> None:
    _seed(database_engine)
    proposals = contact_proposals(database_engine)

    jane = [p for p in proposals if p.display_name == "Jane Doe"]
    assert len(jane) == 1  # the Apple and Google cards share a phone, so they are one contact
    matched = {(m.source, m.identity_value, m.match) for m in jane[0].matches}
    assert matched == {
        ("imessages", "+13175550101", "phone"),
        ("facebook", "Jane Doe", "name"),
        ("google", "jane@example.com", "email"),
    }
    assert jane[0].total_events == 10
    assert len(jane[0].card_record_ids) == 2
    assert {"Jane Doe", "Jane"} <= set(jane[0].aliases)
    # Two different contacts are both named Alex Smith, so the Facebook name matches neither;
    # Bob has a card but no activity.
    assert all(p.display_name not in ("Alex Smith", "Bob Roe") for p in proposals)


def test_confirm_links_every_identity_records_aliases_and_is_idempotent(database_engine: Engine) -> None:
    _seed(database_engine)
    key = next(p.key for p in contact_proposals(database_engine) if p.display_name == "Jane Doe")

    result = confirm_contact_proposal(database_engine, key)
    assert result.created_person
    assert len(result.linked) == 3
    person = get_person(database_engine, result.person_id)
    assert person is not None and person.display_name == "Jane Doe"
    assert {(li.source, li.identity_value) for li in person.linked_identities} == set(result.linked)
    with database_engine.connect() as connection:
        aliases = set(
            connection.execute(
                text("SELECT alias FROM identity.alias WHERE person_id = :id"), {"id": result.person_id}
            ).scalars()
        )
        assertions = connection.execute(
            text("SELECT count(*) FROM identity.assertion WHERE person_id = :id AND action = 'link'"),
            {"id": result.person_id},
        ).scalar_one()
    assert {"Jane Doe", "Jane"} <= aliases
    assert assertions == 3

    again = confirm_contact_proposal(database_engine, key)
    assert again.person_id == result.person_id and not again.created_person
    assert again.linked == () and len(again.already_linked) == 3 and again.aliases_added == ()


def test_confirm_respects_exclude_and_never_relinks_another_persons_identity(database_engine: Engine) -> None:
    _seed(database_engine)
    other = create_person(database_engine, "Someone Else")
    merge_identity(database_engine, other.id, "google", "jane@example.com")
    key = next(p.key for p in contact_proposals(database_engine) if p.display_name == "Jane Doe")

    result = confirm_contact_proposal(
        database_engine, key, exclude=frozenset({("facebook", "Jane Doe")})
    )
    assert result.person_id == other.id  # the one person already linked becomes the target
    assert set(result.linked) == {("imessages", "+13175550101")}
    assert result.already_linked == (("google", "jane@example.com"),)


def test_add_alias_rejects_unknown_person_and_dedupes(database_engine: Engine) -> None:
    person = create_person(database_engine, "Jane Doe")
    assert add_alias(database_engine, person.id, "Janie")
    assert not add_alias(database_engine, person.id, "  JANIE ")
    with pytest.raises(ValueError):
        add_alias(database_engine, person.id + 999, "Nobody")
