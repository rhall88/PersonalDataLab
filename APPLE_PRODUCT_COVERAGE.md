# Apple privacy-export product coverage

This is the permanent observed-surface registry for the retained official Apple privacy export.
The per-product file counts below preserve the historical parser-v2 inventory; they are not
parser-v3 counts. Parser v3 has since completed fresh corpus inventory and operational acceptance.
Coverage is evidence-shaped: absent products are not claimed, and independently extracted
iMessages are deliberately excluded.

## Current parser-v3 acceptance summary

- 31 outer ZIP archives and 18 nested ZIP containers;
- 832 recursive entries: 814 classified data leaves, 18 structural containers, and zero unknowns;
- 1,960,490,082 logical leaf bytes processed;
- all 814 data leaves persist, resume, and short-circuit on exact replay;
- zero failures, unknowns, parse warnings, or conflicts;
- 27 present product groups remain `partially_structured`; Business Chat Support, Store Credit,
  and Voice Memos are `fully_structured`; Apple TV and Podcast Information and Siri were not
  present;
- complete replay exits 3 with `production_ready: false` because partial semantic coverage is an
  explicit gate, not because persistence or accounting failed.

See [`PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md`](PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md)
for the accepted parser-v3 evidence.

## Historical parser-v2 per-product inventory

| Product key | Parser-v2 observed data files | Representation |
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
467-member counts remain useful for comparison but are superseded as acceptance evidence by PR16.
Parser v3 correctly reclassifies 347 PDFs as product data, accepts five valid empty TestFlight
datasets, and uses actual parse/import results to finalize support. Generic CSV/JSON/XML coverage
remains explicitly `partially_structured` even when every provider field is preserved. Warnings,
failures, unknowns, and partial support continue to block the stricter `production_ready` gate.
See [`PR14_APPLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR14_APPLE_PRODUCTION_ACCEPTANCE_REPORT.md) for
the historical v2 results and review findings.
