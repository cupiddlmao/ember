# Ember review — 2026-11-04

What this repo does: Ember is a native GTK4 Linux music player. It searches YouTube Music, resolves audio with yt-dlp, plays through GStreamer, and renders timed lyrics. It also has a separate local CSV-to-YouTube playlist transfer helper. This review assumes one desktop user and playlists of roughly 500–1,000 tracks.

This is an inspection and handoff, not an implementation pass. No app code was changed during this review.

## Must fix

1. **YouTube artwork is rejected** — [app.py:28](app.py#L28), [app.py:553](app.py#L553).
   - What this is: all covers pass through an explicit image-host allowlist.
   - Problem: it permits only old Spotify/SoundCloud hosts. Live playlist thumbnails use i.ytimg.com and yt3.googleusercontent.com, so the screenshot shows placeholders even when metadata has artwork.
   - Fix: allow the verified YouTube image hosts while preserving HTTPS/host checks, including redirect targets. Reuse the worker and bounded download.
   - If skipped: no UI styling pass will restore covers in the list, player or lyrics header.

2. **Some tracks break desktop media metadata** — [mpris.py:52](mpris.py#L52).
   - What this is: MPRIS publishes a D-Bus object path identifying each track.
   - Problem: raw YouTube video IDs can contain hyphens, which object-path components forbid. Constructing GLib.Variant('o', '/org/mpris/MediaPlayer2/track/abc-defghij') reproduced a TypeError.
   - Fix: encode the complete ID into a valid component, for example UTF-8 bytes as hexadecimal, consistently in Metadata and SetPosition comparisons. Test hyphens and underscores.
   - If skipped: supported songs can fail metadata/property emission and desktop integrations.

3. **Importing lyrics can cancel pending audio playback** — [app.py:1039](app.py#L1039), [app.py:1196](app.py#L1196).
   - What this is: asynchronous results are rejected when request_id changes.
   - Problem: successful lyric imports and manual matches increment the same ID used by audio resolution. If an import completes while audio resolves, ready() discards the stream and pending_track stays true.
   - Fix: use independent lyric and audio generations, invalidating both when the track changes. Add a focused ordering regression test.
   - If skipped: an available song can remain stuck resolving because of a lyrics action. This case was traced in code, not reproduced through GTK.

## Should fix

4. **The installed app can keep running old code** — [README.md](README.md), [run.sh](run.sh).
   - What this is: the launcher uses a separate installed runtime directory.
   - Problem: PID 200263 was running from that directory since 11:38, before music.py's 11:53 pagination update. Installed and checkout music.py now match on disk, but the process already imported the older module. This strongly explains the screenshot's 100-track limit.
   - Fix: restart the owned app after updating the installed runtime. Add a simple explicit update/install command and visible app version so future handoffs can identify what is running.
   - If skipped: users see fixed bugs persist. The screenshot's 100 limit is not evidence that current source still limits results to 100.

5. **Playlist identity is discarded and playlists cannot be saved** — [music.py:46](music.py#L46), [app.py:931](app.py#L931), [app.py:1013](app.py#L1013).
   - What this is: playlist search returns just normalized tracks, and search_tracks always supplies “Search results”.
   - Problem: title/owner/artwork never reach the page; load_playlists merely opens Search. Sidebar and Home playlist containers have no active population flow. Pasting a URL does not create a persistent local bookmark.
   - Fix: return playlist metadata alongside tracks, render it as a playlist page, and save canonical playlist references locally on explicit action. Reuse existing sidebar/card widgets.
   - If skipped: every playlist looks like temporary search results and has to be pasted again.

6. **Artwork work is not bounded by the current view** — [app.py:188](app.py#L188), [app.py:521](app.py#L521), [app.py:960](app.py#L960).
   - What this is: every result creates a GTK row and queues its artwork immediately; the artwork cache alone is capped at 80 entries.
   - Problem: after fixing host support, a 500-track playlist enqueues hundreds of sequential downloads. Switching searches retains callbacks to abandoned widgets until those jobs drain. Textures are decoded at source resolution, and row widgets can retain them even after cache eviction.
   - Fix: prioritize visible rows/current track, discard abandoned-view waiters/jobs, and decode appropriately sized images. Start here before a larger list-widget rewrite; measure before claiming low RAM.
   - If skipped: covers can arrive slowly behind obsolete work and memory can grow with large playlists. No RAM benchmark was run.

7. **Account and transfer UI still reflects older providers** — [app.py:784](app.py#L784), [app.py:858](app.py#L858), [app.py:1009](app.py#L1009).
   - What this is: the app presents a YouTube Music account-shaped control, stores local likes, and retains old provider branches.
   - Problem: set_account_state('YouTube Music') marks the header connected even though it is a provider label, not a Google login. Settings promotes the limited transfer services despite the working local helper. load_more understands only Spotify URLs; unreachable Spotify sign-in messages remain.
   - Fix: label the header as the provider, state clearly that likes/bookmarks are local, link the local transfer guide, and remove unreachable active-UI branches. Keep existing settings and credentials intact.
   - If skipped: users infer account sync and discover obsolete advice.

8. **Player behavior lacks focused state tests** — [audio.py](audio.py), [app.py:901](app.py#L901), [mpris.py](mpris.py).
   - What this is: existing tests mainly cover metadata, provider boundaries, lyric parsing and transfer safety.
   - Problem: the cancellation bug and invalid media path above are not covered. Seek rejection also updates app.position and emits Seeked before verifying success; an unsupported seek can briefly report movement that did not happen.
   - Fix: add focused tests for those demonstrated boundaries; acknowledge seeks only after success and refresh position from GStreamer. Avoid exhaustive GUI testing.
   - If skipped: backend tests can pass while desktop playback state is wrong.

## Product work after those fixes

9. **Make the existing design useful before expanding it.** A true playlist header, working covers, saved sidebar entries, wider search and a distinct playing-row marker will improve this screenshot more than a new color scheme. Keep the dark/pink style and persistent player. This is a product recommendation rather than a correctness bug.

10. **Finish the lyric workflow before recommendations.** Keep LRCLIB as automatic line/plain fallback. Spicy Lyrics currently requires a key and manual Spotify recording link; local imports also work. Persist recording-specific matches/import references and offer a manual timing offset after the playback-generation bug is fixed. Do not claim direct Apple Music lyrics integration. API eligibility and key approval remain external requirements.

11. **Then add discovery with honest scope.** Start with related-song/radio results supported by the current provider, explicit loading/error states and queue actions. Account-personalized mixes require separate account integration; the transfer auth file does not automatically provide that feature.

12. **Clean old prototypes only after stabilizing the native path.** app.py still imports Spotify for shared settings and carries engine-era fields; engine/ is the old librespot Spotify prototype, not the current GStreamer runtime. Plan removal of the unused runtime wiring and obsolete docs in a separate change. Do not delete old user sessions/libraries or remove Spotify recording links needed for lyrics. No blanket deletion is proposed here.

## Verified in this review

- Inspected the supplied screenshot and active UI, playback, lyrics, MPRIS, catalog, settings/storage and transfer code; read README, dependency/startup configuration and relevant tests.
- 11 focused existing tests passed: test_music, test_import_youtube_auth and test_transfer.
- Live public playlist search returned 511 playable rows and 511 distinct video IDs. This is the current read, not the earlier transfer receipt of 508. The cause of the remote-count change was not established; do not silently force the count back to 508 or treat it as proven pagination duplication.
- Verified returned image hostnames and reproduced GLib rejection of a hyphenated object path.
- Verified installed/source music.py agree on disk and inspected the older running app's working directory/start time.
- Spicy Lyrics developer website fetch returned HTTP 403. Its current application/repository requirements could not be independently verified.

## Recommended order

First restart the updated installed app, then fix artwork, MPRIS and request cancellation. Give DeepSeek DEEPSEEK_PROMPT.md plus the source and screenshot for the playlist/UI pass. Review its diff and run the focused regressions before updating the installed runtime. Then improve lyrics matching/persistence, measure large-playlist behavior, and finally add discovery.

Verdict: keep the native stack and visual direction; fix incomplete provider migration and playback state before adding features.

Not checked: audible playback, a new native GTK visual session, actual word-timed provider responses, Google private library integration, RAM/CPU measurements, all archived SoundCloud/Monochrome/Rust internals, or a full dependency/license audit. No app source fixes or deployment were performed by this review.
