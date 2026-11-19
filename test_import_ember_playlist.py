import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

import import_ember_playlist as importer
from mobile.backend.config import Settings
from mobile.backend.http_api import create_server
from mobile.backend.tests.test_http_api import FakeService, FakePlaylists, FakeLyrics, TOKEN


class EmberImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "songs.csv"
        self.report = self.directory / "review.json"
        self.source.write_text("title,artist\nAwake,Tycho\nAwake,Tycho\nOther,Artist\n")
        row = {"title": "Awake", "artist": "Tycho", "duration_ms": "", "approved": True,
               "video_id": "dm4tkSNKfFI", "matched_title": "Awake", "matched_artists": ["Tycho"]}
        self.data = {"source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                     "matches": [copy.deepcopy(row), copy.deepcopy(row),
                                 {"title": "Other", "artist": "Artist", "duration_ms": "", "approved": False}]}
        self.report.write_text(json.dumps(self.data))
        environment = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.directory / "desktop")})
        environment.start(); self.addCleanup(environment.stop)
        settings = Settings(token=TOKEN, cache_dir=self.directory / "cache",
                            library_dir=self.directory / "library", rate_limit_per_minute=1000)
        self.server = create_server(settings, service=FakeService(self.directory),
            address=("127.0.0.1", 0), playlists=FakePlaylists(), lyrics=FakeLyrics())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}/ember"
        importer.client.private_save(importer.client.root() / "connection.json", {"server": self.address, "key": TOKEN})

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def run_import(self):
        return importer.run(self.source, self.report, "GG", apply=True)

    def write_report(self, rows):
        self.source.write_text("title,artist\n" + "".join(f"{r['title']},{r['artist']}\n" for r in rows))
        self.data = {
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "matches": [{"title": r["title"], "artist": r["artist"], "duration_ms": "",
                         "approved": True, "video_id": r["video_id"],
                         "matched_title": r["title"], "matched_artists": [r["artist"]]}
                        for r in rows],
        }
        self.report.write_text(json.dumps(self.data))

    def test_committed_timeout_retry_preserves_order_duplicates_and_exact_body(self):
        request = importer.client.request
        sent = []
        def timeout(server, key, method, suffix="", body=None):
            result = request(server, key, method, suffix, body)
            if method == "POST":
                sent.append(copy.deepcopy(body))
                raise TimeoutError()
            return result
        with patch.object(importer.client, "request", side_effect=timeout):
            with self.assertRaises(TimeoutError): self.run_import()
        def retry(server, key, method, suffix="", body=None):
            if method == "POST": self.assertEqual(body, sent[0])
            return request(server, key, method, suffix, body)
        with patch.object(importer.client, "request", side_effect=retry): detail = self.run_import()
        self.assertEqual(detail["entries"], sent[0]["entries"])
        self.assertNotEqual(detail["entries"][0]["id"], detail["entries"][1]["id"])
        self.assertEqual(len(request(self.address, TOKEN, "GET")["playlists"]), 1)
        state = next((importer.client.root() / "imports").glob("*.json"))
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(TOKEN, state.read_text())

    def test_completed_import_never_replaces_later_edits_or_recreates_deleted_playlist(self):
        detail = self.run_import()
        state = importer.client.execute({"operation": "rename", "id": detail["id"],
                                        "revision": detail["revision"], "name": "New name"})
        self.assertTrue(state["acknowledged"])
        self.assertEqual(self.run_import()["name"], "New name")
        state = importer.client.execute({"operation": "delete", "id": detail["id"],
                                        "revision": state["detail"]["revision"]})
        self.assertTrue(state["acknowledged"])
        with self.assertRaises(importer.client.Rejected): self.run_import()
        self.assertEqual(importer.client.request(self.address, TOKEN, "GET")["playlists"], [])

    def add_source_albums(self):
        self.source.write_text("title,artist,Album Name\nAwake,Tycho,Awake\nAwake,Tycho,Alternate\nOther,Artist,Other\n")
        self.data["source_sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.report.write_text(json.dumps(self.data))

    def legacy_import(self):
        prepare = importer.prepare
        def legacy(*args):
            fingerprint, title, selected, skipped = prepare(*args)
            for entry in selected: entry["track"]["album"] = ""
            return fingerprint, title, selected, skipped
        with patch.object(importer, "prepare", side_effect=legacy):
            return self.run_import()

    def test_new_import_keeps_per_occurrence_album_and_legacy_receipts_replay_exactly(self):
        self.add_source_albums()
        detail = self.legacy_import()
        self.assertEqual([e["track"]["album"] for e in detail["entries"]], ["", ""])
        self.assertEqual(self.run_import()["entries"], detail["entries"])
        _, _, selected, _ = importer.prepare(self.source, self.report, "GG")
        self.assertEqual([e["track"]["album"] for e in selected], ["Awake", "Alternate"])

    def test_album_repair_preserves_edits_duplicates_order_and_exact_timeout_retry(self):
        import uuid
        self.add_source_albums()
        detail = self.legacy_import()
        entries = copy.deepcopy(detail["entries"])
        entries[0]["track"]["album"] = "Keep my edit"
        extra = copy.deepcopy(entries[1]); extra["id"] = str(uuid.uuid4())
        entries.append(extra)
        importer.client.request(self.address, TOKEN, "PUT", "/"+detail["id"],
            {"requestId":str(uuid.uuid4()), "expectedRevision":detail["revision"], "name":"Renamed", "entries":entries})
        request = importer.client.request
        sent = []
        def timeout(server, key, method, suffix="", body=None):
            result = request(server, key, method, suffix, body)
            if method == "PUT":
                sent.append(copy.deepcopy(body)); raise TimeoutError()
            return result
        with patch.object(importer.client, "request", side_effect=timeout):
            with self.assertRaises(TimeoutError):
                importer.repair_albums(self.source, self.report, "GG", True)
        def retry(server, key, method, suffix="", body=None):
            if method == "PUT": self.assertEqual(body, sent[0])
            return request(server, key, method, suffix, body)
        with patch.object(importer.client, "request", side_effect=retry):
            updated = importer.repair_albums(self.source, self.report, "GG", True)
        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual([e["id"] for e in updated["entries"]], [e["id"] for e in entries])
        self.assertEqual([e["track"]["album"] for e in updated["entries"]], ["Keep my edit", "Alternate", ""])
        self.assertEqual(importer.repair_albums(self.source, self.report, "GG", True)["revision"], updated["revision"])

    def test_add_new_appends_only_missing_occurrences_and_keeps_existing_entries(self):
        detail = self.run_import()
        entries = copy.deepcopy(detail["entries"])
        entries[0]["track"]["album"] = "Keep my edit"
        importer.client.request(self.address, TOKEN, "PUT", "/" + detail["id"],
            {"requestId": str(uuid.uuid4()), "expectedRevision": detail["revision"],
             "name": "GG", "entries": entries})
        before = importer.client.request(self.address, TOKEN, "GET", "/" + detail["id"])

        self.write_report([
            {"title": "Awake", "artist": "Tycho", "video_id": "dm4tkSNKfFI"},
            {"title": "Awake", "artist": "Tycho", "video_id": "dm4tkSNKfFI"},
            {"title": "New", "artist": "Artist", "video_id": "AAAAAAAAAAA"},
        ])
        updated = importer.add_new(self.source, self.report, "GG", True)

        self.assertEqual(updated["name"], "GG")
        self.assertEqual(updated["revision"], before["revision"] + 1)
        self.assertEqual([e["id"] for e in updated["entries"][:2]], [e["id"] for e in before["entries"]])
        self.assertEqual([e["track"] for e in updated["entries"][:2]], [e["track"] for e in before["entries"]])
        self.assertEqual(updated["entries"][0]["track"]["album"], "Keep my edit")
        self.assertEqual(len(updated["entries"]), 3)
        self.assertEqual(updated["entries"][2]["track"]["id"], "AAAAAAAAAAA")

    def test_add_new_with_nothing_missing_does_not_write(self):
        detail = self.run_import()
        request = importer.client.request
        writes = []
        def spy(server, key, method, suffix="", body=None):
            if method != "GET":
                writes.append(method)
            return request(server, key, method, suffix, body)
        with patch.object(importer.client, "request", side_effect=spy):
            result = importer.add_new(self.source, self.report, "GG", True)
        self.assertEqual(writes, [])
        self.assertEqual(result["revision"], detail["revision"])
        self.assertEqual(result["entries"], detail["entries"])

    def test_add_new_settles_a_saved_append_when_the_put_response_was_lost(self):
        self.run_import()
        self.write_report([{"title": "Awake", "artist": "Tycho", "video_id": "dm4tkSNKfFI"},
                           {"title": "New", "artist": "Artist", "video_id": "AAAAAAAAAAA"}])
        request = importer.client.request
        def lost(server, key, method, suffix="", body=None):
            result = request(server, key, method, suffix, body)
            if method == "PUT":
                raise TimeoutError()
            return result
        with patch.object(importer.client, "request", side_effect=lost):
            with self.assertRaises(TimeoutError):
                importer.add_new(self.source, self.report, "GG", True)

        updated = importer.add_new(self.source, self.report, "GG", True)

        self.assertEqual([e["track"]["id"] for e in updated["entries"]], ["dm4tkSNKfFI", "dm4tkSNKfFI", "AAAAAAAAAAA"])
        state = importer.client.read_json(
            importer.client.root() / "imports" / (updated["id"] + "-additions.json"))
        self.assertTrue(state["complete"])

    def test_validation_and_preview_never_send_writes(self):
        with patch.object(importer.client, "request") as request:
            importer.run(self.source, self.report, "GG")
            request.assert_not_called()
        self.data["matches"][0]["title"] = "Changed"
        self.report.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError): self.run_import()
        self.source.write_text("title,artist\nDifferent,Artist\n")
        with self.assertRaises(ValueError): self.run_import()

    def test_failed_receipt_persistence_blocks_post_and_changed_retry_inputs_are_rejected(self):
        with patch.object(importer.client, "private_save", side_effect=OSError()):
            with self.assertRaises(OSError): self.run_import()
        self.assertEqual(importer.client.request(self.address, TOKEN, "GET")["playlists"], [])
        self.run_import()
        self.data["matches"][0]["approved"] = False
        self.report.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError): self.run_import()
        self.assertEqual(len(importer.client.request(self.address, TOKEN, "GET")["playlists"]), 1)


if __name__ == "__main__":
    unittest.main()
