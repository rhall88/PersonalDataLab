# iOS device backup ingestion (source `ios_backup`)

Status: v1 (audio) shipped 2026-09-30. This is the "v1.1 iMazing" source first scoped on
2026-09-25, when the Apple privacy export turned out to hold only 22 Voice Memos, no on-device
transcripts, and no third-party app audio at all.

## What the source is

An unencrypted iOS device backup in the standard iTunes/Finder/iMazing format: `Manifest.plist`,
`Info.plist`, `Status.plist`, `Manifest.db` (SQLite: `Files(fileID, domain, relativePath, flags,
file)`), and every file stored content-addressed at `<fileID[:2]>/<fileID>`. The `file` column is
an NSKeyedArchiver `MBFile` plist with `Size`, `Birth`, `LastModified` (unix seconds).

Encrypted backups are refused (`IosBackupError`). Decrypting needs the backup password, which this
project never handles.

### Retained copy

The first real backup (iPhone 13 Pro, `iPhone14,2`, iOS 26.6.1, 2026-09-30, 32,229 files, 43.3 GB)
lives at `C:\Users\Ryan\Desktop\DataLabData\iMazing\iPhone 13 Pro backup 2026-09-30\`. That folder
is a tree of NTFS **hard links** to iMazing's backup files, not a copy: it cost no disk space, and
it stays byte-identical even when iMazing's next backup rewrites its own folder, because iMazing
replaces changed files rather than editing them in place. Treat it like every other folder under
`DataLabData\`: immutable, never delete.

## Scope of v1: audio

Every file whose extension is an audio type (`.m4a .wav .caf .aac .mp3 .amr .aif(f) .flac .ogg
.opus .m4b`), in every domain except `TonesDomain` (ringtones) and Audible (purchased audiobooks).
Video is out of scope. Products, by owning domain:

| Product | Domain | Real count (2026-09-30) |
|---|---|---|
| `voice_memos` | `AppDomainGroup-group.com.apple.VoiceMemos.shared` | 152 files, 6.2 GB |
| `voicemail` | `HomeDomain` `Library/Voicemail/` | 391 `.amr` (a per-account subfolder repeats many) |
| `motiv` | `AppDomain-com.shure.plus.motiv` | 41 WAV, 16.1 GB |
| `audio_editor` | `AppDomain-com.iuuapp.audiomaker` | 149, 9.2 GB |
| `files_app` | `AppDomainGroup-group.com.apple.FileProvider.LocalStorage` | 29, 2.1 GB |
| `notes_audio` | `AppDomainGroup-group.com.apple.notes` | 10 (Notes call recordings) |
| `messenger_audio` | `AppDomain-com.facebook.Messenger` | 18 |
| `other_app_audio` | anything else | 46, 1.0 GB |

## What gets imported

* **One asset per audio file**, as its own ParsedExport whose source file *is* that backup file
  (byte-level provenance). `asset_namespace = ios_backup/<product>`, `source_asset_id =
  <domain>/<relativePath>` (stable across future backups of the same device), `original_path` =
  the backup file on disk, so `media_transcribe_whisper` opens it as a plain file. `sha256` is the
  file's content hash; `captured_at` is the file's birth time.
* **Voice Memos** (`Recordings/CloudRecordings.db`, table `ZCLOUDRECORDING`): one `voice_memo`
  record per recording, `source_record_id = ZUNIQUEID`, title `ZENCRYPTEDTITLE` (plain text despite
  the name) or `ZCUSTOMLABEL`, date `ZDATE` (Core Data seconds since 2001-01-01), duration
  `ZLOCALDURATION`. **Its text is the on-device transcript**, which iOS embeds in the `.m4a` itself
  as a `tsrp` atom holding JSON `{"attributedString": {"runs": ["text", attrIndex, ...]}}`; the
  string runs are concatenated. Relationship `voice_memo_has_audio` to the asset. A memo whose audio
  is not in the backup (iCloud-only) still gets its record, with a parse warning.
* **Voicemail** (`Library/Voicemail/voicemail.db`, table `voicemail`): one `voicemail` record per
  message, `source_record_id = uuid`, caller `sender` as `actor_identity`, date (unix seconds),
  duration, receiver, trashed flag. **Its text is Apple's transcript** from
  `Library/Voicemail/<ROWID>.transcript`, an NSKeyedArchiver `VMVoicemailTranscript` whose
  `transcriptionString` and `confidence` are kept. Relationship `voicemail_has_audio` to
  `<ROWID>.amr`, preferring the top-level file over the per-account copy. The `.summary` sidecars
  are language-ID metadata (locale and confidence), not summaries, so they are not imported.

Provider transcripts are stored as **record text**, never as `media.transcript` rows: that table
belongs to the Whisper deriver, which clears and rewrites it.

## De-duplication (owner's rule: keep only the most complete copy)

The same recording exists many times: a take saved in MOTIV, trimmed in Audio Editor, exported to
the Files app, and also present in the Apple export or Google Takeout. The owner's rule is one copy
per recording, the most complete one, and nothing else counted, transcribed or searched.

Three layers, none of which deletes anything:

1. **Identical bytes at import.** Each audio file's ParsedExport is keyed by its SHA-256, so a
   byte-identical second copy short-circuits and never gets its own asset row (141 of 836 files on
   the first backup). A relationship that pointed at such a copy is redirected to the row that exists.
2. **Re-delivered voicemails.** A carrier or account change re-delivers old voicemails under new
   ROWIDs (same date, caller and length, identical audio). One record is kept per message: the
   copy with a transcript, else the oldest. The others' ROWIDs go in `metadata.duplicate_rowids`
   (6 on the first backup).
3. **Acoustic de-duplication, all sources** (`media_dedup.py`, migration 027, `personal-data-lab
   audio-dedup`). Every audio asset is fingerprinted (`media.audio_fingerprint`: 32-bit band-energy
   sub-fingerprints at 31.25 per second, 8 kHz mono, plus a voiced mask so silence never matches).
   A sorted index of every voiced frame finds, for each recording, any other recording containing
   it at some time offset, verified in 10-second blocks (block bit error rate under 0.33; unrelated
   audio sits near 0.5). A recording is *contained* when at least 90 percent of its voiced blocks
   match. `media.audio_supersession` then records each redundant asset, its final keeper and the
   evidence (coverage, offset, bit error rate):
   * `exact_duplicate`: identical bytes under another name or source;
   * `same_audio`: contained both ways (another encoding); the better copy wins: lossless over
     lossy, then higher sample rate, then the device original over an export;
   * `contained_in`: a trimmed or partial copy of a longer recording, which wins.

   A partial overlap (each file holds audio the other lacks) keeps both, because neither is the
   more complete copy. `media_transcribe_whisper` skips superseded assets, and also skips audio
   whose linked record already carries a provider transcript (Voice Memos, voicemail).

## Commands

```
personal-data-lab inspect-ios-backup "<backup folder>"
personal-data-lab import-ios-backup-all "<backup folder>"
personal-data-lab import-ios-messages "<backup folder>" [--snapshot "<older chat.db>"]
```

Import is restart-safe: each ParsedExport is idempotent, and a re-run short-circuits unchanged files.
Records and relationships carry the backup date as authority (`exported_at`), so a later backup
of the same device updates them. A changed parse of the *same* backup is a conflict by design (the
importer never guesses between equal-authority claims), which is why the first real import went
through parser versions v1 to v4; see `PROJECT_STATE.md`.

## Messages: source `ios_messages` (v1.2, 2026-09-30)

The phone's own Messages database (`HomeDomain` `Library/SMS/sms.db`, with its `-wal`/`-shm`)
is the device's record of every iMessage, SMS and RCS it still holds. The iMazing CSV source
`imessages` already carries most of that history and every one of its 74 months is extracted, so
the CSV stays the copy of record for the messages it has, and the device database fills the gaps
as its own source, `ios_messages`. A separate source keeps every `imessages` partition untouched
(no re-extraction); downstream, the two read as one iMessage history (see "Downstream" below).

**Grounding (the 2026-09-30 backup).** 91,765 device rows, 2020-08-28 to 2026-09-30 (nothing
older survives on the phone: Keep Messages is now "Forever", the older history was removed on the
device before that). Matching rule: a device message equals a CSV message when both fall in the
same second (+-1 s) with the same direction, decided one-to-one in time order. 66,365 match.
Of the 16,218 device messages with no CSV twin, 96% sit in blocks with no CSV message within 45
minutes (RCS threads, chats deleted before the CSV export, everything after the export of
2026-09-09), so importing them on their own loses no conversational context. Of the matched pairs,
746 have text on the device but none in the CSV (photo captions the CSV dropped); the other text
differences are cosmetic (the CSV turns double quotes into single quotes and drops U+FFFC).

**Older copies of the same database.** `import-ios-messages --snapshot <chat.db>` reads older
copies too; messages are keyed by Apple's GUID and the backup wins. The 2026-04-03 copy (kept as
`DataLabData/iMazing/chat.db snapshot 2026-04-03/chat.db`) holds 1,198 messages since deleted from
the phone and absent from the CSV.

**What is imported** (`ios_backup/messages.py`):

| Device row | Becomes |
|---|---|
| message with no CSV twin | `message.incoming` / `message.outgoing` event (`sms_db/message`, id = Apple GUID) in its chat's conversation |
| message whose CSV twin has no text but the device copy does | the same event, metadata `csv_twin`, plus a `more_complete_copy_of` relationship to the CSV event; counts skip it |
| reaction (associated type 2000-3999) on an imported message, or on a CSV message the CSV shows no reaction for at that second | `message.reaction` / `message.reaction_removed` (`sms_db/reaction`), `reaction_to_message` relationship (to the CSV event when the target is one) |
| group notice (item_type <> 0) with no CSV notification at that second | `message.notification` |
| edit / Recently Deleted entry of an imported message | `message.edit` / `message.deletion` events; a deleted message is `provider_deleted` |
| reply (`thread_originator_guid`) | `message_replies_to` relationship |
| attachment of an imported message | asset `sms_db/attachment` (id = attachment GUID), hashed when the file is in the backup, else a reference with `available_locally: false`; a file the audio import already holds is linked, not stored again; one asset per attachment even when several messages share it |
| every matched CSV message | `csv_message_on_device` relationship from the CSV event to its device chat, carrying the GUID, service, device time and attachment GUIDs |

Text is `text`, or the string decoded from `attributedBody` (typedstream NSAttributedString) when
`text` is NULL; the decoder reproduces `text` exactly on all 77,065 rows that have both. U+FFFC
placeholders are removed and counted. Handles drop iOS's SMS-filter suffixes (`(smsft)`,
`(smsfp)`); the owner's messages carry the CSV source's owner sentinel `unknown`. Conversations
are the device chats (title = group name or participant handles); messages with no chat row become
`orphan:<handle>` threads ("Deleted chat with ...") or, for sent ones with no handle,
`orphan:outgoing`.

After import, `mirror_identity_links` links each device handle to the person its CSV identity is
confirmed as (`tier = computed`, note "same handle as the confirmed imessages identity").

**Downstream.** Policy row by migration 030 (`intimate`, `local_only`); source contract declared
2020-08-28 to 2026-09-30. Text units: 45-minute sessions per chat, like `imessages`.
Interaction dynamics fold `ios_messages` threads into the `("imessages", correspondent)` series
(`measure_interaction_dynamics` / `signal_interaction_state` v2). Daily metrics add
`ios_messages.incoming_count` / `outgoing_count` / `reactions_count`, which
`messaging.total_messages_count` adds when known but does not require
(`measure_metric_daily_global` v2); unique correspondents count a handle seen in both sources
once. The app's relationship views read identity links and messages from both sources.
Everything that counts messages skips `csv_twin` copies.

**Result of the first real import** (backup 2026-09-30 + snapshot 2026-04-03): 18,191 messages,
5,862 reactions, 175 group notices, 85 edits, 477 deleted messages kept as deleted, 3,021
attachment links (128 files present in the backup; the rest are iCloud-only), 66,365
`csv_message_on_device` links, 746 caption copies, 18 identity links mirrored. 4,965 new text
units.

```
personal-data-lab import-ios-messages "<backup folder>" [--snapshot "<older chat.db>"]
```

## Not yet in scope

Notes text and call-recording transcripts (in `NoteStore.sqlite`), Contacts (`AddressBook`),
Calendar, photos, call history / Safari / Health (these need an encrypted backup), app databases
beyond the ones above, and video. Message attachments that live only in iCloud are referenced, not
present.
