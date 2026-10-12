"""Input sequences ("macros") and importers for the replay files GD bots write.

A level file says what the level IS; a macro says what was PRESSED.  .gdr / .gdr2
and friends are macros, so they are loaded with --replay alongside a level.

Supported (the format is detected from the content, not the extension):
  .gdr2            GDReplayFormat 2 (maxnut).  Implemented from the reference reader
                   include/gdr/gdr.hpp + binarystream.hpp on the gdr2 branch.
  .gdr / .gdr.json GDReplayFormat 1, MessagePack or JSON.  Field names ("inputs",
                   "frame", "btn", "2p", "down", "framerate") are from memory of the
                   v1 header - UNVERIFIED, so the reader is deliberately tolerant.
  .json            this project's own format; plus best-effort readers for
                   Mega Hack (.mhr.json), TASBot and EchoBot JSON - UNVERIFIED
                   against a spec, they fail loudly if the structure is not as expected.
  .txt             "fps" on the first line, then "frame down [player2]" per line.
Not supported: .re/.re2/.re3, .xd, .slc, .zbf, .replay, .mhr (binary), .ttr.

Only player 1's jump button matters to this engine (classic mode, no dual), so
player-2 and left/right events are counted and dropped.
"""
import json
import struct

from .constants import TPS


class MacroFormatError(Exception):
    pass


