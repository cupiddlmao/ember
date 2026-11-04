#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ ! -x .venv/bin/python ]; then
  uv venv --python /usr/bin/python3 --system-site-packages .venv
  uv pip install --python .venv/bin/python -r requirements.txt
fi
exec .venv/bin/python app.py
