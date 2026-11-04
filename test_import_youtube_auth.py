import unittest
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
from import_youtube_auth import parse_curl


class ImportAuthTests(unittest.TestCase):
    def test_hidden_terminal_preserves_long_cookie(self):
        with tempfile.TemporaryDirectory() as home:
            master, slave = pty.openpty()
            process = subprocess.Popen([sys.executable, 'import_youtube_auth.py'], stdin=slave, stdout=slave, stderr=slave, env={**os.environ, 'HOME': home})
            os.close(slave)
            try:
                self.assertTrue(select.select([master], [], [], 5)[0])
                os.read(master, 4096)
                cookie = 'SAPISID=' + 'x' * 12000
                command = f"curl https://music.youtube.com/youtubei/v1/search -H 'x-goog-authuser: 0' -b '{cookie}'\n\x04"
                data = command.encode()
                while data:
                    written = os.write(master, data[:1024])
                    data = data[written:]
                self.assertEqual(process.wait(timeout=5), 0)
                path = Path(home) / '.config/ember-player/youtube-transfer-auth.json'
                self.assertEqual(json.loads(path.read_text())['cookie'], cookie)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)

    def test_brave_headers_and_cookie(self):
        result = parse_curl("curl 'https://music.youtube.com/youtubei/v1/search' \\\n+ -H 'x-goog-authuser: 0' -b 'SAPISID=fake; other=example' --data-raw 'ignored'")
        self.assertEqual(result['cookie'], 'SAPISID=fake; other=example')
        self.assertEqual(result['x-goog-authuser'], '0')

    def test_wrong_host_missing_headers_and_shell_text(self):
        for text in ("curl https://example.com -b secret", "curl https://music.youtube.com", "echo $(touch /tmp/never-execute)"):
            with self.assertRaises(ValueError):
                parse_curl(text)
