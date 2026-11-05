import Foundation

@main
struct QueueCheck {
    static func main() {
        let a = Track(id: "aaaaaaaaaaa", title: "A", artist: "Artist", album: "", durationMs: 1000, artworkUrl: nil)
        let b = Track(id: "bbbbbbbbbbb", title: "B", artist: "Artist", album: "", durationMs: 1000, artworkUrl: nil)
        let c = Track(id: "ccccccccccc", title: "C", artist: "Artist", album: "", durationMs: 1000, artworkUrl: nil)

        var queue = PlaybackQueue()
        queue.replace(tracks: [a, b, c], startingAt: 3)
        assert(queue.current?.track.id == c.id)
        assert(queue.upcoming.isEmpty)

        queue.replace(tracks: [a, b, c], startingAt: 0)
        queue.playNext(b)
        queue.append(a)
        assert(queue.entries.map(\.track.title) == ["A", "B", "B", "C", "A"])
        assert(Set(queue.entries.map(\.id)).count == 5)
        assert(queue.entries[1].id != queue.entries[2].id)

        let duplicate = queue.entries[2]
        assert(queue.removeUpcoming(id: duplicate.id))
        assert(queue.entries.map(\.track.title) == ["A", "B", "C", "A"])
        assert(queue.entries[1].track.id == b.id)

        assert(queue.advanceAfterEnd()?.track.id == b.id)
        assert(queue.current?.track.id == b.id)
        assert(queue.previous()?.track.id == a.id)
        queue.select(id: queue.entries[2].id)
        assert(queue.advanceAfterEnd()?.track.id == a.id)
        assert(queue.advanceAfterEnd() == nil)

        queue.select(id: queue.entries[1].id)
        let tail = queue.entries[3]
        assert(queue.moveUpcoming(id: tail.id, offset: -1))
        assert(queue.entries[2].id == tail.id)
        queue.clearUpcoming()
        assert(queue.upcoming.isEmpty)
        assert(queue.current?.track.id == b.id)

        var gate = PlaybackGate()
        let first = gate.next()
        assert(gate.accepts(first))
        let second = gate.next()
        assert(!gate.accepts(first))
        assert(gate.accepts(second))

        print("Queue checks passed")
    }
}
