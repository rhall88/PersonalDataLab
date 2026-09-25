"""Encrypted backup/restore -- the "Permanent operational track" item ROADMAP.md has named as
outstanding since Phase 0 ("record bounded-memory, elapsed-time, database-size, backup, and
restore evidence") and PROJECT_STATE.md has relied on manually (`backups/20260921T195110Z/`, a
plain unencrypted `pg_dump`) rather than as a real, repeatable feature.

`pg_dump`/`pg_restore` run inside this project's own Postgres container (`docker exec`), not as a
host-installed client-tools dependency -- this machine's dev environment (and CI's, via the same
`compose.yaml`) only ever runs Postgres in Docker, so shelling out to the container is the one
approach that works everywhere this project is actually developed, without adding a new
host-level install requirement on top of Docker (already required). `PDL_DB_CONTAINER` overrides
the container name for anyone running a differently-named container.

Encryption is a passphrase-derived Fernet key (PBKDF2HMAC-SHA256, a fresh random salt per backup
stored in the output file's own header) via the `cryptography` package -- deliberately not a new
external binary dependency (age, gpg) on top of Docker, matching this project's existing
dependency bar (fastembed/faster-whisper/llama-cpp-python are all pure-PyPI-installable). There is
no passphrase-recovery mechanism by design: PDL_BACKUP_PASSPHRASE lives wherever the caller keeps
it, never in this repo or in the backup file itself.

Restore refuses to target the canonical database name (config.canonical_database_name()) unless
`force=True` -- reusing this codebase's existing guard-function pattern
(guard_acceptance_run_not_canonical/guard_canonical_import_not_test_like in config.py) rather than
inventing a second safety mechanism, since an accidental restore is exactly the kind of silent,
hard-to-reverse action those guards already exist to prevent.
"""

from __future__ import annotations

import os
import subprocess
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy.engine import make_url

from .config import DatabaseTargetGuardError, canonical_database_name

_SALT_LENGTH = 16
_PBKDF2_ITERATIONS = 600_000
_CONTAINER_ENV = "PDL_DB_CONTAINER"
_CONTAINER_DEFAULT = "personal-data-db"
_PASSPHRASE_ENV = "PDL_BACKUP_PASSPHRASE"


class BackupError(RuntimeError):
    """Raised when pg_dump/pg_restore or the surrounding docker exec fails."""


def _container_name() -> str:
    return os.environ.get(_CONTAINER_ENV, _CONTAINER_DEFAULT)


def _passphrase(explicit: str | None) -> str:
    value = explicit or os.environ.get(_PASSPHRASE_ENV)
    if not value:
        raise BackupError(
            f"{_PASSPHRASE_ENV} is not set and no passphrase was passed explicitly -- refusing "
            "to create an unencrypted backup or guess a default passphrase."
        )
    return value


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_PBKDF2_ITERATIONS
    )
    return urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _run(args: list[str], *, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            args, input=input_bytes, capture_output=True, check=False,
        )
    except FileNotFoundError as error:
        raise BackupError(
            f"could not run {args[0]!r} -- is Docker installed and on PATH?"
        ) from error
    if result.returncode != 0:
        raise BackupError(
            f"{' '.join(args)} exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace')[-2000:]}"
        )
    return result.stdout


@dataclass(frozen=True)
class BackupResult:
    path: Path
    database_name: str
    size_bytes: int
    created_at: datetime


def create_backup(
    database_url: str, *, output_dir: Path, passphrase: str | None = None
) -> BackupResult:
    """pg_dump (custom format, inside the Postgres container) -> encrypt -> write to output_dir.

    Never touches the target database beyond a read-only pg_dump -- safe to run at any time,
    including alongside an active derive-run worker (Postgres's own MVCC snapshot isolation is
    what pg_dump already relies on for a consistent dump of a live, writing database).
    """
    key = _passphrase(passphrase)
    parsed = make_url(database_url)
    db_name = parsed.database or ""
    user = parsed.username or ""
    if not db_name or not user:
        raise BackupError(f"database_url is missing a database name or username: {database_url}")

    dump_bytes = _run(
        [
            "docker", "exec", _container_name(),
            "pg_dump", "-U", user, "-d", db_name, "--format=custom",
        ]
    )

    salt = os.urandom(_SALT_LENGTH)
    token = Fernet(_derive_key(key, salt)).encrypt(dump_bytes)

    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    out_path = output_dir / f"pdl_backup_{db_name}_{stamp}.dump.enc"
    out_path.write_bytes(salt + token)

    return BackupResult(
        path=out_path, database_name=db_name, size_bytes=out_path.stat().st_size,
        created_at=created_at,
    )


def restore_backup(
    encrypted_path: Path,
    target_database_url: str,
    *,
    passphrase: str | None = None,
    force: bool = False,
) -> None:
    """Decrypt encrypted_path and pg_restore it into target_database_url's database, inside the
    Postgres container. Refuses to target the canonical database unless force=True (config.py's
    existing guard-function pattern; an accidental restore over real data is exactly the silent,
    hard-to-reverse action that pattern exists to catch).
    """
    parsed = make_url(target_database_url)
    db_name = parsed.database or ""
    user = parsed.username or ""
    if not db_name or not user:
        raise BackupError(
            f"target_database_url is missing a database name or username: {target_database_url}"
        )
    if db_name == canonical_database_name() and not force:
        raise DatabaseTargetGuardError(
            f"refusing to restore into the canonical database '{db_name}' without force=True -- "
            "a restore overwrites existing data; pass force=True only when that is truly intended."
        )

    key = _passphrase(passphrase)
    raw = encrypted_path.read_bytes()
    salt, token = raw[:_SALT_LENGTH], raw[_SALT_LENGTH:]
    try:
        dump_bytes = Fernet(_derive_key(key, salt)).decrypt(token)
    except InvalidToken as error:
        raise BackupError(
            "could not decrypt backup -- wrong passphrase, or the file is corrupted/truncated"
        ) from error

    _run(
        [
            "docker", "exec", "-i", _container_name(),
            "pg_restore", "-U", user, "-d", db_name,
            "--clean", "--if-exists", "--no-owner",
        ],
        input_bytes=dump_bytes,
    )
