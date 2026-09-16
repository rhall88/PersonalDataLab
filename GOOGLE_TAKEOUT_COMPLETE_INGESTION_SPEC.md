# GOOGLE_TAKEOUT_COMPLETE_INGESTION_SPEC.md

Status: master implementation contract
Repository: rhall88/PersonalDataLab
Depends on:
- PR1 OpenAI foundation
- PR2 Anthropic ingestion
- PR3 cumulative/lossless canonical history
- PR4 Google My Activity ingestion

Objective:
Provide complete, explicit ingestion coverage for every Google product
present in the user's current Google Takeout selection: 68 unique products.

This is an umbrella architecture and implementation specification.

DO NOT implement all 68 products as one giant pull request.

The work MUST be split into coherent implementation phases/PRs while
conforming to this single permanent contract.

======================================================================
1. PRODUCT LIST: THE 68-PRODUCT CONTRACT
======================================================================

The user's current Google Takeout exposes these 68 unique products.

Every product below MUST ultimately have an explicit ingestion disposition:

1. Access Log Activity
2. Alerts
3. Android Device Configuration Service
4. Arts & Culture
5. Assignments
6. Blogger
7. Calendar
8. Chrome
9. Classroom
10. Contacts
11. Content returned from your appeal
12. Discover
13. Drive
14. Flow
15. Gemini
16. Gemini in Workspace
17. Google Account
18. Google Ads
19. Google Business Profile
20. Google Chat
21. Google Cloud Search
22. Google Developers
23. Google Earth
24. Google Feedback
25. Google Fi Wireless
26. Google Finance
27. Google Health
28. Google Help Communities
29. Google Meet
30. Google One
31. Google Pay
32. Google Photos
33. Google Play Games Services
34. Google Play Movies & TV
35. Google Play Store
36. Google Product Surveys
37. Google Shopping
38. Google Store
39. Google Wallet
40. Google Workspace Marketplace
41. Groups
42. Home App
43. Journal
44. Keep
45. Mail
46. Maps
47. Maps (your places)
48. My Activity
49. My Maps
50. Nest
51. News
52. NotebookLM
53. Phone Audio
54. Pinpoint
55. Pixel
56. Profile
57. Purchases & Reservations
58. Recorder
59. Saved
60. Search Contributions
61. Search Notifications
62. Street View
63. Tasks
64. Tenor
65. Timeline
66. Voice
67. Workspace Studio
68. YouTube and YouTube Music

"My Activity" already has an implementation from PR4.

PR4 MUST remain compatible with this architecture rather than being
rewritten unnecessarily.

Complete Google support means every product has one of these explicit states:

- fully_structured
- partially_structured
- asset_indexed
- raw_preserved
- unsupported_known_format
- empty_in_export
- not_present_in_export
- failed

There must NEVER be a product whose data was silently ignored.

======================================================================
2. CORE DESIGN PRINCIPLE
======================================================================

Google Takeout is not one format.

It is a container holding dozens of unrelated provider formats.

Therefore:

DO NOT build a monolithic google_importer.py containing dozens of parsers.

Build:

Google Takeout Orchestrator
        |
        +-- inventory/discovery
        |
        +-- product classifier
        |
        +-- adapter registry
                |
                +-- MyActivityAdapter
                +-- MailAdapter
                +-- CalendarAdapter
                +-- ContactsAdapter
                +-- PhotosAdapter
                +-- TimelineAdapter
                +-- DriveAdapter
                +-- ChatAdapter
                +-- GeminiAdapter
                +-- ...
        |
        +-- normalized models
        |
        +-- PR3 reconciliation/history layer

The orchestrator owns Takeout mechanics.

Adapters own product semantics.

The database layer owns persistence/reconciliation.

Those responsibilities MUST remain separate.

======================================================================
3. THE FIRST NEW FEATURE: TAKEOUT INVENTORY
======================================================================

Before implementing the remaining product parsers, implement a complete
inventory capability.

Command:

personal-data-lab inspect-google <source>

Accepted source shapes:

- one Takeout ZIP
- directory containing split Takeout ZIPs
- extracted Takeout root

The inventory must inspect ZIP central directories without extracting them.

It MUST identify:

- export ID
- inferred export timestamp
- archive filename
- archive path
- archive ordinal where inferable
- product
- member path
- filename
- extension
- uncompressed size
- compressed size when available
- ZIP CRC when available
- directory/file status
- candidate adapter
- support status
- whether member requires byte-level inspection
- whether member appears to be binary
- whether member appears to be structured text
- count of members by product
- bytes by product
- unknown/unclassified members

Inventory must NOT parse every member's content merely to classify obvious
members.

The purpose is to cheaply answer:

"What exactly is inside these 132 GB of Takeout?"

The inventory result must be machine-readable JSON and optionally produce a
human-readable table.

Required aggregate example:

Google Takeout Inventory

Product                 Members       Size        Adapter
My Activity              183          820 MB      supported
Google Photos          84,219          91 GB      planned
Drive                    6,482          18 GB      planned
Mail                         1         6.2 GB      planned
Timeline                    47         910 MB      planned
...
Unknown                      3          18 KB      inspect

The inventory must account for 100% of archive members.

The invariant is:

