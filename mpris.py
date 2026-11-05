"""Expose this player's controls to the Linux desktop and media keys."""
from gi.repository import Gio, GLib

XML = """<node>
<interface name="org.mpris.MediaPlayer2">
 <method name="Raise"/><method name="Quit"/>
 <property name="CanQuit" type="b" access="read"/>
 <property name="CanRaise" type="b" access="read"/>
 <property name="HasTrackList" type="b" access="read"/>
 <property name="Identity" type="s" access="read"/>
 <property name="SupportedUriSchemes" type="as" access="read"/>
 <property name="SupportedMimeTypes" type="as" access="read"/>
</interface>
<interface name="org.mpris.MediaPlayer2.Player">
 <method name="Next"/><method name="Previous"/><method name="Pause"/>
 <method name="PlayPause"/><method name="Stop"/><method name="Play"/>
 <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
 <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/><arg name="Position" type="x" direction="in"/></method>
 <signal name="Seeked"><arg name="Position" type="x"/></signal>
 <property name="PlaybackStatus" type="s" access="read"/>
 <property name="Rate" type="d" access="read"/>
 <property name="MinimumRate" type="d" access="read"/>
 <property name="MaximumRate" type="d" access="read"/>
 <property name="Metadata" type="a{sv}" access="read"/>
 <property name="Position" type="x" access="read"/>
 <property name="CanGoNext" type="b" access="read"/>
 <property name="CanGoPrevious" type="b" access="read"/>
 <property name="CanPlay" type="b" access="read"/>
 <property name="CanPause" type="b" access="read"/>
 <property name="CanSeek" type="b" access="read"/>
 <property name="CanControl" type="b" access="read"/>
</interface></node>"""
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"


def track_component(track_id):
    """Encode arbitrary provider IDs into a valid D-Bus object-path segment."""
    value = str(track_id or "preview").encode("utf-8").hex()
    return value or "70726576696577"


def track_path_for(track_id):
    return PATH + "/track/" + track_component(track_id)


def has_media(app):
    return bool(getattr(app, "track", None) or getattr(app, "demo", False))


def can_play(app):
    return has_media(app) and not getattr(app, "pending_track", False)


def can_seek(app):
    return can_play(app) and float(getattr(app, "duration", 0) or 0) > 0


def can_go_next(app):
    queue = getattr(app, "queue", []) or []
    if not queue or getattr(app, "demo", False):
        return False
    if getattr(app, "repeat", False):
        return True
    if getattr(app, "shuffle", False) and len(queue) > 1:
        return True
    index = getattr(app, "queue_index", -1)
    return 0 <= index < len(queue) - 1


def can_go_previous(app):
    queue = getattr(app, "queue", []) or []
    if not queue or getattr(app, "demo", False):
        return False
    return bool(getattr(app, "repeat", False)) or getattr(app, "queue_index", -1) > 0


def playback_status(app):
    if not has_media(app) or getattr(app, "pending_track", False):
        return "Stopped"
    return "Playing" if getattr(app, "playing", False) else "Paused"


class Mpris:
    def __init__(self, app):
        self.app = app
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.registrations = [self.bus.register_object(PATH, interface, self.call, self.get, None) for interface in Gio.DBusNodeInfo.new_for_xml(XML).interfaces]
        self.owner = Gio.bus_own_name_on_connection(self.bus, "org.mpris.MediaPlayer2.ember", Gio.BusNameOwnerFlags.NONE, None, None)

    def track_path(self):
        return track_path_for((self.app.track or {}).get("id") or "preview")

    def get(self, _bus, _sender, _path, _interface, name):
        app = self.app
        if name == "Metadata":
            track = app.track or {}
            if track:
                title = track.get("name", "")
                artists = [artist["name"] for artist in track.get("artists", [])]
            else:
                title = app.player_title.get_text() if hasattr(app, "player_title") else "Nothing playing"
                artists = [app.player_artist.get_text()] if hasattr(app, "player_artist") else []
            return GLib.Variant("a{sv}", {"mpris:trackid": GLib.Variant("o", self.track_path()), "mpris:length": GLib.Variant("x", int(app.duration * 1_000_000)), "xesam:title": GLib.Variant("s", title), "xesam:artist": GLib.Variant("as", artists)})
        if name == "PlaybackStatus":
            return GLib.Variant("s", playback_status(app))
        if name == "Position":
            return GLib.Variant("x", int((app.current_position() if has_media(app) else 0) * 1_000_000))
        if name == "Identity":
            return GLib.Variant("s", "Ember")
        if name in ("Rate", "MinimumRate", "MaximumRate"):
            return GLib.Variant("d", 1.0)
        if name in ("SupportedUriSchemes", "SupportedMimeTypes"):
            return GLib.Variant("as", [])
        if name in ("CanRaise", "CanQuit"):
            return GLib.Variant("b", True)
        if name == "HasTrackList":
            return GLib.Variant("b", False)
        if name == "CanGoNext":
            return GLib.Variant("b", can_go_next(app))
        if name == "CanGoPrevious":
            return GLib.Variant("b", can_go_previous(app))
        if name == "CanPlay":
            return GLib.Variant("b", can_play(app))
        if name == "CanPause":
            return GLib.Variant("b", can_play(app))
        if name == "CanSeek":
            return GLib.Variant("b", can_seek(app))
        if name == "CanControl":
            return GLib.Variant("b", True)
        return GLib.Variant("b", False)

    def call(self, _bus, _sender, _path, _interface, method, params, invocation):
        app = self.app
        args = params.unpack()
        if method == "Raise":
            app.window.present()
        elif method == "Quit":
            app.window.close()
        elif method == "Next":
            app.skip(1)
        elif method == "Previous":
            app.skip(-1)
        elif method == "PlayPause" or method == "Play" and not app.playing or method == "Pause" and app.playing:
            app.toggle_play()
        elif method == "Stop":
            if app.playing:
                app.toggle_play()
            app.set_position(0)
        elif method == "Seek":
            app.set_position(app.current_position() + args[0] / 1_000_000)
        elif method == "SetPosition" and args[0] == self.track_path():
            app.set_position(args[1] / 1_000_000)
        invocation.return_value(None)

    def changed(self):
        values = {name: self.get(None, None, None, PLAYER, name) for name in ("PlaybackStatus", "Metadata", "CanGoNext", "CanGoPrevious", "CanPlay", "CanPause", "CanSeek")}
        self.bus.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged", GLib.Variant("(sa{sv}as)", (PLAYER, values, [])))

    def seeked(self):
        self.bus.emit_signal(None, PATH, PLAYER, "Seeked", GLib.Variant("(x)", (int(self.app.position * 1_000_000),)))

    def close(self):
        Gio.bus_unown_name(self.owner)
        for registration in self.registrations:
            self.bus.unregister_object(registration)
