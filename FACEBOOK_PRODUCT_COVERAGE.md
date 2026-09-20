# Facebook export product coverage

This is the permanent observed-surface registry for the retained official Facebook "Download Your
Information" export. Coverage is evidence-shaped: every category below is a real (root, category)
path pair found in the retained export, not a category from Facebook's documentation that happens
to be absent.

## Current acceptance summary

- 1 outer ZIP archive, 787,848,832 physical bytes;
- 2,673 recursive entries: 2,673 classified data leaves, zero structural members, zero unknowns;
- all 2,673 data members persist, resume, and short-circuit on exact replay;
- zero failures, unknowns, parse warnings, or conflicts;
- 73 present product groups are `losslessly_structured` / `generic_lossless`; one (`files`) is
  `fully_structured` / `provider_specific`;
- complete replay exits 0 with `production_ready: true`.

See [`FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md`](FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md) for the
accepted real-corpus evidence.

`messages` and `posts` mix the bespoke `facebook_messages`/`facebook_asset` adapters with the
generic `facebook_structured` adapter (settings JSON alongside thread/attachment content, or post
JSON alongside `posts/media/*` attachments), so both report the mixed-product `generic_lossless`
interpretation rather than `provider_specific`, matching the convention PR23 established for Apple.

## Per-product inventory (retained export)

