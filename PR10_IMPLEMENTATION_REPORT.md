# PR10 implementation report: account, commerce, device, and home

## Scope

PR10 implements Google Account, Google One, Google Pay, Google Wallet, Purchases & Reservations,
Google Shopping, Google Store, Google Play Store, Google Finance, Google Fi Wireless, Pixel,
Android Device Configuration Service, Home App, Nest, and Profile.

## Implementation

- Google Pay CSV transactions use durable provider transaction IDs; nested My Activity HTML still
  routes to the PR4 streaming parser.
- Play Store wrapper kinds become typed installs, purchases, orders, devices, subscriptions,
  library items, points transactions, promotions, and account settings.
- Home App named datasets become explicit records without flattening away the dataset identity.
- Nest's observed `.jsonl` is actually CSV-quoted JSON. Valid rows decode structurally. Malformed
  provider rows become explicit opaque `nest_event` objects with line-scoped warnings; no row is
  dropped or speculatively repaired.
- Account, subscription, finance, device, and profile HTML/JSON retain provider-native fields and
  searchable text. Wallet PDFs and profile media remain external SHA-256 assets.
- Google Fi, Pixel, and Purchases & Reservations are synthetically covered because they are absent.

## Real-export acceptance

Every present PR10 member was parsed read-only with zero failures:

- Google Account: 2 records from 2 HTML members.
- Google One: 1 subscription/account record.
- Google Pay: 3 members and 26 transaction/activity events.
- Google Wallet: 1 external PDF asset.
- Google Finance: 2 records.
- Google Shopping: 2 header-only CSV files, explicit empty result.
- Google Store: 2 empty JSON objects, explicit empty result.
- Play Store: 9 JSON members, 101 records, and 169 events.
- Android Device Configuration: 1 record.
- Home App: 8 members and 7 records.
- Nest: all 13 members, 35 events, 31 explicit malformed-row warnings, zero failures.
- Profile: 2 records and 1 external portrait asset.

## Next frontier

PR11 is the remaining long tail and the final exact 68-product reconciliation. It must prove that
every present member resolves to a semantic adapter, explicit asset index, intentional empty state,
or explicit partial/raw status, with no unknown or silently ignored remainder.
