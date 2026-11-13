import Foundation

@main
struct LastFMCheck {
    static func main() {
        var clock = ListeningClock()
        clock.sample(position: 0, time: 0, playing: true)
        clock.sample(position: 1, time: 1, playing: true)
        clock.sample(position: 1, time: 2, playing: false)
        clock.sample(position: 1, time: 100, playing: false)
        clock.sample(position: 1, time: 101, playing: true)
        clock.sample(position: 2, time: 102, playing: true)
        clock.sample(position: 100, time: 103, playing: true)
        clock.sample(position: 100, time: 104, playing: true)
        assert(clock.listened == 2)
        clock.breakContinuity()
        clock.sample(position: 101, time: 105, playing: true)
        assert(clock.listened == 2)
        clock.sample(position: 102, time: 106, playing: true)
        assert(clock.listened == 3)
        print("Last.fm pause, buffering, seek and suspension timing checks passed")
    }
}
