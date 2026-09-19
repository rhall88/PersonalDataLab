from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy.engine import make_url


def database_url(explicit_url: str | None = None) -> str:
    load_dotenv()
    value = explicit_url or os.getenv("DATABASE_URL")
    if not value:
        raise ValueError("DATABASE_URL is required (set it in .env or pass --database-url)")
    return value


CANONICAL_DATABASE_NAME_DEFAULT = "personaldata"


def canonical_database_name() -> str:
    load_dotenv()
    return os.getenv("PDL_CANONICAL_DB_NAME") or CANONICAL_DATABASE_NAME_DEFAULT


def is_test_like_database_name(name: str) -> bool:
    return name.endswith("_test") or name.startswith("pdl_accept_")


class DatabaseTargetGuardError(RuntimeError):
    """Raised when a command's resolved database target violates the canonical/acceptance split."""


def guard_acceptance_run_not_canonical(database_url_value: str) -> None:
    load_dotenv()
    name = make_url(database_url_value).database or ""
    if os.getenv("PDL_ENV", "production") == "acceptance" and name == canonical_database_name():
        raise DatabaseTargetGuardError(
            f"PDL_ENV=acceptance refuses to target the canonical database "
            f"'{name}'. Acceptance runs must never touch canonical."
        )


def guard_canonical_import_not_test_like(database_url_value: str) -> None:
    load_dotenv()
    name = make_url(database_url_value).database or ""
    if os.getenv("PDL_ENV", "production") != "acceptance" and is_test_like_database_name(name):
        raise DatabaseTargetGuardError(
            f"refusing a canonical import against '{name}' — its name matches "
            f"a test/acceptance naming pattern. If this is intentional, set "
            f"PDL_ENV=acceptance."
        )
