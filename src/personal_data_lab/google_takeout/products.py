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
    _product("android_device_configuration", "Android Device Configuration Service", "account_device_adapter", "json", "device"),
    _product("arts_culture", "Arts & Culture", "arts_culture_adapter", "json csv html", "generic_google_record"),
    _product("assignments", "Assignments", "education_adapter", "json csv", "assignment event asset"),
    _product("blogger", "Blogger", "publishing_contribution_adapter", "atom xml json html media", "blog_post conversation asset"),
    _product("calendar", "Calendar", "planning_adapter", "ics", "calendar calendar_event_definition"),
    _product("chrome", "Chrome", "activity_adapter", "json html csv", "event saved_item"),
    _product("classroom", "Classroom", "education_adapter", "json csv", "classroom_course assignment event asset"),
    _product("contacts", "Contacts", "contacts_adapter", "vcard csv", "contact"),
    _product("appeal_content", "Content returned from your appeal", "legal_export_adapter", "document archive binary", "document asset"),
    _product("discover", "Discover", "notification_feed_adapter", "json csv html", "event subscription"),
    _product("drive", "Drive", "drive_adapter", "document binary json", "document asset"),
    _product("flow", "Flow", "google_ai_adapter", "json media", "ai_project conversation asset"),
    _product("gemini", "Gemini", "google_ai_adapter", "json html media", "conversation ai_project asset"),
    _product("gemini_workspace", "Gemini in Workspace", "google_ai_adapter", "json html document", "conversation document asset"),
    _product("google_account", "Google Account", "account_device_adapter", "json html csv", "account device event"),
    _product("google_ads", "Google Ads", "business_ads_adapter", "json csv", "generic_google_record event"),
    _product("business_profile", "Google Business Profile", "business_ads_adapter", "json csv media", "business_profile review asset"),
    _product("google_chat", "Google Chat", "communications_adapter", "json html media", "conversation event asset"),
    _product("cloud_search", "Google Cloud Search", "search_knowledge_adapter", "json csv", "developer_resource saved_item"),
    _product("google_developers", "Google Developers", "activity_adapter", "json csv html", "developer_resource event"),
    _product("google_earth", "Google Earth", "geo_adapter", "kml kmz json", "map place asset"),
    _product("google_feedback", "Google Feedback", "feedback_survey_adapter", "json html media", "survey_response asset"),
    _product("google_fi", "Google Fi Wireless", "telecom_adapter", "json csv", "event account"),
    _product("google_finance", "Google Finance", "commerce_adapter", "json csv", "saved_item"),
    _product("google_health", "Google Health", "health_adapter", "json csv", "health_record"),
    _product("help_communities", "Google Help Communities", "publishing_contribution_adapter", "json csv html", "contribution conversation"),
    _product("google_meet", "Google Meet", "communications_adapter", "json csv document media", "conversation event document asset"),
    _product("google_one", "Google One", "account_device_adapter", "json csv", "subscription account"),
    _product("google_pay", "Google Pay", "commerce_adapter", "json csv", "payment purchase event"),
    _product("google_photos", "Google Photos", "photos_adapter", "json image video", "photo_metadata video_metadata album asset"),
    _product("play_games", "Google Play Games Services", "entertainment_adapter", "json csv", "game_record event"),
    _product("play_movies_tv", "Google Play Movies & TV", "entertainment_adapter", "json csv", "generic_google_record event"),
    _product("play_store", "Google Play Store", "commerce_adapter", "json csv", "purchase subscription event"),
    _product("product_surveys", "Google Product Surveys", "feedback_survey_adapter", "json csv", "survey_response"),
    _product("google_shopping", "Google Shopping", "commerce_adapter", "json csv", "purchase saved_item event"),
    _product("google_store", "Google Store", "commerce_adapter", "json csv", "purchase reservation"),
    _product("google_wallet", "Google Wallet", "commerce_adapter", "json csv", "payment reservation"),
    _product("workspace_marketplace", "Google Workspace Marketplace", "marketplace_adapter", "json csv", "subscription"),
    _product("groups", "Groups", "communications_adapter", "mbox atom xml json html", "conversation event generic_google_record"),
    _product("home_app", "Home App", "home_iot_adapter", "json csv media", "home_device event asset"),
    _product("journal", "Journal", "notes_adapter", "json html media", "note asset"),
    _product("keep", "Keep", "notes_adapter", "json html media", "note asset"),
    _product("mail", "Mail", "gmail_adapter", "mbox", "conversation event asset"),
    _product("maps", "Maps", "geo_adapter", "json csv html", "place event"),
    _product("maps_your_places", "Maps (your places)", "geo_adapter", "json csv", "place saved_item"),
    _product(
        "my_activity",
        "My Activity",
        "my_activity",
        "json html",
        "event",
        support="fully_structured",
        notes="PR4 streaming My Activity adapter; JSON and HTML activity members are supported.",
    ),
    _product("my_maps", "My Maps", "geo_adapter", "kml kmz csv json", "map place asset"),
    _product("nest", "Nest", "home_iot_adapter", "json csv media", "home_device event asset"),
    _product("news", "News", "notification_feed_adapter", "json csv html", "event subscription"),
    _product("notebooklm", "NotebookLM", "google_ai_adapter", "json document media", "notebook document asset"),
    _product("phone_audio", "Phone Audio", "telecom_adapter", "audio json", "audio_recording transcript asset"),
    _product("pinpoint", "Pinpoint", "search_knowledge_adapter", "json csv document", "ai_project document"),
    _product("pixel", "Pixel", "account_device_adapter", "json csv", "device event"),
    _product("profile", "Profile", "account_device_adapter", "json csv media", "account asset"),
    _product("purchases_reservations", "Purchases & Reservations", "commerce_adapter", "json csv", "purchase reservation"),
    _product("recorder", "Recorder", "recorder_adapter", "json audio", "audio_recording transcript asset"),
    _product("saved", "Saved", "geo_adapter", "json csv", "saved_item place"),
    _product("search_contributions", "Search Contributions", "publishing_contribution_adapter", "json csv media", "contribution review asset"),
    _product("search_notifications", "Search Notifications", "notification_feed_adapter", "json csv html", "event subscription"),
    _product("street_view", "Street View", "publishing_contribution_adapter", "json image", "contribution asset"),
    _product("tasks", "Tasks", "planning_adapter", "json csv", "task"),
    _product("tenor", "Tenor", "tenor_adapter", "json csv media", "event saved_item asset"),
    _product("timeline", "Timeline", "geo_adapter", "json kml", "place event map"),
    _product("voice", "Voice", "communications_adapter", "json csv html audio", "conversation event transcript asset"),
    _product("workspace_studio", "Workspace Studio", "google_ai_adapter", "json document media", "ai_project document asset"),
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
