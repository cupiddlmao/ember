# Last.fm (one shared account)

The backend owns Last.fm authorization and submissions. Desktop and iPhone report actual listening; audio downloads do not count as listens. Discord RPC is unchanged.

## Setup

1. Create a Last.fm API application at https://www.last.fm/api/account/create (desktop authorization flow; no callback URL needed).
2. Add `LASTFM_API_KEY` and `LASTFM_API_SECRET` to the backend's private `.env`. Never put these values in source control, screenshots or chat.
3. Rebuild/restart the backend with the existing Compose project. Compose passes these variables through; leave them blank to keep the integration disabled.
4. In either app's Settings, choose **Connect Last.fm**, approve the connection in your browser, then choose **Finish authorization**. One connection serves both apps. Status may take up to a minute to reach an already-playing client.
5. **Disconnect Last.fm** affects both clients and clears the backend's pending queue. It does not delete your Last.fm listening history.

The Last.fm session and pending scrobbles live in owner-only `lastfm.sqlite3` in the existing persistent playlist-library volume. The API key/secret stay on the server. Clients retain only bounded listening-event outboxes in their private application data, scoped to server/account; no Last.fm session is sent to clients. Back up the volume as private account data.

## Behavior and limits

- Tracks must be longer than 30 seconds. Scrobbling requires half the duration or four minutes, whichever is first. Paused, buffering, suspended and seek-jump time earns no listening credit.
- Now-playing updates are sent only while playing. Last.fm has no clear-now-playing method, so its prior flag can remain briefly after pause. No Discord RPC integration is added.
- Completed listens are saved locally before submission, retained across app restarts, and transferred into a durable backend outbox. Playback UUIDs make app retries idempotent. The original listening timestamp is preserved for upstream retries. A network failure after Last.fm accepts a submission is inherently ambiguous; there is no upstream idempotency key.
- Upstream retry runs while the backend is running. Client retries run while the app's playback timer is active; iPhone resumes retries with playback. Offline cold starts require a successful status check before new listening is counted. Partial listening counters are in memory and are lost if the app is terminated. No background timer is invented to bypass iOS suspension.
- Both devices listening independently count as separate playbacks. Metadata uses the catalog's title, artist and album when available; incorrect YouTube metadata can produce an incorrect Last.fm match.
- Client outboxes are capped at 1,000 events; the backend keeps up to 20,000 pending/receipt records and expires records after 14 days. These are bounded personal-use queues, not an archival history store.

## Authenticated API

`POST /v1/lastfm` (also under `/ember`) uses the existing Ember bearer access key and a JSON body limited to 4 KiB. Actions: `status`, `connect`, `finish`, `disconnect`, `now`, `scrobble`, `radio`.

`now` needs `title` and `artist`; optional `album` is forwarded to Last.fm for both now-playing and scrobble reports. Older clients and queued events without an album remain supported. `scrobble` additionally needs a playback UUID `id`, integer Unix `started`, and integer seconds `duration` and `listened`. The backend validates listening thresholds and timestamps, stores the event before acknowledging it, then submits asynchronously. Status exposes configuration, connected username and pending count, never session keys.

## Song radio

The iPhone track menus and Now Playing song-options menu offer **Song radio**.
`POST /v1/lastfm` with `{"action":"radio","title":"…","artist":"…"}` returns
`{"tracks":[…]}` in the standard search-track format. Ember authentication is
required; a connected personal Last.fm account is not. The server uses its existing
Last.fm API credentials for `track.getSimilar` (up to12 suggestions), then searches
YouTube Music's songs catalog with four concurrent workers and5second network
timeouts. Matches require normalized title and artist equality; unavailable or
failed lookups and duplicate IDs are skipped. This favors exact recordings over
loose matches and can produce a short or empty mix for obscure metadata.

The app places the selected seed first, then the matched recommendations. Empty
or failed requests leave the queue intact. New radio requests, playback changes,
pause or disconnect invalidate an older pending response. Radio is a finite mix;
there is no similar-artist fallback or automatic extension in this pass.

This feature needs both the updated backend and a newly built IPA. Existing
scrobbling configuration/credentials are reused; no additional secret is needed.