class InputSequence:
    """Button edges on a 240 Hz timeline: events = [(tick, down), ...].

    An event with tick t is applied at the start of step t (the first step is 0).
    """

    def __init__(self, events=(), meta=None):
        cleaned, state = [], False
        for tick, down in sorted((int(t), bool(d)) for t, d in events):
            if down != state:
                cleaned.append((tick, down))
                state = down
        self.events = cleaned
        self.meta = dict(meta or {})

    def __len__(self):
        return len(self.events)

    def pressed_at(self, tick):
        state = False
        for t, down in self.events:
            if t > tick:
                break
            state = down
        return state

    def iter_states(self):
        """Yields the button state for tick 0, 1, 2, ... forever."""
        i, n, ev, state, tick = 0, len(self.events), self.events, False, 0
        while True:
            while i < n and ev[i][0] <= tick:
                state = ev[i][1]
                i += 1
            yield state
            tick += 1

    @property
    def last_tick(self):
        return self.events[-1][0] if self.events else 0

    def save(self, path):
        doc = {"format": "gd-remake-inputs", "tps": TPS, "meta": self.meta,
               "events": [[t, int(d)] for t, d in self.events]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    @classmethod
    def from_frames(cls, frames, fps, offset=0, meta=None):
        """frames: iterable of (frame, down) recorded at `fps` -> 240 Hz ticks."""
        scale = TPS / float(fps or TPS)
        return cls(((int(round(f * scale)) + offset, d) for f, d in frames), meta)


# ------------------------------------------------------------------ GDR2 (binary)
class _Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def left(self):
        return len(self.d) - self.p

    def varint(self, type_size):
        """LEB128, at most sizeof(T)+1 bytes - same limit as the C++ reader."""
        result = 0
        for i in range(type_size + 1):
            if self.p + i >= len(self.d):
                raise MacroFormatError("unexpected end of file inside a number")
            b = self.d[self.p + i]
            result |= (b & 0x7F) << (7 * i)
            if not b & 0x80:
                self.p += i + 1
                return result
        self.p += type_size + 1
        return result

    def string(self):
        n = self.varint(8)
        if n > 0xFFFF or n > self.left():
            raise MacroFormatError("bad string length %d" % n)
        s = self.d[self.p:self.p + n]
        self.p += n
        return s.decode("utf-8", "replace")

    def fixed(self, fmt, size):
        if self.left() < size:
            raise MacroFormatError("unexpected end of file")
        v = struct.unpack_from(fmt, self.d, self.p)[0]      # floats are stored big-endian
        self.p += size
        return v

    def skip(self, n):
        if n > self.left():
            raise MacroFormatError("extension block runs past the end of the file")
        self.p += n


def _parse_gdr2(data):
    r = _Reader(data)
    r.p = 3                                   # "GDR"
    meta = {"format": "gdr2", "version": r.varint(4)}
    input_tag = r.string()
    meta["author"] = r.string()
    meta["description"] = r.string()
    meta["duration"] = r.fixed(">f", 4)
    meta["gameVersion"] = r.varint(4)
    fps = r.fixed(">d", 8)
    meta["framerate"] = fps
    meta["seed"] = r.varint(4)
    meta["coins"] = r.varint(4)
    meta["ldm"] = bool(r.varint(1))
    platformer = bool(r.varint(1))
    meta["platformer"] = platformer
    meta["bot"] = r.string()
    meta["botVersion"] = r.varint(4)
    meta["levelID"] = r.varint(4)
    meta["levelName"] = r.string()
    r.skip(r.varint(8))                       # replay extension block
    deaths, acc = [], 0
    for _ in range(r.varint(8)):
        acc += r.varint(8)
        deaths.append(acc)
    meta["deaths"] = deaths
    r.varint(8)                               # total input count (only a reserve() hint)
    p1_left = r.varint(8)
    frames, dropped, prev = [], 0, 0
    while r.left() > 0:
        packed = r.varint(8)
        if platformer:
            frame, button, down = (packed >> 3) + prev, (packed >> 1) & 3, bool(packed & 1)
        else:
            frame, button, down = (packed >> 1) + prev, 1, bool(packed & 1)
        player2 = p1_left == 0
        if input_tag:
            r.skip(r.varint(8))               # per-input extension
        prev = frame
        if p1_left > 0:
            p1_left -= 1
            if p1_left == 0:
                prev = 0                      # player-2 deltas restart from frame 0
        if player2 or button != 1:
            dropped += 1
        else:
            frames.append((frame, down))
    meta["dropped_events"] = dropped
    return frames, fps, meta


# ------------------------------------------------------------------ MessagePack (for GDR1)
def _msgpack(data):
    pos = 0

    def take(n):
        nonlocal pos
        if pos + n > len(data):
            raise MacroFormatError("truncated MessagePack data")
        chunk = data[pos:pos + n]
        pos += n
        return chunk

    def num(fmt, n):
        return struct.unpack(fmt, take(n))[0]

    def item():
        b = take(1)[0]
        if b <= 0x7F:
            return b
        if b >= 0xE0:
            return b - 0x100
        if 0x80 <= b <= 0x8F:
            return {item(): item() for _ in range(b & 0x0F)}
        if 0x90 <= b <= 0x9F:
            return [item() for _ in range(b & 0x0F)]
        if 0xA0 <= b <= 0xBF:
            return take(b & 0x1F).decode("utf-8", "replace")
        if b == 0xC0:
            return None
        if b == 0xC2:
            return False
        if b == 0xC3:
            return True
        if b in (0xC4, 0xC5, 0xC6):
            return bytes(take(num({0xC4: ">B", 0xC5: ">H", 0xC6: ">I"}[b], 1 << (b - 0xC4))))
        if b in (0xC7, 0xC8, 0xC9):
            n = num({0xC7: ">B", 0xC8: ">H", 0xC9: ">I"}[b], 1 << (b - 0xC7))
            take(1)
            return bytes(take(n))
        if b == 0xCA:
            return num(">f", 4)
        if b == 0xCB:
            return num(">d", 8)
        if 0xCC <= b <= 0xCF:
            return num((">B", ">H", ">I", ">Q")[b - 0xCC], 1 << (b - 0xCC))
        if 0xD0 <= b <= 0xD3:
            return num((">b", ">h", ">i", ">q")[b - 0xD0], 1 << (b - 0xD0))
        if 0xD4 <= b <= 0xD8:
            take(1)
            return bytes(take(1 << (b - 0xD4)))
        if b in (0xD9, 0xDA, 0xDB):
            return take(num({0xD9: ">B", 0xDA: ">H", 0xDB: ">I"}[b], 1 << (b - 0xD9))).decode("utf-8", "replace")
        if b in (0xDC, 0xDD):
            return [item() for _ in range(num(">H" if b == 0xDC else ">I", 2 if b == 0xDC else 4))]
        if b in (0xDE, 0xDF):
            return {item(): item() for _ in range(num(">H" if b == 0xDE else ">I", 2 if b == 0xDE else 4))}
        raise MacroFormatError("unsupported MessagePack type 0x%02x" % b)

    return item()


# ------------------------------------------------------------------ dictionary-shaped formats
def _first(d, *names, default=None):
    for n in names:
        if isinstance(d, dict) and n in d:
            return d[n]
    return default


def _from_document(doc):
    """-> (frames, fps, meta) for the JSON / MessagePack shaped formats."""
    if not isinstance(doc, dict):
        raise MacroFormatError("macro document is not an object")

    if doc.get("format") == "gd-remake-inputs":
        return [(t, bool(d)) for t, d in doc["events"]], doc.get("tps", TPS), dict(doc.get("meta") or {}, format="gd-remake")

    frames, dropped = [], 0
    if isinstance(doc.get("inputs"), list):                       # GDR 1
        fps = _first(doc, "framerate", "fps", default=240.0)
        for e in doc["inputs"]:
            if not isinstance(e, dict) or "frame" not in e:
                raise MacroFormatError("GDR input without a 'frame' field")
            if _first(e, "2p", "player2", default=False) or int(_first(e, "btn", "button", default=1)) != 1:
                dropped += 1
                continue
            frames.append((int(e["frame"]), bool(_first(e, "down", "holding", default=False))))
        bot = doc.get("bot") if isinstance(doc.get("bot"), dict) else {}
        meta = {"format": "gdr1", "author": doc.get("author"), "bot": bot.get("name"),
                "gameVersion": doc.get("gameVersion"), "framerate": fps}
    elif isinstance(doc.get("events"), list) and isinstance(doc.get("meta"), dict):   # Mega Hack json
        fps = doc["meta"].get("fps", 240.0)
        for e in doc["events"]:
            if "down" not in e:
                continue                                           # position-correction record
            if e.get("p2"):
                dropped += 1
                continue
            frames.append((int(e["frame"]), bool(e["down"])))
        meta = {"format": "megahack-json", "framerate": fps}
    elif isinstance(doc.get("macro"), list):                       # TASBot
        fps = doc.get("fps", 240.0)
        for e in doc["macro"]:
            click = (e.get("player_1") or {}).get("click", 0)
            if click in (1, 2):
                frames.append((int(e["frame"]), click == 1))
            if (e.get("player_2") or {}).get("click", 0):
                dropped += 1
        meta = {"format": "tasbot-json", "framerate": fps}
    elif isinstance(doc.get("Echo Replay"), list):                 # EchoBot
        fps = doc.get("FPS", 240.0)
        for e in doc["Echo Replay"]:
            if e.get("Player 2"):
                dropped += 1
                continue
            frames.append((int(e["Frame"]), bool(_first(e, "Hold", "Holding", default=False))))
        meta = {"format": "echo-json", "framerate": fps}
    else:
        raise MacroFormatError("unrecognised macro structure (keys: %s)" % ", ".join(sorted(map(str, doc))[:12]))
    meta["dropped_events"] = dropped
    return frames, fps, meta


def _from_text(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise MacroFormatError("empty macro file")
    try:
        fps = float(lines[0])
    except ValueError:
        raise MacroFormatError("plain-text macro must start with the frame rate")
    frames, dropped = [], 0
    for ln in lines[1:]:
        parts = ln.replace(",", " ").replace("|", " ").split()
        if len(parts) < 2:
            raise MacroFormatError("bad macro line: %r" % ln)
        if len(parts) > 2 and parts[2] not in ("0", "false", "False"):
            dropped += 1
            continue
        frames.append((int(float(parts[0])), parts[1] not in ("0", "false", "False")))
    return frames, fps, {"format": "plain-text", "framerate": fps, "dropped_events": dropped}


def load_macro(path, offset=0):
    """Read any supported macro file into an InputSequence.

    `offset` shifts every event by that many 240 Hz ticks.  GDR says the first frame
    of a replay is frame 0, which is what offset=0 assumes; bots differ by a frame
    here, so try +1/-1 if a macro dies early.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:3] == b"GDR":
        frames, fps, meta = _parse_gdr2(data)
    else:
        head = data.lstrip()[:1]
        if head in (b"{", b"["):
            try:
                doc = json.loads(data.decode("utf-8-sig"))
            except ValueError as exc:
                raise MacroFormatError("not valid JSON: %s" % exc)
            frames, fps, meta = _from_document(doc)
        elif data and (0x80 <= data[0] <= 0x8F or data[0] in (0xDE, 0xDF)):
            frames, fps, meta = _from_document(_msgpack(data))
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise MacroFormatError("unrecognised binary macro format (%s)" % path)
            frames, fps, meta = _from_text(text)
    if meta.get("platformer"):
        meta["warning"] = "platformer macro: left/right inputs are ignored"
    meta["source"] = path
    return InputSequence.from_frames(frames, fps, offset, meta)


# ------------------------------------------------------------------ GDR2 writer (tests / export)
def _leb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _lstr(s):
    b = s.encode("utf-8")
    return _leb(len(b)) + b


def write_gdr2(path, sequence, level_name="", author="gd-remake"):
    """Write a classic-mode, player-1-only .gdr2 file."""
    out = bytearray(b"GDR") + _leb(2) + _lstr("")
    out += _lstr(author) + _lstr("exported by gd-remake")
    out += struct.pack(">f", sequence.last_tick / TPS) + _leb(22074) + struct.pack(">d", float(TPS))
    out += _leb(0) + _leb(0) + _leb(0) + _leb(0)            # seed, coins, ldm, platformer
    out += _lstr("gd-remake") + _leb(1) + _leb(0) + _lstr(level_name)
    out += _leb(0)                                          # extension size
    out += _leb(0)                                          # deaths
    out += _leb(len(sequence.events)) + _leb(len(sequence.events))
    prev = 0
    for tick, down in sequence.events:
        out += _leb(((tick - prev) << 1) | int(down))
        prev = tick
    with open(path, "wb") as fh:
        fh.write(bytes(out))
