"""Reading (and writing) Geometry Dash level files.

The loader looks at the file CONTENT, not the extension, so anything that wraps a
level string in one of these ways will load:

  .gmd            GDShare export: RobTop's plist dialect (<k>/<s>/<i>...), either a
                  full <plist> document or a bare <d> dictionary.
  .gmd2           GDShare v2: a ZIP archive holding the same dictionary (level.data)
                  plus metadata/song files.
  .lvl            GDShare legacy: the same dictionary, zlib/gzip compressed.
  .dat            CCLocalLevels.dat save file (all your created levels).  Windows/
                  Android saves are XOR-11 + base64 + gzip.  macOS saves are AES-256-
                  ECB; those are decrypted with the system `openssl` if present.
  .txt / none     A bare level string: "H4sI..." (base64 + gzip) or already decoded
                  ("kS38,...;1,1,2,15,3,15;...").
  .json           Any JSON with the level string in a "data"/"levelString"/"k4"
                  style field (e.g. a GDBrowser API dump).
  server reply    A raw downloadGJLevel22 response ("1:<id>:2:<name>:...:4:<string>").

Level string layout (Wyliemaster/gddocs, "level string" + "level start"):
  base64 (URL-safe) -> gzip -> "header;object;object;...", the header and each object
  being comma separated key,value lists.
"""
import base64
import gzip
import html
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
import zlib


class LevelFormatError(Exception):
    pass


class RawLevel:
    """One level as stored in a container: decoded string plus metadata."""

    def __init__(self, name, level_string, source, meta=None):
        self.name = name or "Unnamed"
        self.level_string = level_string      # decoded: "header;obj;obj;..."
        self.source = source                  # human readable description of where it came from
        self.meta = meta or {}

    def __repr__(self):
        return "<RawLevel %r from %s, %d chars>" % (self.name, self.source, len(self.level_string))


# ----------------------------------------------------------------- level string codec
def _b64_pad(s):
    return s + "=" * (-len(s) % 4)


def decode_level_string(s):
    """Turn the k4 value into "header;obj;..." (accepts already-decoded strings)."""
    if isinstance(s, bytes):
        s = s.decode("utf-8", "replace")
    s = s.strip().strip("\x00")
    if ";" in s or s.startswith(("kS", "kA")):   # ';' and ',' never occur in base64
        return s
    try:
        raw = base64.urlsafe_b64decode(_b64_pad(s))
    except Exception as exc:
        raise LevelFormatError("level string is neither raw nor base64: %s" % exc)
    for wbits in (zlib.MAX_WBITS | 32, -zlib.MAX_WBITS):   # gzip/zlib header, then raw deflate
        try:
            return zlib.decompress(raw, wbits).decode("utf-8", "replace")
        except zlib.error:
            continue
    raise LevelFormatError("level string would not decompress (not gzip, zlib or deflate)")


