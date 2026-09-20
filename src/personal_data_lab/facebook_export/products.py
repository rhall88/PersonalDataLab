from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FacebookProduct:
    key: str
    display_name: str
    root: str


# (root, category) -> (key, display_name). The category segment is the export's own
# second path component: either a standalone "name.json" file or a folder name. This
# table is built directly from the retained export's observed structure, not inferred
# from documentation, so an unmatched (root, category) pair is reported as unknown
# rather than silently guessed at.
_PRODUCTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "ads_information",
        "ad_preferences.json",
        "ad_preferences",
        "Ad Preferences",
    ),
    ("ads_information", "ads_viewed.json", "ads_viewed", "Ads Viewed"),
    (
        "ads_information",
        "advertisers_using_your_activity_or_information.json",
        "advertisers_using_your_activity",
        "Advertisers Using Your Activity or Information",
    ),
    (
        "ads_information",
        "advertisers_you've_interacted_with.json",
        "advertisers_interacted_with",
        "Advertisers You've Interacted With",
    ),
    (
        "ads_information",
        "apps_detected_from_your_activity.json",
        "apps_detected_from_activity",
        "Apps Detected From Your Activity",
    ),
    (
        "ads_information",
        "apps_detected_on_your_account.json",
        "apps_detected_on_account",
        "Apps Detected on Your Account",
    ),
    (
        "ads_information",
        "messenger_threads_you_started_by_clicking_on_an_ad.json",
        "messenger_threads_from_ads",
        "Messenger Threads Started From an Ad",
    ),
    (
        "ads_information",
        "other_categories_used_to_reach_you.json",
        "ad_categories_used_to_reach_you",
        "Other Categories Used to Reach You",
    ),
    (
        "ads_information",
        "settings_for_ads_shown_outside_of_meta.json",
        "ads_outside_meta_settings",
        "Settings for Ads Shown Outside of Meta",
    ),
    (
        "ads_information",
        "story_views_in_past_7_days.json",
        "story_views_recent",
        "Story Views in Past 7 Days",
    ),
    ("ads_information", "your_pages_mentions.json", "pages_mentions", "Your Pages' Mentions"),
    (
        "ads_information",
        "your_recent_reported_conversions.json",
        "recent_reported_conversions",
        "Your Recent Reported Conversions",
    ),
    (
        "ads_information",
        "your_sampled_locations.json",
        "sampled_locations",
        "Your Sampled Locations",
    ),
    (
        "apps_and_websites_off_of_facebook",
        "connected_apps_and_websites.json",
        "connected_apps_and_websites",
        "Connected Apps and Websites",
    ),
    (
        "apps_and_websites_off_of_facebook",
        "permissions_you_have_granted_to_apps.json",
        "app_permissions_granted",
        "Permissions Granted to Apps",
    ),
    (
        "apps_and_websites_off_of_facebook",
        "posts_from_apps_and_websites.json",
        "posts_from_apps_and_websites",
        "Posts From Apps and Websites",
    ),
    (
        "apps_and_websites_off_of_facebook",
        "your_activity_off_meta_technologies.json",
        "activity_off_meta_technologies",
        "Your Activity off Meta Technologies",
    ),
    (
        "apps_and_websites_off_of_facebook",
        "your_activity_off_meta_technologies_settings.json",
        "activity_off_meta_technologies_settings",
        "Activity off Meta Technologies Settings",
    ),
    ("connections", "followers", "followers", "Followers"),
    ("connections", "friends", "friends", "Friends"),
    ("files", "", "files", "Other Files"),
    ("logged_information", "activity_messages", "activity_messages", "Activity Messages"),
    ("logged_information", "form_submissions", "form_submissions", "Form Submissions"),
    ("logged_information", "in-app_messages", "in_app_messages", "In-App Messages"),
    ("logged_information", "interactions", "interactions", "Interactions"),
    ("logged_information", "location", "location", "Location"),
    ("logged_information", "notifications", "notifications", "Notifications"),
    (
        "logged_information",
        "other_logged_information",
        "other_logged_information",
        "Other Logged Information",
    ),
    ("logged_information", "search", "search_history", "Search History"),
    ("personal_information", "avatars_store", "avatars_store", "Avatars Store"),
    (
        "personal_information",
        "facebook_accounts_center",
        "accounts_center",
        "Accounts Center",
    ),
    (
        "personal_information",
        "other_personal_information",
        "other_personal_information",
        "Other Personal Information",
    ),
    (
        "personal_information",
        "profile_information",
        "profile_information",
        "Profile Information",
    ),
    ("personal_information", "worlds", "worlds", "Worlds"),
    ("preferences", "feed", "feed_preferences", "Feed Preferences"),
    ("preferences", "memories", "memories", "Memories"),
    ("preferences", "preferences", "preferences", "Preferences"),
    (
        "security_and_login_information",
        "account_activity.json",
        "account_activity",
        "Account Activity",
    ),
    (
        "security_and_login_information",
        "browser_cookies.json",
        "browser_cookies",
        "Browser Cookies",
    ),
    (
        "security_and_login_information",
        "device_login_cookies.json",
        "device_login_cookies",
        "Device Login Cookies",
    ),
    (
        "security_and_login_information",
        "email_address_verifications.json",
        "email_address_verifications",
        "Email Address Verifications",
    ),
    (
        "security_and_login_information",
        "information_about_your_last_login.json",
        "last_login_information",
        "Information About Your Last Login",
    ),
    (
        "security_and_login_information",
        "ip_address_activity.json",
        "ip_address_activity",
        "IP Address Activity",
    ),
    (
        "security_and_login_information",
        "login_messages_we_have_shown.json",
        "login_messages_shown",
        "Login Messages We Have Shown",
    ),
    (
        "security_and_login_information",
        "login_protection_data.json",
        "login_protection_data",
        "Login Protection Data",
    ),
    (
        "security_and_login_information",
        "logins_and_logouts.json",
        "logins_and_logouts",
        "Logins and Logouts",
    ),
    (
        "security_and_login_information",
        "recognized_devices.json",
        "recognized_devices",
        "Recognized Devices",
    ),
    (
        "security_and_login_information",
        "record_details.json",
        "record_details",
        "Record Details",
    ),
    (
        "security_and_login_information",
        "trusting_frequently_used_devices.json",
        "trusted_devices",
        "Trusting Frequently Used Devices",
    ),
    (
        "security_and_login_information",
        "two-factor_authentication.json",
        "two_factor_authentication",
        "Two-Factor Authentication",
    ),
    (
        "security_and_login_information",
        "where_you're_logged_in.json",
        "where_logged_in",
        "Where You're Logged In",
    ),
    (
        "security_and_login_information",
        "your_facebook_activity_history.json",
        "facebook_activity_history",
        "Your Facebook Activity History",
    ),
    (
        "security_and_login_information",
        "your_recent_profile_recovery_successes.json",
        "profile_recovery_successes",
        "Recent Profile Recovery Successes",
    ),
    (
        "your_facebook_activity",
        "activity_you're_tagged_in",
        "tagged_in_activity",
        "Activity You're Tagged In",
    ),
    ("your_facebook_activity", "ai", "ai", "AI"),
    (
        "your_facebook_activity",
        "comments_and_reactions",
        "comments_and_reactions",
        "Comments and Reactions",
    ),
    ("your_facebook_activity", "dating", "dating", "Dating"),
    ("your_facebook_activity", "events", "events", "Events"),
    ("your_facebook_activity", "facebook_gaming", "facebook_gaming", "Facebook Gaming"),
    (
        "your_facebook_activity",
        "facebook_marketplace",
        "facebook_marketplace",
        "Facebook Marketplace",
    ),
    ("your_facebook_activity", "facebook_payments", "facebook_payments", "Facebook Payments"),
    ("your_facebook_activity", "facebook_support", "facebook_support", "Facebook Support"),
    ("your_facebook_activity", "fundraisers", "fundraisers", "Fundraisers"),
    ("your_facebook_activity", "groups", "groups", "Groups"),
    ("your_facebook_activity", "messages", "messages", "Messages"),
    ("your_facebook_activity", "navigation_bar", "navigation_bar", "Navigation Bar"),
    ("your_facebook_activity", "other_activity", "other_activity", "Other Activity"),
    ("your_facebook_activity", "pages", "pages", "Pages"),
    ("your_facebook_activity", "polls", "polls", "Polls"),
    ("your_facebook_activity", "posts", "posts", "Posts"),
    (
        "your_facebook_activity",
        "saved_items_and_collections",
        "saved_items_and_collections",
        "Saved Items and Collections",
    ),
    ("your_facebook_activity", "shops", "shops", "Shops"),
    ("your_facebook_activity", "stories", "stories", "Stories"),
    ("your_facebook_activity", "your_places", "your_places", "Your Places"),
)

PRODUCT_REGISTRY = {key: FacebookProduct(key, display_name, root) for root, _, key, display_name in _PRODUCTS}

_LOOKUP = {(root, category): key for root, category, key, _ in _PRODUCTS}

# Thread-container folders under your_facebook_activity/messages/ whose immediate
# children are per-conversation directories rather than category settings files.
THREAD_CONTAINERS = frozenset(
    {
        "inbox",
        "archived_threads",
        "e2ee_cutover",
        "e2ee_cutover_archived",
        "filtered_threads",
        "message_requests",
    }
)


def product_for(member_path: str) -> FacebookProduct | None:
    """Resolve an observed Facebook export path to its registered product, if any."""

    parts = member_path.split("/")
    root = parts[0]
    if root == "files":
        return PRODUCT_REGISTRY["files"]
    category = parts[1] if len(parts) > 1 else ""
    key = _LOOKUP.get((root, category))
    return PRODUCT_REGISTRY[key] if key else None
