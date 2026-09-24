from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url

from . import __version__, derive
from .anthropic_export.inventory import inspect_anthropic
from .anthropic_export.inventory import render_inventory_table as render_anthropic_table
from .anthropic_export.orchestrator import import_anthropic_all
from .apple_export.inventory import inspect_apple
from .apple_export.inventory import render_inventory_table as render_apple_table
from .apple_export.orchestrator import import_apple_all
from .compare import DateRangeError, compare_periods, parse_date_range
from .config import (
    canonical_database_name,
    database_url,
    guard_acceptance_run_not_canonical,
    guard_canonical_import_not_test_like,
    is_test_like_database_name,
)
from .database import (
    create_database_engine,
    import_openai_export,
    import_parsed_exports,
    list_sibling_databases,
)
from .derive import asof
from .derive.derivers.episode import declare_episode, list_episodes
from .facebook_export.inventory import inspect_facebook
from .facebook_export.inventory import render_inventory_table as render_facebook_table
from .facebook_export.orchestrator import import_facebook_all
from .google_importer import iter_google_exports
from .google_takeout.inventory import inspect_google, render_inventory_table
from .google_takeout.orchestrator import import_google_all
from .identity import (
    create_person,
    list_identity_candidates,
    list_people,
    merge_identity,
    not_same_as_identity,
    split_identity,
)
from .identity_candidates import cross_source_name_candidates
from .identity_rules import run_rules_tier
from .imessages.inventory import inspect_imessages
from .imessages.inventory import render_inventory_table as render_imessages_table
from .imessages.orchestrator import import_imessages_all
from .migrations import LATEST_SCHEMA_VERSION, current_schema_version, migrate_database
from .openai_export.inventory import inspect_openai
from .openai_export.orchestrator import import_openai_all
from .openai_importer import parse_openai_export
from .search import ALL_KINDS, search_canonical


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-data-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    openai = subparsers.add_parser("import-openai", help="import an official OpenAI data export")
    openai.add_argument(
        "source", help="official ZIP, extracted export, or legacy conversations.json"
    )
    openai.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_openai_parser = subparsers.add_parser(
        "inspect-openai",
        help="inventory every physical and logical member of an official OpenAI export",
    )
    inspect_openai_parser.add_argument("source", help="official OpenAI ZIP or extracted export")
    inspect_openai_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_openai_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive export member paths in JSON output",
    )
    anthropic = subparsers.add_parser(
        "import-anthropic",
        help="inventory and import an Anthropic manifest and its local source archives",
    )
    anthropic.add_argument("manifest", help="manifest JSON beside the untouched ZIP archives")
    anthropic.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    anthropic_all = subparsers.add_parser(
        "import-anthropic-all",
        help="alias for the production Anthropic manifest importer",
    )
    anthropic_all.add_argument(
        "manifest", help="manifest JSON beside the untouched ZIP archives"
    )
    anthropic_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_anthropic_parser = subparsers.add_parser(
        "inspect-anthropic",
        help="inventory an Anthropic manifest and every local archive member",
    )
    inspect_anthropic_parser.add_argument(
        "manifest", help="manifest JSON beside the untouched ZIP archives"
    )
    inspect_anthropic_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_anthropic_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive archive and member paths in JSON output",
    )
    google = subparsers.add_parser(
        "import-google",
        help="import Google My Activity from Takeout ZIPs or an extracted export",
    )
    google.add_argument(
        "source",
        help="Takeout ZIP, folder of split ZIPs, extracted Takeout, or MyActivity file",
    )
    google.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect = subparsers.add_parser(
        "inspect-google",
        help="inventory every member of Takeout ZIPs or an extracted export",
    )
    inspect.add_argument("source", help="Takeout ZIP, folder of split ZIPs, or extracted Takeout")
    inspect.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
        help="output machine-readable JSON (default) or a human-readable table",
    )
    google_all = subparsers.add_parser(
        "import-google-all",
        help="inventory and import all members supported by registered Takeout adapters",
    )
    google_all.add_argument(
        "source", help="Takeout ZIP, folder of split ZIPs, or extracted Takeout"
    )
    google_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_apple_parser = subparsers.add_parser(
        "inspect-apple",
        help="recursively inventory official Apple privacy-export ZIPs",
    )
    inspect_apple_parser.add_argument("source", help="Apple export ZIP or directory of ZIPs")
    inspect_apple_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_apple_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive member paths in JSON output",
    )
    apple_all = subparsers.add_parser(
        "import-apple-all",
        help="inventory and import all official Apple privacy-export data members",
    )
    apple_all.add_argument("source", help="Apple export ZIP or directory of ZIPs")
    apple_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_facebook_parser = subparsers.add_parser(
        "inspect-facebook",
        help='inventory an official Facebook "Download Your Information" export ZIP',
    )
    inspect_facebook_parser.add_argument("source", help="Facebook export ZIP or directory of ZIPs")
    inspect_facebook_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_facebook_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive member paths in JSON output",
    )
    facebook_all = subparsers.add_parser(
        "import-facebook-all",
        help="inventory and import all official Facebook export data members",
    )
    facebook_all.add_argument("source", help="Facebook export ZIP or directory of ZIPs")
    facebook_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_imessages_parser = subparsers.add_parser(
        "inspect-imessages",
        help="inventory independently acquired iMazing Messages CSV exports",
    )
    inspect_imessages_parser.add_argument(
        "source", help="Messages CSV or directory containing one or more exports"
    )
    inspect_imessages_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_imessages_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive relative paths in JSON output",
    )
    imessages_all = subparsers.add_parser(
        "import-imessages-all",
        help="import all independently acquired iMazing Messages CSV exports",
    )
    imessages_all.add_argument(
        "source", help="Messages CSV or directory containing one or more exports"
    )
    imessages_all.add_argument(
        "--timezone",
        required=True,
        help="IANA timezone used by the export's timezone-naive timestamps",
    )
    imessages_all.add_argument(
        "--device-label",
        help="optional acquisition label; no device identifier is inferred from CSV content",
    )
    imessages_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    migrate = subparsers.add_parser("migrate", help="upgrade an existing database schema")
    migrate.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    doctor = subparsers.add_parser(
        "doctor",
        help="report the configured database target, schema version, and every "
        "sibling database on the same Postgres instance",
    )
    doctor.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
        help="output machine-readable JSON (default) or a human-readable table",
    )
    doctor.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    search = subparsers.add_parser(
        "search",
        help="provenance-aware full-text search over canonical conversations, events, "
        "records, and assets",
    )
    search.add_argument("query", help="full-text query (supports quotes, OR, and - exclusion)")
    search.add_argument(
        "--kind",
        dest="kinds",
        action="append",
        choices=("conversation", "event", "record", "asset"),
        help="restrict to one or more kinds (repeatable); default is all kinds",
    )
    search.add_argument(
        "--source",
        dest="sources",
        action="append",
        help="restrict to one or more sources (repeatable), e.g. apple, google, facebook",
    )
    search.add_argument(
        "--limit", type=int, default=25, help="maximum number of ranked hits to return"
    )
    search.add_argument(
        "--include-deleted",
        action="store_true",
        help="include provider-deleted (tombstoned) rows; excluded by default",
    )
    search.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
        help="output machine-readable JSON (default) or a human-readable table",
    )
    search.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    candidates = subparsers.add_parser(
        "identity-candidates",
        help="list distinct provider actor_identity values across events and records",
    )
    candidates.add_argument("--source", help="restrict to one source, e.g. apple, google, facebook")
    candidates.add_argument(
        "--min-count", type=int, default=1, help="minimum combined events+records occurrences"
    )
    candidates.add_argument(
        "--include-linked",
        action="store_true",
        help="include values that already have an active identity link; excluded by default",
    )
    candidates.add_argument(
        "--cross-source",
        action="store_true",
        help="list exact normalized-name matches across sources instead -- Phase 2's "
        "review queue, see PHASE2_IMPLEMENTATION_PLAN.md",
    )
    candidates.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
        help="output machine-readable JSON (default) or a human-readable table",
    )
    candidates.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    create_person_parser = subparsers.add_parser(
        "identity-create-person", help="create a new person to link identities to"
    )
    create_person_parser.add_argument("display_name", help="human-readable name for this person")
    create_person_parser.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    people = subparsers.add_parser(
        "identity-people", help="list people and their active identity links"
    )
    people.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
        help="output machine-readable JSON (default) or a human-readable table",
    )
    people.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    merge = subparsers.add_parser(
        "identity-merge",
        help="link a provider actor_identity value to a person, with inspectable evidence",
    )
    merge.add_argument("person_id", type=int)
    merge.add_argument("source")
    merge.add_argument("identity_value")
    merge.add_argument("--note", help="free-text note explaining the evidence for this link")
    merge.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    split = subparsers.add_parser(
        "identity-split",
        help="revert the active identity link for a provider actor_identity value",
    )
    split.add_argument("source")
    split.add_argument("identity_value")
    split.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    not_same_as = subparsers.add_parser(
        "identity-not-same-as",
        help="permanently declare (source, identity_value) is never the given person "
        "(or never auto-linked at all)",
    )
    not_same_as.add_argument("source")
    not_same_as.add_argument("identity_value")
    not_same_as.add_argument(
        "--person-id", type=int, help="omit to block auto-linking this value to anyone"
    )
    not_same_as.add_argument("--note")
    not_same_as.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    rules_run = subparsers.add_parser(
        "identity-rules-run",
        help="auto-link identities that share a normalized email or phone across sources",
    )
    rules_run.add_argument(
        "--dry-run", action="store_true", help="report what would link without writing"
    )
    rules_run.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    derive_sync = subparsers.add_parser(
        "derive-sync",
        help="read the observation change feed and dirty affected derivation partitions",
    )
    derive_sync.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    derive_run = subparsers.add_parser("derive-run", help="compute dirty derivation partitions")
    derive_run.add_argument(
        "--global",
        dest="include_global",
        action="store_true",
        help="also process GLOBAL partitions (skipped by default)",
    )
    derive_run.add_argument(
        "--limit", type=int, default=None, help="maximum number of partitions to process"
    )
    derive_run.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    derive_status = subparsers.add_parser(
        "derive-status", help="report each deriver's active version and partition counts"
    )
    derive_status.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    derive_rebuild = subparsers.add_parser(
        "derive-rebuild",
        help="clear a deriver's (or every deriver's) output and mark it dirty again",
    )
    derive_rebuild.add_argument("--deriver", dest="deriver_name", help="limit to one deriver")
    derive_rebuild.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    derive_promote = subparsers.add_parser(
        "derive-promote", help="promote a shadow deriver version to active"
    )
    derive_promote.add_argument("deriver_name")
    derive_promote.add_argument("version_hash", help="hex-encoded version_hash to promote")
    derive_promote.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    compare_parser = subparsers.add_parser(
        "compare",
        help="rank Phase 3 life-ledger metrics by change between two date ranges",
    )
    compare_parser.add_argument(
        "--focal", required=True, help="YYYY-MM-DD:YYYY-MM-DD, end exclusive"
    )
    compare_parser.add_argument(
        "--baseline", required=True, help="YYYY-MM-DD:YYYY-MM-DD, end exclusive"
    )
    compare_parser.add_argument(
        "--metric", dest="metrics", action="append", default=None,
        help="limit to this metric name (repeatable); default is every metric",
    )
    compare_parser.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    episode_declare = subparsers.add_parser(
        "episode-declare", help="name a period as a declared episode (PHASE4_IMPLEMENTATION_PLAN.md)"
    )
    episode_declare.add_argument("--start", required=True, help="YYYY-MM-DD")
    episode_declare.add_argument("--end", required=True, help="YYYY-MM-DD")
    episode_declare.add_argument("--label", required=True)
    episode_declare.add_argument("--note")
    episode_declare.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    episode_list = subparsers.add_parser("episode-list", help="list declared/derived/detected episodes")
    episode_list.add_argument("--kind", choices=("declared", "derived", "detected"))
    episode_list.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    declare_add = subparsers.add_parser("declare-add", help="record a user-asserted declaration")
    declare_add.add_argument(
        "kind",
        choices=(
            "episode",
            "identity_same_as",
            "identity_not_same_as",
            "metric_definition",
            "label",
            "egress_override",
            "exclude",
            "purge",
        ),
    )
    declare_add.add_argument("--payload", required=True, help="JSON payload for this declaration")
    declare_add.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    policy_show = subparsers.add_parser(
        "policy-show", help="show each source's current sensitivity/egress defaults"
    )
    policy_show.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    contract_declare = subparsers.add_parser(
        "contract-declare", help="declare or update a source's observability contract"
    )
    contract_declare.add_argument("source")
    contract_declare.add_argument(
        "--observable-kinds",
        required=True,
        help="comma-separated kinds this source can observe, e.g. conversation,event,record,asset",
    )
    contract_declare.add_argument(
        "--covered-start", help="ISO date; omit for an unbounded start"
    )
    contract_declare.add_argument("--covered-end", help="ISO date; omit for an unbounded end")
    contract_declare.add_argument(
        "--gap-semantics",
        required=True,
        choices=("not_exported", "deleted_by_provider", "device_absent", "unknown"),
    )
    contract_declare.add_argument(
        "--default-timezone", required=True, help="IANA timezone name, e.g. America/Chicago"
    )
    contract_declare.add_argument("--notes")
    contract_declare.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    contract_show = subparsers.add_parser(
        "contract-show", help="show declared source_contract rows"
    )
    contract_show.add_argument("source", nargs="?", help="limit to one source")
    contract_show.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    asof_parser = subparsers.add_parser(
        "asof", help="reconstruct canonical rows visible as of a historical import run cutoff"
    )
    asof_parser.add_argument(
        "--object",
        required=True,
        choices=("conversations", "events", "records", "assets", "relationships"),
    )
    cutoff_group = asof_parser.add_mutually_exclusive_group(required=True)
    cutoff_group.add_argument("--cutoff-run", type=int, dest="cutoff_run_id")
    cutoff_group.add_argument("--cutoff-at", dest="cutoff_at", help="ISO timestamp")
    asof_parser.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    return parser


