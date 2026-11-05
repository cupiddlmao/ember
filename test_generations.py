import unittest

from generations import RequestGenerations


class RequestGenerationTests(unittest.TestCase):
    def test_lyric_action_does_not_invalidate_audio_resolution(self):
        requests = RequestGenerations()
        audio, lyrics = requests.start_track()
        self.assertTrue(requests.audio_current(audio))
        self.assertTrue(requests.lyrics_current(lyrics))

        new_lyrics = requests.change_lyrics()
        self.assertTrue(requests.audio_current(audio))
        self.assertFalse(requests.lyrics_current(lyrics))
        self.assertTrue(requests.lyrics_current(new_lyrics))

        next_audio, next_lyrics = requests.start_track()
        self.assertFalse(requests.audio_current(audio))
        self.assertFalse(requests.lyrics_current(new_lyrics))
        self.assertTrue(requests.audio_current(next_audio))
        self.assertTrue(requests.lyrics_current(next_lyrics))


if __name__ == "__main__":
    unittest.main()