classified_members + intentionally_ignored_structural_members
+ unknown_members == all_archive_members

No silent remainder.

======================================================================
4. PRODUCT DISCOVERY MUST BE EMPIRICAL
======================================================================

Do not assume Google Takeout formats remain stable forever.

Do not implement a parser merely because a format is remembered from
documentation or prior Takeouts.

For every product adapter:

1. inventory actual exported filenames
2. inspect representative members
3. identify format variants
4. document discovered format
5. build fixtures from synthetic/minimized equivalents
6. implement parser
7. validate counts against actual export structure

Never commit personal Takeout data to Git.

Real source material may be used locally for validation only.

Tests must use synthetic fixtures containing no personal data.

If Google changes a format later:

- preserve the new source member
- report it as unmatched/unsupported
- do not silently partially parse it using an incompatible parser

======================================================================
5. ADAPTER INTERFACE
======================================================================

Introduce a Google Takeout adapter protocol/interface.

Conceptually:

GoogleTakeoutAdapter

- adapter_name
- parser_version
- product_keys
- supported_patterns
- probe(member/context) -> MatchResult
- parse(stream/context) -> ParsedExport or iterable[ParsedExport]
- support_level
- representation_kind

Adapters MUST NOT:

- open arbitrary unrelated files
- perform network requests
- modify source exports
- delete source data
- write directly to canonical DB tables
- infer provider deletion from absence
- rely on filesystem modification time as provider authority

Adapters receive a Takeout member abstraction containing at minimum:

- archive/container path
- member path
- product
- member filename
- uncompressed size
- compressed size if available
- CRC if available
- export_id
- exported_at
- product-relative path

For extracted trees provide equivalent context.

Adapters return normalized domain objects plus immutable provenance.

======================================================================
6. NORMALIZED DATA MODEL EXPANSION
======================================================================

Do NOT force every Google object into NormalizedEvent.

Existing primitives:

- NormalizedConversation
- NormalizedEvent

remain valid and must remain backward compatible.

Add a general durable-resource primitive for objects whose identity persists
independently of one moment in time.

Recommended model:

NormalizedRecord

Fields:

source
record_type
source_record_namespace
source_record_id
title
text
created_at
updated_at
occurred_at
actor_identity
metadata
provider_updated_at
authority_at
authority_kind
lifecycle_state

Examples of record_type:

contact
calendar
calendar_event_definition
document
note
task
place
saved_item
map
photo_metadata
video_metadata
album
purchase
reservation
payment
device
account
subscription
home_device
health_record
game_record
blog_post
classroom_course
assignment
survey_response
review
contribution
ai_project
notebook
audio_recording
transcript
business_profile
developer_resource
generic_google_record

Create persistence equivalents matching PR3 semantics:

records
record_versions
record_observations

Canonical identity:

(source, source_record_namespace, source_record_id)

Apply the same permanent guarantees already used by conversations/events:

- one canonical logical object
- all distinct normalized versions preserved
- exact duplicate version reused
- stale version retained
- conflicting version retained
- canonical state authority-driven
- no deletion from absence
- explicit provider deletion only
- observation points to exact version
- immutable source provenance retained

Do not duplicate reconciliation logic unnecessarily.

Refactor/generalize PR3 mechanics where safe.

DO NOT destabilize existing conversation/event behavior while doing this.

======================================================================
7. ASSET MODEL
======================================================================

Large binaries must NOT be stored in PostgreSQL bytea fields.

Introduce or reuse an existing prepared attachment/asset abstraction after
inspecting the current schema.

Canonical concept:

Asset

Examples:

- photo
- video
- audio
- PDF
- Drive binary
- archive member
- voice recording
- Recorder audio
- Street View image
- Blogger media

Database stores metadata/reference, not duplicate binary bytes.

Recommended fields:

source
asset_namespace
source_asset_id
sha256 when computed
mime_type
filename
original_path
size_bytes
created_at
captured_at
modified_at
width
height
duration_ms
metadata
authority
lifecycle_state

Assets MUST retain a resolvable source locator such as:

C:\...\takeout.zip!/Takeout/Google Photos/...

Do not extract permanent duplicate copies by default.

If content extraction requires temporary materialization:

- use bounded temp storage
- delete only temporary derived files
- never delete or alter original Takeout
- never confuse temp copies with source evidence

For giant media, SHA-256 may require reading the whole member.

Design hashing policy explicitly:

SUPPORTED STRUCTURED MEMBER:
hash exact parsed bytes during parsing.

INDEXED BINARY ASSET:
hash content when necessary for durable identity/deduplication.

INVENTORY-ONLY BINARY:
do not force-read 30 GB of video merely to inventory it.
Use container/member locator + ZIP metadata until deeper ingestion requires
content hashing.

Never pretend CRC32 is cryptographic identity.

======================================================================
8. TEXT EXTRACTION POLICY
======================================================================

Where a product contains useful text:

preserve BOTH:

1. canonical/queryable text
2. provider-native structured metadata

Examples:

Drive document:
- extracted text -> text
- Google/native metadata -> metadata.google

Keep note:
- note body -> text
- labels/colors/archive state -> metadata.google

Recorder:
- transcript -> text
- recording/audio -> asset relation

Gemini:
- prompts/responses -> conversation events
- conversation/session metadata -> conversation metadata