| Product key | Root | Display name | Observed data files | Status | Interpretation |
|---|---|---|---:|---|---|
| account_activity | security_and_login_information | Account Activity | 1 | losslessly_structured | generic_lossless |
| accounts_center | personal_information | Accounts Center | 1 | losslessly_structured | generic_lossless |
| activity_messages | logged_information | Activity Messages | 2 | losslessly_structured | generic_lossless |
| activity_off_meta_technologies | apps_and_websites_off_of_facebook | Your Activity off Meta Technologies | 1 | losslessly_structured | generic_lossless |
| activity_off_meta_technologies_settings | apps_and_websites_off_of_facebook | Activity off Meta Technologies Settings | 1 | losslessly_structured | generic_lossless |
| ad_categories_used_to_reach_you | ads_information | Other Categories Used to Reach You | 1 | losslessly_structured | generic_lossless |
| ad_preferences | ads_information | Ad Preferences | 1 | losslessly_structured | generic_lossless |
| ads_outside_meta_settings | ads_information | Settings for Ads Shown Outside of Meta | 1 | losslessly_structured | generic_lossless |
| ads_viewed | ads_information | Ads Viewed | 1 | losslessly_structured | generic_lossless |
| advertisers_interacted_with | ads_information | Advertisers You've Interacted With | 1 | losslessly_structured | generic_lossless |
| advertisers_using_your_activity | ads_information | Advertisers Using Your Activity or Information | 1 | losslessly_structured | generic_lossless |
| ai | your_facebook_activity | AI | 1 | losslessly_structured | generic_lossless |
| app_permissions_granted | apps_and_websites_off_of_facebook | Permissions Granted to Apps | 1 | losslessly_structured | generic_lossless |
| apps_detected_from_activity | ads_information | Apps Detected From Your Activity | 1 | losslessly_structured | generic_lossless |
| apps_detected_on_account | ads_information | Apps Detected on Your Account | 1 | losslessly_structured | generic_lossless |
| avatars_store | personal_information | Avatars Store | 1 | losslessly_structured | generic_lossless |
| browser_cookies | security_and_login_information | Browser Cookies | 1 | losslessly_structured | generic_lossless |
| comments_and_reactions | your_facebook_activity | Comments and Reactions | 11 | losslessly_structured | generic_lossless |
| connected_apps_and_websites | apps_and_websites_off_of_facebook | Connected Apps and Websites | 1 | losslessly_structured | generic_lossless |
| dating | your_facebook_activity | Dating | 19 | losslessly_structured | generic_lossless |
| device_login_cookies | security_and_login_information | Device Login Cookies | 1 | losslessly_structured | generic_lossless |
| email_address_verifications | security_and_login_information | Email Address Verifications | 1 | losslessly_structured | generic_lossless |
| events | your_facebook_activity | Events | 3 | losslessly_structured | generic_lossless |
| facebook_activity_history | security_and_login_information | Your Facebook Activity History | 1 | losslessly_structured | generic_lossless |
| facebook_gaming | your_facebook_activity | Facebook Gaming | 10 | losslessly_structured | generic_lossless |
| facebook_marketplace | your_facebook_activity | Facebook Marketplace | 12 | losslessly_structured | generic_lossless |
| facebook_payments | your_facebook_activity | Facebook Payments | 5 | losslessly_structured | generic_lossless |
| facebook_support | your_facebook_activity | Facebook Support | 1 | losslessly_structured | generic_lossless |
| feed_preferences | preferences | Feed Preferences | 4 | losslessly_structured | generic_lossless |
| files | files | Other Files | 6 | fully_structured | provider_specific |
| followers | connections | Followers | 2 | losslessly_structured | generic_lossless |
| form_submissions | logged_information | Form Submissions | 1 | losslessly_structured | generic_lossless |
| friends | connections | Friends | 7 | losslessly_structured | generic_lossless |
| fundraisers | your_facebook_activity | Fundraisers | 3 | losslessly_structured | generic_lossless |
| groups | your_facebook_activity | Groups | 12 | losslessly_structured | generic_lossless |
| in_app_messages | logged_information | In-App Messages | 1 | losslessly_structured | generic_lossless |
| interactions | logged_information | Interactions | 11 | losslessly_structured | generic_lossless |
| ip_address_activity | security_and_login_information | IP Address Activity | 1 | losslessly_structured | generic_lossless |
| last_login_information | security_and_login_information | Information About Your Last Login | 1 | losslessly_structured | generic_lossless |
| location | logged_information | Location | 5 | losslessly_structured | generic_lossless |
| login_messages_shown | security_and_login_information | Login Messages We Have Shown | 1 | losslessly_structured | generic_lossless |
| login_protection_data | security_and_login_information | Login Protection Data | 1 | losslessly_structured | generic_lossless |
| logins_and_logouts | security_and_login_information | Logins and Logouts | 1 | losslessly_structured | generic_lossless |
| memories | preferences | Memories | 2 | losslessly_structured | generic_lossless |
| messages | your_facebook_activity | Messages | 1,955 | losslessly_structured | generic_lossless |
| messenger_threads_from_ads | ads_information | Messenger Threads Started From an Ad | 1 | losslessly_structured | generic_lossless |
| navigation_bar | your_facebook_activity | Navigation Bar | 6 | losslessly_structured | generic_lossless |
| notifications | logged_information | Notifications | 2 | losslessly_structured | generic_lossless |
| other_activity | your_facebook_activity | Other Activity | 16 | losslessly_structured | generic_lossless |
| other_logged_information | logged_information | Other Logged Information | 7 | losslessly_structured | generic_lossless |
| other_personal_information | personal_information | Other Personal Information | 6 | losslessly_structured | generic_lossless |
| pages | your_facebook_activity | Pages | 4 | losslessly_structured | generic_lossless |
| pages_mentions | ads_information | Your Pages' Mentions | 1 | losslessly_structured | generic_lossless |
| polls | your_facebook_activity | Polls | 1 | losslessly_structured | generic_lossless |
| posts | your_facebook_activity | Posts | 476 | losslessly_structured | generic_lossless |
| posts_from_apps_and_websites | apps_and_websites_off_of_facebook | Posts From Apps and Websites | 1 | losslessly_structured | generic_lossless |
| preferences | preferences | Preferences | 22 | losslessly_structured | generic_lossless |
| profile_information | personal_information | Profile Information | 13 | losslessly_structured | generic_lossless |
| profile_recovery_successes | security_and_login_information | Recent Profile Recovery Successes | 1 | losslessly_structured | generic_lossless |
| recent_reported_conversions | ads_information | Your Recent Reported Conversions | 1 | losslessly_structured | generic_lossless |
| recognized_devices | security_and_login_information | Recognized Devices | 1 | losslessly_structured | generic_lossless |
| record_details | security_and_login_information | Record Details | 1 | losslessly_structured | generic_lossless |
| sampled_locations | ads_information | Your Sampled Locations | 1 | losslessly_structured | generic_lossless |
| saved_items_and_collections | your_facebook_activity | Saved Items and Collections | 2 | losslessly_structured | generic_lossless |
| search_history | logged_information | Search History | 2 | losslessly_structured | generic_lossless |
| shops | your_facebook_activity | Shops | 1 | losslessly_structured | generic_lossless |
| stories | your_facebook_activity | Stories | 2 | losslessly_structured | generic_lossless |
| story_views_recent | ads_information | Story Views in Past 7 Days | 1 | losslessly_structured | generic_lossless |
| tagged_in_activity | your_facebook_activity | Activity You're Tagged In | 1 | losslessly_structured | generic_lossless |
| trusted_devices | security_and_login_information | Trusting Frequently Used Devices | 1 | losslessly_structured | generic_lossless |
| two_factor_authentication | security_and_login_information | Two-Factor Authentication | 1 | losslessly_structured | generic_lossless |
| where_logged_in | security_and_login_information | Where You're Logged In | 1 | losslessly_structured | generic_lossless |
| worlds | personal_information | Worlds | 1 | losslessly_structured | generic_lossless |
| your_places | your_facebook_activity | Your Places | 2 | losslessly_structured | generic_lossless |

74 products present; 0 registered-but-absent (unlike Apple/Google, every registered Facebook
product was observed in this retained export). No product is `partially_structured`, `failed`, or
`not_present_in_export`.
