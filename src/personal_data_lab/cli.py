from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .anthropic_importer import parse_anthropic_export
from .config import database_url
from .database import create_database_engine, import_openai_export, import_parsed_exports
from .google_importer import iter_google_exports
from .migrations import LATEST_SCHEMA_VERSION, migrate_database
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
    google = subparsers.add_parser(
        "import-google",
        help="import Google My Activity from Takeout ZIPs or an extracted export",
    )
    google.add_argument(
        "source",
        help="Takeout ZIP, folder of split ZIPs, extracted Takeout, or MyActivity file",
    )
    google.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    migrate = subparsers.add_parser("migrate", help="upgrade an existing database schema")
    migrate.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    return parser


def _batch_summary(parsed_exports: Sequence, results: Sequence) -> dict:
    files = [
        parsed.model_dump(include={"filename", "sha256", "size_bytes", "parser_version"})
        | result.model_dump()
        | {"reconciled": result.reconciled}
        for parsed, result in zip(parsed_exports, results, strict=True)
    ]
    return {
        "files": files,
        "source_files": len(files),
        "source_conversations": sum(result.source_conversations for result in results),
        "database_conversations": sum(result.database_conversations for result in results),
        "source_events": sum(result.source_events for result in results),
        "database_events": sum(result.database_events for result in results),
        "inserted_conversations": sum(result.inserted_conversations for result in results),
        "updated_conversations": sum(result.updated_conversations for result in results),
        "unchanged_conversations": sum(result.unchanged_conversations for result in results),
        "stale_conversations": sum(result.stale_conversations for result in results),
        "conflicted_conversations": sum(result.conflicted_conversations for result in results),
        "tombstoned_conversations": sum(result.tombstoned_conversations for result in results),
        "inserted_events": sum(result.inserted_events for result in results),
        "updated_events": sum(result.updated_events for result in results),
        "unchanged_events": sum(result.unchanged_events for result in results),
        "stale_events": sum(result.stale_events for result in results),
        "conflicted_events": sum(result.conflicted_events for result in results),
        "tombstoned_events": sum(result.tombstoned_events for result in results),
        "new_distinct_versions": sum(
            result.new_conversation_versions + result.new_event_versions for result in results
        ),
        "reused_versions": sum(
            result.reused_conversation_versions + result.reused_event_versions for result in results
        ),
        "short_circuited": all(result.short_circuited for result in results),
        "reconciled": all(result.reconciled for result in results),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "import-openai":
        parsed = parse_openai_export(args.source)
        engine = create_database_engine(database_url(args.database_url))
        result = import_openai_export(engine, parsed)
        print(json.dumps(result.model_dump() | {"reconciled": result.reconciled}, indent=2))
        return 2 if result.has_conflicts else 0
    if args.command == "import-anthropic":
        parsed_exports = parse_anthropic_export(args.manifest)
        engine = create_database_engine(database_url(args.database_url))
        results = import_parsed_exports(engine, parsed_exports)
        print(json.dumps(_batch_summary(parsed_exports, results), indent=2))
        return 2 if any(result.has_conflicts for result in results) else 0
    if args.command == "import-google":
        engine = create_database_engine(database_url(args.database_url))
        parsed_exports = []
        results = []
        for parsed in iter_google_exports(args.source):
            result = import_parsed_exports(engine, (parsed,))[0]
            parsed_exports.append(parsed.model_copy(update={"events": (), "conversations": ()}))
            results.append(result)
        print(json.dumps(_batch_summary(parsed_exports, results), indent=2))
        return 2 if any(result.has_conflicts for result in results) else 0
    if args.command == "migrate":
        engine = create_database_engine(database_url(args.database_url))
        applied = migrate_database(engine)
        print(
            json.dumps(
                {
                    "schema_version": LATEST_SCHEMA_VERSION,
                    "applied": list(applied),
                    "changed": bool(applied),
                },
                indent=2,
            )
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
