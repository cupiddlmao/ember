# DeepSeek change review — 2026-11-04

What this change does: adds real playlist pages, local playlist bookmarks, YouTube artwork validation and redirect checks, scaled cover decoding, a playing-row indicator, independent audio/lyrics request generations and valid MPRIS track IDs. It keeps the native GTK/GStreamer stack and adds no pip dependency. Reviewed for one desktop user with roughly 500-track playlists.

The workspace has no Git metadata. Comparison used the previous installed runtime and the earlier published source context; new helper files and their callers/tests were read directly. This report changes no application code.

## Must fix

1. **A lyrics dialog can apply the previous recording's lyrics to the current song** — [app.py:1287](app.py#L1287), [app.py:1393](app.py#L1393).
   - What this is: manual matching and file import use the new independent lyrics generation.
   - Problem: generation is now captured only when the dialog is submitted. Open a dialog for track A, let the queue advance to B, then accept A's lyrics: the callback creates a fresh generation for B and accepts A's text. The old dialog-opening track check was removed. Automatic queue advance can occur even with a modal dialog open.
   - Fix: capture the audio/track generation when opening either dialog; reject submission if that track changed, then increment the independent lyrics generation. Add an ordering test through the actual dialog callback or extracted submission logic, not just RequestGenerations alone.
   - If skipped: correctly timed lyrics can appear on the wrong song. Code flow and generation acceptance were checked; no GTK dialog interaction was performed.

## Should fix

2. **Artwork scopes collide between tracks and saved playlists** — [app.py:196](app.py#L196), [app.py:566](app.py#L566), [app.py:940](app.py#L940), [app.py:957](app.py#L957).
   - What this is: callbacks have a scope so clearing one view can discard its artwork work.
   - Problem: track_art_scope and saved_art_scope are separate integer counters starting at zero. Both become 1, 2, etc.; retire_art_scope matches only that integer. Leaving a track view can remove pending sidebar/Home cover callbacks, and refreshing saved playlists can remove pending track covers. An isolated call reproduced removal of an equal-valued scope.
   - Fix: use distinct scope identities such as ('tracks', generation) and ('saved', generation), or fresh identity objects. Test that retiring either group leaves the other's callbacks intact.
   - If skipped: some covers remain placeholders until the view is rebuilt, depending on download timing.

3. **Abandoned artwork still blocks the current track behind old downloads** — [app.py:515](app.py#L515), [app.py:562](app.py#L562), [app.py:566](app.py#L566), [app.py:975](app.py#L975).
   - What this is: one FIFO worker downloads covers; every displayed track is queued immediately.
   - Problem: retiring a scope removes callbacks, but art_worker never checks whether a queued key still has listeners before downloading it. Open a 500-track list and immediately switch views/play another track: old jobs still drain before new cover requests. Slow requests can each take up to 10 seconds. The obsolete work is discarded only after downloading.
   - Fix: skip keys without listeners before starting a download, clear their queued markers safely, and prioritize the current-track/header cover. Keep queue/callback synchronization coherent; test a retired job is not fetched. Visible-row loading can follow if measurements justify it.
   - If skipped: working artwork support can still feel broken because current covers wait behind hundreds of irrelevant requests. No long-running load benchmark was performed.

4. **MPRIS now advertises that Ember cannot raise its window or quit** — [mpris.py:120](mpris.py#L120).
   - What this is: desktop clients query CanRaise and CanQuit before showing actions.
   - Problem: the new explicit capability handling falls through to False for both root properties, although Raise and Quit handlers still work. Direct property calls reproduced False for each. This is a regression from the earlier default True.
   - Fix: return True explicitly for CanRaise and CanQuit and add a root-property test. Keep unsupported/unknown properties separate.
   - If skipped: desktop integrations can hide supported actions.

## Improvements that look correct

- Valid YouTube artwork hosts now pass explicit HTTPS validation; redirect targets are validated before following, with size/time limits preserved.
- Normal artwork decode scales to the target, reducing retained texture size. The fallback still permits full-resolution decoding if scaling fails; memory use has not been measured.
- Catalog results preserve playlist metadata, use limit=None, keep repeated entries, and count unavailable entries separately. Both active app callers use the new result dictionary.
- Local playlist saves allowlist metadata and canonical URLs, and use the existing private storage helper. No Google library sync is claimed.
- Audio and lyric request counters are separated, fixing the original bug where a lyric action canceled audio resolution. Track changes invalidate both pipelines.
- Hex encoding makes hyphenated YouTube IDs valid D-Bus object-path components without collisions between hyphens and underscores.
- Search uses the available width; playlist identity/actions and the current-row style are added without changing the playback provider.

## Installation and remaining old issues

The desktop runtime is still the previous version: app.py, music.py, mpris.py and style.css differ; artwork.py, generations.py and playlists.py are missing from the installed directory. Updating only app.py would therefore break startup. After fixes, copy all changed runtime files together and restart the owned application. This review did not install, restart or publish the new changes.

The earlier failed-seek reporting problem remains: set_position updates internal position and emits Seeked regardless of audio seek success. MPRIS CanSeek also reflects duration/media presence rather than actual stream seekability. These predate this UI change and are not new regressions.

## Verification

- Ran 13 focused tests successfully: test_music, test_artwork, test_playlists, test_mpris and test_generations.
- Ran isolated checks against actual methods confirming artwork-scope collision, false CanRaise/CanQuit, and generation acceptance after a track change.
- Inspected changed app flows and every music.search caller; read new modules/tests and README. Compared the old installed app to the checkout and checked installed-file completeness.
- Did not repeat already passing checks, run the full test suite, exercise native UI interactions, play audio, fetch provider lyrics, benchmark RAM/CPU, or verify live catalog playback. README's prior UI smoke results are DeepSeek's recorded results, not independently repeated here.

Verdict: fix 1–4 before installing this version. Keep the design and most implementation changes.
