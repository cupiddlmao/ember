import tempfile
import unittest
from pathlib import Path

import services


class PrivateStorageTests(unittest.TestCase):
    def test_missing_and_malformed_files_are_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            self.assertEqual(services.load_private(path), {})
            path.write_text("{broken")
            self.assertEqual(services.load_private(path), {})
            self.assertEqual(path.read_text(), "{broken")

    def test_oversized_file_is_not_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.json"
            path.write_bytes(b"x" * (services.MAX_PRIVATE_BYTES + 1))
            self.assertEqual(services.load_private(path), {})
            self.assertEqual(path.stat().st_size, services.MAX_PRIVATE_BYTES + 1)


if __name__ == "__main__":
    unittest.main()
