from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SupportLevel = Literal[
    "fully_structured",
    "partially_structured",
    "asset_indexed",
    "raw_preserved",
    "unsupported_known_format",
    "empty_in_export",
    "not_present_in_export",
    "failed",
]


@dataclass(frozen=True)
class GoogleProduct:
    canonical_product_key: str
    takeout_display_names: tuple[str, ...]
    adapter: str
    support_level: SupportLevel
    expected_format_families: tuple[str, ...]
    record_kinds: tuple[str, ...]
    notes: str


_RAW = "No semantic PR5 adapter; inventory and immutable source provenance are retained."


def _product(
    key: str,
    display: str,
    adapter: str,
    formats: str,
    kinds: str,
    *,
    support: SupportLevel = "raw_preserved",
    notes: str = _RAW,
    aliases: tuple[str, ...] = (),
) -> GoogleProduct:
    return GoogleProduct(
        canonical_product_key=key,
        takeout_display_names=(display, *aliases),
        adapter=adapter,
        support_level=support,
        expected_format_families=tuple(formats.split()),
        record_kinds=tuple(kinds.split()),
        notes=notes,
    )


_PRODUCTS = (
    _product("access_log_activity", "Access Log Activity", "account_device_adapter", "json csv", "event"),
    _product("alerts", "Alerts", "notification_feed_adapter", "json csv html", "event subscription"),
    _product("android_device_configuration", "Android Device Configuration Service", "account_commerce_semantic", "json html", "device", support="fully_structured", notes="PR10 device configuration records."),
    _product("arts_culture", "Arts & Culture", "arts_culture_adapter", "json csv html", "generic_google_record"),
    _product("assignments", "Assignments", "education_adapter", "json csv", "assignment event asset"),
    _product("blogger", "Blogger", "publishing_contribution_adapter", "atom xml json html media", "blog_post conversation asset"),
    _product(
        "calendar",
        "Calendar",
        "calendar_semantic",
        "ics",
        "calendar calendar_event_definition",
        support="fully_structured",
        notes="PR6 iCalendar definitions, bounded one-off event projections, and relationships.",
    ),
    _product("chrome", "Chrome", "activity_adapter", "json html csv", "event saved_item"),
    _product("classroom", "Classroom", "education_adapter", "json csv", "classroom_course assignment event asset"),
    _product(
        "contacts",
        "Contacts",
        "contacts_semantic",
        "vcard csv json",
        "contact contact_group asset",
        support="fully_structured",
        notes="PR6 vCard/CSV/JSON contacts with explicit group membership and photo references.",
    ),
    _product("appeal_content", "Content returned from your appeal", "legal_export_adapter", "document archive binary", "document asset"),
    _product("discover", "Discover", "notification_feed_adapter", "json csv html", "event subscription"),
    _product(
        "drive", "Drive", "rich_asset_semantic", "document binary json", "document asset",
        support="fully_structured", notes="PR8 document text/structure extraction plus external assets."
    ),
    _product(
        "flow", "Flow", "ai_knowledge_semantic", "json media", "ai_project conversation asset",
        support="fully_structured", notes="PR9 projects, generated-media records, prompts, and external media."
    ),
    _product(
        "gemini", "Gemini", "ai_knowledge_semantic", "json html media", "conversation ai_project asset",
        support="fully_structured", notes="PR9 lossless HTML conversation documents."
    ),
    _product(
        "gemini_workspace", "Gemini in Workspace", "ai_knowledge_semantic", "json html document",
        "conversation document asset", support="fully_structured",
        notes="PR9 workspace Gemini text/conversation documents."
    ),
    _product("google_account", "Google Account", "account_commerce_semantic", "json html csv", "account device event", support="fully_structured", notes="PR10 account/device documents."),
    _product("google_ads", "Google Ads", "business_ads_adapter", "json csv", "generic_google_record event"),
    _product("business_profile", "Google Business Profile", "business_ads_adapter", "json csv media", "business_profile review asset"),
    _product(
        "google_chat", "Google Chat", "communications_semantic", "json media",
        "conversation event asset", support="fully_structured",
        notes="PR7 message, membership, and external-asset semantics."
    ),
    _product(
        "cloud_search", "Google Cloud Search", "ai_knowledge_semantic", "json csv",
        "developer_resource saved_item", support="fully_structured",
        notes="PR9 structured knowledge records; synthetic-only until observed."
    ),
    _product("google_developers", "Google Developers", "activity_adapter", "json csv html", "developer_resource event"),
    _product("google_earth", "Google Earth", "geo_adapter", "kml kmz json", "map place asset"),
    _product("google_feedback", "Google Feedback", "feedback_survey_adapter", "json html media", "survey_response asset"),
    _product("google_fi", "Google Fi Wireless", "account_commerce_semantic", "json csv", "event account", support="fully_structured", notes="PR10 telecom account/event contract; synthetic-only until observed."),
    _product("google_finance", "Google Finance", "account_commerce_semantic", "json csv", "saved_item", support="fully_structured", notes="PR10 portfolio/settings records."),
    _product("google_health", "Google Health", "health_adapter", "json csv", "health_record"),
    _product("help_communities", "Google Help Communities", "publishing_contribution_adapter", "json csv html", "contribution conversation"),
    _product(
        "google_meet", "Google Meet", "communications_semantic", "json csv document media",
        "conversation event document asset", support="fully_structured",
        notes="PR7 call events and external assets."
    ),
    _product("google_one", "Google One", "account_commerce_semantic", "json csv html", "subscription account", support="fully_structured", notes="PR10 subscription/account documents."),
    _product("google_pay", "Google Pay", "account_commerce_semantic", "json csv html", "payment purchase event", support="fully_structured", notes="PR10 payment transactions and account documents."),
    _product(
        "google_photos", "Google Photos", "rich_asset_semantic", "json image video",
        "photo_metadata video_metadata album asset", support="fully_structured",
        notes="PR8 Photos sidecars, albums, and path-linked external media assets."
    ),
    _product("play_games", "Google Play Games Services", "entertainment_adapter", "json csv", "game_record event"),
    _product("play_movies_tv", "Google Play Movies & TV", "entertainment_adapter", "json csv", "generic_google_record event"),
    _product("play_store", "Google Play Store", "account_commerce_semantic", "json csv", "purchase subscription event", support="fully_structured", notes="PR10 installs, purchases, orders, subscriptions, devices, library, points, and settings."),
    _product("product_surveys", "Google Product Surveys", "feedback_survey_adapter", "json csv", "survey_response"),
    _product("google_shopping", "Google Shopping", "account_commerce_semantic", "json csv", "purchase saved_item event", support="fully_structured", notes="PR10 shopping review/event semantics."),
    _product("google_store", "Google Store", "account_commerce_semantic", "json csv", "purchase reservation", support="fully_structured", notes="PR10 store purchase/reservation semantics."),
    _product("google_wallet", "Google Wallet", "account_commerce_semantic", "json csv pdf", "payment reservation", support="fully_structured", notes="PR10 structured records and external documents."),
    _product("workspace_marketplace", "Google Workspace Marketplace", "marketplace_adapter", "json csv", "subscription"),
    _product(
        "groups", "Groups", "communications_semantic", "mbox atom xml json html csv",
        "conversation event group_membership", support="fully_structured",
        notes="PR7 group membership plus bounded streaming MBOX when present."
    ),
    _product("home_app", "Home App", "account_commerce_semantic", "json csv media", "home_device event asset", support="fully_structured", notes="PR10 home datasets and external assets."),
    _product(
        "journal", "Journal", "ai_knowledge_semantic", "json html media", "note asset",
        support="fully_structured", notes="PR9 journal note and nanosecond timestamp semantics."
    ),
    _product(
        "keep", "Keep", "ai_knowledge_semantic", "json html media", "note asset",
        support="fully_structured", notes="PR9 Keep notes with lifecycle flags and microsecond timestamps."
    ),
    _product(
        "mail", "Mail", "gmail_mbox", "mbox json", "conversation event asset mail_setting",
        support="fully_structured",
        notes="PR7 two-pass bounded streaming MBOX plus settings semantics."
    ),
    _product(
        "maps", "Maps", "geo_semantic", "json csv kml kmz xml", "place event",
        support="fully_structured", notes="PR6 semantic geographic adapter."
    ),
    _product(
        "maps_your_places", "Maps (your places)", "geo_semantic", "json csv kml kmz xml",
        "place saved_item", support="fully_structured",
        notes="PR6 semantic geographic adapter."
    ),
    _product(
        "my_activity",
        "My Activity",
        "my_activity",
        "json html",
        "event",
        support="fully_structured",
        notes="PR4 streaming My Activity adapter; JSON and HTML activity members are supported.",
    ),
    _product(
        "my_maps", "My Maps", "geo_semantic", "kml kmz csv json xml", "map place asset",
        support="fully_structured",
        notes="PR6 synthetic contract coverage; real export canary remains not present."
    ),
    _product("nest", "Nest", "account_commerce_semantic", "json jsonl csv media", "home_device event asset", support="partially_structured", notes="PR10 Nest devices and CSV-wrapped JSONL events; malformed provider rows remain explicit opaque records."),
    _product("news", "News", "notification_feed_adapter", "json csv html", "event subscription"),
    _product(
        "notebooklm", "NotebookLM", "ai_knowledge_semantic", "json document media",
        "notebook document asset", support="fully_structured",
        notes="PR9 notebooks, bounded document text, external assets, and explicit membership."
    ),
    _product(
        "phone_audio", "Phone Audio", "rich_asset_semantic", "audio json",
        "audio_recording transcript asset", support="fully_structured",
        notes="PR8 semantic metadata plus external audio assets; synthetic-only until observed."
    ),
    _product(
        "pinpoint", "Pinpoint", "ai_knowledge_semantic", "json csv document", "ai_project document",
        support="fully_structured", notes="PR9 knowledge document contract; synthetic-only until observed."
    ),
    _product("pixel", "Pixel", "account_commerce_semantic", "json csv", "device event", support="fully_structured", notes="PR10 device/event contract; synthetic-only until observed."),
    _product("profile", "Profile", "account_commerce_semantic", "json html media", "account asset", support="fully_structured", notes="PR10 profile/account records and external media."),
    _product("purchases_reservations", "Purchases & Reservations", "account_commerce_semantic", "json csv", "purchase reservation", support="fully_structured", notes="PR10 purchase/reservation contract; synthetic-only until observed."),
    _product(
        "recorder", "Recorder", "rich_asset_semantic", "json audio", "audio_recording transcript asset",
        support="fully_structured", notes="PR8 transcript-to-audio relationships and external audio assets."
    ),
    _product(
        "saved", "Saved", "geo_semantic", "json csv kml kmz xml", "saved_list saved_item place",
        support="fully_structured", notes="PR6 saved-list semantics and ordered membership."
    ),
    _product("search_contributions", "Search Contributions", "publishing_contribution_adapter", "json csv media", "contribution review asset"),
    _product("search_notifications", "Search Notifications", "notification_feed_adapter", "json csv html", "event subscription"),
    _product(
        "street_view", "Street View", "rich_asset_semantic", "json image", "contribution asset",
        support="fully_structured", notes="PR8 semantic metadata plus external imagery; synthetic-only until observed."
    ),
    _product(
        "tasks", "Tasks", "tasks_semantic", "json csv", "task_list task",
        support="fully_structured", notes="PR6 task-list, task, and parent-child semantics."
    ),
    _product("tenor", "Tenor", "tenor_adapter", "json csv media", "event saved_item asset"),
    _product(
        "timeline", "Timeline", "geo_semantic", "json csv kml kmz xml txt",
        "place event map timeline_settings timeline_notice",
        support="fully_structured", notes="PR6 provider-evidenced timeline and geographic semantics."
    ),
    _product(
        "voice", "Voice", "communications_semantic", "json csv html vcard audio media",
        "conversation event transcript asset voice_contact", support="fully_structured",
        notes="PR7 Voice communications, referenced contacts, audio, and media."
    ),
    _product(
        "workspace_studio", "Workspace Studio", "ai_knowledge_semantic", "json document media",
        "ai_project document asset", support="fully_structured",
        notes="PR9 structured project/document records; observed export is empty."
    ),
    _product("youtube", "YouTube and YouTube Music", "activity_adapter", "json csv html media", "event saved_item subscription asset"),
)

PRODUCT_REGISTRY: dict[str, GoogleProduct] = {
    product.canonical_product_key: product for product in _PRODUCTS
}
EXPECTED_68_PRODUCTS: tuple[str, ...] = tuple(product.canonical_product_key for product in _PRODUCTS)
EXPECTED_68_DISPLAY_NAMES: tuple[str, ...] = tuple(
    product.takeout_display_names[0] for product in _PRODUCTS
)


def normalize_display_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


DISPLAY_NAME_TO_KEY: dict[str, str] = {
    normalize_display_name(alias): product.canonical_product_key
    for product in _PRODUCTS
    for alias in product.takeout_display_names
}


def product_for_display_name(value: str) -> GoogleProduct | None:
    key = DISPLAY_NAME_TO_KEY.get(normalize_display_name(value))
    return PRODUCT_REGISTRY.get(key) if key else None


if len(PRODUCT_REGISTRY) != 68 or set(EXPECTED_68_PRODUCTS) != set(PRODUCT_REGISTRY):
    raise RuntimeError("Google product registry must contain exactly the contracted 68 products")
