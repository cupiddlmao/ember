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
exec desktop-rust/target/release/ember-rust "$@"
