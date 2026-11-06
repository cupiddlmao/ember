#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ ! -x .venv/bin/python ]; then
  uv venv --python /usr/bin/python3 --system-site-packages .venv
  uv pip install --python .venv/bin/python -r requirements.txt -r mobile/backend/requirements.txt
fi
cargo build --release --manifest-path desktop-rust/Cargo.toml
exec desktop-rust/target/release/ember-rust "$@"