def encode_level_string(decoded):
    """Inverse of decode_level_string: gzip + URL-safe base64, like the game."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(decoded.encode("utf-8"))
    return base64.urlsafe_b64encode(buf.getvalue()).decode("ascii")


def split_level_string(decoded):
    """-> (header dict of str->str, list of object dicts of int->str)."""
    parts = [p for p in decoded.split(";") if p]
    header, objects = {}, []
    if not parts:
        return header, objects
    first = parts[0].split(",")
    if first and not first[0].lstrip("-").isdigit():    # header keys are named (kA2, kS38, ...)
        header = {first[i]: first[i + 1] for i in range(0, len(first) - 1, 2)}
        parts = parts[1:]
    for p in parts:
        tok = p.split(",")
        obj = {}
        for i in range(0, len(tok) - 1, 2):
            try:
                obj[int(tok[i])] = tok[i + 1]
            except ValueError:
                continue
        if 1 in obj:
            objects.append(obj)
    return header, objects


# ----------------------------------------------------------------- RobTop plist dialect
_TAG = re.compile(r"<(/?)(dict|d|key|k|string|s|integer|i|real|r|true|t|false|f|array|a)\s*(/?)>")
_LONG = {"dict": "d", "key": "k", "string": "s", "integer": "i", "real": "r",
         "true": "t", "false": "f", "array": "a"}


def parse_plist(text):
    """Tolerant parser for GD's plist dialect (short or long tag names).

    Returns a dict; values that had no key (a bare top-level <d>) are stored under
    "_0", "_1", ...
    """
    root = {}
    stack = [[root, None]]          # [container, pending key]
    pos = 0

    def put(value):
        container, key = stack[-1]
        if isinstance(container, list):
            container.append(value)
        else:
            container[key if key is not None else "_%d" % len(container)] = value
            stack[-1][1] = None

    while True:
        m = _TAG.search(text, pos)
        if not m:
            break
        closing, name, selfclose = m.group(1), m.group(2), m.group(3)
        name = _LONG.get(name, name)
        pos = m.end()
        if closing:
            if name in ("d", "a") and len(stack) > 1:
                stack.pop()
            continue
        if name in ("t", "f"):
            put(name == "t")
            if not selfclose:                       # <true></true>
                end = text.find(">", text.find("</", pos))
                pos = end + 1 if end > 0 else pos
            continue
        if name in ("d", "a"):
            value = {} if name == "d" else []
            put(value)
            if not selfclose:
                stack.append([value, None])
            continue
        if selfclose:                               # <s /> : empty leaf
            content = ""
        else:
            end = text.find("</", pos)
            if end < 0:
                break
            content = text[pos:end]
            close = text.find(">", end)
            pos = close + 1 if close > 0 else len(text)
        if name == "k":
            stack[-1][1] = content
        elif name == "s":
            put(html.unescape(content))
        elif name == "i":
            try:
                put(int(content))
            except ValueError:
                put(content)
        elif name == "r":
            try:
                put(float(content))
            except ValueError:
                put(content)
    return root


def _find_level_dicts(node, out):
    if isinstance(node, dict):
        if isinstance(node.get("k4"), str) and node["k4"]:
            out.append(node)
        else:
            for v in node.values():
                _find_level_dicts(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_level_dicts(v, out)


def _levels_from_plist(text, source):
    found = []
    _find_level_dicts(parse_plist(text), found)
    levels = []
    for d in found:
        try:
            decoded = decode_level_string(d["k4"])
        except LevelFormatError:
            continue
        meta = {"creator": d.get("k5"), "id": d.get("k1"), "song": d.get("k8", d.get("k45")),
                "description_b64": d.get("k3"), "objects_declared": d.get("k48")}
        levels.append(RawLevel(d.get("k2"), decoded, source, meta))
    return levels


# ----------------------------------------------------------------- save files
_MAC_SAVE_KEY = b"ipu9TUv54yv]isFMh5@;t.5w34E2Ry@{"   # MEM: key used by GD's macOS saves (community save tools)


def _decode_windows_save(data):
    xored = bytes(b ^ 11 for b in data.rstrip(b"\x00"))
    raw = base64.urlsafe_b64decode(_b64_pad(xored.decode("ascii", "ignore")))
    return zlib.decompress(raw, zlib.MAX_WBITS | 32).decode("utf-8", "replace")


def _decode_mac_save(data):
    exe = shutil.which("openssl")
    if not exe:
        raise LevelFormatError("this looks like a macOS save (AES); `openssl` is needed to decrypt it")
    proc = subprocess.run([exe, "enc", "-d", "-aes-256-ecb", "-nopad", "-K", _MAC_SAVE_KEY.hex()],
                          input=data, capture_output=True)
    if proc.returncode != 0:
        raise LevelFormatError("openssl could not decrypt the save: %s" % proc.stderr.decode(errors="replace").strip())
    out = proc.stdout
    if out and 0 < out[-1] <= 16:              # PKCS#7 padding
        out = out[:-out[-1]]
    return out.decode("utf-8", "replace")


# ----------------------------------------------------------------- sniffing loader
_SERVER_REPLY = re.compile(r"^1:\d+:2:")
_JSON_KEYS = ("data", "levelString", "level_string", "levelstring", "k4", "string", "level")


def _from_json(text, source):
    try:
        doc = json.loads(text)
    except ValueError:
        return []
    out = []

    def walk(node, name=None):
        if isinstance(node, dict):
            nm = node.get("name") or node.get("k2") or name
            for key in _JSON_KEYS:
                v = node.get(key)
                if isinstance(v, str) and len(v) > 20:
                    try:
                        out.append(RawLevel(nm if isinstance(nm, str) else None,
                                            decode_level_string(v), source + " (json)"))
                        return
                    except LevelFormatError:
                        pass
            for v in node.values():
                walk(v, nm if isinstance(nm, str) else None)
        elif isinstance(node, list):
            for v in node:
                walk(v, name)

    walk(doc)
    return out


def _sniff(data, source, depth=0):
    """bytes -> list[RawLevel].  Recurses through compression/encryption layers."""
    if depth > 4:
        return []
    # --- zip container (.gmd2)
    if data[:4] == b"PK\x03\x04":
        levels = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            ordered = [n for n in names if n.lower().endswith("level.data")] + \
                      [n for n in names if not n.lower().endswith("level.data")]
            for n in ordered:
                if n.endswith("/") or n.lower().endswith((".mp3", ".ogg", ".wav", ".png")):
                    continue
                levels += _sniff(zf.read(n), "%s!%s" % (source, n), depth + 1)
                if levels:
                    break
        return levels
    # --- gzip / zlib blob (.lvl, or a gzipped save)
    if data[:2] == b"\x1f\x8b" or (len(data) > 2 and data[0] == 0x78 and data[1] in (0x01, 0x5e, 0x9c, 0xda)):
        try:
            return _sniff(zlib.decompress(data, zlib.MAX_WBITS | 32), source + " (decompressed)", depth + 1)
        except zlib.error:
            pass
    # --- Windows/Android save: XOR 11 of "H4sI..." starts with "C?xB"
    if data[:4] == b"C?xB":
        try:
            return _levels_from_plist(_decode_windows_save(data), source + " (save file)")
        except Exception as exc:
            raise LevelFormatError("save file is damaged: %s" % exc)

    text = None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if b"<k>k4</k>" in data or b"<key>k4</key>" in data:
            text = data.decode("utf-8", "replace")
    if text is not None:
        stripped = text.strip().lstrip("﻿")
        if "<k>k4</k>" in stripped or "<key>k4</key>" in stripped:
            return _levels_from_plist(stripped, source)
        if stripped[:1] in "{[":
            got = _from_json(stripped, source)
            if got:
                return got
        if _SERVER_REPLY.match(stripped):
            fields = stripped.split("#")[0].split(":")
            kv = {fields[i]: fields[i + 1] for i in range(0, len(fields) - 1, 2)}
            if "4" in kv:
                return [RawLevel(kv.get("2"), decode_level_string(kv["4"]), source + " (server reply)",
                                 {"id": kv.get("1")})]
        if stripped:
            try:
                decoded = decode_level_string(stripped)
            except LevelFormatError:
                decoded = None
            if decoded and (";" in decoded or decoded.startswith(("kS", "kA"))):
                name = os.path.splitext(os.path.basename(source))[0]
                return [RawLevel(name, decoded, source + " (bare level string)")]
    # --- macOS save: opaque, block aligned
    if len(data) >= 32 and len(data) % 16 == 0 and text is None and data[:3] != b"GDR":
        try:
            plain = _decode_mac_save(data)
        except LevelFormatError:
            if source.lower().endswith(".dat"):
                raise
            return []
        if "<k>k4</k>" in plain or "<k>LLM_01</k>" in plain:
            return _levels_from_plist(plain, source + " (macOS save file)")
    return []


def list_levels(path):
    """All levels contained in a file (one for exports, many for save files)."""
    with open(path, "rb") as fh:
        data = fh.read()
    levels = _sniff(data, os.path.basename(path))
    if not levels:
        hint = ""
        if data[:3] == b"GDR" or path.lower().endswith((".gdr", ".gdr2", ".re", ".re2", ".re3", ".xd", ".slc", ".mhr", ".echo", ".zbf", ".replay")):
            hint = ("  (This looks like a macro/replay file - it stores inputs, not a level. "
                    "Pass it with --replay next to a level file.)")
        raise LevelFormatError("no Geometry Dash level found in %s.%s" % (path, hint))
    return levels


def load_level_file(path, select=None):
    """Load one RawLevel.  `select` picks a level in multi-level files by index or name."""
    levels = list_levels(path)
    if select is None:
        return levels[0]
    if isinstance(select, int) or str(select).lstrip("-").isdigit():
        idx = int(select)
        if not -len(levels) <= idx < len(levels):
            raise LevelFormatError("level index %d out of range (file has %d)" % (idx, len(levels)))
        return levels[idx]
    wanted = str(select).lower()
    for lv in levels:
        if lv.name.lower() == wanted:
            return lv
    for lv in levels:
        if wanted in lv.name.lower():
            return lv
    raise LevelFormatError("no level named %r in %s" % (select, path))


# ----------------------------------------------------------------- writers (used by tools/tests)
def gmd_document(name, decoded_level_string, creator="gd-remake"):
    """A GDShare-style .gmd document for `decoded_level_string`."""
    k4 = encode_level_string(decoded_level_string)
    body = ("<k>kCEK</k><i>4</i><k>k2</k><s>%s</s><k>k4</k><s>%s</s><k>k5</k><s>%s</s>"
            "<k>k13</k><t /><k>k21</k><i>2</i><k>k50</k><i>45</i>"
            % (html.escape(name), k4, html.escape(creator)))
    return '<?xml version="1.0"?><plist version="1.0" gjver="2.0"><dict>%s</dict></plist>' % body


def write_gmd(path, name, decoded_level_string):
    """Written to a temporary file that is then swapped in, so a crash or a full disk can
    never leave half a level behind (the editor autosaves, so this runs often)."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(gmd_document(name, decoded_level_string))
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
