# Ember release checklist

Work top to bottom. Each gate is written so it can be re-run by someone who was
not in the room. Anything CI already proves is marked **[CI]**; anything it
cannot prove is listed in [What CI cannot prove](#what-ci-cannot-prove).

`main` is protected: merges require `Rust desktop`, `Python core, bridge and
mobile backend` and `iOS Swift checks`, and the branch must be up to date.
Direct pushes still bypass those checks, so confirm the run on the commit you
are releasing.

## 0. Versions must agree

There is no single version constant; four places have to be edited by hand.

| Component | Where | Current |
| --- | --- | --- |
| Desktop | `desktop-rust/Cargo.toml` | `0.1.0` |
| Backend | `mobile/backend/http_api.py` (`VERSION`) | `0.3.0` |
| Backend image | `mobile/backend/docker-compose.yml` (`image:`) | `ember-mobile-api:0.3.0` |
| iPhone | `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION` | `0.3.0` / `3` |

```bash
grep -m1 '^version' desktop-rust/Cargo.toml
grep -n 'VERSION = ' mobile/backend/http_api.py
grep -n 'image:' mobile/backend/docker-compose.yml
grep -E 'MARKETING_VERSION|CURRENT_PROJECT_VERSION' \
  mobile/ios/EmberMobile.xcodeproj/project.pbxproj | sort -u
```

- [ ] Backend `VERSION` and the Compose image tag match.
- [ ] The iPhone bundle version is bumped when app behaviour changes, so a
      SideStore install is distinguishable from the previous one.
- [ ] No stale version strings elsewhere. LRCLIB identifies clients by the
      `USER_AGENT` in `mobile/backend/lyrics_service.py`, so bump it with the
      version instead of leaving it at the previous release's string.

## 1. Pre-flight

- [ ] `git status` is clean and you are on `main`.
- [ ] `git fetch && test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"`.
- [ ] The CI run for this exact commit is green in all three jobs.
- [ ] `git log --oneline origin/main -1` is the commit you intend to tag.

## 2. Automated gates

```bash
python -m unittest discover -q
cd desktop-rust && python -m unittest -q test_bridge.py test_cache.py test_library.py
cd desktop-rust && cargo test --locked          # 80 passed, 12 ignored
.venv/bin/python -m unittest discover -s mobile/backend/tests -t . -v
```

- [ ] Every command exits `0`.
- [ ] `cargo test` still reports **12 ignored**. A higher number means new
      display coverage was added that this run did not execute — the ignored
      tests are the ones that catch visual regressions.

The ignored display tests need **one process each**; the GTK harness aborts if
two run in the same process:

```bash
GDK_BACKEND=x11 DISPLAY=:0 cargo test --locked <full::test::path> \
  -- --ignored --exact --test-threads=1
```

- [ ] Run each ignored display test you touched this cycle, one at a time.

## 3. Desktop build

Requirements: Rust 1.92+, GTK 4.14+, GStreamer 1.24+ (including `souphttpsrc`
and `playbin`), libsoup 3, and the checkout's `.venv`.

```bash
bash run.sh          # creates .venv, builds release, launches
```

- [ ] Release build succeeds and the window opens.
- [ ] `desktop-rust/target/release/ember-rust` exists.
- [ ] Smoke test: search, play, pause, seek, next/previous, lyrics follow and
      tap-to-seek, queue reorder, Settings opens, shared playlist opens,
      Last.fm shows its connected state.
- [ ] Offline behaviour is sane: cached audio still plays, uncached audio
      reports an error rather than hanging.

Shipping note: there is no installer or package. The binary is not standalone —
it locates `music.py`, `.venv/bin/python` and `desktop-rust/bridge.py` by
walking up from the executable, or honours `EMBER_CHECKOUT_ROOT`. A release is
the binary plus a working checkout, not a single file.

## 4. Python backend

Deploy on the Ubuntu VM. Keep the existing `.env`, token, Compose project
(`ember-mobile`) and Tailscale `/ember` mount.

```bash
cd mobile/backend
[ -f .env ] || cp .env.example .env   # then set EMBER_API_TOKEN
chmod 600 .env
docker compose -p ember-mobile up -d --build
docker compose -p ember-mobile ps
```

- [ ] **Never `down -v`** — it deletes the audio cache and the playlist library.
- [ ] `EMBER_API_TOKEN` is at least 24 characters (`openssl rand -hex 32`).
- [ ] Health returns the version you just bumped:

```bash
set -a; . ./.env; set +a
curl -fsS -H "Authorization: Bearer $EMBER_API_TOKEN" \
  http://127.0.0.1:18080/v1/health      # {"status":"ok","version":"0.3.0","commit":"…"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/v1/health   # 401
```

- [ ] Playlist preview, a second page, and a lyrics lookup all return data.
- [ ] `tailscale serve status` still mounts `/ember` to `127.0.0.1:18080`.
- [ ] Request logs contain method, path, status and duration only — no query
      strings, headers or access keys.
- [ ] Playlist library volume is backed up before and after the deploy (see
      `mobile/backend/LIBRARY_API.md`).

## 5. Unsigned IPA

The workflow is manual (`workflow_dispatch`), runs on `macos-26`, and needs no
Apple account.

```bash
# Actions → iOS unsigned IPA → Run workflow → branch: main
```

- [ ] All steps succeed. The only expected skip is
      `Upload build log on failure`.
- [ ] The `Package unsigned IPA` step printed a SHA-256 — record it here:
      `____________________`.
- [ ] Artifact `EmberMobile-unsigned-ipa` downloaded (about 1.06 MB).
- [ ] `Git commit:` in the log equals `git rev-parse --short=7 HEAD`.

The workflow already asserts no `_CodeSignature`, that `UIBackgroundModes`
contains `audio`, and that `EmberGitCommit` in the packaged `Info.plist` matches
the checked-out commit, so a green run proves the IPA is unsigned and stamped.

- [ ] Install with SideStore, enter the Tailscale HTTPS origin (including
      `/ember` if that is the base path) and the server's `EMBER_API_TOKEN`.
- [ ] Settings shows the expected version, build and short commit.
- [ ] Note: a free Apple ID signature expires after 7 days.

## 6. Cross-device acceptance

Nothing here can be automated; it needs the desktop and a phone against the same
backend.

- [ ] Create an Ember playlist on the desktop, add the same song twice, reorder,
      then confirm the phone shows both occurrences in the new order.
- [ ] Delete on the phone; the desktop reflects it after refresh.
- [ ] Conflict: edit on both, then save the stale one. Expect a reload message,
      never a silent overwrite.
- [ ] Airplane mode on the phone: cached playlists read-only, edits disabled,
      cached audio plays.
- [ ] Last.fm: play a track past the threshold and confirm one scrobble; pause,
      buffer and seek must not produce extra scrobbles.
- [ ] Song radio starts from a track menu and leaves the queue intact on failure.
- [ ] Lyrics: tap-to-seek lands on the line; Earlier/Later shifts presentation
      only and never moves the audio.

## 7. Tag

Tag the commit whose CI run you verified in gate 1, and build the IPA from the
tag so the bundle can be traced back to it.

```bash
git tag -a v0.3.0 -m "Ember 0.3.0"
git push origin v0.3.0
```

- [ ] Tag pushed, pointing at the commit you actually released.
- [ ] `git cat-file -t v0.3.0` prints `tag`: annotated, not lightweight.
- [ ] IPA built by dispatching `ios-unsigned` **on the tag**, and
      `EmberGitCommit` inside the bundle equals the tag's short SHA.
- [ ] Record the three artifacts: desktop build, IPA SHA-256 and size, backend
      image tag.

Record them in the GitHub Release for the tag, not in this file, so the record
can never describe the wrong release. `v0.3.0` holds commit `3b586e3`; its
release notes carry CI run `45314961731`, IPA
`b4d6e76ec00f29fcdb5de658dcc41b6aec22ff44a0119ddfcb36ff519665255d`
(1,071,802 bytes, built by run `45315342964`) and image
`ember-mobile-api:0.3.0`.

## Rollback

- **Backend:** check out the previous tag and
  `docker compose -p ember-mobile up -d --build`. Volumes are preserved; do not
  delete them to "start clean".
- **iPhone:** reinstall the previous IPA through SideStore.
- **Desktop:** rebuild from the previous tag.

## What CI cannot prove

Do not treat a green run as a release. CI does not cover:

- Audible playback, real audio devices, or long-running memory behaviour.
- Visual correctness. The display tests check specific geometry and transitions,
  not that the app looks right at every size.
- iPhone behaviour at all beyond compiling the Swift self-checks: no install,
  no launch, no background audio, no lock-screen controls, no SideStore signing.
- Tailscale Serve, certificate trust, or the `/ember` path on a real device.
- The Docker image against live YouTube Music, or upstream availability in
  general (ytmusicapi and yt-dlp break independently of this repository).
- Last.fm delivery to the real service, or scrobble deduplication on their side.