Do not throw provider-specific fields away merely because the first
normalized schema does not use them.

Lossless source fidelity beats premature elegance.

======================================================================
9. RELATIONSHIPS BETWEEN OBJECTS
======================================================================

Introduce relationships only where provider evidence explicitly supports
them.

Examples:

photo -> album
email -> attachment
calendar event -> attendees
contact -> email addresses
task -> task list
Drive file -> parent folder
Keep note -> attachment
Recorder transcript -> audio asset
Gemini conversation -> uploaded attachment
YouTube record -> playlist/subscription where present
purchase -> merchant
reservation -> place
saved place -> list

Do NOT infer person/entity equivalence during provider ingestion.

Example:

"Bob Smith" in Contacts
bob@gmail.com in Gmail
"Bob" in Chat

must not automatically become one person merely because an importer thinks
they look similar.

Cross-provider identity resolution belongs to a later derived/intelligence
layer.

Provider-declared IDs/links may be preserved safely.

======================================================================
10. GOOGLE PRODUCT ADAPTER MATRIX
======================================================================

Every one of the 68 products must map into an adapter family.

----------------------------------------------------------------------
A. SECURITY / ACCOUNT / DEVICE STATE
----------------------------------------------------------------------

Products:

1. Access Log Activity
3. Android Device Configuration Service
17. Google Account
30. Google One
55. Pixel
56. Profile

Adapter family:
account_device_adapter

Normalize into:

- events for logins/access/security/device activity
- records for account/profile/device state
- assets only where exported files exist

Preserve:

- IP/address-like provider fields if present
- user agents/device names
- timestamps
- account settings
- device identifiers
- service status
- profile fields
- subscription/storage metadata

Do NOT enrich IP addresses over the network during ingestion.

----------------------------------------------------------------------
B. NOTIFICATIONS / ALERTS / DISCOVERY
----------------------------------------------------------------------

Products:

2. Alerts
12. Discover
51. News
61. Search Notifications

Adapter family:
notification_feed_adapter

Normalize:

- notification/delivery/click activity -> events
- persistent subscriptions/preferences -> records

Preserve query/topic/source URLs and provider metadata.

----------------------------------------------------------------------
C. EDUCATION / CLASSROOM
----------------------------------------------------------------------

Products:

5. Assignments
9. Classroom

Adapter family:
education_adapter

Normalize:

- courses/classes -> records
- assignments -> records
- submissions/grades/activity -> events
- comments/messages where exported -> conversations/events
- attachments -> asset references

Provider IDs must be preferred for canonical identity.

----------------------------------------------------------------------
D. BLOG / COMMUNITY / CONTRIBUTIONS
----------------------------------------------------------------------

Products:

6. Blogger
28. Google Help Communities
41. Groups
60. Search Contributions
62. Street View

Adapter family:
publishing_contribution_adapter

Normalize:

- posts/articles/contributions/reviews -> records
- comments/replies -> conversation/event where appropriate
- publication/edit actions -> events
- attached media -> assets

For Blogger/Groups, inspect whether exports use MBOX, Atom/XML, JSON, HTML,
media directories, or combinations and parse according to discovered
formats.

Street View imagery is an asset; contribution metadata is structured data.

----------------------------------------------------------------------
E. CALENDAR / TASKS / PLANNING
----------------------------------------------------------------------

Products:

7. Calendar
63. Tasks

Adapter family:
planning_adapter

Calendar:

- parse ICS standards correctly
- recurring event definitions must be preserved
- timezone definitions must be respected
- attendee/organizer/location metadata retained
- cancellations/status retained
- do NOT eagerly explode infinite recurrence rules into unlimited rows

Store event definitions as durable records.

Optionally emit occurrence events only for explicitly materialized instances
or bounded derived views later.

Tasks:

- task lists -> records
- tasks -> records
- completed timestamps -> fields/events
- due dates -> fields
- parent/subtask relationships retained when explicit

----------------------------------------------------------------------
F. BROWSER / SEARCH ACTIVITY
----------------------------------------------------------------------

Products:

8. Chrome
22. Google Developers
48. My Activity
68. YouTube and YouTube Music

Adapter family:
activity_adapter

My Activity already implemented in PR4.

Do not duplicate My Activity rows through a second parser.

Other product-specific exports may contain data not present in My Activity:

Chrome:
- bookmarks
- browser history if supplied
- autofill/settings only when exported
- tabs/sessions if supplied

Google Developers:
- inspect actual export
- normalize developer-specific activity/resources explicitly

YouTube:
- watch/search history may already overlap My Activity
- playlists
- subscriptions
- comments
- likes
- channel metadata
- uploads metadata
- live chat where exported

Avoid semantic duplicate merging during ingestion.

Use provider IDs when present.

Cross-representation dedup can be derived later.

----------------------------------------------------------------------
G. CONTACTS
----------------------------------------------------------------------

Product:

10. Contacts

Adapter family:
contacts_adapter

Parse discovered vCard/CSV/other structured forms.

Normalize contacts as records, NOT events.

Preserve:

- provider contact ID when present
- names
- phone numbers
- email addresses
- organizations
- addresses
- birthdays
- notes
- labels/groups
- URLs
- custom fields

