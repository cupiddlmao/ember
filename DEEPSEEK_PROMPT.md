# Prompt for DeepSeek

You are working on Ember, an existing native Linux music player. Fix the current GTK4 UI and the specific functional problems below. Read README.md, app.py, music.py, audio.py, lyrics.py, mpris.py, style.css and the relevant tests first. Read REVIEW.md for the implementation findings. Make targeted changes to this project; do not replace it with a mockup.

## Product and visual direction

Keep the existing Apple Music-inspired desktop vibe: near-black main canvas, slightly lighter sidebar, warm pink/red accent, clear typography, rounded artwork, restrained separators, a persistent bottom player, and an expressive full lyrics view. Keep it lightweight and native with Python/PyGObject GTK4, GStreamer, ytmusicapi and yt-dlp. No Electron, embedded browser, React rewrite, new theme framework or new playback provider. It plays music itself, rather than controlling another player. No Spotify Premium requirement.

The supplied screenshot shows a playlist displayed as generic “Search results”, a long URL crammed into a 400px field, missing artwork everywhere, a mostly empty Playlists sidebar, and a 100-track result count. Improve those actual problems instead of decorating placeholders.

## Scope, in order

1. Fix cover art. app.py ARTWORK_HOSTS still contains Spotify/SoundCloud hosts, while live YouTube Music results use i.ytimg.com and yt3.googleusercontent.com. Keep HTTPS and explicit host validation; validate redirects as well. Keep bounded downloads and graceful placeholders. Size artwork for its destination; avoid decoding hundreds of oversized originals or retaining abandoned view callbacks. Reuse the existing artwork worker.
2. Make pasted playlist URLs open a real playlist page: actual title, artwork, count, owner if provided, Play and Shuffle controls, and a clear action to save/remove the playlist locally. Store only metadata and canonical URLs, never media stream URLs. Saved playlists must reappear in the sidebar and Home after restart. Distinguish local saved playlists from Google account synchronization. Return playlist metadata from music.py and update every caller/test affected by that return shape.
3. Preserve complete playlist loading. music.py already uses get_playlist(limit=None). The screenshot's running installed process predates that change; restart/update the installed runtime before diagnosing pagination. Never hardcode a track count, truncate at 100, or silently remove intentional duplicate entries. Counts must reflect current returned playable entries, with an explanation if unavailable entries are excluded. Keep network work off the GTK thread and provide loading/error states. Do not change the remote playlist.
4. Improve layout within the existing style. Use available width for the search entry, with sensible minimum/maximum widths. Separate “Search” from playlist identity. Keep rows readable and consistent, align duration labels, distinguish the currently playing track from the selected row, and retain keyboard activation and focus indicators. Keep the bottom player's artwork, track text, transport, seek and volume balanced at the existing 980px minimum and 1280px screenshot size. Avoid fake albums, fake mixes or fake personal account information.
5. Preserve timed lyrics. Keep real word/syllable timing, line/plain fallback, local TTML/LRC import, credits, duet/background-vocal treatment, smooth follow and reduced-motion behavior. Never fabricate timestamps or claim direct Apple Music access. Spicy Lyrics currently needs a user-provided key and a Spotify recording link for lyrics only. Keep keys private and optional. Separate lyric-request cancellation from audio-request cancellation so importing lyrics while a track resolves cannot prevent playback.
6. Fix MPRIS track paths: raw YouTube IDs containing hyphens are invalid D-Bus object-path components. Encode IDs consistently and retain SetPosition identity checks. Make capability properties match actual queue/track state.
7. Remove misleading Spotify sign-in copy and obsolete Spotify-only pagination in the active UI. Do not delete existing user settings, old provider sessions or libraries. Replace settings' transfer-service-only advice with the existing local CSV helper documentation.

## Guardrails

Do not read, print, upload or commit browser cookies, auth JSON, provider keys, local settings, playlist review reports, or files in ~/.config/ember-player. Do not change the working transfer parser or create/upload playlists as part of UI work. Do not weaken media-host validation or promise restricted/subscription content will play. The local transfer credential file is not an implemented Ember account sign-in.

The desktop launcher runs an independent copy in ~/.local/share/ember-player. Source changes alone do not update it. Report the changed runtime files and update that copy only when working on the user's PC with authorization. Do not kill unrelated processes or publish code without instruction.

## Deliverables and verification

Implement the changes, not just suggestions. Use the existing code and standard library; add no dependency unless unavoidable. Add small regression tests for the changed metadata contract, artwork host checks, invalid MPRIS IDs, saved playlist persistence, and independent lyrics/audio request cancellation. Run only relevant tests. Explain what changed and what remains unverified. Do not claim visual, audible playback, RAM, or live provider verification unless actually performed. Supply before/after screenshots when your environment supports native GTK screenshots. Keep app.py/style.css changes coherent and preserve existing behavior.
