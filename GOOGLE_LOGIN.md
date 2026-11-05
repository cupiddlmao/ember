# Google login for Ember

Ember uses ytmusicapi's Google device-code OAuth flow. It opens the system browser; you approve on Google's page and Ember receives the result automatically. No Google password or browser cookies are entered into Ember.

## One-time Google Cloud setup

1. Open [Google Cloud Console](https://console.cloud.google.com/), select or create a project, and enable **YouTube Data API v3** under APIs & Services → Library.
2. Configure Google Auth Platform / OAuth consent for your project. If the app is in Testing, add the Google account you will use under its test users. Complete any required audience/app information.
3. Create an OAuth client with application type **TVs and Limited Input devices**. A Web or Desktop client is not interchangeable with the device flow used here.
4. In Ember, open **Settings → Sign in with Google** and enter that client's ID and secret. Enter them only in Ember's local window, never in chat or GitHub.
5. Click **Sign in with Google**. Ember opens the Google verification page and displays a code. Enter that code, choose your account and approve the access Google describes. Keep the Ember window open while it waits.

When approval succeeds, Ember saves the client and refresh token in private mode-0600 files under your Ember configuration directory, then loads account playlists. Reopening Ember reuses the token; ytmusicapi refreshes access tokens when necessary. Google can revoke or expire authorization, especially during project testing; sign in again when required. Project policies or verification requirements can prevent authorization. No credentials are bundled with the source.

Close the sign-in window to cancel. If the browser does not open, click **Continue in browser**. Expired codes require restarting sign-in.

OAuth playlist browsing uses the official YouTube Data API: it lists playlists you own, including private ones. Playlists you saved from other creators are not reliably exposed by this API; open those using their public/unlisted links and save them locally. Video metadata from this API may identify the upload channel instead of the music artist. Local likes/bookmarks stay local. Playback still uses the existing public yt-dlp/GStreamer path. OAuth does not unlock restricted or subscription content.

Existing browser-header authentication remains a fallback when no OAuth token exists. OAuth takes precedence once configured. The standalone CSV transfer helper still uses its own `--auth` browser-header file; this change does not migrate its upload authentication.

## Checked

Focused tests cover device-code responses, pending/slow-down polling, cancellation, expiration/decline, private token storage, credential wiring, official API pagination and repeated playlist tracks. Live OAuth credentials loaded six owned playlists and 511 tracks from the first playlist. A specific private playlist, long-term token refresh and audible playback were not verified. The unofficial YouTube Music OAuth browse endpoint returned HTTP400; OAuth playlist reads now use the official API.