Do not collapse two contacts because names/emails resemble one another unless
the provider explicitly identifies them as the same logical contact.

----------------------------------------------------------------------
H. LEGAL / APPEAL MATERIAL
----------------------------------------------------------------------

Product:

11. Content returned from your appeal

Adapter family:
legal_export_adapter

Treat conservatively.

Preserve:

- documents
- returned content
- metadata
- timestamps
- assets

Do not reinterpret legal meaning.

Do not silently discard unknown document types.

----------------------------------------------------------------------
I. DRIVE / DOCUMENT STORAGE
----------------------------------------------------------------------

Product:

13. Drive

Adapter family:
drive_adapter

High-value adapter.

Inventory folders/files first.

Normalize:

- folders -> records
- Google-native documents -> records with extracted text where available
- ordinary files -> assets + record metadata
- shortcuts -> relationships
- file metadata -> provider metadata

Support content extraction for text-friendly formats when practical:

TXT
MD
CSV
JSON
HTML
XML
PDF
DOCX
PPTX
XLSX
other formats only where safe libraries already exist

DO NOT add heavyweight conversion dependencies without justification.

Native Google exports may appear in Office/PDF/etc. based on Takeout.

The exported artifact is source evidence.

Do not attempt network recovery of original Google Docs.

Large binary content remains external.

----------------------------------------------------------------------
J. GENERATIVE AI / AI WORKSPACES
----------------------------------------------------------------------

Products:

14. Flow
15. Gemini
16. Gemini in Workspace
52. NotebookLM
67. Workspace Studio

Adapter family:
google_ai_adapter

These are strategically high-value because they contain expressed reasoning,
intentions, prompts, generated responses, projects, and source relationships.

Normalize:

AI conversations:
- NormalizedConversation
- user prompt events
- assistant/model response events
- timestamps
- model/tool metadata where supplied

AI durable objects:
- projects
- notebooks
- workflows
- sources
- generated artifacts
-> records

Attachments/media:
-> assets

NotebookLM:
preserve notebook -> source relationships explicitly.

Flow:
preserve project/shot/media/prompt structures according to actual export.

Gemini in Workspace:
do not assume it has the same schema as consumer Gemini.
Separate adapter variant when formats differ.

Never merge Gemini conversations with other providers during ingestion.

----------------------------------------------------------------------
K. ADVERTISING / BUSINESS
----------------------------------------------------------------------

Products:

18. Google Ads
19. Google Business Profile

Adapter family:
business_ads_adapter

Normalize:

- account/profile objects -> records
- campaign/activity data -> records/events
- reviews/contributions where exported -> records
- business media -> assets

Do not infer financial value or business ownership beyond provider data.

----------------------------------------------------------------------
L. MESSAGING / COMMUNICATION
----------------------------------------------------------------------

Products:

20. Google Chat
29. Google Meet
41. Groups
66. Voice

Adapter family:
communications_adapter

Google Chat:

- spaces/rooms -> conversation containers
- messages -> conversation events
- threads -> parent relationships
- reactions -> metadata/events
- attachments -> assets
- members -> provider metadata

Meet:

- meeting records -> records/events
- chat/transcripts where exported -> conversations/documents
- recordings -> assets
- participant info -> provider metadata

Groups:

- mailing-list messages -> conversations/events or email records
- group metadata -> records

Voice:

inspect actual export variants.

Potential data includes:
- calls
- SMS/text messages
- voicemail
- transcripts
- recordings

Normalize calls as events.
Messages as conversations/events.
Voicemail transcript as text.
Audio as assets.

----------------------------------------------------------------------
M. SEARCH / KNOWLEDGE TOOLS
----------------------------------------------------------------------

Products:

21. Google Cloud Search
54. Pinpoint

Adapter family:
search_knowledge_adapter

Normalize:

- saved searches/queries -> records/events
- collections/projects -> records
- indexed/source document metadata -> records/assets
- annotations/bookmarks where present -> records

Do NOT recreate massive provider search indexes locally from metadata alone.

----------------------------------------------------------------------
N. MAPS / GEO / PLACES
----------------------------------------------------------------------

Products:

23. Google Earth
46. Maps
47. Maps (your places)
49. My Maps
59. Saved
65. Timeline

Adapter family:
geo_adapter

This is one of the highest-value families.

Support appropriate discovered formats such as:

JSON
GeoJSON
KML/KMZ
CSV
structured Maps export formats

Normalize separately:

- places -> records
- saved lists -> records
- visits -> events
- routes/trips -> records/events
- geographic tracks -> structured geo metadata
- custom maps/layers -> records
- pins/features -> records

Preserve coordinates exactly as provider data.

Normalize timestamps to UTC where provider timezone/offset exists.

Do NOT reverse-geocode during ingestion.

Do NOT call Maps APIs.

Do not invent "home" or "work" semantics unless Google explicitly labels them.

Timeline must preserve:

- visit/activity segments
- source coordinates
- confidence fields
- inferred activity type from Google
- start/end times
- source-specific metadata

Do not convert Google's inference into established fact.

Example:
Google says "IN_PASSENGER_VEHICLE"

store that as provider classification, not:
"user definitely drove."

----------------------------------------------------------------------
O. FEEDBACK / SURVEYS
----------------------------------------------------------------------

