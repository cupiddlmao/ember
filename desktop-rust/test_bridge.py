import io
import json
import unittest

from bridge import (
    MAX_LYRIC_LINES,
    MAX_LYRIC_TEXT_CHARS,
    MAX_OUTPUT_BYTES,
    emit,
    lyrics_for,
    normalize_lyrics_payload,
    request_track,
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


if __name__ == "__main__":
    unittest.main()
