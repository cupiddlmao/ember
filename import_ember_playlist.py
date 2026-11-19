"""Import an approved Spotify CSV match report into Ember's shared library."""
import argparse
import copy
import csv
import fcntl
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent / "desktop-rust"))
import library_client as client
from mobile.backend.library_store import entries_metadata, text
from transfer_playlist import read_tracks, clean


def prepare(source, report, title):
    if source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("CSV exceeds 16 MiB.")
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    data = client.read_json(report)
    tracks = read_tracks(source)
    with source.open(encoding="utf-8-sig", newline="") as stream:
        albums = []
        for row in csv.DictReader(stream):
            fields = {clean(key): value for key, value in row.items() if key}
            albums.append(text(fields.get("album name") or fields.get("album") or "", 300, True))
    rows = data.get("matches")
    if data.get("source_sha256") != fingerprint or not isinstance(rows, list) or len(rows) != len(tracks):
        raise ValueError("Review report does not match the complete CSV.")
    entries = []
    for index, (track, row) in enumerate(zip(tracks, rows)):
        if any(row.get(key) != track[key] for key in ("title", "artist", "duration_ms")):
            raise ValueError("Review report row does not match its CSV row.")
        if row.get("approved") is not True:
            continue
        artists = row.get("matched_artists")
        if not isinstance(artists, list) or not artists or not all(isinstance(a, str) for a in artists):
            raise ValueError("Approved match is missing artist metadata.")
        video = row.get("video_id")
        entries.append({"index": index, "track": {
            "id": video, "source": "youtube", "title": row.get("matched_title"),
            "artist": ", ".join(artists), "album": albums[index],
            "durationMs": int(row.get("duration_ms") or 0),
            "artworkUrl": f"https://i.ytimg.com/vi/{video}/hqdefault.jpg",
        }})
    if not entries:
        raise ValueError("No approved matches to import.")
    return fingerprint, text(title, 120), entries, len(rows) - len(entries)


