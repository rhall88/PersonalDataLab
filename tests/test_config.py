from __future__ import annotations

import pytest

from personal_data_lab.config import (
    CANONICAL_DATABASE_NAME_DEFAULT,
    DatabaseTargetGuardError,
    canonical_database_name,
    guard_acceptance_run_not_canonical,
    guard_canonical_import_not_test_like,
    is_test_like_database_name,
)


def test_is_test_like_database_name_matches_test_suffix() -> None:
    assert is_test_like_database_name("personaldata_google_acceptance_test")
    assert is_test_like_database_name("personaldata_test")


def test_is_test_like_database_name_matches_pdl_accept_prefix() -> None:
    assert is_test_like_database_name("pdl_accept_google_pr13")


def test_is_test_like_database_name_rejects_canonical_and_unrelated_names() -> None:
    assert not is_test_like_database_name("personaldata")
    assert not is_test_like_database_name("some_other_database")


def test_is_test_like_database_name_rejects_empty_string() -> None:
    assert not is_test_like_database_name("")


def test_canonical_database_name_defaults_without_env_override(monkeypatch) -> None:
    monkeypatch.delenv("PDL_CANONICAL_DB_NAME", raising=False)

    assert canonical_database_name() == CANONICAL_DATABASE_NAME_DEFAULT == "personaldata"


def test_canonical_database_name_respects_env_override(monkeypatch) -> None:
    monkeypatch.setenv("PDL_CANONICAL_DB_NAME", "some_other_canonical")

    assert canonical_database_name() == "some_other_canonical"


def test_guard_acceptance_run_not_canonical_blocks_acceptance_mode_against_canonical(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PDL_CANONICAL_DB_NAME", raising=False)
    monkeypatch.setenv("PDL_ENV", "acceptance")

    with pytest.raises(DatabaseTargetGuardError, match="refuses to target the canonical database"):
        guard_acceptance_run_not_canonical("postgresql://user:pass@localhost/personaldata")


def test_guard_acceptance_run_not_canonical_allows_acceptance_mode_against_test_name(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PDL_CANONICAL_DB_NAME", raising=False)
    monkeypatch.setenv("PDL_ENV", "acceptance")

    guard_acceptance_run_not_canonical(
        "postgresql://user:pass@localhost/personaldata_google_acceptance_test"
    )


def test_guard_acceptance_run_not_canonical_allows_normal_mode_against_canonical(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PDL_CANONICAL_DB_NAME", raising=False)
    monkeypatch.delenv("PDL_ENV", raising=False)

    guard_acceptance_run_not_canonical("postgresql://user:pass@localhost/personaldata")


def test_guard_acceptance_run_not_canonical_allows_empty_database_name(monkeypatch) -> None:
    monkeypatch.delenv("PDL_CANONICAL_DB_NAME", raising=False)
    monkeypatch.setenv("PDL_ENV", "acceptance")

    guard_acceptance_run_not_canonical("postgresql://user:pass@localhost/")


def test_guard_canonical_import_not_test_like_blocks_normal_mode_against_test_name(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PDL_ENV", raising=False)

    with pytest.raises(DatabaseTargetGuardError, match="test/acceptance naming pattern"):
        guard_canonical_import_not_test_like("postgresql://user:pass@localhost/personaldata_test")


def test_guard_canonical_import_not_test_like_allows_acceptance_mode_against_test_name(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PDL_ENV", "acceptance")

    guard_canonical_import_not_test_like("postgresql://user:pass@localhost/personaldata_test")


def test_guard_canonical_import_not_test_like_allows_normal_mode_against_canonical(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PDL_ENV", raising=False)

    guard_canonical_import_not_test_like("postgresql://user:pass@localhost/personaldata")


def test_guard_canonical_import_not_test_like_allows_empty_database_name(monkeypatch) -> None:
    monkeypatch.delenv("PDL_ENV", raising=False)

    guard_canonical_import_not_test_like("postgresql://user:pass@localhost/")
