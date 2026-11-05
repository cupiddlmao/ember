import AVFoundation
import Combine
import MediaPlayer
import UIKit

@MainActor
final class PlayerController: NSObject, ObservableObject {
    enum PlaybackState: Equatable {
        case idle
        case loading
        case playing
        case paused
        case finished
        case failed
    }

    @Published private(set) var state: PlaybackState = .idle
    @Published private(set) var track: Track?
    @Published private(set) var currentTime: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var errorMessage: String?

    private let player = AVPlayer()
    private var timeObserver: Any?
    private var endObserver: NSObjectProtocol?
    private var itemObservation: NSKeyValueObservation?
    private var playbackObservation: NSKeyValueObservation?
    private var sessionObservers: [NSObjectProtocol] = []
    private var resumeAfterInterruption = false
    private var playbackRequested = false
    private var artworkTask: Task<Void, Never>?
    private var artwork: MPMediaItemArtwork?

    override init() {
        super.init()
        configureAudioSession()
        configureRemoteCommands()
        startObserving()
    }

    func play(track: Track, fileURL: URL) {
        resumeAfterInterruption = false
        self.track = track
        errorMessage = nil
        state = .loading
        currentTime = 0
        duration = track.duration
        artwork = nil
        artworkTask?.cancel()
        guard activateAudioSession() else { return }
        playbackRequested = true

        let item = AVPlayerItem(url: fileURL)
        itemObservation = item.observe(\.status, options: [.new]) { [weak self] observed, _ in
            guard observed.status == .failed else { return }
            Task { @MainActor [weak self] in
                guard let self, self.player.currentItem?.status == .failed else { return }
                self.errorMessage = "This audio could not be played."
                self.playbackRequested = false
                self.state = .failed
                self.updateNowPlayingElapsed()
            }
        }
        player.replaceCurrentItem(with: item)
        player.play()
        updateNowPlayingMetadata()
        loadArtwork(for: track)
    }

    func togglePlayPause() {
        if state == .playing || state == .loading {
            pause()
        } else {
            resume()
        }
    }

    func pause() {
        playbackRequested = false
        resumeAfterInterruption = false
        guard track != nil else { return }
        player.pause()
        state = .paused
        updateNowPlayingElapsed()
    }

    func resume() {
        guard player.currentItem != nil, state != .failed else { return }
        if state == .finished {
            seek(to: 0)
        }
        guard activateAudioSession() else { return }
        playbackRequested = true
        state = player.timeControlStatus == .playing ? .playing : .loading
        player.play()
        updateNowPlayingElapsed()
    }

