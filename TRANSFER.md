# Import into Ember’s shared playlists

Ember-owned playlists need only your existing Ember server/access key, configured
under **Ember → Settings** on PC. No Spotify or YouTube login is needed.
The CSV must first have a complete match report with explicitly approved rows.
The old matching command below still produces that report; its `upload` command
writes to YouTube Music, not Ember.

Preview an existing reviewed match report:

```bash
.venv/bin/python import_ember_playlist.py ~/Downloads/GG.csv ~/Downloads/GG-youtube-review.json --title GG
```

Add `--apply` to import into the configured shared backend. Approved rows keep
CSV order, duplicate occurrences, and album names from Album Name/album CSV columns when available. Unapproved/missing matches are omitted.
The importer checks the CSV fingerprint, validates metadata, saves a private exact
retry body before writing, and verifies the saved playlist. Interrupted imports can
be resumed with the identical command. Stable IDs prevent duplicate creation when a
saved receipt is retried. Keep the private `rust-library/imports` receipt files;
a completed import never overwrites later edits or recreates a deleted playlist.
A saved receipt is tied to its report and title, so retry with the identical
command: a different title is rejected before anything is written. When no receipt
exists yet, an existing playlist with the same name is left untouched and the
import is refused; choose another title.

Refresh Library on PC and iPhone after import. On PC, open a playlist and use the
**⋯ beside Play → Rename playlist / Delete playlist**. On iPhone, open the shared
playlist and use the **top-right ⋯ → Rename / Delete playlist**. Deletion asks for
confirmation and removes that shared playlist from all devices. Songs remain
available in search. Both devices use revisions to reject stale edits.
The iPhone controls require the shared-library-enabled build; new source must be
built on macOS and sideloaded before it appears on an older installed version.

---

# Transfer Spotify playlists to YouTube Music

This command-line helper has no imposed 200/500-song cap. YouTube account/rate/playlist limits still apply. It does not download audio. Existing destination playlists are never deleted or replaced. Repeated video IDs are deduplicated, so repeated songs appear once.

## 1. Export Spotify CSV

Export your playlist with [Exportify](https://exportify.net/) (third-party export tool), or another CSV exporter. The CSV needs `Track Name` and `Artist Name(s)` columns; `title`/`artist` also work. Optional `Duration (ms)` improves matching. Exportify's present availability and limits have not been verified here.

Keep your CSV/report outside this source folder if you do not want your library accidentally committed. No Spotify Premium or Spotify developer credentials are needed by this script; exporting may require Spotify authorization.

## 2. Match and review

Run from the Ember project folder:

```bash
.venv/bin/python transfer_playlist.py match /path/to/spotify.csv /path/to/review.json
```

Matching uses YouTube Music Songs search, title/artist similarity and optional duration. It conservatively rejects changes such as original to remix/cover/live. No heuristic guarantees the correct recording.

The report saves progress after each track. Repeating the command resumes; changing the source CSV requires a new report. Review `matched_title`, `matched_artists`, `score`, and `video_id`. Set `approved` to `true` only for matches you want. Leave misses/uncertain entries false; you may correct a video ID manually. Reports contain library metadata but no login credentials. If search fails/rate limits, stop and retry later rather than repeatedly hammering the service.

## 3. Authorize locally

The installed ytmusicapi supports browser-header authentication, but marks it deprecated. Follow its [browser authentication documentation](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html). Never send browser headers/cookies in chat, commit them, or put them in the review report. They grant account access.

Run this locally in your terminal, then follow its prompts:

```bash
mkdir -p ~/.config/ember-player
chmod 700 ~/.config/ember-player
umask 077
.venv/bin/ytmusicapi browser --file ~/.config/ember-player/youtube-transfer-auth.json
```

This is separate from Ember's public playback; it does not sign Ember into Google. If browser auth stops working, this helper needs an auth update; OAuth requires your own Google client credentials and is not configured here.

## 4. Create the reviewed playlist

This command writes to your YouTube Music account and creates an **unlisted** playlist (anyone with its link can view it):

```bash
.venv/bin/python transfer_playlist.py upload /path/to/review.json \
  --auth ~/.config/ember-player/youtube-transfer-auth.json \
  --title 'Imported Spotify playlist' \
  --state /path/to/transfer-state.json
```

Only explicitly approved entries are uploaded, in batches of50. Keep the state file: reruns reuse that destination and reread its contents before adding missing tracks. If playlist creation has an uncertain network outcome, it stops instead of creating a duplicate; check your account and recover its ID in the state file as instructed. Avoid editing the destination from another device during transfer. Do not reuse a state file for an unrelated playlist.

The command prints the destination URL. Paste it into Ember Search. Ember now requests all available public playlist pages. Large playlists take longer to load; unavailable tracks may be omitted by YouTube.

## Verification

Four focused tests cover CSVs over500 tracks, recording matching, checkpoint/approval preservation, batched writes and resume. No authenticated playlist creation has been run; your CSV and local account authorization are still needed.

Repair missing album names on an existing Ember import (fills blanks only; preserves
occurrence IDs, ordering, edits and newly added songs):

```bash
.venv/bin/python import_ember_playlist.py ~/Downloads/GG.csv ~/Downloads/GG-youtube-review.json --repair-albums
```

Review the count, then repeat with `--apply`. Repair snapshots and exact retry bodies
are saved privately beside import receipts. Revision conflicts stop without replacing
concurrent changes. Existing import retries continue using their original saved bodies.
New songs added in either app retain the catalog album when supplied; a song with no
catalog album is not assigned a guessed album.