def run(source, report, title, apply=False):
    fingerprint, title, selected, skipped = prepare(source, report, title)
    server, key = client.connection()
    # Stable IDs also prevent duplicate creation if the local receipt is lost.
    pid = str(uuid.uuid5(uuid.NAMESPACE_URL, server + "/csv/" + fingerprint))
    entries = entries_metadata([{"id": str(uuid.uuid5(uuid.UUID(pid), str(row["index"]))),
                                "track": row["track"]} for row in selected])
    body = {"id": pid, "name": title, "entries": entries}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    body["requestId"] = str(uuid.uuid5(uuid.UUID(pid), digest))
    print(f"{title}: {len(entries)} approved occurrences, {len({e['track']['id'] for e in entries})} unique recordings; {skipped} omitted.")
    if not apply:
        print("Preview only. Add --apply to save to the configured Ember server.")
        return None
    path = client.root() / "imports" / (pid + ".json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            state = client.read_json(path)
            legacy = copy.deepcopy(body)
            legacy.pop("requestId")
            for entry in legacy["entries"]:
                entry["track"]["album"] = ""
            digest = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
            legacy["requestId"] = str(uuid.uuid5(uuid.UUID(pid), digest))
            if state.get("server") != server or state.get("body") not in (body, legacy):
                raise ValueError("Import inputs changed. Keep the original report/title for safe retries.")
            # Old imports must replay their exact saved payload, not enriched metadata.
            body = state["body"]
            entries = body["entries"]
        else:
            summaries = client.request(server, key, "GET")["playlists"]
            if any(p["name"] == title for p in summaries):
                raise ValueError("A playlist with this name already exists. Choose a different title; nothing was overwritten.")
            state = {"server": server, "body": body, "complete": False}
            client.private_save(path, state)
        if not state["complete"]:
            receipt = client.request(server, key, "POST", body=state["body"])
            if receipt != {"id": pid, "revision": 1, "deleted": False}:
                raise ValueError("Import receipt could not be verified. Retry the same command.")
        detail = client.request(server, key, "GET", "/" + pid)
        if not state["complete"]:
            if detail["name"] != title or detail["entries"] != entries:
                raise ValueError("Playlist changed before verification. Import record retained; nothing was overwritten.")
            state["complete"] = True
            client.private_save(path, state)
        print(f"Verified saved playlist: {detail['name']} ({detail['trackCount']} songs). Refresh Ember Library on both devices.")
        return detail


def add_new(source, report, title, apply=False):
    """Append occurrences the playlist is missing; leave every existing entry alone."""
    fingerprint, title, selected, _ = prepare(source, report, title)
    server, key = client.connection()
    matches = [p for p in client.request(server, key, "GET")["playlists"] if p["name"] == title]
    if not matches:
        raise ValueError("No playlist with that title exists; nothing was added.")
    if len(matches) > 1:
        raise ValueError("More than one playlist has that title; nothing was added.")
    pid = matches[0]["id"]
    detail = client.request(server, key, "GET", "/" + pid)
    existing = copy.deepcopy(detail["entries"])
    remaining = Counter(entry["track"]["id"] for entry in existing)
    present = {entry["id"] for entry in existing}
    additions = []
    for row in selected:
        track_id = row["track"]["id"]
        if remaining[track_id] > 0:
            remaining[track_id] -= 1
            continue
        entry = {"id": str(uuid.uuid5(uuid.UUID(pid), f"add:{fingerprint}:{row['index']}")),
                 "track": row["track"]}
        if entry["id"] not in present:
            additions.append(entry)
            present.add(entry["id"])
    print(f"{detail['name']}: {len(additions)} new occurrences; {len(existing)} existing entries kept.")
    if not additions:
        print("Nothing new to add; the playlist was not changed.")
        return detail
    if not apply:
        print("Preview only. Add --apply to append these songs.")
        return None
    path = client.root() / "imports" / (pid + "-additions.json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            state = client.read_json(path)
            if (state.get("server") != server or state.get("fingerprint") != fingerprint
                    or state.get("playlist") != pid):
                raise ValueError("Add-new inputs changed. Retry the original report and title.")
            if state["complete"]:
                print("These songs were already added; nothing was changed.")
                return client.request(server, key, "GET", "/" + pid)
        else:
            body = {
                "requestId": str(uuid.uuid5(uuid.UUID(pid), f"add:{fingerprint}:{detail['revision']}")),
                "expectedRevision": detail["revision"], "name": detail["name"],
                "entries": entries_metadata(existing + additions),
            }
            state = {"server": server, "fingerprint": fingerprint, "playlist": pid,
                     "complete": False, "body": body}
            client.private_save(path, state)
        receipt = client.request(server, key, "PUT", "/" + pid, state["body"])
        expected = state["body"]["expectedRevision"] + 1
        if receipt != {"id": pid, "revision": expected, "deleted": False}:
            raise ValueError("Add-new receipt could not be verified. Retry the identical command.")
        detail = client.request(server, key, "GET", "/" + pid)
        if detail["revision"] != expected or detail["entries"] != state["body"]["entries"]:
            raise ValueError("Playlist changed during verification; add-new receipt retained.")
        state["complete"] = True
        client.private_save(path, state)
        print(f"Verified {len(additions)} new songs at revision {expected}. Refresh Ember Library.")
        return detail


def album_repair(detail, selected, playlist_id):
    entries = copy.deepcopy(detail["entries"])
    originals = {str(uuid.uuid5(uuid.UUID(playlist_id), str(row["index"]))): row["track"] for row in selected}
    changed = 0
    for entry in entries:
        original = originals.get(entry["id"])
        if (original and entry["track"]["id"] == original["id"]
                and not entry["track"].get("album") and original["album"]):
            entry["track"]["album"] = original["album"]
            changed += 1
    return entries_metadata(entries), changed


def repair_albums(source, report, title, apply=False):
    fingerprint, _, selected, _ = prepare(source, report, title)
    server, key = client.connection()
    pid = str(uuid.uuid5(uuid.NAMESPACE_URL, server + "/csv/" + fingerprint))
    path = client.root() / "imports" / (pid + "-albums.json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (client.root() / "library.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            state = client.read_json(path)
            if state["server"] != server or state["fingerprint"] != fingerprint:
                raise ValueError("Album repair inputs changed.")
            if state["complete"]:
                print("Album repair already completed; later edits were left untouched.")
                return client.request(server, key, "GET", "/" + pid)
        else:
            before = client.request(server, key, "GET", "/" + pid)
            entries, changed = album_repair(before, selected, pid)
            print(f"{before['name']}: fill {changed} missing album names; preserve all {len(entries)} occurrences.")
            if not apply or not changed:
                return before
            state = {"server": server, "fingerprint": fingerprint, "complete": False,
                     "before": before, "body": {"requestId": str(uuid.uuid4()),
                        "expectedRevision": before["revision"], "name": before["name"], "entries": entries}}
            client.private_save(path, state)
        if not apply:
            print("Pending album repair retained; add --apply to retry its exact payload.")
            return None
        receipt = client.request(server, key, "PUT", "/" + pid, state["body"])
        expected = state["body"]["expectedRevision"] + 1
        if receipt != {"id": pid, "revision": expected, "deleted": False}:
            raise ValueError("Album repair receipt could not be verified.")
        detail = client.request(server, key, "GET", "/" + pid)
        if detail["revision"] != expected or detail["entries"] != state["body"]["entries"]:
            raise ValueError("Playlist changed during verification; repair receipt retained.")
        state["complete"] = True
        client.private_save(path, state)
        print(f"Verified album repair at revision {expected}. Refresh Ember Library.")
        return detail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--title", default="GG")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repair-albums", action="store_true", help="Fill missing albums on the existing import; preserve all songs and ordering.")
    parser.add_argument("--add-new", action="store_true", help="Append only occurrences an existing playlist lacks; leave its entries alone.")
    args = parser.parse_args()
    try:
        if args.repair_albums and args.add_new:
            parser.exit(2, "Choose one of --repair-albums or --add-new.\n")
        action = repair_albums if args.repair_albums else add_new if args.add_new else run
        action(args.csv, args.report, args.title, args.apply)
    except client.Rejected as error:
        parser.exit(1, f"Ember rejected the request (HTTP {error.status}). Import state retained; no other playlist was overwritten.\n")
    except ValueError:
        parser.exit(1, "Import validation failed. Check CSV/report, title and saved import state; no other playlist was overwritten.\n")
    except Exception:
        parser.exit(1, "Import not confirmed. Check Ember Settings/Tailscale and rerun the identical command. Private retry state is retained.\n")


if __name__ == "__main__":
    main()