    func seek(to seconds: Double) {
        guard player.currentItem != nil else { return }
        let upperBound = duration > 0 ? duration : max(0, seconds)
        let bounded = max(0, min(upperBound, seconds))
        currentTime = bounded
        player.seek(
            to: CMTime(seconds: bounded, preferredTimescale: 600),
            toleranceBefore: .zero,
            toleranceAfter: .zero
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if self.state == .finished, bounded < self.duration {
                    self.state = .paused
                }
                self.updateNowPlayingElapsed()
            }
        }
    }

    func stop() {
        playbackRequested = false
        resumeAfterInterruption = false
        player.pause()
        player.replaceCurrentItem(with: nil)
        itemObservation = nil
        artworkTask?.cancel()
        artwork = nil
        track = nil
        state = .idle
        currentTime = 0
        duration = 0
        errorMessage = nil
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    private func startObserving() {
        playbackObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self, self.track != nil,
                      self.state != .failed, self.state != .finished else { return }
                switch self.player.timeControlStatus {
                case .playing: self.state = .playing
                case .waitingToPlayAtSpecifiedRate: self.state = .loading
                case .paused: self.state = .paused
                @unknown default: break
                }
                self.updateNowPlayingElapsed()
            }
        }
        sessionObservers.append(NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
        ) { [weak self] notification in
            let type = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            let options = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            Task { @MainActor [weak self] in
                self?.handleInterruption(type: type, options: options)
            }
        })
        sessionObservers.append(NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
        ) { [weak self] notification in
            let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
            Task { @MainActor [weak self] in
                if reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue {
                    self?.pause()
                }
            }
        })
        timeObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.5, preferredTimescale: 600),
            queue: .main
        ) { [weak self] time in
            Task { @MainActor [weak self] in
                self?.tick(time)
            }
        }
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor [weak self] in
                guard let self,
                      let item = notification.object as? AVPlayerItem,
                      item === self.player.currentItem else { return }
                self.handleEnd()
            }
        }
    }

    private func handleInterruption(type: UInt?, options: UInt) {
        if type == AVAudioSession.InterruptionType.began.rawValue {
            let wasPlaying = playbackRequested
            pause()
            resumeAfterInterruption = wasPlaying
        } else if type == AVAudioSession.InterruptionType.ended.rawValue {
            let shouldResume = resumeAfterInterruption &&
                AVAudioSession.InterruptionOptions(rawValue: options).contains(.shouldResume)
            resumeAfterInterruption = false
            if shouldResume { resume() }
        }
    }

    private func tick(_ time: CMTime) {
        guard track != nil else { return }
        if time.seconds.isFinite {
            currentTime = max(0, time.seconds)
        }
        if let itemDuration = player.currentItem?.duration.seconds,
           itemDuration.isFinite,
           itemDuration > 0 {
            duration = itemDuration
        }
        updateNowPlayingElapsed()
    }

    private func handleEnd() {
        playbackRequested = false
        state = .finished
        currentTime = duration
        player.pause()
        updateNowPlayingElapsed()
    }

    private func configureAudioSession() {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default, options: [])
        } catch {
            errorMessage = "Could not configure background audio."
        }
    }

    @discardableResult
    private func activateAudioSession() -> Bool {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default, options: [])
            try AVAudioSession.sharedInstance().setActive(true)
            return true
        } catch {
            errorMessage = "Could not activate audio playback."
            state = .failed
            return false
        }
    }

    private func updateNowPlayingMetadata() {
        guard let track else {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        var information: [String: Any] = [
            MPMediaItemPropertyTitle: track.title,
            MPMediaItemPropertyArtist: track.artist,
            MPMediaItemPropertyAlbumTitle: track.album,
            MPMediaItemPropertyPlaybackDuration: max(duration, track.duration),
            MPNowPlayingInfoPropertyElapsedPlaybackTime: currentTime,
            MPNowPlayingInfoPropertyPlaybackRate: state == .playing ? 1.0 : 0.0
        ]
        if let artwork {
            information[MPMediaItemPropertyArtwork] = artwork
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = information
        UIApplication.shared.beginReceivingRemoteControlEvents()
    }

    private func updateNowPlayingElapsed() {
        guard track != nil else { return }
        var information = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
        information[MPNowPlayingInfoPropertyElapsedPlaybackTime] = currentTime
        information[MPNowPlayingInfoPropertyPlaybackRate] = state == .playing ? 1.0 : 0.0
        information[MPMediaItemPropertyPlaybackDuration] = max(duration, 0)
        MPNowPlayingInfoCenter.default().nowPlayingInfo = information
    }

    private func loadArtwork(for track: Track) {
        guard let url = track.artworkUrl else { return }
        artworkTask = Task { [weak self] in
            guard let result = try? await URLSession.shared.data(from: url) else { return }
            guard let image = UIImage(data: result.0), image.size.width > 0 else { return }
            guard !Task.isCancelled, let self, self.track?.id == track.id else { return }
            let artwork = MPMediaItemArtwork(boundsSize: image.size) { _ in image }
            self.artwork = artwork
            if var information = MPNowPlayingInfoCenter.default().nowPlayingInfo {
                information[MPMediaItemPropertyArtwork] = artwork
                MPNowPlayingInfoCenter.default().nowPlayingInfo = information
            }
        }
    }

    private func configureRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()
        center.playCommand.isEnabled = true
        center.pauseCommand.isEnabled = true
        center.togglePlayPauseCommand.isEnabled = true
        center.changePlaybackPositionCommand.isEnabled = true

        center.playCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.resume()
            }
            return .success
        }
        center.pauseCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.pause()
            }
            return .success
        }
        center.togglePlayPauseCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.togglePlayPause()
            }
            return .success
        }
        center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else {
                return .commandFailed
            }
            Task { @MainActor [weak self] in
                self?.seek(to: event.positionTime)
            }
            return .success
        }
    }
}
