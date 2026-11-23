# DeepSeek handoff: Rust desktop queue and lyrics

Extend desktop-rust/ with queue controls and lyrics. Keep the current dark/pink GTK4 layout for this pass. Do not redesign Home, sidebar or library, and do not attempt full Python-app feature parity.

Read RUST_DESKTOP_HANDOFF.md, desktop-rust/README.md and the current Rust/Python bridge implementation first. Review existing queueing.py/test_queueing.py and lyrics.py for behavior to reuse or mirror, rather than inventing competing semantics.

## Preserve these fixes
- The GLib polling source strongly retains AppUi until close; other callbacks remain weak. Preserve shutdown and child-process cleanup.
- AppUi volume uses percentage units (initial70) and converts once for GStreamer.
- bridge.py validates controls with unicodedata, not nonexistent str.iscontrol(). Keep test_bridge.py.
- Bounded subprocess JSON, explicit process arguments, request deadlines, stale-result rejection, sanitized errors and in-memory-only stream URLs must remain intact.
- Keep the iPhone/backend code and installed ~/.local/share/ember-player unchanged. Preserve unrelated checkout edits.
- No commits, pushes, deployment or installed-app replacement.

## 1. Queue behavior
Implement a small Rust queue model separate from GTK widgets:
- Each occurrence has its own stable queue-entry ID. Duplicate recordings are independent.
- Activating a search result replaces the queue with the displayed search results and starts that occurrence.
- Track-row menus: Play next and Add to queue.
- Play next inserts immediately after the current occurrence; the latest Play next selection becomes next. Add to queue appends.
- With no current entry, queue actions create a pending queue without unexpectedly starting audio; explicit Play or selecting an entry starts it.
- Queue panel shows current and upcoming entries. Support selecting an entry, removing an upcoming occurrence, moving it up/down, and clearing upcoming.
- Reordering/removing/clearing upcoming must not interrupt current playback. Never remove the current entry through an upcoming action.
- Add previous/next transport controls. Previous restarts after3 seconds; otherwise selects the previous entry (restart current if no previous).
- End of track advances exactly once. At queue end stop cleanly; no autoplay radio or implicit looping this pass.
- Stop cancels pending resolution and stops audio but preserves the queue so Play can restart the selected entry.
- A failed resolve/playback keeps the selected queue entry and permits retry, skip and opening the queue. Do not auto-skip repeatedly.
- Resolving/downloading is not playing: pause during preparation must cancel pending autoplay and remain silent until explicit Play.
- Reject stale search, resolve, lyrics and playback events after selection/stop/close. Check bus messages belong to the appropriate pipeline/source; child state changes must not masquerade as whole-player readiness.
- Keep pending work bounded and clean it up on close. Do not add polling workers per queue entry.

Do not add shuffle, repeat modes, persistence, downloads or prefetch in this pass.

## 2. LRCLIB lyrics
- Fetch automatically when the selected track changes. Use the existing metadata lookup/parser if suitable; inspect lyrics.py and mobile/backend/lyrics_service.py before duplicating provider code.
- Keep provider I/O off GTK's main thread. Extend the bounded Python bridge only as needed; add a separate cancellable lyrics lane so lyric loading cannot block search or audio resolution.
- Results need explicit synced/plain/instrumental/missing/error states. Handle loading and retry visibly; show LRCLIB attribution.
- Use actual provider timestamps. Do not invent word timing or depend on Apple Music, Spotify Premium or an API key.
- Display lyrics in a toggleable panel with a pinned visible close button, bounded width and wrapped text. Opening, failing or closing lyrics must not trap the window or stop music.
- Keep font size/weight, spacing and row geometry stable during highlighting. Change color/opacity only; never reconstruct different visible text for the active line.
- Smoothly follow the current timed line. Manual scrolling suspends following until Resume following is pressed. Avoid interpreting programmatic scrolling as manual input.
- Tap a timed line to seek; plain lyrics are not seekable. Read real GStreamer playback position and duration, handle unavailable values, and clamp audio seek requests appropriately.
- If showing timing-offset controls, defer them: this pass does not need them.
- Bound response size, lyric count and metadata lengths; reject invalid/nonfinite timestamps and escape provider text rather than treating it as markup.
- Clear old lyrics when changing tracks and reject late completions. Closing the panel during a request must remain safe.

## 3. Artwork
Artwork is outside this pass. Do not add downloads, a cache, placeholders for future pages or a redesigned player.

## Validation
Use focused Rust queue tests for duplicate occurrences, Play next ordering, add/remove/reorder/clear, boundaries, stop/retry and end-of-track advancing once. Cover paused preparation and stale results through the actual playback state logic, not only a standalone token helper.
Test the actual Python bridge with fake provider data for lyric states, timestamp validation and bounded output. Keep tests offline.
Run cargo test --locked and the Python bridge tests; compile the changed GTK app. Do not run redundant full build/check commands after an equivalent passing compile.
If a native desktop is available, perform a small interaction check covering queue actions, close/reopen lyrics and stop while loading; report exactly what was exercised. Never claim audible playback or visual correctness from compilation alone.
Update desktop-rust/README.md with delivered behavior, commands and known limitations. Finish with changed files, checks actually run and any remaining device/native checks. Do not claim RAM savings without measurement.
