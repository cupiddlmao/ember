"""Native GStreamer playback. No browser, ad scripts or remote player."""
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)


class Audio:
    def __init__(self, callback, sink=None):
        self.callback = callback
        self.play_requested = False
        self.player = Gst.ElementFactory.make("playbin", "ember-soundcloud")
        if self.player is None:
            raise RuntimeError("GStreamer playbin is missing. Install your distribution's playback plugins.")
        if sink is not None:
            self.player.set_property("audio-sink", sink)
        self.player.set_property("volume", .5)
        self.bus = self.player.get_bus()
        self.bus.add_signal_watch()
        self.handler = self.bus.connect("message", self.message)

    def load(self, url):
        self.player.set_state(Gst.State.NULL)
        self.player.set_property("uri", url)
        self.callback("loading")
        self.play_requested = True
        self.player.set_state(Gst.State.PLAYING)

    def message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            self.play_requested = False
            error, _detail = message.parse_error()
            self.player.set_state(Gst.State.NULL)
            self.callback("missing-plugin" if error.matches(Gst.CoreError.quark(), Gst.CoreError.MISSING_PLUGIN) else "error")
        elif message.type == Gst.MessageType.EOS:
            self.play_requested = False
            self.callback("ended")
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self.player:
            _old, state, _pending = message.parse_state_changed()
            if state == Gst.State.PLAYING:
                self.callback("playing")
            elif state == Gst.State.PAUSED:
                self.callback("paused")
        elif message.type == Gst.MessageType.BUFFERING:
            percent = message.parse_buffering()
            if self.play_requested:
                self.player.set_state(Gst.State.PAUSED if percent < 100 else Gst.State.PLAYING)
            self.callback("buffering" if percent < 100 else "buffered")

    def position(self):
        ok, value = self.player.query_position(Gst.Format.TIME)
        return value / Gst.SECOND if ok else None

    def duration(self):
        ok, value = self.player.query_duration(Gst.Format.TIME)
        return value / Gst.SECOND if ok and value > 0 else None

    def command(self, command, **data):
        if command == "play":
            self.play_requested = True
            self.player.set_state(Gst.State.PLAYING)
        elif command == "pause":
            self.play_requested = False
            self.player.set_state(Gst.State.PAUSED)
        elif command == "stop":
            self.play_requested = False
            self.player.set_state(Gst.State.NULL)
        elif command == "volume": self.player.set_property("volume", data["value"] / 65535)
        elif command == "seek":
            return self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, data["ms"] * Gst.MSECOND)
        return True

    def close(self):
        self.player.set_state(Gst.State.NULL)
        self.bus.disconnect(self.handler)
        self.bus.remove_signal_watch()
