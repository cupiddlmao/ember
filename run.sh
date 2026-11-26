#!/usr/bin/env bash
# Build and run Ember from this checkout. To start it from the desktop's app
# list instead, run ./install-desktop.sh once; it points the launcher at this
# same binary, so the two never become different builds.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
. ./desktop-env.sh
ember_ensure_venv
ember_export_cookies
cargo build --release --manifest-path desktop-rust/Cargo.toml
exec desktop-rust/target/release/ember-rust "$@"
