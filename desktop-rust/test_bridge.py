import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge import (
    MAX_LYRIC_LINES,
    MAX_LYRIC_TEXT_CHARS,
    MAX_OUTPUT_BYTES,
    DependencyError,
    RequestError,
    csv_import_action,
    emit,
    fail,
    lyrics_for,
    normalize_lyrics_payload,
    playable_audio,
    request_track,
    signin_refusal_message,
    slim_track,
    stream_url,
)

TRACK = {
    "id": "dm4tkSNKfFI",
    "uri": "https://music.youtube.com/watch?v=dm4tkSNKfFI",
    "name": "Awake",
    "artists": [{"name": "Tycho"}],
    "album": {"name": "Awake"},
    "duration_ms": 284000,
}


class FakeService:
    def __init__(self, payload):
        self.payload = payload
        self.arguments = None

    def lookup(self, *arguments):
        self.arguments = arguments
        return self.payload


class LyricBridgeTests(unittest.TestCase):
    def test_synced_state_uses_integer_provider_timestamps_and_filters_bad_ones(self):
        payload = {
            "source": "lrclib",
            "kind": "synced",
            "lines": [
                {"text": "One", "startMs": 0, "endMs": 900},
                {"text": "float", "startMs": 10.5, "endMs": 20},
                {"text": "bool", "startMs": True, "endMs": 20},
                {"text": "negative", "startMs": -1, "endMs": 20},
                {"text": "reversed", "startMs": 2000, "endMs": 1000},
                {"text": "Two", "startMs": 1500, "endMs": None},
            ],
        }
        result = normalize_lyrics_payload(payload, TRACK["id"])
        self.assertEqual(result["kind"], "synced")
        self.assertEqual([line["text"] for line in result["lines"]], ["One", "Two"])
        self.assertTrue(all(line["start_ms"] is not None for line in result["lines"]))
        self.assertIsNone(result["lines"][1]["end_ms"])

    def test_plain_instrumental_and_missing_states_are_explicit(self):
        plain = normalize_lyrics_payload(
            {
                "source": "lrclib",
                "kind": "plain",
                "lines": [{"text": "Words", "startMs": 0, "endMs": 5}],
            },
            TRACK["id"],
        )
        self.assertEqual(plain["kind"], "plain")
        self.assertIsNone(plain["lines"][0]["start_ms"])

        instrumental = normalize_lyrics_payload(
            {"source": "lrclib", "kind": "instrumental", "lines": []},
            TRACK["id"],
        )
        self.assertEqual(instrumental["kind"], "instrumental")
        self.assertEqual(instrumental["lines"], [])

        missing = normalize_lyrics_payload(
            {"source": "lrclib", "kind": "none", "lines": []},
            TRACK["id"],
        )
        self.assertEqual(missing["kind"], "missing")
        self.assertEqual(missing["lines"], [])

    def test_fake_provider_integration_and_output_bounds(self):
        service = FakeService(
            {
                "source": "lrclib",
                "kind": "synced",
                "lines": [
                    {"text": "x" * (MAX_LYRIC_TEXT_CHARS + 100), "startMs": index, "endMs": index + 1}
                    for index in range(MAX_LYRIC_LINES + 50)
                ],
            }
        )
        result = lyrics_for(service, TRACK)
        self.assertEqual(service.arguments[0], TRACK["id"])
        self.assertEqual(service.arguments[3], "Awake")
        self.assertEqual(len(result["lines"]), MAX_LYRIC_LINES)
        self.assertTrue(all(len(line["text"]) <= MAX_LYRIC_TEXT_CHARS for line in result["lines"]))

        output = io.StringIO()
        emit(output, {"ok": True, "data": {"text": "x" * (MAX_OUTPUT_BYTES + 1)}})
        encoded = output.getvalue()
        self.assertLessEqual(len(encoded.encode("utf-8")), MAX_OUTPUT_BYTES)
        envelope = json.loads(encoded)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "bridge")

    def test_invalid_provider_state_is_rejected(self):
        with self.assertRaises(RuntimeError):
            normalize_lyrics_payload({"kind": "unexpected", "lines": []}, TRACK["id"])
        with self.assertRaises(RuntimeError):
            normalize_lyrics_payload({"kind": "synced", "lines": "nope"}, TRACK["id"])


