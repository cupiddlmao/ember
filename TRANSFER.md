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
