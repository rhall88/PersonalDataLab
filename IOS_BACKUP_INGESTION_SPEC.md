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
```

Import is restart-safe: each ParsedExport is idempotent, and a re-run short-circuits unchanged files.
Records and relationships carry the backup date as authority (`exported_at`), so a later backup
of the same device updates them. A changed parse of the *same* backup is a conflict by design (the
importer never guesses between equal-authority claims), which is why the first real import went
through parser versions v1 to v4; see `PROJECT_STATE.md`.

## Not yet in scope

Messages (`sms.db`, which would supersede the iMazing CSV source), Notes text and call-recording
transcripts (in `NoteStore.sqlite`), photos, Health, app databases beyond the two above, and video.
