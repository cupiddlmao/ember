# DeepSeek: redesign Ember Rust desktop to match the supplied reference

The user will attach an Apple Music desktop screenshot. Use it as the visual reference for desktop-rust/. Read this file and the existing Rust implementation before editing. Deliver the UI redesign, not another plan.

## Visual target
- Follow the screenshot's restrained native desktop layout: narrow charcoal left sidebar, darker main canvas, compact persistent top playback toolbar, and artwork-led content grids.
- Aim for its spacing, density, hierarchy and proportions. Keep Ember branding and pink/red accents. Do not copy Apple logos, Apple Music labels, promotional artwork or fake editorial content.
- Replace the current "Find a recording" prototype hero and technical explanatory copy. Remove prominent RUST PREVIEW branding from the product UI; keep development details in README.
- Sidebar search near the top, clearly grouped navigation, subtle selected-row background, concise section labels. Build navigation only for working destinations (Home, Search, Queue); expose existing playlist data only if it really exists in the Rust app.
- Do not add inert Browse/Radio/Artists/Albums buttons or pretend synced playlists already exist. Playlists are a planned backend feature, not implemented by this UI pass.
- Main Home can show real recent search/selected recordings from this session; use an honest attractive empty state on first launch. No fake playlists, generated recommendations, mock catalog or hard-coded sample songs.
- Real search results should retain usable track rows and their queue menus. Any artwork cards must activate real content; keep titles/artist captions readable and ellipsized.
- Compact top player: previous, play/pause, next; current artwork/title/artist; real playback progress/seek with elapsed/duration; volume; accessible queue and lyrics buttons. Preserve Stop as an accessible control/menu action.
- Retain the queue/lyrics side panel with a pinned close control. Opening it must not squeeze the main view beyond usable bounds: adapt layout or provide a bounded overlay on smaller windows.
- Lyrics retain fixed typography and stable wrapping, color-only active highlighting, smooth auto-follow, manual-scroll suspension, Resume following, and tap-to-seek.
- Use subtle surfaces, separators and restrained corners. Avoid oversized headings, marketing cards, decorative gradients, excessive padding and web-dashboard styling.
- GTK4 native Rust only. No Electron, browser/webview or new UI framework.
- Match the reference at wide sizes, and remain usable at the current minimum window size. Use bounded/reflowing content rather than forcing horizontal overflow.
- Keyboard focus, tooltips/accessibility names for icons, readable contrast and sensible tab order are required.

## Artwork
Current Rust tracks omit artwork. If needed, extend the existing bridge/model with an optional canonical artwork URL from existing music metadata.
Validate allowed HTTPS thumbnail hosts. Limit concurrent fetches, bytes and decoded dimensions; use bounded thumbnail memory/cache and stable placeholders. Do not decode full-size images repeatedly or block GTK. Reject stale artwork delivery after navigation/track changes.
Prefer existing dependencies/helpers and platform facilities. Do not introduce a large networking/image stack just to render thumbnails.
No audio download/cache or account work this pass.

## Preserve all working behavior
Read RUST_QUEUE_LYRICS_HANDOFF.md and desktop-rust/README.md. Preserve queue occurrence IDs/duplicates, Play next/add/reorder/remove/clear, automatic next, previous restart, stop/retry, paused preparation and stale result rejection.
Preserve the strong GLib ownership of AppUi and close cleanup, percentage volume conversion and Python control-character validation.
Preserve the latest fixes: only playbin StateChanged readiness is filtered by source; child GStreamer errors still reach the handler with player-ID isolation. Keep the real bus regression.
Preserve the cancellable300ms lyric scrolling animation and its manual-scroll/track/panel/close cancellation.
Keep the bounded Python bridge and Python music integration. Do not replace it with the abandoned Spotify Premium engine/.
Never log/persist temporary stream URLs or credentials. Never put user/provider text into unescaped GTK markup.

## Scope boundaries
Only modify desktop-rust/ and its own docs, except a minimal explicitly documented shared helper change if truly necessary.
Do not modify the shared Python music core, installed ~/.local/share/ember-player, mobile/backend or SwiftUI. Preserve unrelated working-tree edits.
No commits, pushes, installation or deployment.
Do not implement playlist sync now; see docs/playlist-sync-plan.md for the next architecture. Leave sensible space for a future playlist sidebar without adding dead controls.
Do not add radio, downloads, shuffle/repeat, local filesystem music scanning or session persistence.

## Validate and deliver
Run cargo test --locked and relevant Python tests if bridge changes. Add focused tests only for new meaningful logic such as artwork validation/bounds or seek conversion.
Compilation does not prove the UI renders. If native screenshot/interaction tooling is available, inspect wide and minimum-size layouts and exercise navigation, panel closure and transport briefly. Otherwise state that visual validation could not run.
Do not claim audible playback or lower RAM unless measured.
Update desktop-rust/README.md, and report changed files, tests actually run, remaining limitations and run command.