Products:

24. Google Feedback
36. Google Product Surveys

Adapter family:
feedback_survey_adapter

Normalize submissions/responses as records/events.

Preserve:

- question/prompt
- answer
- timestamp
- product
- feedback metadata
- attachment screenshots/assets if present

----------------------------------------------------------------------
P. TELECOM / AUDIO
----------------------------------------------------------------------

Products:

25. Google Fi Wireless
53. Phone Audio
66. Voice

Adapter family:
telecom_adapter

Fi:

- call/message/network/account records according to actual export
- billing-like metadata if supplied

Phone Audio:

- audio assets
- timestamp/device metadata
- transcript if provided

Voice handled jointly with communications semantics.

No audio transcription should be synthesized during raw ingestion unless
explicitly designed as a derived later stage.

Provider-supplied transcripts must be retained.

----------------------------------------------------------------------
Q. FINANCE / COMMERCE
----------------------------------------------------------------------

Products:

26. Google Finance
31. Google Pay
35. Google Play Store
37. Google Shopping
38. Google Store
39. Google Wallet
57. Purchases & Reservations

Adapter family:
commerce_adapter

Normalize:

- purchases -> records
- transactions/payment activity -> events/records
- reservations -> records
- passes/cards/tickets -> records
- subscriptions -> records
- watchlists -> records
- product/order history -> records

Preserve currencies exactly.

Do not convert currencies at ingestion time.

Do not infer merchant identity across providers.

Sensitive payment tokens/account numbers must remain local and must never
appear in progress logs.

If Google export contains secrets, tokens, or credential-like material,
preserve only when necessary for faithful archival and mark sensitive fields
appropriately if the schema supports it.

Never use them for outbound requests.

----------------------------------------------------------------------
R. HEALTH
----------------------------------------------------------------------

Product:

27. Google Health

Adapter family:
health_adapter

Normalize provider-exported health information losslessly.

Possible categories must be discovered empirically.

Preserve:

- measurements
- dates
- units
- source
- record type
- provider metadata

Do not change units destructively.

A normalized derived unit may be added alongside original value/unit.

Health ingestion is archival/data normalization, not diagnosis.

----------------------------------------------------------------------
S. PHOTOS / MEDIA LIBRARY
----------------------------------------------------------------------

Product:

32. Google Photos

Adapter family:
photos_adapter

This will likely represent most Takeout bytes.

DO NOT store image/video bytes in PostgreSQL.

Index:

- media file
- media SHA when selected for full asset ingestion
- filename
- MIME/type
- size
- captured timestamp
- modified timestamp
- GPS if present
- albums
- descriptions
- archive/favorite metadata
- sidecar data
- device/camera metadata when exported
- media -> album relationships

Takeout sidecars must be matched to media conservatively.

Do not match solely by truncated filename if ambiguous.

Handle Takeout's filename/path oddities explicitly.

Preserve unmatched sidecars rather than dropping them.

Do NOT perform:
- facial recognition
- object recognition
- OCR
- perceptual duplicate detection

during core ingestion.

Those belong to later derived/enrichment pipelines.

----------------------------------------------------------------------
T. GAMING / MEDIA PURCHASE HISTORY
----------------------------------------------------------------------

Products:

33. Google Play Games Services
34. Google Play Movies & TV

Adapter family:
entertainment_adapter

Normalize:

- game profile/state/achievements -> records
- play/watch/library activity -> events/records
- purchased/rented media -> records

Retain provider IDs.

----------------------------------------------------------------------
U. MARKETPLACE / INSTALLED SERVICES
----------------------------------------------------------------------

Product:

40. Google Workspace Marketplace

Adapter family:
marketplace_adapter

Normalize installed/authorized marketplace applications as records.

Preserve:

- app identity
- install status
- timestamps
- permissions/scopes only if included
- provider metadata

Do not execute integrations.

----------------------------------------------------------------------
V. HOME / IOT
----------------------------------------------------------------------

Products:

42. Home App
50. Nest

Adapter family:
home_iot_adapter

Normalize:

- homes/structures -> records
- devices -> records
- device states/settings -> record versions
- sensor/activity history -> events
- clips/media -> assets

Preserve provider-generated device IDs.

Do not infer occupancy/person presence beyond provider labels.

Do not connect to live devices.

----------------------------------------------------------------------
W. JOURNAL / NOTES
----------------------------------------------------------------------

Products:

43. Journal
44. Keep

Adapter family:
notes_adapter

Normalize:

- notes -> records
- journal entries -> records
- checklists -> structured metadata/children
- labels -> metadata/relationships
- attachments -> assets
- created/updated/archive timestamps

Preserve checklist state.

Do not flatten structured checklist data irreversibly into text only.

Also generate queryable text projection.

----------------------------------------------------------------------
X. EMAIL
----------------------------------------------------------------------

Product:

45. Mail

Adapter family:
gmail_adapter

High-value adapter.

Support MBOX when that is the discovered Takeout representation.

Do not load the entire MBOX into memory.

Stream one message at a time.

Normalize:

- email thread/conversation where threading IDs can be safely determined
- message -> event
- sender/recipient/cc/bcc
- subject
- sent/received dates
- message IDs
- Gmail labels when represented
- headers
- body text
- HTML body
- attachment metadata/assets

