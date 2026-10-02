"""The phone's photo library: every item becomes a record; items whose original another source already
holds link to that full-size file, the rest to a thumbnail asset."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from test_ios_backup import Backup

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.ios_backup.orchestrator import import_ios_photos
from personal_data_lab.models import NormalizedAsset, ParsedExport

pytestmark = pytest.mark.integration
CAMERA = "CameraRollDomain"
EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


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
        c.execute(text("TRUNCATE TABLE relationships, assets, records, events, conversations, source_files, "
                       "import_runs RESTART IDENTITY CASCADE"))
        c.execute(text("TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                       "derive.evidence_epoch RESTART IDENTITY CASCADE"))
        c.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
    yield


def _library(backup: Backup) -> None:
    taken = (datetime(2025, 6, 1, 12, 0, tzinfo=UTC) - EPOCH).total_seconds()
    asset_cols = ("Z_PK INTEGER PRIMARY KEY, ZUUID TEXT, ZFILENAME TEXT, ZDIRECTORY TEXT, ZDATECREATED REAL, "
                  "ZADDEDDATE REAL, ZKIND INTEGER, ZKINDSUBTYPE INTEGER, ZWIDTH INTEGER, ZHEIGHT INTEGER, "
                  "ZDURATION REAL, ZFAVORITE INTEGER, ZHIDDEN INTEGER, ZTRASHEDSTATE INTEGER, ZLATITUDE REAL, "
                  "ZLONGITUDE REAL, ZCLOUDLOCALSTATE INTEGER, ZMOMENT INTEGER")
    stmts: list[tuple[str, tuple]] = [
        (f"CREATE TABLE ZASSET ({asset_cols})", ()),
        (("CREATE TABLE ZADDITIONALASSETATTRIBUTES (Z_PK INTEGER PRIMARY KEY, ZASSET INTEGER, ZORIGINALFILENAME TEXT, "
          "ZORIGINALFILESIZE INTEGER, ZTIMEZONENAME TEXT, ZIMPORTEDBYDISPLAYNAME TEXT)"), ()),
        ("CREATE TABLE ZASSETDESCRIPTION (Z_PK INTEGER PRIMARY KEY, ZASSETATTRIBUTES INTEGER, ZLONGDESCRIPTION TEXT)", ()),
        ("CREATE TABLE ZMOMENT (Z_PK INTEGER PRIMARY KEY, ZTITLE TEXT)", ()),
        ("CREATE TABLE ZPERSON (Z_PK INTEGER PRIMARY KEY, ZFULLNAME TEXT, ZDISPLAYNAME TEXT)", ()),
        ("CREATE TABLE ZDETECTEDFACE (Z_PK INTEGER PRIMARY KEY, ZASSETFORFACE INTEGER, ZPERSONFORFACE INTEGER)", ()),
        ("CREATE TABLE ZGENERICALBUM (Z_PK INTEGER PRIMARY KEY, ZTITLE TEXT, ZKIND INTEGER, ZTRASHEDSTATE INTEGER)", ()),
        ("CREATE TABLE Z_26ASSETS (Z_3ALBUMS INTEGER, Z_26ASSETS INTEGER)", ()),
        ("INSERT INTO ZMOMENT VALUES (1, 'Shelbyville')", ()),
        ("INSERT INTO ZPERSON VALUES (1, 'Veyda', NULL)", ()), ("INSERT INTO ZPERSON VALUES (2, '', NULL)", ()),
        ("INSERT INTO ZGENERICALBUM VALUES (1, 'Mommy', 2, 0)", ()),
    ]
    # 1: same name+size as a Google file already held; 2: no copy, has a thumbnail; 3: no copy, no file
    photos = [(1, "U1", "U1.jpeg", "0", "IMG_0001.JPG", 1000), (2, "U2", "U2.jpeg", "0", "IMG_0002.JPG", 2000),
              (3, "U3", "IMG_0003.JPG", "DCIM/100APPLE", "IMG_0003.JPG", 3000)]
    for pk, uuid, filename, directory, original, size in photos:
        stmts.append(("INSERT INTO ZASSET VALUES (?, ?, ?, ?, ?, ?, 0, 0, 4000, 3000, NULL, ?, 0, 0, ?, ?, 1, 1)",
                      (pk, uuid, filename, directory, taken + pk, taken, int(pk == 2),
                       39.52 if pk == 1 else -180.0, -85.77 if pk == 1 else -180.0)))
        stmts.append(("INSERT INTO ZADDITIONALASSETATTRIBUTES VALUES (?, ?, ?, ?, 'America/Indiana/Indianapolis', 'Camera')",
                      (pk, pk, original, size)))
    stmts += [
        ("INSERT INTO ZASSETDESCRIPTION VALUES (1, 1, 'First day of summer')", ()),
        ("INSERT INTO ZDETECTEDFACE VALUES (1, 1, 1)", ()), ("INSERT INTO ZDETECTEDFACE VALUES (2, 1, 2)", ()),
        ("INSERT INTO Z_26ASSETS VALUES (1, 2)", ()),
    ]
    backup.add_db(CAMERA, "Media/PhotoData/Photos.sqlite", stmts)
    backup.add(CAMERA, "Media/PhotoData/Thumbnails/V2/PhotoData/CPLAssets/group1/U2.JPG/5005.JPG", b"\xff\xd8thumb2")
    backup.add(CAMERA, "Media/PhotoData/Thumbnails/V2/PhotoData/CPLAssets/group1/U1.JPG/5005.JPG", b"\xff\xd8thumb1")


def test_records_link_to_held_originals_or_thumbnails(tmp_path: Path, database_engine: Engine) -> None:
    import_parsed_exports(database_engine, (ParsedExport(
        source="google", parser_version="t", filename="IMG_0001.JPG", original_path="x", sha256="a" * 64, size_bytes=1000,
        assets=(NormalizedAsset(source="google", asset_namespace="google_takeout/google_photos/asset",
                                source_asset_id="g1", sha256="a" * 64, mime_type="image/jpeg", filename="IMG_0001.JPG",
                                original_path="x", size_bytes=1000),),
    ),))
    backup = Backup(tmp_path / "backup")
    _library(backup)

    summary = import_ios_photos(database_engine, backup.root)
    assert (summary.library_items, summary.linked_to_full_size, summary.thumbnails_imported) == (3, 1, 1)
    assert summary.without_any_file == 1 and summary.geotagged == 1 and summary.with_named_people == 1
    assert summary.in_albums == 1

    with database_engine.connect() as c:
        records = {r.source_record_id: r for r in c.execute(text(
            "SELECT source_record_id, title, text, occurred_at, metadata FROM records WHERE record_type = 'photo'")).all()}
        links = {r.subject_source_id: (r.object_source, r.object_source_id) for r in c.execute(text(
            "SELECT subject_source_id, object_source, object_source_id FROM relationships "
            "WHERE relationship_type = 'photo_has_file'")).all()}
        thumbs = c.execute(text("SELECT source_asset_id, sha256, metadata FROM assets "
                                "WHERE source = 'ios_backup'")).all()
    assert records["U1"].text == "First day of summer" and records["U1"].metadata["people"] == ["Veyda"]
    assert records["U1"].metadata["latitude"] == 39.52 and records["U1"].metadata["place"] == "Shelbyville"
    assert "latitude" not in records["U2"].metadata and records["U2"].metadata["favorite"] is True
    assert records["U2"].metadata["albums"] == ["Mommy"]
    assert links == {"U1": ("google", "g1"), "U2": ("ios_backup", "U2")}
    assert [(t.source_asset_id, t.metadata["original_available"]) for t in thumbs] == [("U2", False)]

    assert import_ios_photos(database_engine, backup.root).thumbnails_already_assets == 1
