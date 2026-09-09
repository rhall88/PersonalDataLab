from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .anthropic_importer import parse_anthropic_export
from .config import database_url
from .database import create_database_engine, import_openai_export, import_parsed_exports
from .openai_importer import parse_openai_export


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-data-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    openai = subparsers.add_parser("import-openai", help="import an OpenAI data export")
    openai.add_argument("source", help="ZIP, extracted directory, or conversations.json")
    openai.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    anthropic = subparsers.add_parser(
        "import-anthropic",
        help="import an Anthropic manifest and its local source archives",
    )
    anthropic.add_argument("manifest", help="manifest JSON beside the untouched ZIP archives")
    anthropic.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "import-openai":
        parsed = parse_openai_export(args.source)
        engine = create_database_engine(database_url(args.database_url))
        result = import_openai_export(engine, parsed)
        print(json.dumps(result.model_dump() | {"reconciled": result.reconciled}, indent=2))
        return 0
    if args.command == "import-anthropic":
        parsed_exports = parse_anthropic_export(args.manifest)
        engine = create_database_engine(database_url(args.database_url))
        results = import_parsed_exports(engine, parsed_exports)
        files = [
            parsed.model_dump(
                include={"filename", "sha256", "size_bytes", "parser_version"}
            )
            | result.model_dump()
            | {"reconciled": result.reconciled}
            for parsed, result in zip(parsed_exports, results, strict=True)
        ]
        print(
            json.dumps(
                {
                    "files": files,
                    "source_files": len(files),
                    "source_conversations": sum(
                        result.source_conversations for result in results
                    ),
                    "database_conversations": sum(
                        result.database_conversations for result in results
                    ),
                    "source_events": sum(result.source_events for result in results),
                    "database_events": sum(result.database_events for result in results),
                    "reconciled": all(result.reconciled for result in results),
                },
                indent=2,
            )
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
