"""The phone's photo library (`CameraRollDomain` `Media/PhotoData/Photos.sqlite`) as `photo` records
under source `ios_backup`.

What the backup has: the library database for all 5,574 items (dates, location, people recognized,
albums, favorites, original file name and size) and a thumbnail for nearly all of them. The original
files are mostly in iCloud (Optimize iPhone Storage) and are not in the backup.

One copy of each item (owner's rule): a library item whose original file name and byte size equal an
asset another source already holds (Google Photos holds most) is that file, so the record links to the
full-size asset and the thumbnail is not stored. Only items with no such copy get a thumbnail asset
(`ios_backup/photo_thumbnail`), flagged `original_available: false`. Every item gets a record, because
the phone's metadata (when, where, who, which album) is what the other copies lack.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from ..models import NormalizedAsset, NormalizedRecord, NormalizedRelationship, ParsedExport
from .inventory import BackupFile, IosBackupInventory, find_backup_files

SOURCE = "ios_backup"
NAMESPACE = "ios_backup/photos/photo"
THUMBNAIL_NAMESPACE = "ios_backup/photo_thumbnail"
PARSER_VERSION = "ios-photos-v1"
CAMERA_ROLL = "CameraRollDomain"
PHOTOS_DB = "Media/PhotoData/Photos.sqlite"
THUMBNAILS = "Media/PhotoData/Thumbnails/V2/"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
_KINDS = {(0, 0): "photo", (0, 2): "live_photo", (0, 10): "screenshot", (0, 1): "panorama",
          (1, 0): "video", (1, 101): "slow_motion_video", (1, 102): "time_lapse_video", (1, 103): "cinematic_video"}


@dataclass
class LibraryPhoto:
    uuid: str
    filename: str
    directory: str
    original_filename: str | None
    original_size: int | None
    created: datetime | None
    added: datetime | None
    kind: str
    width: int | None
    height: int | None
    duration: float | None
    favorite: bool
    hidden: bool
    trashed: bool
    latitude: float | None
    longitude: float | None
    timezone: str | None
    imported_by: str | None
    description: str | None
    place: str | None
    cloud_local_state: int | None
    albums: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    faces: int = 0

    @property
    def stem(self) -> str:
        return PurePosixPath(self.filename).stem.upper()


def _when(value: float | None) -> datetime | None:
    return APPLE_EPOCH + timedelta(seconds=value) if value else None


def _valid_coordinate(lat: float | None, lon: float | None) -> tuple[float, float] | None:
    # Photos stores -180/-180 for "no location".
    if lat is None or lon is None or lat == -180 or lon == -180 or not (-90 <= lat <= 90 and -180 < lon <= 180):
        return None
    return lat, lon


def read_library(inventory: IosBackupInventory) -> list[LibraryPhoto]:
    found = {
        suffix: find_backup_files(inventory.root, CAMERA_ROLL, path=PHOTOS_DB + suffix)
        for suffix in ("", "-wal", "-shm")
    }
    if not found[""]:
        return []
    with tempfile.TemporaryDirectory(prefix="pdl-photos-") as tmp:
        for suffix, files in found.items():
            if files:
                shutil.copyfile(next(iter(files.values())).path, Path(tmp) / f"p.db{suffix}")
        db = sqlite3.connect(Path(tmp) / "p.db")
        try:
            return _read(db)
        finally:
            db.close()


def _read(db: sqlite3.Connection) -> list[LibraryPhoto]:
    moments = {pk: title for pk, title in db.execute("SELECT Z_PK, ZTITLE FROM ZMOMENT")}
    names = {pk: (full or display or "").strip() for pk, full, display in db.execute(
        "SELECT Z_PK, ZFULLNAME, ZDISPLAYNAME FROM ZPERSON")}
    albums = {pk: title for pk, title, kind in db.execute(
        "SELECT Z_PK, ZTITLE, ZKIND FROM ZGENERICALBUM WHERE ZTITLE IS NOT NULL AND ZTITLE <> '' "
        "AND ZTRASHEDSTATE = 0") if kind == 2}
    membership: dict[int, list[str]] = {}
    for table in (r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")):
        match = re.fullmatch(r"Z_(\d+)ASSETS", table)
        if not match:
            continue
        columns = [c[1] for c in db.execute(f"PRAGMA table_info({table})")]
        album_col = next((c for c in columns if c.endswith("ALBUMS")), None)
        asset_col = next((c for c in columns if c.endswith("ASSETS")), None)
        if not album_col or not asset_col:
            continue
        for album, asset in db.execute(f"SELECT {album_col}, {asset_col} FROM {table}"):
            if album in albums:
                membership.setdefault(asset, []).append(albums[album])
    faces: dict[int, list[int]] = {}
    for asset, person in db.execute("SELECT ZASSETFORFACE, ZPERSONFORFACE FROM ZDETECTEDFACE"):
        faces.setdefault(asset, []).append(person)

    photos = []
    for (pk, uuid, filename, directory, created, added, kind, subtype, width, height, duration, favorite,
         hidden, trashed, lat, lon, cloud, moment, orig_name, orig_size, tz, imported_by, description) in db.execute(
        "SELECT a.Z_PK, a.ZUUID, a.ZFILENAME, a.ZDIRECTORY, a.ZDATECREATED, a.ZADDEDDATE, a.ZKIND, "
        "a.ZKINDSUBTYPE, a.ZWIDTH, a.ZHEIGHT, a.ZDURATION, a.ZFAVORITE, a.ZHIDDEN, a.ZTRASHEDSTATE, "
        "a.ZLATITUDE, a.ZLONGITUDE, a.ZCLOUDLOCALSTATE, a.ZMOMENT, x.ZORIGINALFILENAME, "
        "x.ZORIGINALFILESIZE, x.ZTIMEZONENAME, x.ZIMPORTEDBYDISPLAYNAME, d.ZLONGDESCRIPTION "
        "FROM ZASSET a LEFT JOIN ZADDITIONALASSETATTRIBUTES x ON x.ZASSET = a.Z_PK "
        "LEFT JOIN ZASSETDESCRIPTION d ON d.ZASSETATTRIBUTES = x.Z_PK ORDER BY a.Z_PK"
    ):
        coordinate = _valid_coordinate(lat, lon)
        people = sorted({names[p] for p in faces.get(pk, []) if names.get(p)})
        photos.append(LibraryPhoto(
            uuid=uuid, filename=filename or uuid, directory=directory or "", original_filename=orig_name,
            original_size=orig_size, created=_when(created), added=_when(added),
            kind=_KINDS.get((kind, subtype), f"kind{kind}_{subtype}"), width=width, height=height,
            duration=duration or None, favorite=bool(favorite), hidden=bool(hidden), trashed=bool(trashed),
            latitude=coordinate[0] if coordinate else None, longitude=coordinate[1] if coordinate else None,
            timezone=tz, imported_by=imported_by, description=(description or "").strip() or None,
            place=moments.get(moment), cloud_local_state=cloud, albums=sorted(set(membership.get(pk, []))),
            people=people, faces=len(faces.get(pk, [])),
        ))
    return photos


def thumbnail_files(inventory: IosBackupInventory) -> dict[str, list[tuple[str, BackupFile]]]:
    """Thumbnail JPEGs by upper-cased stem of the library file name: `.../V2/<dir>/<file>/<n>.JPG`."""
    out: dict[str, list[tuple[str, BackupFile]]] = {}
    for relative, item in find_backup_files(inventory.root, CAMERA_ROLL, prefix=THUMBNAILS).items():
        parts = PurePosixPath(relative).parts
        if len(parts) < 6 or not parts[-1].lower().endswith((".jpg", ".jpeg")):
            continue
        out.setdefault(PurePosixPath(parts[-2]).stem.upper(), []).append(("/".join(parts[5:-2]), item))
    return out


def pick_thumbnail(photo: LibraryPhoto, thumbs: dict[str, list[tuple[str, BackupFile]]]) -> BackupFile | None:
    candidates = thumbs.get(photo.stem, [])
    if len(candidates) == 1:
        return candidates[0][1]
    wanted = photo.directory.removeprefix("PhotoData/")
    for directory, item in candidates:
        if directory == wanted:
            return item
    return candidates[0][1] if candidates else None


def existing_copy(
    photo: LibraryPhoto, held: dict[tuple[str, int], tuple[str, str, str]]
) -> tuple[str, str, str] | None:
    """(source, namespace, source_asset_id) of a full-size file another source holds."""
    if not photo.original_filename or photo.original_size is None:
        return None
    return held.get((photo.original_filename.lower(), photo.original_size))


def thumbnail_asset_export(
    photo: LibraryPhoto, item: BackupFile, inventory: IosBackupInventory, sha256: str
) -> ParsedExport:
    asset = NormalizedAsset(
        source=SOURCE, asset_namespace=THUMBNAIL_NAMESPACE, source_asset_id=photo.uuid, sha256=sha256,
        mime_type="image/jpeg", filename=f"{photo.original_filename or photo.filename}.thumbnail.jpg",
        original_path=item.path, size_bytes=item.size_bytes, captured_at=photo.created,
        created_at=photo.created, width=photo.width, height=photo.height,
        metadata={k: v for k, v in {
            "thumbnail": True, "original_available": False, "original_filename": photo.original_filename,
            "original_size": photo.original_size, "relative_path": item.relative_path,
        }.items() if v is not None},
        authority_at=inventory.backup_date,
        authority_kind="exported_at" if inventory.backup_date else "unknown",
    )
    return ParsedExport(
        source=SOURCE, parser_version=PARSER_VERSION, category="photo_thumbnail", filename=item.filename,
        original_path=item.path, sha256=sha256, size_bytes=item.size_bytes, exported_at=inventory.backup_date,
        export_id=f"{inventory.unique_device_id}:{inventory.manifest_sha256[:16]}",
        snapshot_kind="partial", snapshot_scope="device_backup_photos", assets=(asset,),
    )


def library_export(
    photos: list[LibraryPhoto],
    inventory: IosBackupInventory,
    links: dict[str, tuple[str, str, str] | None],
    store: BackupFile,
    sha256: str,
) -> ParsedExport:
    """A record per library item; `links[uuid]` is the asset its file is (full-size copy held by
    another source, or this import's thumbnail), if any."""
    stamp = inventory.backup_date
    authority = {"authority_at": stamp, "authority_kind": "exported_at" if stamp else "unknown"}
    records, relationships = [], []
    for photo in photos:
        link = links.get(photo.uuid)
        records.append(NormalizedRecord(
            source=SOURCE, record_type="photo", source_record_namespace=NAMESPACE, source_record_id=photo.uuid,
            title=photo.original_filename or photo.filename, text=photo.description, occurred_at=photo.created,
            created_at=photo.created, updated_at=photo.added,
            metadata={k: v for k, v in {
                "kind": photo.kind, "width": photo.width, "height": photo.height, "duration_seconds": photo.duration,
                "favorite": photo.favorite or None, "hidden": photo.hidden or None,
                "latitude": photo.latitude, "longitude": photo.longitude, "timezone": photo.timezone,
                "place": photo.place, "imported_by": photo.imported_by, "albums": photo.albums or None,
                "people": photo.people or None, "faces": photo.faces or None,
                "original_filename": photo.original_filename, "original_size": photo.original_size,
                "original_in_backup": photo.cloud_local_state == 0 or None,
                "file": {"source": link[0], "kind": "full_size" if link[0] != SOURCE else "thumbnail"}
                if link else None,
            }.items() if v is not None},
            lifecycle_state="provider_deleted" if photo.trashed else "active", **authority,
        ))
        if link:
            identity = f"{photo.uuid}|{link[0]}|{link[1]}|{link[2]}"
            relationships.append(NormalizedRelationship(
                source=SOURCE, relationship_type="photo_has_file",
                source_relationship_namespace="ios_backup/photos/photo_has_file",
                source_relationship_id=f"relationship:{hashlib.sha256(identity.encode()).hexdigest()}",
                subject_kind="record", subject_source=SOURCE, subject_namespace=NAMESPACE,
                subject_source_id=photo.uuid, object_kind="asset", object_source=link[0],
                object_namespace=link[1], object_source_id=link[2], **authority,
            ))
    return ParsedExport(
        source=SOURCE, parser_version=PARSER_VERSION, category="photos", filename=store.filename,
        original_path=store.path, sha256=sha256, size_bytes=store.size_bytes, exported_at=stamp,
        export_id=f"{inventory.unique_device_id}:{inventory.manifest_sha256[:16]}",
        snapshot_kind="partial", snapshot_scope="device_backup_photos", records=tuple(records),
        relationships=tuple(relationships),
    )
