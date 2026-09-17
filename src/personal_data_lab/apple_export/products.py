from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AppleProduct:
    key: str
    display_name: str


_PRODUCTS = (
    ("account_device", "Apple Account and device information"),
    ("app_notifications", "App install and push notification activity"),
    ("apple_media_services", "Apple Media Services"),
    ("apple_music", "Apple Music"),
    ("apps_services_analytics", "Apps and Services Analytics"),
    ("business_chat_support", "Business Chat Support"),
    ("calendars_reminders", "iCloud Calendars and Reminders"),
    ("contacts", "iCloud Contacts"),
    ("developer_program", "Apple Developer Program"),
    ("feedback_assistant", "Feedback Assistant"),
    ("find_my", "Find My"),
    ("freeform", "Freeform"),
    ("game_center", "Game Center"),
    ("icloud_activity_sharing", "iCloud Activity Sharing"),
    ("icloud_bookmarks", "iCloud Bookmarks"),
    ("icloud_books", "iCloud Books"),
    ("icloud_mail", "iCloud Mail"),
    ("icloud_maps", "iCloud Maps"),
    ("icloud_usage", "iCloud Usage"),
    ("icloud_wifi", "iCloud Wi-Fi"),
    ("marketing_communications", "Marketing communications"),
    ("media_entertainment", "Apple TV and Podcast Information"),
    ("messaging_devices", "Devices Registered with Apple Messaging"),
    ("notes", "iCloud Notes"),
    ("other_apple_data", "Other Apple Data"),
    ("recovery_devices", "Recovery Devices"),
    ("siri", "Siri"),
    ("store_activity", "Apple Store and Media Store Activity"),
    ("store_credit", "Store Credit"),
    ("testflight", "TestFlight"),
    ("voice_memos", "Voice Memos"),
    ("wallet", "Wallet Activity"),
)

PRODUCT_REGISTRY = {key: AppleProduct(key, name) for key, name in _PRODUCTS}


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def product_for(outer_archive: str, member_path: str) -> AppleProduct:
    """Resolve an observed Apple surface without relying on user-data filenames."""

    outer = _token(outer_archive.removesuffix(".zip"))
    path = _token(member_path)
    combined = f"{outer} {path}"
    if "apple media services" in combined:
        if "apple music" in path or "music library" in path:
            key = "apple_music"
        elif "testflight" in path:
            key = "testflight"
        elif "game center" in path:
            key = "game_center"
        elif "apps and services analytics" in path:
            key = "apps_services_analytics"
        elif "stores activity" in path or "store activity" in path:
            key = "store_activity"
        elif "apple tv" in path or "podcast" in path:
            key = "media_entertainment"
        else:
            key = "apple_media_services"
    elif "account and device information" in outer or outer == "apple id":
        key = "account_device"
    elif "app install" in outer:
        key = "app_notifications"
    elif "apple online and retail stores" in outer or "apple com and apple store" in outer:
        key = "store_activity"
    elif "applecare" in outer:
        key = "business_chat_support" if "business chat" in path else "other_apple_data"
    elif "feedback assistant" in outer:
        key = "feedback_assistant"
    elif "icloud bookmarks" in outer:
        key = "icloud_bookmarks"
    elif "icloud calendars and reminders" in outer:
        key = "calendars_reminders"
    elif "icloud contacts" in outer:
        key = "contacts"
    elif "icloud notes" in outer:
        key = "notes"
    elif "marketing communications" in outer:
        key = "marketing_communications"
    elif "wallet activity" in outer or "apple pay activity" in outer:
        key = "wallet"
    elif "find my" in path:
        key = "find_my"
    elif "developer program" in path:
        key = "developer_program"
    elif "activitysharing" in path or "activity sharing" in path:
        key = "icloud_activity_sharing"
    elif "freeform" in path:
        key = "freeform"
    elif "ibooks" in path:
        key = "icloud_books"
    elif re.search(r"(^| )mail( |$)", path):
        key = "icloud_mail"
    elif re.search(r"(^| )maps( |$)", path):
        key = "icloud_maps"
    elif "voice memos" in path:
        key = "voice_memos"
    elif "wi fi" in path:
        key = "icloud_wifi"
    elif "icloudusagedata" in path or "icloud usage" in path:
        key = "icloud_usage"
    elif "siri corrections" in path:
        key = "siri"
    elif "devices registered with apple messaging" in path:
        key = "messaging_devices"
    elif "recovery devices" in path:
        key = "recovery_devices"
    elif "store credit" in path:
        key = "store_credit"
    elif "game center" in path:
        key = "game_center"
    else:
        key = "other_apple_data"
    return PRODUCT_REGISTRY[key]