Preserve raw MIME-relevant metadata necessary for forensic reconstruction.

Do not fetch remote images.

Do not dereference links.

Prefer RFC Message-ID/provider IDs for identity.

Fallback identities must be deterministic and documented.

MBOX must support resume/reimport without duplication.

Large attachments must not be loaded into DB as bytes.

----------------------------------------------------------------------
Y. RECORDER
----------------------------------------------------------------------

Product:

58. Recorder

Adapter family:
recorder_adapter

Normalize:

recording -> record
audio -> asset
provider transcript -> text
speaker/timestamp segments -> structured metadata when supplied

Preserve relationship:

recording -> audio
recording -> transcript

Do not run new speech-to-text during ingestion.

----------------------------------------------------------------------
Z. TENOR
----------------------------------------------------------------------

Product:

64. Tenor

Adapter family:
tenor_adapter

Inspect actual exported content.

Normalize:

- searches/activity -> events
- favorites/saved GIF metadata -> records
- local media if supplied -> assets

Do not fetch GIF URLs.

======================================================================
11. SOURCE OF TRUTH AND OVERLAP POLICY
======================================================================

Google products may duplicate the same human behavior across different
export surfaces.

Examples:

YouTube watch history
vs
My Activity YouTube events

Google Pay purchase
vs
Purchases & Reservations

Maps saved place
vs
Saved

Do NOT perform heuristic cross-product deduplication during ingestion.

Each provider surface is evidence.

Keep each source observation independently attributable.

Later derived layers may conclude:

"these two records appear to describe the same real-world event."

That derived assertion must not destroy either original observation.

======================================================================
12. AUTHORITY
======================================================================

Authority precedence remains consistent with PR3.

Prefer, where available:

1. provider revision/version
2. provider updated timestamp
3. Takeout exported_at
4. unknown

Do not use:

- filesystem mtime
- ZIP extraction time
- local import time

as provider authority.

Takeout filename export identity rules from PR4 remain canonical.

======================================================================
13. TIMESTAMPS
======================================================================

Preserve:

- raw provider time representation
- normalized UTC timestamp when safely parseable
- provider timezone/offset when available

Never guess locale-sensitive dates silently.

If a timestamp cannot be parsed:

normalized time = null
raw value retained in metadata
warning/stat recorded

A malformed timestamp must not normally cause an otherwise valid record to
disappear.

======================================================================
14. UNKNOWN-FIELD POLICY
======================================================================

Provider formats evolve.

Known fields may receive normalized projections.

Unknown fields MUST remain preserved in provider metadata wherever reasonably
possible.

Never silently drop a field solely because the parser does not understand it.

This is especially important because future analysis may use data that the
current importer did not anticipate.

======================================================================
15. FAILURE / RESUME MODEL
======================================================================

Process incrementally.

A 132 GB import must never become one transaction.

Transaction boundary:

one logical source member or bounded unit.

If member N fails:

- previously committed members remain committed
- failing member makes no partial canonical mutation
- subsequent rerun safely short-circuits completed members
- failure report identifies exact archive/member/adapter
- source data remains untouched

The importer must be restart-safe.

======================================================================
16. MEMORY / DISK REQUIREMENTS
======================================================================

Target:

memory proportional to one bounded parsing unit, not Takeout size.

Do not:

- extract the entire Takeout
- load an entire MBOX into RAM
- load an entire massive JSON file where incremental parsing is feasible
- materialize full photo/video libraries
- copy giant binaries merely for parsing metadata

ZIP members should be streamed where feasible.

Any adapter requiring unavoidable whole-file parsing must document the
maximum expected boundary and why.

======================================================================
17. GOOGLE ORCHESTRATOR CLI
======================================================================

Add:

personal-data-lab inspect-google <source>

and eventually:

personal-data-lab import-google-all <source>

Keep existing:

personal-data-lab import-google

backward compatible for My Activity if practical.

Recommended final behavior:

import-google-all:

1. inventory archives
2. resolve adapters
3. print plan
4. process supported members serially
5. report progress to stderr
6. preserve stdout for final JSON
7. produce product-level coverage report

Final summary example:

{
  "export_id": "...",
  "products_detected": 68,
  "products_fully_supported": 68,
  "members_total": ...,
  "members_imported": ...,
  "members_asset_indexed": ...,
  "members_raw_preserved": ...,
  "members_failed": 0,
  "members_unknown": 0,
  "bytes_accounted_for": ...,
  "coverage": {
     "Mail": {...},
     "Drive": {...},
     ...
  }
}

Exit codes:

0 = complete successful processing for all supported/discovered members
2 = reconciliation conflicts
3 = known unsupported members remain
4 = parse/import failure
5 = unknown/unclassified source members

Use existing conventions where possible, but document exact final meanings.

======================================================================
18. COVERAGE MANIFEST
======================================================================

Create a machine-readable registry such as:

google_products.py
or
google_product_registry.json

Each of the 68 products MUST have:

canonical_product_key
takeout_display_names
adapter
support_level
expected_format_families
record_kinds
notes

Tests must assert:

set(EXPECTED_68_PRODUCTS) == set(PRODUCT_REGISTRY)

This prevents somebody from accidentally implementing 67 and declaring
victory.

