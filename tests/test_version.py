from __future__ import annotations

import tomllib
from pathlib import Path

import personal_data_lab


def test_runtime_version_matches_project_metadata() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert personal_data_lab.__version__ == project["project"]["version"]
