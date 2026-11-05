"""Independent request generations for audio resolution and lyric actions."""


class RequestGenerations:
    def __init__(self):
        self.audio = 0
        self.lyrics = 0

    def start_track(self):
        """Invalidate both pipelines when the current recording changes."""
        self.audio += 1
        self.lyrics += 1
        return self.audio, self.lyrics

    def change_lyrics(self):
        """Cancel an in-flight lyric lookup/import without touching audio."""
        self.lyrics += 1
        return self.lyrics

    def audio_current(self, generation):
        return generation == self.audio

    def lyrics_current(self, generation):
        return generation == self.lyrics