Do not use display strings as permanent canonical identifiers.

Example:

display:
"YouTube and YouTube Music"

canonical:
youtube

Aliases may map to the same product key only when they genuinely represent
one Takeout product.

======================================================================
19. GENERIC STRUCTURED FALLBACK
======================================================================

Some low-value Google products may contain simple JSON/CSV/XML records where
building a bespoke semantic parser immediately has little value.

Allow a generic structured adapter ONLY when:

- source product is known
- format can be parsed losslessly
- every record retains product/filename/path provenance
- fields are preserved
- stable deterministic identity can be generated
- support status is reported as partially_structured rather than fully_structured

Generic ingestion is preferable to throwing useful structured data away.

But generic ingestion MUST NOT masquerade as full semantic support.

======================================================================
20. RAW PRESERVATION
======================================================================

The immutable Takeout remains the ultimate source evidence.

"Raw preserved" means:

- original source remains untouched
- inventory identifies it
- path/member provenance is recorded
- unsupported status/reason is explicit

It does NOT mean PostgreSQL must duplicate the raw bytes.

======================================================================
21. SECURITY / PRIVACY
======================================================================

Personal Data Lab is local-first.

Importers must:

- make no outbound network calls
- never upload content
- never print message/email/note/search contents in progress logs
- avoid printing sensitive provider metadata
- parameterize SQL
- treat filenames/content as untrusted input
- reject unsafe archive behavior
- never execute HTML/JS/scripts
- never extract archive traversal paths onto disk

ZIP path traversal protections are mandatory for any adapter that
materializes a member.

HTML is data, never executable content.

======================================================================
22. OBSERVABILITY
======================================================================

For every adapter report:

- members discovered
- members processed
- records parsed
- events parsed
- conversations parsed
- assets indexed
- bytes processed
- parse warnings
- unsupported variants
- failures
- conflicts
- inserted
- unchanged
- stale
- version count
- elapsed time

No personal content in standard logs.

Optional verbose/debug logging must still avoid dumping entire user payloads
by default.

======================================================================
23. INGESTION QUALITY LEVELS
======================================================================

Define these terms exactly.

FULLY_STRUCTURED

The meaningful exported structure has explicit normalized semantics and
known fields while unknown fields are preserved.

PARTIALLY_STRUCTURED

The format is safely ingested and queryable but not all product semantics
have been modeled.

ASSET_INDEXED

Binary/resource is represented as an asset with provenance and metadata;
content semantics are not derived.

RAW_PRESERVED

Known source exists and is inventoried but has no safe parser.

EMPTY_IN_EXPORT

Product exists in export layout but contains no actual user records.

NOT_PRESENT_IN_EXPORT

Product was selected/known but no source members exist.

FAILED

Adapter matched but ingestion failed.

No "success" state may hide unsupported content.

======================================================================
24. PRIORITY / IMPLEMENTATION PR PLAN
======================================================================

DO NOT build this in one PR.

Recommended sequence:

PR5 — Google Takeout Framework

Implement:

- complete inventory
- product registry containing all 68
- adapter protocol
- routing/orchestrator
- product coverage reporting
- NormalizedRecord + PR3-equivalent persistence/versioning
- asset reference foundation
- generic structured fallback
- existing My Activity registered as adapter #1

No giant product rollout yet.

Acceptance:
all 68 registry entries exist and inventory accounts for 100% of members.

--------------------------------------------------

PR6 — High-Value Personal Timeline

Implement:

- Timeline
- Maps
- Maps (your places)
- My Maps
- Saved
- Calendar
- Contacts
- Tasks

Goal:
places + people + commitments + temporal structure.

--------------------------------------------------

PR7 — Communications

Implement:

- Mail
- Google Chat
- Voice
- Google Meet
- Groups

Goal:
major interpersonal communications and administrative history.

MBOX and message streams must be truly streaming/bounded.

--------------------------------------------------

PR8 — Photos / Drive / Rich Assets

Implement:

- Google Photos
- Drive
- Recorder
- Phone Audio
- Street View

Goal:
asset graph plus textual/document extraction without DB binary bloat.

--------------------------------------------------

PR9 — AI / Knowledge / Notes

Implement:

- Gemini
- Gemini in Workspace
- NotebookLM
- Flow
- Workspace Studio
- Keep
- Journal
- Pinpoint
- Google Cloud Search

Goal:
ideas, reasoning, notes, research, generated work.

--------------------------------------------------

PR10 — Commerce / Account / Device / Home

Implement:

- Google Account
- Google One
- Google Pay
- Google Wallet
- Purchases & Reservations
- Google Shopping
- Google Store
- Google Play Store
- Google Finance
- Google Fi Wireless
- Pixel
- Android Device Configuration Service
- Home App
- Nest
- Profile

--------------------------------------------------

PR11 — Remaining Long Tail

Implement:

- Access Log Activity
- Alerts
- Arts & Culture
- Assignments
- Blogger
- Chrome product-specific non-My-Activity data
- Classroom
- Content returned from your appeal
- Discover
- Google Ads
- Google Business Profile
- Google Developers
- Google Earth
- Google Feedback
- Google Health
- Google Help Communities
- Google Play Games Services
- Google Play Movies & TV
- Google Product Surveys
- Google Workspace Marketplace
- News
- Search Contributions
- Search Notifications
- Tenor
- YouTube and YouTube Music non-My-Activity data

