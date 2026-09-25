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

**Streaming, not single-shot.** A first version used `cryptography.fernet.Fernet`, which buffers
its entire input in memory to encrypt it in one call -- fine for small payloads, but a real
`MemoryError` against the actual canonical database (13GB+) on this project's real hardware, found
running this feature for real the same day it shipped. Fixed by encrypting/decrypting in bounded
`_CHUNK_SIZE` chunks with AES-256-CTR (a genuine stream cipher, `cryptography`'s low-level
`Cipher`/`CipherContext` API) while `pg_dump`'s stdout is read incrementally, so memory use stays
roughly constant regardless of database size. Integrity is a separate encrypt-then-MAC step
(HMAC-SHA256 over the ciphertext, verified in a first streaming pass over the file before any
byte is decrypted or handed to `pg_restore`) -- CTR mode alone provides no tamper detection, so a
wrong passphrase or corrupted file is still caught cleanly and safely as a `BackupError`, not
silently fed as garbage into `pg_restore`.

Encryption keys are passphrase-derived (PBKDF2HMAC-SHA256, a fresh random salt per backup stored
in the file's own header) via the `cryptography` package -- deliberately not a new external binary
dependency (age, gpg) on top of Docker, matching this project's existing dependency bar
(fastembed/faster-whisper/llama-cpp-python are all pure-PyPI-installable). There is no
passphrase-recovery mechanism by design: `PDL_BACKUP_PASSPHRASE` lives wherever the caller keeps
it, never in this repo or in the backup file itself.

Restore refuses to target the canonical database name (`config.canonical_database_name()`) unless
`force=True` -- reusing this codebase's existing guard-function pattern
(`guard_acceptance_run_not_canonical`/`guard_canonical_import_not_test_like` in `config.py`)
rather than inventing a second safety mechanism, since an accidental restore is exactly the kind
of silent, hard-to-reverse action those guards already exist to prevent.

File layout: `salt(16) || nonce(16) || ciphertext(streamed pg_dump bytes) || hmac_tag(32)`.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy.engine import make_url

from .config import DatabaseTargetGuardError, canonical_database_name

_SALT_LENGTH = 16
_NONCE_LENGTH = 16
_HMAC_LENGTH = 32
_PBKDF2_ITERATIONS = 600_000
_CHUNK_SIZE = 4 * 1024 * 1024
_CONTAINER_ENV = "PDL_DB_CONTAINER"
_CONTAINER_DEFAULT = "personal-data-db"
_PASSPHRASE_ENV = "PDL_BACKUP_PASSPHRASE"


class BackupError(RuntimeError):
    """Raised when pg_dump/pg_restore, the surrounding docker exec, or integrity checking fails."""


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


def _derive_keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    """64 bytes of key material split into a 32-byte AES key and a 32-byte HMAC key -- one KDF
    call, two independent keys, standard encrypt-then-MAC practice (never reuse one key for both).
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=64, salt=salt, iterations=_PBKDF2_ITERATIONS
    )
    material = kdf.derive(passphrase.encode("utf-8"))
    return material[:32], material[32:]


def _db_and_user(database_url: str) -> tuple[str, str]:
    parsed = make_url(database_url)
    db_name = parsed.database or ""
    user = parsed.username or ""
    if not db_name or not user:
        raise BackupError(f"database_url is missing a database name or username: {database_url}")
    return db_name, user


def _popen(args: list[str], **kwargs) -> subprocess.Popen:
    try:
        return subprocess.Popen(args, **kwargs)
    except FileNotFoundError as error:
        raise BackupError(
            f"could not run {args[0]!r} -- is Docker installed and on PATH?"
        ) from error


@dataclass(frozen=True)
class BackupResult:
    path: Path
    database_name: str
    size_bytes: int
    created_at: datetime


def create_backup(
    database_url: str, *, output_dir: Path, passphrase: str | None = None
) -> BackupResult:
    """pg_dump (custom format, inside the Postgres container) -> stream-encrypt -> write to
    output_dir, never holding the full dump in memory at once (see module docstring).

    Never touches the target database beyond a read-only pg_dump -- safe to run at any time,
    including alongside an active derive-run worker (Postgres's own MVCC snapshot isolation is
    what pg_dump already relies on for a consistent dump of a live, writing database).
    """
    key = _passphrase(passphrase)
    db_name, user = _db_and_user(database_url)

    salt = os.urandom(_SALT_LENGTH)
    nonce = os.urandom(_NONCE_LENGTH)
    enc_key, mac_key = _derive_keys(key, salt)
    encryptor = Cipher(algorithms.AES(enc_key), modes.CTR(nonce)).encryptor()
    tag_builder = hmac.HMAC(mac_key, hashes.SHA256())

    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    out_path = output_dir / f"pdl_backup_{db_name}_{stamp}.dump.enc"

    proc = _popen(
        [
            "docker", "exec", _container_name(),
            "pg_dump", "-U", user, "-d", db_name, "--format=custom",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        with open(out_path, "wb") as handle:
            handle.write(salt)
            handle.write(nonce)
            while True:
                chunk = proc.stdout.read(_CHUNK_SIZE)
                if not chunk:
                    break
                ciphertext = encryptor.update(chunk)
                tag_builder.update(ciphertext)
                handle.write(ciphertext)
            final = encryptor.finalize()
            if final:
                tag_builder.update(final)
                handle.write(final)
            handle.write(tag_builder.finalize())
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read()
        proc.stderr.close()
        returncode = proc.wait()

    if returncode != 0:
        out_path.unlink(missing_ok=True)
        raise BackupError(
            f"pg_dump exited {returncode}: {stderr.decode(errors='replace')[-2000:]}"
        )

    return BackupResult(
        path=out_path, database_name=db_name, size_bytes=out_path.stat().st_size,
        created_at=created_at,
    )


def _read_header(handle) -> tuple[bytes, bytes, int]:
    salt = handle.read(_SALT_LENGTH)
    nonce = handle.read(_NONCE_LENGTH)
    total_size = os.fstat(handle.fileno()).st_size
    ciphertext_size = total_size - _SALT_LENGTH - _NONCE_LENGTH - _HMAC_LENGTH
    if len(salt) != _SALT_LENGTH or len(nonce) != _NONCE_LENGTH or ciphertext_size < 0:
        raise BackupError("backup file is too short to be valid -- truncated or not a backup file")
    return salt, nonce, ciphertext_size


def _verify_hmac(path: Path, mac_key: bytes, ciphertext_size: int) -> None:
    """A full streaming pass verifying the trailer's HMAC tag against the ciphertext, before
    decrypting or writing a single byte to pg_restore -- so a wrong passphrase or a corrupted
    file is caught cleanly here, never fed as garbage into pg_restore's own stdin.
    """
    tag_builder = hmac.HMAC(mac_key, hashes.SHA256())
    with open(path, "rb") as handle:
        handle.seek(_SALT_LENGTH + _NONCE_LENGTH)
        remaining = ciphertext_size
        while remaining > 0:
            chunk = handle.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            tag_builder.update(chunk)
            remaining -= len(chunk)
        tag = handle.read(_HMAC_LENGTH)
    try:
        tag_builder.verify(tag)
    except InvalidSignature as error:
        raise BackupError(
            "could not decrypt backup -- wrong passphrase, or the file is corrupted/truncated"
        ) from error


def restore_backup(
    encrypted_path: Path,
    target_database_url: str,
    *,
    passphrase: str | None = None,
    force: bool = False,
) -> None:
    """Verify (streaming HMAC pass), then stream-decrypt encrypted_path directly into
    pg_restore's stdin (a second streaming pass) -- inside the Postgres container. Refuses to
    target the canonical database unless force=True (config.py's existing guard-function pattern;
    an accidental restore over real data is exactly the silent, hard-to-reverse action that
    pattern exists to catch).
    """
    db_name, user = _db_and_user(target_database_url)
    if db_name == canonical_database_name() and not force:
        raise DatabaseTargetGuardError(
            f"refusing to restore into the canonical database '{db_name}' without force=True -- "
            "a restore overwrites existing data; pass force=True only when that is truly intended."
        )

    key = _passphrase(passphrase)
    with open(encrypted_path, "rb") as handle:
        salt, nonce, ciphertext_size = _read_header(handle)
    enc_key, mac_key = _derive_keys(key, salt)

    _verify_hmac(encrypted_path, mac_key, ciphertext_size)

    decryptor = Cipher(algorithms.AES(enc_key), modes.CTR(nonce)).decryptor()
    proc = _popen(
        [
            "docker", "exec", "-i", _container_name(),
            "pg_restore", "-U", user, "-d", db_name,
            "--clean", "--if-exists", "--no-owner",
        ],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        with open(encrypted_path, "rb") as handle:
            handle.seek(_SALT_LENGTH + _NONCE_LENGTH)
            remaining = ciphertext_size
            while remaining > 0:
                chunk = handle.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                proc.stdin.write(decryptor.update(chunk))
                remaining -= len(chunk)
            proc.stdin.write(decryptor.finalize())
    finally:
        proc.stdin.close()
        stderr = proc.stderr.read()
        proc.stderr.close()
        returncode = proc.wait()

    if returncode != 0:
        raise BackupError(
            f"pg_restore exited {returncode}: {stderr.decode(errors='replace')[-2000:]}"
        )
