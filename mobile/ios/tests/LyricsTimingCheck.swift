import Foundation

@main
struct LyricsTimingCheck {
    static func main() {
        check(LyricsTiming.stepped(0, by: 1) == 0.25, "one step later")
        check(LyricsTiming.stepped(0, by: -1) == -0.25, "one step earlier")

        check(
            LyricsTiming.lyricTime(forPlaybackTime: 10, offset: 0.25) == 9.75,
            "positive offset should delay lyric display by using an earlier effective time"
        )
        check(
            LyricsTiming.playbackTime(forLyricStart: 10, offset: 0.25) == 10.25,
            "positive offset should make a tapped line seek later"
        )
        check(
            LyricsTiming.lyricTime(forPlaybackTime: 10, offset: -0.25) == 10.25,
            "negative offset should advance lyric display by using a later effective time"
        )
        check(
            LyricsTiming.playbackTime(forLyricStart: 10, offset: -0.25) == 9.75,
            "negative offset should make a tapped line seek earlier"
        )

        check(
            LyricsTiming.lyricTime(forPlaybackTime: 0, offset: 5) == -5,
            "a delayed opening lyric must not highlight at playback zero"
        )
        check(
            LyricsTiming.lyricTime(forPlaybackTime: 4.75, offset: 5) == -0.25,
            "opening lyrics must remain pending until their delay elapses"
        )
        check(
            LyricsTiming.lyricTime(forPlaybackTime: 5, offset: 5) == 0,
            "a zero-timestamp lyric becomes due when its delay elapses"
        )

        for offset in [0.25, -0.25, 2.5, -2.5] {
            let playback = LyricsTiming.playbackTime(forLyricStart: 42.5, offset: offset)
            check(
                LyricsTiming.lyricTime(forPlaybackTime: playback, offset: offset) == 42.5,
                "tap-to-seek conversion should reverse at offset \(offset)"
            )
        }

        check(LyricsTiming.normalized(8) == 5, "upper bound")
        check(LyricsTiming.normalized(-8) == -5, "lower bound")
        check(LyricsTiming.stepped(5, by: 1) == 5, "upper bound step")
        check(LyricsTiming.stepped(-5, by: -1) == -5, "lower bound step")
        check(
            LyricsTiming.playbackTime(forLyricStart: 0.1, offset: -5) == 0,
            "tap-to-seek should not go before the start of the track"
        )

        check(LyricsTiming.offsetText(0.25) == "+0.25 s", "offset display")
        check(LyricsTiming.directionText(0.25) == "Lyrics appear later", "later direction")
        check(LyricsTiming.directionText(-0.25) == "Lyrics appear earlier", "earlier direction")
        check(LyricsTiming.directionText(0) == "Aligned with audio", "reset direction")

        let lines = [
            LyricsLine(text: "first", startMs: 1_000, endMs: 4_000, words: []),
            LyricsLine(text: "second", startMs: 5_000, endMs: nil, words: [])
        ]
        check(
            LyricsTiming.activeLineIndex(in: lines, playbackTime: 0.9, offset: 0) == nil,
            "a line must not activate before its timestamp"
        )
        check(
            LyricsTiming.activeLineIndex(in: lines, playbackTime: 1.0, offset: 0) == 0,
            "a line should activate at its timestamp"
        )
        check(
            LyricsTiming.activeLineIndex(in: lines, playbackTime: 5.0, offset: 0) == 1,
            "the latest due line should be active"
        )
        check(
            LyricsTiming.activeLineIndex(in: lines, playbackTime: 5.16, offset: 0.25) == 0,
            "a positive offset should delay the second line beyond the early-highlight window"
        )
        check(
            LyricsTiming.activeLineIndex(in: lines, playbackTime: 5.18, offset: 0.25) == 1,
            "the second line should activate inside the 80 ms early-highlight window"
        )
        check(
            LyricsTiming.activeLineIndex(in: lines, playbackTime: 5.2, offset: 0.25) == 1,
            "a positive offset retains the existing early-highlight allowance"
        )
        check(
            LyricsTiming.activeLineIndex(in: lines, playbackTime: 4.8, offset: -0.25) == 1,
            "a negative offset should advance the second line"
        )

        print("Lyrics timing offset, active-line, bounds, and tap-to-seek checks passed")
    }

    private static func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        precondition(condition(), "Lyrics timing check failed: \(message)")
    }
}
