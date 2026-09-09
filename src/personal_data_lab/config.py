from __future__ import annotations

import os

from dotenv import load_dotenv


def database_url(explicit_url: str | None = None) -> str:
    load_dotenv()
    value = explicit_url or os.getenv("DATABASE_URL")
    if not value:
        raise ValueError("DATABASE_URL is required (set it in .env or pass --database-url)")
    return value
