from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .config import database_url
from .database import create_database_engine, import_openai_export
from .openai_importer import parse_openai_export


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-data-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("import-openai", help="import an OpenAI data export")
    command.add_argument("source", help="ZIP, extracted directory, or conversations.json")
    command.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "import-openai":
        parsed = parse_openai_export(args.source)
        engine = create_database_engine(database_url(args.database_url))
        result = import_openai_export(engine, parsed)
        print(json.dumps(result.model_dump() | {"reconciled": result.reconciled}, indent=2))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
