"""Local, non-mutating playlist text filtering."""


def _album_name(track):
    album = (track or {}).get("album")
    if isinstance(album, dict):
        return str(album.get("name") or "")
    return str(album or "")


def search_text(track):
    track = track or {}
    artists = []
    for artist in track.get("artists") or []:
        name = artist.get("name") if isinstance(artist, dict) else artist
        if name:
            artists.append(str(name))
    return " ".join([str(track.get("name") or ""), " ".join(artists), _album_name(track)]).casefold()


def filter_tracks(tracks, query):
    """Return ``(original_index, track)`` pairs; an empty query returns all."""
    values = list(enumerate(tracks or []))
    needle = " ".join(str(query or "").split()).casefold()
    if not needle:
        return values
    return [(index, track) for index, track in values if needle in search_text(track)]
