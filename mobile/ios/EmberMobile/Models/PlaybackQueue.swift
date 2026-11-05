import Foundation

struct QueueEntry: Identifiable, Equatable {
    let id: UUID
    let track: Track

    init(track: Track, id: UUID = UUID()) {
        self.id = id
        self.track = track
    }
}

struct PlaybackQueue {
    private(set) var entries: [QueueEntry] = []
    private(set) var currentID: UUID?

    var currentIndex: Int? {
        guard let currentID else { return nil }
        return entries.firstIndex { $0.id == currentID }
    }

    var current: QueueEntry? {
        guard let currentIndex else { return nil }
        return entries[currentIndex]
    }

    var upcoming: [QueueEntry] {
        guard let currentIndex else { return [] }
        return Array(entries.dropFirst(currentIndex + 1))
    }

    @discardableResult
    mutating func start(track: Track) -> QueueEntry {
        let entry = QueueEntry(track: track)
        entries = [entry]
        currentID = entry.id
        return entry
    }

    @discardableResult
    mutating func replace(tracks: [Track], startingAt index: Int) -> QueueEntry? {
        guard !tracks.isEmpty else {
            entries = []
            currentID = nil
            return nil
        }
        entries = tracks.map { QueueEntry(track: $0) }
        let safeIndex = min(max(0, index), entries.count - 1)
        currentID = entries[safeIndex].id
        return entries[safeIndex]
    }

    @discardableResult
    mutating func playNext(_ track: Track) -> QueueEntry {
        guard let currentIndex else {
            return start(track: track)
        }
        let entry = QueueEntry(track: track)
        entries.insert(entry, at: currentIndex + 1)
        return entry
    }

    @discardableResult
    mutating func append(_ track: Track) -> QueueEntry {
        let entry = QueueEntry(track: track)
        entries.append(entry)
        if currentID == nil {
            currentID = entry.id
        }
        return entry
    }

    @discardableResult
    mutating func select(id: UUID) -> QueueEntry? {
        guard let entry = entries.first(where: { $0.id == id }) else {
            return nil
        }
        currentID = id
        return entry
    }

    @discardableResult
    mutating func advanceAfterEnd() -> QueueEntry? {
        guard let currentIndex, currentIndex + 1 < entries.count else {
            return nil
        }
        currentID = entries[currentIndex + 1].id
        return entries[currentIndex + 1]
    }

    @discardableResult
    mutating func previous() -> QueueEntry? {
        guard let currentIndex, currentIndex > 0 else {
            return nil
        }
        currentID = entries[currentIndex - 1].id
        return entries[currentIndex - 1]
    }

    mutating func removeUpcoming(id: UUID) -> Bool {
        let lowerBound = currentIndex.map { $0 + 1 } ?? 0
        guard let index = entries.firstIndex(where: { $0.id == id }), index >= lowerBound else {
            return false
        }
        entries.remove(at: index)
        return true
    }

    mutating func moveUpcoming(id: UUID, offset: Int) -> Bool {
        guard offset != 0,
              let lowerBound = currentIndex.map({ $0 + 1 }),
              let source = entries.firstIndex(where: { $0.id == id }),
              source >= lowerBound else {
            return false
        }
        let target = source + offset
        guard target >= lowerBound, target < entries.count else {
            return false
        }
        let entry = entries.remove(at: source)
        entries.insert(entry, at: target)
        return true
    }

    mutating func clearUpcoming() {
        guard let currentIndex else {
            entries = []
            currentID = nil
            return
        }
        entries = Array(entries.prefix(currentIndex + 1))
    }

    mutating func removeAll() {
        entries = []
        currentID = nil
    }
}

/// Invalidates a download when the user selects another track or stops.
struct PlaybackGate {
    private(set) var value: UInt = 0

    mutating func next() -> UInt {
        value &+= 1
        return value
    }

    func accepts(_ token: UInt) -> Bool {
        token == value
    }
}
