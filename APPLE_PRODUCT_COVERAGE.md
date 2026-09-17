# Apple privacy-export product coverage

This is the permanent observed-surface registry for the retained official Apple privacy export.
The file counts below describe the parser v2 inventory; v3 requires a fresh corpus inventory.
It is evidence-shaped: absent products are not claimed, and independently extracted iMessages
are deliberately excluded.

| Product key | Observed data files | Representation |
|---|---:|---|
| account_device | 11 | structured records and account/device events |
| app_notifications | 1 | structured notification events and records |
| apple_media_services | 4 | structured media-service records/events |
| apple_music | 26 | library, preference, play, and activity records/events |
| apps_services_analytics | 3 | bounded structured analytics records/events |
| business_chat_support | 3 | text support-transcript records |
| calendars_reminders | 6 | ICS events/reminders plus structured preferences/metadata |
| contacts | 119 | VCF contacts/groups plus structured usage metadata |
| developer_program | 1 | structured account records |
| feedback_assistant | 1 | structured participant records |
| find_my | 3 | structured device, person, and activation records/events |
| freeform | 1 | structured board records |
| game_center | 2 | structured profile/social records/events |
| icloud_activity_sharing | 2 | structured sharing/activity records/events |
| icloud_bookmarks | 2 | structured bookmark and usage records |
| icloud_books | 1 | concatenated-JSON book records |
| icloud_mail | 1 | structured recent-recipient records |
| icloud_maps | 1 | structured map/location-attribute records |
| icloud_usage | 1 | bounded structured usage records/events |
| icloud_wifi | 1 | structured known-network records |
| marketing_communications | 5 | structured subscription/delivery/response records/events |
| media_entertainment | 0 | registered; not present as a distinct retained leaf |
| messaging_devices | 1 | official device-registration metadata, not iMessage content |
| notes | 163 | note records, metadata, external assets, attachment relationships |
| other_apple_data | 8 | structured/text retained Apple surfaces |
| recovery_devices | 1 | structured recovery-device records |
| siri | 1 | structured correction records/events |
| store_activity | 64 | bounded Store account, transaction, review, and activity records/events |
| store_credit | 0 | registered; retained rows route under Store activity in this generation |
| testflight | 7 | structured app, install, session, crash, and feedback records/events |
| voice_memos | 22 | content-addressed external audio assets |
| wallet | 6 | structured cards, tokens, preferences, account, and transaction records/events |

The v2 retained corpus had 29 present registry products and three absent entries. These historical
467-member counts are not a v3 acceptance claim. In v3, generic CSV/JSON/XML members are explicitly
`partially_structured` even when all fields are preserved; native-format support is finalized from
actual parse/import results. Warnings, failures, unknowns, and partial support block the production
ready gate. The revised guide classification may increase asset counts. See
[`PR14_APPLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR14_APPLE_PRODUCTION_ACCEPTANCE_REPORT.md).
