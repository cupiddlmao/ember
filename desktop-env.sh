# Shared desktop setup, sourced by run.sh and by the installed launcher.
#
# The two must not drift. A launcher that skips the requirements check keeps
# running an old .venv, and an old .venv is what made YouTube appear to refuse
# sign-in on age-restricted recordings when the real gap was a missing
# challenge-solver package: the app still played everything else, so nothing
# pointed at the environment.

ember_ensure_venv() {
    wanted=$(cat requirements.txt mobile/backend/requirements.txt | sha256sum | cut -d' ' -f1)
    if [ ! -x .venv/bin/python ]; then
        uv venv --python /usr/bin/python3 --system-site-packages .venv || return 1
    fi
    if [ "$(cat .venv/.requirements.sha256 2>/dev/null || true)" != "$wanted" ]; then
        printf 'Ember: refreshing the Python environment for this checkout.\n' >&2
        uv pip install --python .venv/bin/python -r requirements.txt -r mobile/backend/requirements.txt || return 1
        printf '%s\n' "$wanted" > .venv/.requirements.sha256
    fi
}

ember_export_cookies() {
    # Optional Netscape cookies.txt for age-restricted and sign-in-gated
    # recordings. The server reads the same variable from its own environment.
    if [ -z "${EMBER_YOUTUBE_COOKIES:-}" ] && [ -f "$HOME/.config/ember/cookies.txt" ]; then
        EMBER_YOUTUBE_COOKIES="$HOME/.config/ember/cookies.txt"
        export EMBER_YOUTUBE_COOKIES
    fi
}
