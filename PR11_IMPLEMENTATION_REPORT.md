# PR11 implementation report: long tail and exact Google closure

## Scope

PR11 implements the remaining 25 products in the 68-product Google Takeout contract: Access Log
Activity, Alerts, Arts & Culture, Assignments, Blogger, Chrome non-My-Activity data, Classroom,
Content returned from your appeal, Discover, Google Ads, Google Business Profile, Google
Developers, Google Earth, Google Feedback, Google Health, Google Help Communities, Google Play
Games Services, Google Play Movies & TV, Google Product Surveys, Google Workspace Marketplace,
News, Search Contributions, Search Notifications, Tenor, and YouTube/YouTube Music
non-My-Activity data.

The final audit also identified 774 provider-supplied files under top-level My Activity that the
PR4 activity parser correctly did not claim. PR11 indexes those documents, images, audio, video,
and extensionless files as content-addressed external assets; it does not reinterpret them as
activity events.

## Implementation

- Delimiter-aware CSV handling supports both comma and observed tab-delimited exports.
- JSON collections are expanded where their provider wrapper carries independent Chrome or
  Google Developers objects; unknown fields remain under `metadata.google`.
- Chrome devices, bookmarks, extensions, preferences, apps, search engines, and autofill data
  remain distinct durable records.
- YouTube channel, playlist, subscription, comment, chat, upload, song, location, and caption
  rows are typed without merging overlapping My Activity evidence.
- HTML tables and bookmark anchors are preserved structurally where present. XML, Atom, KML,
  text, and extensionless provider documents receive conservative searchable projections.
- Empty objects/files produce zero invented objects. Every other unmatched format is streamed to
  SHA-256 and retained as an external asset; binary bytes never enter PostgreSQL.
- Provider IDs are preferred and namespaced by the source surface. No network enrichment, media
  fetching, geocoding, health inference, legal interpretation, or cross-product deduplication is
  performed.

## Real-export acceptance

Every observed PR11 structured member parsed read-only with zero failures or warnings:

- Access Log Activity: 2 CSV members / 17.10 MB and 104,216 events.
- Alerts: 1 empty JSON member.
- Google Business Profile: 2 JSON members and 1 record.
- Chrome: 9 members / 6.58 MB and 12,323 records.
- Discover: 3 CSV members and 2 persistent setting/follow records.
- Google Developers: 5 JSON members and 18 records.
- Google Feedback: 6 records.
- Google Play Games Services: 7 HTML members, 10 records, and representative PNG assets.
- Google Play Movies & TV: 5 empty JSON members.
- Google Product Surveys: 84 records.
- Google Workspace Marketplace: 1 extensionless text record.
- News: 5 zero-byte text members.
- Search Contributions: 5 records.
- Search Notifications: 7 records.
- Tenor: 5 records.
- YouTube/YouTube Music: all 34 CSV members plus 2 activity HTML members parsed into 1,094
  records and 1,994 events; representative MP4s were content-addressed without loading the
  10.48 GB video corpus into memory.

Arts & Culture, Assignments, Blogger, Classroom, appeal content, Google Ads, Google Earth, Google
Health, and Google Help Communities are absent from this export and have synthetic structured and
asset contract fixtures.

## Exact closure audit

The unchanged retained export contains 68 contract products, 51 present products, 17 absent
products, 25,427 data members, and 146,248,662,729 data bytes. Deterministic registry resolution
produces:

- 25,427 resolved data members;
- zero unknown inventory members;
- zero ambiguous members;
- zero unsupported members;
- zero generic-fallback members;
- zero raw-preserved members.

This is exact routing/accounting closure for the retained Takeout generation. External assets are
read and hashed only during import or representative canaries; routing audit alone does not claim
that every one of the 146.25 GB of binary payloads was re-read.

## Verification

- Ruff: clean.
- Combined PR5–PR11 unit suite: 147 passed.
- PostgreSQL integration suite: 35 tests collected and skipped locally when the disposable test
  database variable is absent; stacked GitHub CI is the merge gate for those tests.
- Release artifacts: source distribution and `personal_data_lab-0.11.0-py3-none-any.whl` built
  successfully. Wheel SHA-256:
  `A04336D8FC206737B8F2324956D972F13AE40E9851B3D07F8CE4CD493E3428DA`.
- Wheel manifest includes the PR11 adapter and migration 007.

## Boundaries

No source export was modified. No source payload, personal value, generated inventory, canary log,
database dump, or source-derived fixture is committed. Google ingestion ends at PR11; Apple and
iMessages remain separate future source families with their own provenance.