class StreamURLTests(unittest.TestCase):
    def test_accepts_realistic_https_stream(self):
        url = "https://rr1.example.googlevideo.com/videoplayback?id=fake&sig=fake"
        self.assertEqual(stream_url(url), url)

    def test_rejects_whitespace_and_controls(self):
        for character in [" ", "\t", "\n", "\x00", "\x7f", "\x80"]:
            with self.subTest(character=repr(character)):
                with self.assertRaises(RuntimeError):
                    stream_url("https://rr1.googlevideo.com/audio?x=" + character)

    def test_rejects_other_hosts_and_protocols(self):
        for url in [
            "http://rr1.googlevideo.com/audio",
            "https://googlevideo.com.example.org/audio",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(RuntimeError):
                    stream_url(url)


class ArtworkTests(unittest.TestCase):
    def test_extracts_bounded_canonical_artwork(self):
        item = {
            "id": TRACK["id"],
            "uri": TRACK["uri"],
            "name": TRACK["name"],
            "artists": TRACK["artists"],
            "album": {
                "name": "Awake",
                "images": [
                    {"url": "https://example.com/fake.jpg", "width": 900},
                    {"url": "https://i.ytimg.com/vi/dm4tkSNKfFI/mqdefault.jpg", "width": 320},
                    {"url": "https://lh3.googleusercontent.com/cover=w1200", "width": 1200},
                ],
            },
            "duration_ms": 284000,
        }
        track = slim_track(item)
        self.assertEqual(
            track["artwork_url"],
            "https://i.ytimg.com/vi/dm4tkSNKfFI/mqdefault.jpg",
        )
        self.assertEqual(request_track(track)["artwork_url"], track["artwork_url"])

        bad = dict(item)
        bad["album"] = {"name": "Awake", "images": [{"url": "http://i.ytimg.com/fake.jpg"}]}
        self.assertNotIn("artwork_url", slim_track(bad))


class CsvImportTests(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        # Validation must not depend on the real import module being importable.
        self.module_token = object()
        self.previous_module = sys.modules.get("import_ember_playlist")
        self.fake = types.ModuleType("import_ember_playlist")
        sys.modules["import_ember_playlist"] = self.fake

    def tearDown(self):
        if self.previous_module is not None:
            sys.modules["import_ember_playlist"] = self.previous_module
        else:
            sys.modules.pop("import_ember_playlist", None)

    def install_calls(self):
        calls = []

        def make(name):
            def action(source, report, title, apply):
                calls.append((name, str(source), str(report), title, apply))
                print(f"{title}: 2 approved occurrences.")
                return {"id": "pid", "revision": 1} if apply else None

            return action

        self.fake.run = make("run")
        self.fake.add_new = make("add_new")
        self.fake.repair_albums = make("repair_albums")
        return calls

    def envelope(self):
        return json.loads(self.out.getvalue())

    def run_action(self, command):
        # Mirror the bridge entry point: request-shape errors become envelopes.
        self.out = io.StringIO()
        try:
            csv_import_action(command, self.out)
        except RequestError as error:
            fail(self.out, "input", str(error))
        except DependencyError as error:
            fail(self.out, "dependencies", str(error))
        return self.envelope()

    def test_relative_and_missing_paths_are_refused(self):
        for value in (None, 5, "", "relative.csv", "/nonexistent/ember-report.json"):
            with self.subTest(value=value):
                envelope = self.run_action(
                    {"source": value, "report": value, "title": "T", "mode": "import"}
                )
                self.assertFalse(envelope["ok"])
                self.assertIn(envelope["error"]["code"], {"input", "invalid_input"})

    def test_bad_title_and_mode_are_refused(self):
        import tempfile

        with tempfile.NamedTemporaryFile() as handle:
            path = handle.name
            for title in ("", "x" * 121, "bad\x01title"):
                envelope = self.run_action(
                    {"source": path, "report": path, "title": title, "mode": "import"}
                )
                self.assertFalse(envelope["ok"])
            for mode in ("upload", None, 3):
                envelope = self.run_action(
                    {"source": path, "report": path, "title": "T", "mode": mode}
                )
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["error"]["code"], "input")

    def test_action_routes_by_mode_and_captures_messages(self):
        import tempfile

        calls = self.install_calls()
        with tempfile.NamedTemporaryFile() as handle:
            path = handle.name
            envelope = self.run_action(
                {"source": path, "report": path, "title": "GG", "mode": "import"}
            )
            self.assertTrue(envelope["ok"])
            self.assertEqual(envelope["data"]["messages"], ["GG: 2 approved occurrences."])
            self.assertIsNone(envelope["data"]["playlist"])
            self.assertEqual(calls[-1], ("run", path, path, "GG", False))

            envelope = self.run_action(
                {
                    "source": path,
                    "report": path,
                    "title": "GG",
                    "mode": "add",
                    "apply": True,
                }
            )
            self.assertTrue(envelope["ok"])
            self.assertEqual(envelope["data"]["playlist"], {"id": "pid", "revision": 1})
            self.assertEqual(calls[-1][0], "add_new")
            self.assertTrue(calls[-1][4])

            envelope = self.run_action(
                {
                    "source": path,
                    "report": path,
                    "title": "GG",
                    "mode": "repair",
                    "apply": True,
                }
            )
            self.assertTrue(envelope["ok"])
            self.assertEqual(calls[-1][0], "repair_albums")

    def test_value_error_from_script_reaches_the_user_sanitized(self):
        import tempfile

        def run(source, report, title, apply):
            raise ValueError("A playlist with this name already exists. https://leak.example")

        self.fake.run = run
        self.fake.add_new = lambda *a: None
        self.fake.repair_albums = lambda *a: None
        with tempfile.NamedTemporaryFile() as handle:
            path = handle.name
            envelope = self.run_action(
                {"source": path, "report": path, "title": "GG", "mode": "import"}
            )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")
        self.assertNotIn("https://", envelope["error"]["message"])


class SignInRefusalTests(unittest.TestCase):
    """A refusal from broken cookies must not read as a per-track problem."""

    REFUSAL = (
        "YouTube could not provide this audio. It may be unavailable, "
        "region/age restricted, or require sign-in. Try another recording."
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def use(self, text):
        path = self.directory / "cookies.txt"
        path.write_text(text)
        return patch.dict(os.environ, {"EMBER_YOUTUBE_COOKIES": str(path)})

    def test_an_unusable_cookie_file_names_the_repair(self):
        with self.use(""):
            message = signin_refusal_message(self.REFUSAL)
        self.assertIsNotNone(message)
        self.assertIn("EMBER_YOUTUBE_COOKIES", message)

    def test_a_healthy_cookie_file_leaves_a_private_video_generic(self):
        with self.use(".youtube.com\tTRUE\t/\tFALSE\t0\tSAPISID\tvalue\n"):
            self.assertIsNone(signin_refusal_message(self.REFUSAL))

    def test_an_unsupported_stream_is_not_a_signin_failure(self):
        with self.use(""):
            self.assertIsNone(
                signin_refusal_message(
                    "YouTube returned an unsupported or restricted audio stream."
                )
            )


class PlayableAudioTests(unittest.TestCase):
    """A first play must not hand YouTube's address-bound stream URL to the player."""

    def setUp(self):
        self.downloads = []

    def resolve_with(self, lookups, enabled, error=None):
        queue = list(lookups)

        def fake_cached_audio(_track):
            return queue.pop(0) if queue else None

        def fake_cache_action(action, track=None):
            self.downloads.append(action)
            if error:
                raise error

        with patch("bridge.cached_audio", fake_cached_audio), \
                patch("bridge.cache_enabled", return_value=enabled), \
                patch("bridge.cache_action", fake_cache_action):
            return playable_audio(TRACK)

    def test_a_cached_copy_is_played_without_another_download(self):
        self.assertEqual(self.resolve_with(["file:///audio/a.m4a"], True), "file:///audio/a.m4a")
        self.assertEqual(self.downloads, [])

    def test_an_uncached_recording_is_fetched_before_the_player_touches_youtube(self):
        self.assertEqual(self.resolve_with([None, "file:///audio/a.m4a"], True), "file:///audio/a.m4a")
        self.assertEqual(self.downloads, ["download"])

    def test_a_disabled_cache_streams_the_url_instead(self):
        self.assertIsNone(self.resolve_with([None], False))
        self.assertEqual(self.downloads, [])

    def test_a_refused_fetch_falls_back_to_streaming(self):
        error = RuntimeError("YouTube could not provide this audio for download.")
        self.assertIsNone(self.resolve_with([None], True, error))
        self.assertEqual(self.downloads, ["download"])


if __name__ == "__main__":
    unittest.main()