def _batch_summary(parsed_exports: Sequence, results: Sequence) -> dict:
    files = [
        parsed.model_dump(
            mode="json",
            include={
                "category",
                "filename",
                "original_path",
                "sha256",
                "size_bytes",
                "exported_at",
                "export_id",
                "parser_version",
            },
        )
        | result.model_dump(
            exclude={
                "source_records",
                "database_records",
                "source_assets",
                "database_assets",
                "inserted_records",
                "updated_records",
                "unchanged_records",
                "stale_records",
                "conflicted_records",
                "tombstoned_records",
                "new_record_versions",
                "reused_record_versions",
                "inserted_assets",
                "updated_assets",
                "unchanged_assets",
                "stale_assets",
                "conflicted_assets",
                "tombstoned_assets",
                "new_asset_versions",
                "reused_asset_versions",
            }
        )
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


def _google_member_started(original_path: str, category: str, size_bytes: int) -> None:
    print(
        f"[google] parsing member={json.dumps(original_path)} "
        f"category={category} size_bytes={size_bytes}",
        file=sys.stderr,
        flush=True,
    )


def _google_member_parsed(parsed) -> None:
    print(
        f"[google] parsed member={json.dumps(parsed.original_path)} "
        f"category={parsed.category} size_bytes={parsed.size_bytes} "
        f"source_events={parsed.event_count}",
        file=sys.stderr,
        flush=True,
    )


def _google_member_imported(parsed, result) -> None:
    print(
        f"[google] imported member={json.dumps(parsed.original_path)} "
        f"category={parsed.category} source_events={result.source_events} "
        f"inserted={result.inserted_events} unchanged={result.unchanged_events} "
        f"conflicted={result.conflicted_events}",
        file=sys.stderr,
        flush=True,
    )


def _google_all_member_started(member, adapter) -> None:
    print(
        f"[google-all] parsing member={json.dumps(member.source_locator)} "
        f"product={member.product_key} adapter={adapter.adapter_name} "
        f"size_bytes={member.uncompressed_size}",
        file=sys.stderr,
        flush=True,
    )


def _google_all_member_imported(member, parsed, result) -> None:
    print(
        f"[google-all] imported member={json.dumps(member.source_locator)} "
        f"product={member.product_key} records={result.source_records} "
        f"events={result.source_events} conversations={result.source_conversations} "
        f"assets={result.source_assets} short_circuited={str(result.short_circuited).lower()} "
        f"conflicts="
        f"{result.conflicted_records + result.conflicted_events + result.conflicted_conversations + result.conflicted_assets}",
        file=sys.stderr,
        flush=True,
    )


def _apple_member_started(member, adapter) -> None:
    print(
        f"[apple-all] parsing member_id={member.member_id} "
        f"product={member.product_key} adapter={adapter.adapter_name} "
        f"size_bytes={member.uncompressed_size}",
        file=sys.stderr,
        flush=True,
    )


def _apple_member_imported(member, parsed, result) -> None:
    conflicts = (
        result.conflicted_records
        + result.conflicted_events
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[apple-all] imported member_id={member.member_id} product={member.product_key} "
        f"records={result.source_records} events={result.source_events} "
        f"assets={result.source_assets} relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _facebook_member_started(member, adapter) -> None:
    print(
        f"[facebook-all] parsing member_id={member.member_id} "
        f"product={member.product_key} adapter={adapter.adapter_name} "
        f"size_bytes={member.uncompressed_size}",
        file=sys.stderr,
        flush=True,
    )


def _facebook_member_imported(member, parsed, result) -> None:
    conflicts = (
        result.conflicted_conversations
        + result.conflicted_records
        + result.conflicted_events
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[facebook-all] imported member_id={member.member_id} product={member.product_key} "
        f"conversations={result.source_conversations} records={result.source_records} "
        f"events={result.source_events} assets={result.source_assets} "
        f"relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _imessages_member_started(member) -> None:
    print(
        f"[imessages] parsing member_id={member.member_id} "
        f"records={member.records} size_bytes={member.size_bytes}",
        file=sys.stderr,
        flush=True,
    )


def _imessages_member_imported(member, parsed, result) -> None:
    conflicts = (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[imessages] imported member_id={member.member_id} category={parsed.category} "
        f"conversations={result.source_conversations} events={result.source_events} "
        f"records={result.source_records} assets={result.source_assets} "
        f"relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _openai_member_started(member) -> None:
    print(
        f"[openai] parsing member_id={member.member_id} "
        f"category={member.accounting_category} size_bytes={member.size_bytes}",
        file=sys.stderr,
        flush=True,
    )


def _openai_member_imported(member, parsed, result) -> None:
    conflicts = (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[openai] imported member_id={member.member_id} category={member.accounting_category} "
        f"conversations={result.source_conversations} events={result.source_events} "
        f"records={result.source_records} assets={result.source_assets} "
        f"relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _anthropic_member_started(source_id: str, category: str, size_bytes: int) -> None:
    print(
        f"[anthropic] parsing source_id={source_id} "
        f"category={category} size_bytes={size_bytes}",
        file=sys.stderr,
        flush=True,
    )


def _anthropic_member_imported(source_id: str, parsed, result) -> None:
    conflicts = (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[anthropic] imported source_id={source_id} category={parsed.category} "
        f"conversations={result.source_conversations} events={result.source_events} "
        f"records={result.source_records} assets={result.source_assets} "
        f"relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _resolve_guarded_engine(explicit_url: str | None, *, canonical_import: bool) -> Engine:
    url = database_url(explicit_url)
    guard_acceptance_run_not_canonical(url)
    if canonical_import:
        guard_canonical_import_not_test_like(url)
    return create_database_engine(url)


def _classify_database_name(name: str) -> str:
    if name == canonical_database_name():
        return "canonical"
    if is_test_like_database_name(name):
        return "test-like"
    return "unclassified"


def _build_doctor_report(explicit_url: str | None) -> dict:
    # Deliberately does not call _resolve_guarded_engine: doctor must be able
    # to report on any target, including one the import guards would refuse.
    url = database_url(explicit_url)
    parsed = make_url(url)
    target_name = parsed.database or ""
    target_classification = _classify_database_name(target_name)
    pdl_env = os.getenv("PDL_ENV", "production")

    engine = create_database_engine(url)
    schema_version = current_schema_version(engine)
    siblings = [
        {
            "name": name,
            "size_bytes": size_bytes,
            "classification": _classify_database_name(name),
            "is_target": name == target_name,
        }
        for name, size_bytes in list_sibling_databases(engine)
    ]

    suspicious: list[str] = []
    if pdl_env not in {"production", "acceptance"}:
        suspicious.append(f"PDL_ENV={pdl_env!r} is not 'production' or 'acceptance'")
    if target_classification == "unclassified":
        suspicious.append(
            f"target database '{target_name}' is neither the configured canonical "
            f"name ('{canonical_database_name()}') nor a recognized test/acceptance "
            f"naming pattern"
        )

    return {
        "repo_version": __version__,
        "pdl_env": pdl_env,
        "target_host": parsed.host,
        "target_port": parsed.port,
        "target_database": target_name,
        "target_classification": target_classification,
        "canonical_database_name": canonical_database_name(),
        "schema_version": schema_version,
        "latest_schema_version": LATEST_SCHEMA_VERSION,
        "schema_up_to_date": schema_version == LATEST_SCHEMA_VERSION,
        "sibling_databases": siblings,
        "test_like_sibling_count": sum(
            1 for sibling in siblings if sibling["classification"] == "test-like"
        ),
        "suspicious_config": suspicious,
    }


def _render_doctor_table(report: dict) -> str:
    lines = [
        f"personal-data-lab doctor - repo version {report['repo_version']}",
        f"PDL_ENV: {report['pdl_env']}",
        f"target: {report['target_database']} @ {report['target_host']}:{report['target_port']}",
        (
            f"target classification: {report['target_classification']} "
            f"(canonical name configured as '{report['canonical_database_name']}')"
        ),
        (
            f"schema version: {report['schema_version']} "
            f"({'up to date' if report['schema_up_to_date'] else 'OUTDATED'}, "
            f"latest={report['latest_schema_version']})"
        ),
        "",
        (
            f"sibling databases on this instance ({len(report['sibling_databases'])} found, "
            f"{report['test_like_sibling_count']} test-like):"
        ),
    ]
    for sibling in report["sibling_databases"]:
        marker = " <- THIS TARGET" if sibling["is_target"] else ""
        lines.append(
            f"  {sibling['name']:<50} {sibling['classification']:<12} "
            f"{sibling['size_bytes']:>14,} bytes{marker}"
        )
    lines.append("")
    if report["suspicious_config"]:
        lines.append("suspicious config:")
        lines.extend(f"  - {item}" for item in report["suspicious_config"])
    else:
        lines.append("suspicious config: none")
    return "\n".join(lines)


def _build_search_report(
    query: str,
    *,
    kinds: Sequence[str] | None,
    sources: Sequence[str] | None,
    limit: int,
    include_deleted: bool,
    explicit_url: str | None,
) -> dict:
    engine = _resolve_guarded_engine(explicit_url, canonical_import=False)
    results = search_canonical(
        engine,
        query,
        kinds=tuple(kinds) if kinds else ALL_KINDS,
        sources=tuple(sources) if sources else (),
        limit=limit,
        include_deleted=include_deleted,
    )
    return results.model_dump(mode="json")


def _render_search_table(report: dict) -> str:
    lines = [
        (
            f"personal-data-lab search: {report['query']!r} "
            f"({len(report['hits'])} of up to {report['limit']} hits)"
        ),
        "",
    ]
    for hit in report["hits"]:
        title = hit["title"] or "(untitled)"
        lines.append(
            f"[{hit['kind']:<12}] {hit['source']:<10} rank={hit['rank']:.4f} "
            f"occurred_at={hit['occurred_at'] or '-'} {title}"
        )
        if hit["snippet"]:
            lines.append(f"    {hit['snippet']}")
        lines.append(
            f"    namespace={hit['namespace']} id={hit['identifier']} "
            f"canonical_run_id={hit['canonical_run_id']} lifecycle={hit['lifecycle_state']}"
        )
    if not report["hits"]:
        lines.append("(no hits)")
    return "\n".join(lines)


def _build_identity_candidates_report(
    *,
    source: str | None,
    min_count: int,
    include_linked: bool,
    explicit_url: str | None,
) -> dict:
    engine = _resolve_guarded_engine(explicit_url, canonical_import=False)
    candidates = list_identity_candidates(
        engine, source=source, min_count=min_count, include_linked=include_linked
    )
    return {"candidates": [candidate.model_dump(mode="json") for candidate in candidates]}


def _render_identity_candidates_table(report: dict) -> str:
    lines = [f"{len(report['candidates'])} identity candidate(s)", ""]
    for candidate in report["candidates"]:
        link = (
            f"-> person {candidate['linked_person_id']} ({candidate['linked_display_name']})"
            if candidate["linked_person_id"]
            else "(unlinked)"
        )
        lines.append(
            f"[{candidate['source']:<10}] {candidate['identity_value']:<40} "
            f"n={candidate['occurrences']:<6} {link}"
        )
    return "\n".join(lines)


def _build_identity_people_report(explicit_url: str | None) -> dict:
    engine = _resolve_guarded_engine(explicit_url, canonical_import=False)
    return {"people": [person.model_dump(mode="json") for person in list_people(engine)]}


def _render_identity_people_table(report: dict) -> str:
    lines = [f"{len(report['people'])} people", ""]
    for person in report["people"]:
        lines.append(
            f"[{person['id']}] {person['display_name']} "
            f"({len(person['linked_identities'])} linked identities)"
        )
        for linked in person["linked_identities"]:
            lines.append(f"    {linked['source']}: {linked['identity_value']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    # Real actor_identity/title/filename values routinely contain non-ASCII text (accented
    # names, emoji); Windows consoles default to a codepage (e.g. cp1252) that can't encode
    # them, so printing real data would crash with UnicodeEncodeError. Force UTF-8 output on
    # any stream that supports it (a plain TextIOWrapper does; pytest's capsys fixture may not).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    if args.command == "import-openai":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        source = Path(args.source).expanduser()
        if source.is_file() and source.name.casefold() == "conversations.json":
            parsed = parse_openai_export(source)
            result = import_openai_export(engine, parsed)
            print(json.dumps(result.model_dump() | {"reconciled": result.reconciled}, indent=2))
            return 2 if result.has_conflicts else 0
        summary = import_openai_all(
            engine,
            args.source,
            on_member_start=_openai_member_started,
            on_member_imported=_openai_member_imported,
        )
        for item in summary.coverage.values():
            if item.failures:
                print(
                    f"[openai] failed member_id={item.member_id} "
                    f"category={item.accounting_category}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2)
        )
        return summary.exit_code
    if args.command == "inspect-openai":
        inventory = inspect_openai(args.source)
        if args.output_format == "table":
            print(
                "\n".join(
                    (
                        f"physical members: {inventory.physical_members_total}",
                        f"logical files: {inventory.logical_files_total}",
                        f"conversation shards: {inventory.conversation_shards}",
                        f"structured JSON files: {inventory.structured_json_files}",
                        f"binary assets: {inventory.binary_asset_files}",
                        f"rendered HTML files: {inventory.rendered_html_files}",
                        f"unknown files: {inventory.unknown_files}",
                    )
                )
            )
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_files else 0
    if args.command in {"import-anthropic", "import-anthropic-all"}:
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_anthropic_all(
            engine,
            args.manifest,
            on_member_start=_anthropic_member_started,
            on_member_imported=_anthropic_member_imported,
        )
        print(json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2))
        return summary.exit_code
    if args.command == "inspect-anthropic":
        inventory = inspect_anthropic(args.manifest)
        if args.output_format == "table":
            print(render_anthropic_table(inventory))
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_files else 0
    if args.command == "import-google":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        parsed_exports = []
        results = []
        for parsed in iter_google_exports(
            args.source,
            on_member_start=_google_member_started,
        ):
            _google_member_parsed(parsed)
            result = import_parsed_exports(engine, (parsed,))[0]
            _google_member_imported(parsed, result)
            parsed_exports.append(parsed.model_copy(update={"events": (), "conversations": ()}))
            results.append(result)
        print(json.dumps(_batch_summary(parsed_exports, results), indent=2))
        return 2 if any(result.has_conflicts for result in results) else 0
    if args.command == "inspect-google":
        inventory = inspect_google(args.source)
        if args.output_format == "table":
            print(render_inventory_table(inventory))
        else:
            print(json.dumps(inventory.model_dump(mode="json"), indent=2))
        return 5 if inventory.unknown_members_count else 0
    if args.command == "import-google-all":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_google_all(
            engine,
            args.source,
            on_member_start=_google_all_member_started,
            on_member_imported=_google_all_member_imported,
        )
        for product in summary.coverage.values():
            for failed_member in product.failed_members:
                print(
                    f"[google-all] failed member={json.dumps(failed_member)}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(
                summary.model_dump(mode="json") | {"exit_code": summary.exit_code},
                indent=2,
            )
        )
        return summary.exit_code
    if args.command == "inspect-apple":
        inventory = inspect_apple(args.source)
        if args.output_format == "table":
            print(render_apple_table(inventory))
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_members_count else 0
    if args.command == "import-apple-all":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_apple_all(
            engine,
            args.source,
            on_member_start=_apple_member_started,
            on_member_imported=_apple_member_imported,
        )
        for product in summary.coverage.values():
            for member_id in product.failed_member_ids:
                print(
                    f"[apple-all] failed member_id={member_id} product={product.product_key}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2)
        )
        return summary.exit_code
    if args.command == "inspect-facebook":
        inventory = inspect_facebook(args.source)
        if args.output_format == "table":
            print(render_facebook_table(inventory))
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_members_count else 0
    if args.command == "import-facebook-all":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_facebook_all(
            engine,
            args.source,
            on_member_start=_facebook_member_started,
            on_member_imported=_facebook_member_imported,
        )
        for product in summary.coverage.values():
            for member_id in product.failed_member_ids:
                print(
                    f"[facebook-all] failed member_id={member_id} product={product.product_key}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2)
        )
        return summary.exit_code
    if args.command == "inspect-imessages":
        inventory = inspect_imessages(args.source)
        if args.output_format == "table":
            print(render_imessages_table(inventory))
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_files else 0
    if args.command == "import-imessages-all":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_imessages_all(
            engine,
            args.source,
            timezone_name=args.timezone,
            device_label=args.device_label,
            on_member_start=_imessages_member_started,
            on_member_imported=_imessages_member_imported,
        )
        for item in summary.coverage.values():
            if item.failures:
                print(
                    f"[imessages] failed member_id={item.member_id}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2)
        )
        return summary.exit_code
    if args.command == "migrate":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
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
    if args.command == "doctor":
        report = _build_doctor_report(args.database_url)
        if args.output_format == "table":
            print(_render_doctor_table(report))
        else:
            print(json.dumps(report, indent=2))
        return 0
    if args.command == "search":
        report = _build_search_report(
            args.query,
            kinds=args.kinds,
            sources=args.sources,
            limit=args.limit,
            include_deleted=args.include_deleted,
            explicit_url=args.database_url,
        )
        if args.output_format == "table":
            print(_render_search_table(report))
        else:
            print(json.dumps(report, indent=2))
        return 0
    if args.command == "identity-candidates" and args.cross_source:
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        candidates = cross_source_name_candidates(engine)
        report = {
            "candidates": [
                {"normalized_name": c.normalized_name, "members": [list(m) for m in c.members]}
                for c in candidates
            ]
        }
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "identity-candidates":
        report = _build_identity_candidates_report(
            source=args.source,
            min_count=args.min_count,
            include_linked=args.include_linked,
            explicit_url=args.database_url,
        )
        if args.output_format == "table":
            print(_render_identity_candidates_table(report))
        else:
            print(json.dumps(report, indent=2))
        return 0
    if args.command == "identity-create-person":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        person = create_person(engine, args.display_name)
        print(json.dumps(person.model_dump(mode="json"), indent=2))
        return 0
    if args.command == "identity-people":
        report = _build_identity_people_report(args.database_url)
        if args.output_format == "table":
            print(_render_identity_people_table(report))
        else:
            print(json.dumps(report, indent=2))
        return 0
    if args.command == "identity-merge":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        try:
            link = merge_identity(
                engine, args.person_id, args.source, args.identity_value, note=args.note
            )
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr, flush=True)
            return 1
        print(json.dumps(link.model_dump(mode="json"), indent=2))
        return 0
    if args.command == "identity-split":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        try:
            link = split_identity(engine, args.source, args.identity_value)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr, flush=True)
            return 1
        print(json.dumps(link.model_dump(mode="json"), indent=2))
        return 0
    if args.command == "identity-not-same-as":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        try:
            declaration = not_same_as_identity(
                engine, args.source, args.identity_value,
                person_id=args.person_id, note=args.note,
            )
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr, flush=True)
            return 1
        print(json.dumps(declaration.model_dump(mode="json"), indent=2))
        return 0
    if args.command == "identity-rules-run":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        outcome = run_rules_tier(engine, dry_run=args.dry_run)
        report = {
            "dry_run": args.dry_run,
            "links": [
                {
                    "normalized_value": link.normalized_value,
                    "shape": link.shape,
                    "members": [list(m) for m in link.members],
                    "person_id": link.person_id,
                    "created_person": link.created_person,
                }
                for link in outcome.links
            ],
            "conflicts": [
                {
                    "normalized_value": conflict.normalized_value,
                    "shape": conflict.shape,
                    "members": [list(m) for m in conflict.members],
                    "person_ids": list(conflict.person_ids),
                }
                for conflict in outcome.conflicts
            ],
        }
        print(json.dumps(report, indent=2))
        return 1 if outcome.conflicts else 0
    if args.command == "derive-sync":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        outcome = derive.sync(engine)
        print(
            json.dumps(
                {
                    "epoch_id": outcome.epoch_id,
                    "dirtied_local_keys": outcome.dirtied_local_keys,
                    "is_noop": outcome.is_noop,
                    "backfilled_derivers": outcome.backfilled_derivers,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "derive-run":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        outcomes = derive.run(engine, include_global=args.include_global, limit=args.limit)
        print(
            json.dumps(
                [
                    {
                        "deriver_name": outcome.deriver_name,
                        "partition_key": outcome.partition_key,
                        "status": outcome.status,
                        "rows_written": outcome.rows_written,
                        "error": outcome.error,
                    }
                    for outcome in outcomes
                ],
                indent=2,
            )
        )
        return 2 if any(outcome.status == "failed" for outcome in outcomes) else 0
    if args.command == "derive-status":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        print(json.dumps(derive.status(engine), indent=2))
        return 0
    if args.command == "derive-rebuild":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        dirtied = derive.rebuild(engine, deriver_name=args.deriver_name)
        print(json.dumps({"dirtied": dirtied}, indent=2))
        return 0
    if args.command == "derive-promote":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        try:
            derive.promote(engine, args.deriver_name, args.version_hash)
        except derive.VersioningError as error:
            print(f"error: {error}", file=sys.stderr, flush=True)
            return 1
        print(
            json.dumps(
                {
                    "promoted": True,
                    "deriver_name": args.deriver_name,
                    "version_hash": args.version_hash,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "compare":
        try:
            focal = parse_date_range(args.focal)
            baseline = parse_date_range(args.baseline)
        except DateRangeError as error:
            print(f"error: {error}", file=sys.stderr, flush=True)
            return 1
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        metric_names = tuple(args.metrics) if args.metrics else None
        ranked = compare_periods(
            engine, focal=focal, baseline=baseline, metric_names=metric_names
        )
        print(json.dumps(ranked, indent=2, default=str))
        return 0
    if args.command == "episode-declare":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        try:
            start = date.fromisoformat(args.start)
            end = date.fromisoformat(args.end)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr, flush=True)
            return 1
        try:
            episode_id = declare_episode(
                engine, start=start, end=end, label=args.label, note=args.note
            )
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr, flush=True)
            return 1
        print(json.dumps({"id": episode_id, "label": args.label}, indent=2))
        return 0
    if args.command == "episode-list":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        episodes = list_episodes(engine, kind=args.kind)
        print(json.dumps(episodes, indent=2, default=str))
        return 0
    if args.command == "declare-add":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as error:
            print(f"error: --payload is not valid JSON: {error}", file=sys.stderr, flush=True)
            return 1
        with engine.begin() as connection:
            declaration_id = connection.execute(
                text(
                    "INSERT INTO declare.declaration (kind, payload) "
                    "VALUES (:kind, CAST(:payload AS jsonb)) RETURNING id"
                ),
                {"kind": args.kind, "payload": json.dumps(payload)},
            ).scalar_one()
        print(json.dumps({"id": declaration_id, "kind": args.kind}, indent=2))
        return 0
    if args.command == "policy-show":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT DISTINCT ON (source) source, sensitivity, egress "
                    "FROM policy.source_policy ORDER BY source, egress_policy_version_id DESC"
                )
            ).all()
        print(
            json.dumps(
                [
                    {"source": row.source, "sensitivity": list(row.sensitivity), "egress": row.egress}
                    for row in rows
                ],
                indent=2,
            )
        )
        return 0
    if args.command == "contract-declare":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        kinds = [k.strip() for k in args.observable_kinds.split(",") if k.strip()]
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO declare.source_contract
                        (source, observable_kinds, covered_range_start, covered_range_end,
                         gap_semantics, default_timezone, notes, updated_at)
                    VALUES
                        (:source, :observable_kinds, :covered_start, :covered_end,
                         :gap_semantics, :default_timezone, :notes, CURRENT_TIMESTAMP)
                    ON CONFLICT (source) DO UPDATE SET
                        contract_version = declare.source_contract.contract_version + 1,
                        observable_kinds = EXCLUDED.observable_kinds,
                        covered_range_start = EXCLUDED.covered_range_start,
                        covered_range_end = EXCLUDED.covered_range_end,
                        gap_semantics = EXCLUDED.gap_semantics,
                        default_timezone = EXCLUDED.default_timezone,
                        notes = EXCLUDED.notes,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "source": args.source,
                    "observable_kinds": kinds,
                    "covered_start": args.covered_start,
                    "covered_end": args.covered_end,
                    "gap_semantics": args.gap_semantics,
                    "default_timezone": args.default_timezone,
                    "notes": args.notes,
                },
            )
        print(json.dumps({"source": args.source, "observable_kinds": kinds}, indent=2))
        return 0
    if args.command == "contract-show":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        clause = "WHERE source = :source" if args.source else ""
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT source, contract_version, observable_kinds, covered_range_start, "
                    f"covered_range_end, gap_semantics, default_timezone, notes, updated_at "
                    f"FROM declare.source_contract {clause} ORDER BY source"
                ),
                {"source": args.source} if args.source else {},
            ).all()
        print(
            json.dumps(
                [
                    {
                        "source": row.source,
                        "contract_version": row.contract_version,
                        "observable_kinds": list(row.observable_kinds),
                        "covered_range_start": (
                            row.covered_range_start.isoformat()
                            if row.covered_range_start
                            else None
                        ),
                        "covered_range_end": (
                            row.covered_range_end.isoformat() if row.covered_range_end else None
                        ),
                        "gap_semantics": row.gap_semantics,
                        "default_timezone": row.default_timezone,
                        "notes": row.notes,
                        "updated_at": row.updated_at.isoformat(),
                    }
                    for row in rows
                ],
                indent=2,
            )
        )
        return 0
    if args.command == "asof":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
        cutoff_at = datetime.fromisoformat(args.cutoff_at) if args.cutoff_at else None
        with engine.connect() as connection:
            try:
                cutoff_run_id = asof.resolve_cutoff_run_id(
                    connection, cutoff_run_id=args.cutoff_run_id, cutoff_at=cutoff_at
                )
            except ValueError as error:
                print(f"error: {error}", file=sys.stderr, flush=True)
                return 1
            rows = asof.AS_OF_FUNCTIONS[args.object](connection, cutoff_run_id)
        print(
            json.dumps(
                {"cutoff_run_id": cutoff_run_id, "count": len(rows), "rows": rows},
                indent=2,
                default=str,
            )
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
