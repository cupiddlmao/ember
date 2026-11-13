import Foundation

enum LyricsTiming {
    static let step = 0.25
    static let limit = 5.0

    static func normalized(_ offset: Double) -> Double {
        guard offset.isFinite else { return 0 }
        let clamped = min(limit, max(-limit, offset))
        let stepped = (clamped / step).rounded() * step
        return min(limit, max(-limit, stepped))
    }

    static func stepped(_ offset: Double, by steps: Int) -> Double {
        normalized(offset + (Double(steps) * step))
    }

    static func lyricTime(forPlaybackTime time: Double, offset: Double) -> Double {
        time - normalized(offset)
    }

    static func playbackTime(forLyricStart start: Double, offset: Double) -> Double {
        max(0, start + normalized(offset))
    }

    static func activeLineIndex(
        in lines: [LyricsLine],
        playbackTime: Double,
        offset: Double
    ) -> Int? {
        guard playbackTime.isFinite else { return nil }
        let lyricTime = lyricTime(forPlaybackTime: playbackTime, offset: offset)
        var selected: Int?
        for (index, line) in lines.enumerated() {
            guard let start = line.start else { continue }
            if start <= lyricTime + 0.08 {
                selected = index
            } else {
                break
            }
        }
        return selected
    }

    static func offsetText(_ offset: Double) -> String {
        let value = normalized(offset)
        if value == 0 { return "0.00 s" }
        return String(
            format: "%+.2f s",
            locale: Locale(identifier: "en_US_POSIX"),
            value
        )
    }

    static func directionText(_ offset: Double) -> String {
        let value = normalized(offset)
        if value == 0 { return "Aligned with audio" }
        return value > 0 ? "Lyrics appear later" : "Lyrics appear earlier"
    }
}

enum LyricsOffsetStore {
    static func offset(for trackID: String) -> Double {
        guard !trackID.isEmpty else { return 0 }
        return LyricsTiming.normalized(
            UserDefaults.standard.double(forKey: key(for: trackID))
        )
    }

    static func set(_ offset: Double, for trackID: String) {
        guard !trackID.isEmpty else { return }
        let key = key(for: trackID)
        let normalized = LyricsTiming.normalized(offset)
        if normalized == 0 {
            UserDefaults.standard.removeObject(forKey: key)
        } else {
            UserDefaults.standard.set(normalized, forKey: key)
        }
    }

    private static func key(for trackID: String) -> String {
        "ember.lyrics.timingOffset.\(trackID)"
    }
}
