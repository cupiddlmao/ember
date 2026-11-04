"""Normalize real timing data without inventing word timestamps."""
from dataclasses import dataclass, field
import math
import re
import xml.etree.ElementTree as ET
from services import request_json


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Line:
    text: str
    start: float
    end: float
    words: list[Word] = field(default_factory=list)
    opposite: bool = False
    background: bool = False


def seconds(value):
    value = str(value).strip()
    if value.endswith("ms"):
        result = float(value[:-2]) / 1000
    elif value.endswith("s"):
        result = float(value[:-1])
    else:
        result = 0.0
        for part in value.split(":"):
            result = result * 60 + float(part)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Invalid lyric timestamp")
    return result


def interval(start, end):
    start, end = seconds(start), seconds(end)
    if end < start:
        raise ValueError("Lyric ends before it starts")
    return start, end


def spicy_lines(body):
    lines = []
    if body["Type"] == "Static":
        return [Line(str(item["Text"]), -1, -1) for item in body["Lines"]]
    if body["Type"] not in ("Line", "Syllable"):
        raise ValueError("Unsupported lyrics format")
    for item in body["Content"]:
        if item.get("Type") != "Vocal":
            continue
        opposite = bool(item.get("OppositeAligned"))
        if body["Type"] == "Line":
            start, end = interval(item["StartTime"], item["EndTime"])
            lines.append(Line(str(item["Text"]), start, end, opposite=opposite))
            continue
        groups = [(item["Lead"], False)] + [(group, True) for group in item.get("Background", [])]
        for group, background in groups:
            words = []
            for syllable in group["Syllables"]:
                start, end = interval(syllable["StartTime"], syllable["EndTime"])
                text = str(syllable["Text"]) + ("" if syllable.get("IsPartOfWord") else " ")
                words.append(Word(text, start, end))
            if words:
                start, end = interval(group.get("StartTime", words[0].start), group.get("EndTime", words[-1].end))
                lines.append(Line("".join(word.text for word in words).rstrip(), start, end, words, opposite, background))
    if len(lines) > 5000:
        raise ValueError("Too many lyric lines")
    return sorted(lines, key=lambda line: line.start)


def fetch_lyrics(track_id, key):
    if not re.fullmatch(r"[A-Za-z0-9]{22}", track_id):
        raise ValueError("Invalid Spotify track ID")
    if not key:
        raise ValueError("Add your Spicy Lyrics key in Settings, or import a TTML/LRC file.")
    payload = request_json("https://api.spicylyrics.org/v1/lyrics/" + track_id, headers={"Authorization": "Bearer " + key})
    if payload.get("Status") != 200:
        raise ValueError("No usable lyrics returned by provider")
    body = payload["Body"]
    lines = spicy_lines(body)
    if not lines:
        raise ValueError("No lyrics available for this track")
    return lines, body


def metadata_lyrics(track):
    """Exact metadata lookup; never assign a Spotify ID from another provider ID."""
    import urllib.parse
    artist = track["artists"][0]["name"]
    params = urllib.parse.urlencode({"track_name": track["name"], "artist_name": artist, "duration": round(track["duration_ms"] / 1000)})
    payload = request_json("https://lrclib.net/api/get?" + params)
    if payload.get("syncedLyrics"):
        return parse_lrc(payload["syncedLyrics"]), {"source": "lrclib"}
    if payload.get("plainLyrics"):
        return [Line(row, -1, -1) for row in payload["plainLyrics"].splitlines()], {"source": "lrclib"}
    raise ValueError("No lyrics matched this recording. Import a TTML/LRC file for word timing.")


def parse_lrc(text):
    offset = re.search(r"\[offset:([+-]?\d+)\]", text, re.I)
    offset = int(offset[1]) / 1000 if offset else 0
    lines = []
    for row in text.splitlines():
        stamps = re.findall(r"\[(\d+:\d+(?:\.\d+)?)\]", row)
        content = re.sub(r"\[[^]]*\]", "", row).strip()
        for stamp in stamps:
            lines.append(Line(content, max(0, seconds(stamp) + offset), 0))
    lines.sort(key=lambda line: line.start)
    for index, line in enumerate(lines):
        line.end = lines[index + 1].start if index + 1 < len(lines) else line.start + 15
    return lines


def parse_ttml(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML entities and document types are not supported")
    root = ET.fromstring(text)
    lines = []
    for paragraph in root.iter():
        if paragraph.tag.split("}")[-1] != "p" or "begin" not in paragraph.attrib:
            continue
        start = seconds(paragraph.attrib["begin"])
        end = seconds(paragraph.attrib["end"]) if "end" in paragraph.attrib else start + seconds(paragraph.attrib.get("dur", "15s"))
        interval(start, end)
        words = []
        # shortcut: absolute TTML clock times only; add a timing resolver for relative/sequence documents.
        for span in paragraph.iter():
            if span is paragraph or "begin" not in span.attrib:
                continue
            if any("begin" in child.attrib for child in span):
                continue
            wstart = seconds(span.attrib["begin"])
            wend = seconds(span.attrib["end"]) if "end" in span.attrib else wstart + seconds(span.attrib.get("dur", "0s"))
            interval(wstart, wend)
            words.append(Word("".join(span.itertext()) + (span.tail or ""), wstart, wend))
        content = "".join(paragraph.itertext()).strip()
        lines.append(Line(content, start, end, words))
    return sorted(lines, key=lambda line: line.start)


def import_lyrics(path):
    with open(path, "rb") as source:
        raw = source.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Lyric file exceeds 2 MB")
    text = raw.decode("utf-8-sig")
    lines = parse_ttml(text) if text.lstrip().startswith("<") else parse_lrc(text)
    if not lines or len(lines) > 5000:
        raise ValueError("No supported timed lyrics found (maximum 5,000 lines)")
    return lines


def demo_lines():
    # Original words written for the silent interface preview.
    phrases = ["Let the evening settle in", "Leave the noise outside", "Every little moment", "Has a rhythm of its own", "Somewhere in the quiet", "We find our way back home", "Let the colors carry you", "Stay a little longer"]
    lines = []
    for index, phrase in enumerate(phrases):
        words = [Word(word + " ", index * 5 + n * 0.55, index * 5 + (n + 1) * 0.55) for n, word in enumerate(phrase.split())]
        lines.append(Line(phrase, index * 5, (index + 1) * 5, words))
    return lines
