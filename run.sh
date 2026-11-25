#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Reinstall when the pinned requirements change, not only when .venv is missing.
wanted=$(cat requirements.txt mobile/backend/requirements.txt | sha256sum | cut -d' ' -f1)
if [ ! -x .venv/bin/python ]; then
  uv venv --python /usr/bin/python3 --system-site-packages .venv
fi
if [ "$(cat .venv/.requirements.sha256 2>/dev/null || true)" != "$wanted" ]; then
  uv pip install --python .venv/bin/python -r requirements.txt -r mobile/backend/requirements.txt
  printf '%s\n' "$wanted" > .venv/.requirements.sha256
fi
cargo build --release --manifest-path desktop-rust/Cargo.toml
# Optional: sign-in cookies for age-restricted / sign-in-gated recordings.
# Drop a Netscape cookies.txt at ~/.config/ember/cookies.txt (or export
# EMBER_YOUTUBE_COOKIES to your own path) and the desktop app's yt-dlp
# fetches will use that account's permissions.
if [ -z "${EMBER_YOUTUBE_COOKIES:-}" ] && [ -f "$HOME/.config/ember/cookies.txt" ]; then
  export EMBER_YOUTUBE_COOKIES="$HOME/.config/ember/cookies.txt"
fi
exec desktop-rust/target/release/ember-rust "$@"