Before closing PR11, compare implemented registry coverage to the exact
68-product contract and prove nothing remains accidentally unhandled.

======================================================================
25. REAL-DATA VALIDATION STRATEGY
======================================================================

Never start by blasting all 132 GB through untested code.

For each adapter family:

1. inventory real Takeout
2. identify smallest representative source
3. run canary import
4. inspect provenance
5. inspect normalized counts
6. inspect representative normalized rows locally
7. reimport identical source
8. prove idempotency
9. import overlapping/newer source if available
10. prove cumulative semantics
11. then enable full Takeout processing

For large datasets:

sample validation must not replace count reconciliation.

Every parsed member must reconcile its source record count to persisted
observations before commit.

======================================================================
26. TESTING REQUIREMENTS
======================================================================

Every adapter requires tests for:

- discovery
- valid minimal source
- realistic multi-record source
- malformed source
- empty source
- unknown fields
- timestamp normalization
- deterministic identity
- duplicate records
- identical reimport
- overlapping later export
- later absence
- provenance
- authority
- source rename/path move when identity should remain stable
- bounded memory behavior where material
- source content not leaked into logs

Framework tests:

- all 68 products registered
- unknown product detection
- unknown member detection
- multiple split archives
- mixed supported/unsupported products
- invalid ZIP
- duplicate browser downloads
- deterministic routing
- adapter ambiguity is an error
- 100% inventory accounting
- restart after partial success
- clean stdout JSON
- progress on stderr
- no source-content leakage

Database tests:

- record version preservation
- stale versions
- conflicts
- authority watermark
- exact observation pointer
- run short circuit
- source-file location provenance
- no absence-based deletion

======================================================================
27. PERFORMANCE ACCEPTANCE
======================================================================

A Takeout exceeding 100 GB must be operationally reasonable.

No requirement for extreme optimization yet.

Required:

- bounded RAM
- bounded temp disk
- serial processing by default
- resumable/idempotent
- no extraction explosion
- no O(total Takeout size) in-memory structures

Inventory may maintain lightweight member metadata for the whole export.

Do not retain all normalized records in memory.

Do not parallelize by default until correctness and resource behavior are
proven.

======================================================================
28. DOCUMENTATION
======================================================================

Create:

GOOGLE_TAKEOUT_COMPLETE_INGESTION_SPEC.md

and maintain:

GOOGLE_PRODUCT_COVERAGE.md

Coverage document should list all 68 products with:

Product
Adapter
Formats observed
Support level
Objects produced
Tests
Known limitations

README must clearly distinguish:

Google My Activity support

from:

complete Google Takeout support

until all 68 are covered.

Never claim complete Google support merely because import-google-all exits
successfully while products remain raw_preserved or unsupported.

======================================================================
29. DEFINITION OF COMPLETE GOOGLE INGESTION
======================================================================

Google ingestion is "complete" for this Takeout generation only when:

- all 68 products exist in registry
- all archive members are inventoried
- zero unknown members remain unexplained
- zero accidental silent skips
- every product has explicit support state
- meaningful structured data is normalized
- large binaries are asset-indexed rather than copied into PostgreSQL
- raw provenance is preserved
- reimport is idempotent
- later exports are cumulative and non-destructive
- failures resume safely
- all supported records reconcile
- integration suite passes
- full real Takeout run completes successfully
- second full run produces no unintended duplicates/versions

======================================================================
30. NON-GOALS FOR THE GOOGLE INGESTION PROGRAM
======================================================================

Do NOT add yet:

- embeddings
- vector search
- semantic search
- personal knowledge graph inference
- person/entity resolution across providers
- OCR
- facial recognition
- image understanding
- AI summarization
- sentiment analysis
- relationship inference
- behavioral inference
- recommendation engines
- MCP
- agent reasoning APIs

Those are consumers of the normalized corpus.

The Google ingestion program's job is simpler and more important:

ACQUIRE
CLASSIFY
NORMALIZE
PRESERVE
PROVE PROVENANCE
NEVER LOSE INFORMATION

======================================================================
31. CODEX EXECUTION INSTRUCTION
======================================================================

Start with PR5 only.

Before writing implementation code:

1. inspect current main after merged PR4
2. inspect migrations/schema/models/fingerprints/database reconciliation
3. identify whether any attachment/version infrastructure already exists
4. avoid duplicating reusable PR3 mechanisms
5. produce a short architecture note describing proposed changes
6. then implement the Google Takeout framework

Do NOT begin implementing all product adapters in PR5.

PR5 exists to create the rails on which the remaining 67 product adapters
will run.

At completion of PR5 return:

- files changed
- migrations added
- normalized models added
- adapter protocol
- complete 68-product registry
- inventory output example
- routing behavior
- coverage accounting behavior
- compatibility statement for PR4 My Activity
- unit/integration test results
- Ruff/compile/build results
- known limitations
- recommended exact scope for PR6

Most important architectural test:

If Google adds a 69th product tomorrow, supporting it should normally require:

1. add registry entry
2. implement/register adapter
3. add fixtures/tests

It should NOT require redesigning the Takeout orchestrator or database.
