"""Import Brave's Copy as cURL locally; never execute the copied command."""
import json
import os
from pathlib import Path
import shlex
import sys
import termios
from urllib.parse import urlsplit

from ytmusicapi import setup
from services import save_private


def parse_curl(value):
    value = value.replace('\x1b[200~', '').replace('\x1b[201~', '').replace('\r\n', '\n')
    try:
        tokens = shlex.split(value.replace('\\\n', ''))
    except ValueError:
        raise ValueError('Copied text has incomplete quotes. Copy the entire request as cURL (bash).') from None
    if not tokens or tokens[0] != 'curl':
        raise ValueError('Choose Copy as cURL (bash) in Brave.')
    urls = [t for t in tokens[1:] if t.startswith('https://')]
    if len(urls) != 1 or urlsplit(urls[0]).hostname != 'music.youtube.com':
        raise ValueError('Copy a request from music.youtube.com.')
    headers = {}
    for index, token in enumerate(tokens[1:], 1):
        if token in ('-H', '--header', '-b', '--cookie'):
            if index + 1 >= len(tokens):
                raise ValueError('Incomplete copied request.')
            item = tokens[index + 1]
            if token in ('-b', '--cookie'):
                headers['cookie'] = item
            else:
                name, separator, content = item.partition(':')
                if separator and name:
                    headers[name.lower()] = content.strip()
    if any('\n' in v or '\r' in v for v in headers.values()):
        raise ValueError('Invalid copied headers.')
    missing = [k for k in ('cookie', 'x-goog-authuser') if not headers.get(k)]
    if missing:
        raise ValueError('Missing header names: ' + ', '.join(missing) + '. Copy a search request while signed in.')
    try:
        return json.loads(setup(headers_raw='\n'.join(f'{k}: {v}' for k, v in headers.items())))
    except Exception:
        raise ValueError('ytmusicapi could not convert the headers.') from None


def main():
    if not sys.stdin.isatty():
        sys.exit('Run this in your local terminal.')
    print('Paste Copy as cURL (bash), press Enter, then Ctrl+D. Input stays hidden.', flush=True)
    settings = termios.tcgetattr(sys.stdin)
    hidden = termios.tcgetattr(sys.stdin)
    # Canonical terminal input truncates long cookie lines at about 4 KB.
    hidden[3] &= ~(termios.ECHO | termios.ICANON)
    hidden[6][termios.VMIN] = 1
    hidden[6][termios.VTIME] = 0
    try:
        termios.tcsetattr(sys.stdin, termios.TCSANOW, hidden)
        chunks = bytearray()
        while True:
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk or b'\x04' in chunk:
                chunks.extend(chunk.split(b'\x04', 1)[0])
                break
            chunks.extend(chunk)
            if len(chunks) > 1_000_000:
                sys.exit('\nCopied request is too large.')
        value = chunks.decode('utf-8', errors='replace')
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSANOW, settings)
    try:
        headers = parse_curl(value)
    except ValueError as error:
        sys.exit('\nCould not import: ' + str(error))
    destination = Path.home() / '.config/ember-player/youtube-transfer-auth.json'
    save_private(destination, headers)
    print('\nSaved private login file. You can return to the chat; do not share its contents.')


if __name__ == '__main__':
    main()
