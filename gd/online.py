"""Downloading a public level from Geometry Dash's own level server by its ID.

This is the request the game itself makes - "give me this level" - sent to
www.boomlings.com.  Nothing is uploaded and no account is used.

  * HTTPS.  On a Mac the request is made by macOS's own `curl` (it uses the system's
    certificates; the self-contained app carries no TLS library of its own).  Elsewhere
    Python's https is used.  Plain HTTP is not an option: the server stalls part-way
    through sending a level that way.
  * The "secret" field below is the fixed value every Geometry Dash client sends with
    these requests (documented by the community, e.g. Wyliemaster/gddocs); it is not a
    password and identifies no one.
  * Everything that comes back is treated as untrusted text: size-limited, parsed by
    hand, and a level only ever goes through this program's own level parser.  Nothing
    received is run.
  * The server limits how often it may be asked; the menu only sends a request when the
    player presses Play.

No window code here; gd/menu_tk.py runs these calls on a background thread.
"""
import http.client
import os
import shutil
import socket
import subprocess
import urllib.parse

from .level import Level
from .levelio import LevelFormatError, RawLevel, decode_level_string, write_gmd
from .paths import SAVEDATA

HOST = "www.boomlings.com"
SECRET = "Wmfd2893gb7"                 # the common client value, see above
TIMEOUT = 45.0                         # seconds to wait for a level (the server can be slow to send a big one)
MAX_REPLY = 40_000_000                 # bytes; a huge level is a few MB
# features of a level that this engine does not have, by object id (see gd/objects.py)
MISSING = {1346: "rotating objects", 1594: "toggle orbs", 1815: "collision triggers", 1611: "count triggers",
           1913: "camera triggers", 1914: "camera triggers", 1916: "camera triggers"}


class OnlineError(Exception):
    """A problem worth telling the player in plain words."""


def cache_folder():
    return os.path.join(SAVEDATA, "online")


CURL = "/usr/bin/curl"


def _send(path, body, timeout):
    """POST `body` over HTTPS -> (status, bytes).  Raises OSError when it cannot be sent."""
    url = "https://%s%s" % (HOST, path)
    if os.path.isfile(CURL) or shutil.which("curl"):
        # -A "": no User-Agent at all - the server turns away browser-style ones
        cmd = [CURL if os.path.isfile(CURL) else shutil.which("curl"), "-sS", "-X", "POST", "-A", "",
               "-H", "Content-Type: application/x-www-form-urlencoded", "--data-binary", "@-",
               "--max-time", str(int(timeout)), "--max-filesize", str(MAX_REPLY), "-w", "\n%{http_code}", url]
        try:
            done = subprocess.run(cmd, input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            raise OSError("timed out")
        if done.returncode != 0:
            raise OSError(done.stderr.decode("utf-8", "replace").strip() or "curl failed")
        data, _, code = done.stdout.rpartition(b"\n")
        return int(code or 0), data
    try:
        import ssl                            # not in the macOS app (it uses curl, above)
    except ImportError:
        raise OSError("no way to make an HTTPS request on this system")
    conn = http.client.HTTPSConnection(HOST, timeout=timeout, context=ssl.create_default_context())
    conn.putrequest("POST", path, skip_accept_encoding=True)
    conn.putheader("Content-Type", "application/x-www-form-urlencoded")
    conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(body)
    reply = conn.getresponse()
    data = reply.read(MAX_REPLY + 1)
    conn.close()
    return reply.status, data


def _post(path, fields, timeout=TIMEOUT):
    """One request to the level server -> the reply as text."""
    body = urllib.parse.urlencode(dict(fields, secret=SECRET, gameVersion=22, binaryVersion=42)).encode()
    try:
        status, data = _send(path, body, timeout)
    except (OSError, socket.timeout, http.client.HTTPException, ValueError):   # (ssl errors are OSErrors)
        raise OnlineError("Could not reach the Geometry Dash level server. Check the internet connection.")
    if status != 200:
        raise OnlineError("The level server answered with an error (%d). Try again in a minute." % status)
    if len(data) > MAX_REPLY:
        raise OnlineError("The level server sent more than expected.")
    text = data.decode("utf-8", "replace").strip()
    if text in ("-1", ""):
        return ""
    if text.startswith("error") or text.startswith("<"):
        raise OnlineError("The level server refused the request. Try again in a minute.")
    return text


def _fields(record):
    """"1:abc:2:def" -> {"1": "abc", "2": "def"}"""
    parts = record.split(":")
    return dict(zip(parts[0::2], parts[1::2]))


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value, limit=80):
    value = "".join(c if c.isprintable() else " " for c in str(value or ""))
    return " ".join(value.split())[:limit]


def download(level_id):
    """A level's name, creator and decoded level text from the server."""
    level_id = _int(level_id)
    if level_id <= 0:
        raise OnlineError("That is not a level ID.")
    try:
        reply = _post("/database/downloadGJLevel22.php", {"levelID": level_id}, TIMEOUT)
    except OnlineError:                       # the server sometimes stalls: one more try
        reply = _post("/database/downloadGJLevel22.php", {"levelID": level_id}, TIMEOUT)
    f = _fields(reply.split("#")[0]) if reply else {}
    if _int(f.get("1")) != level_id or not f.get("4"):
        raise OnlineError("That level could not be downloaded (it may have been deleted).")
    try:
        text = decode_level_string(f["4"])
    except LevelFormatError:
        raise OnlineError("That level's data could not be read.")
    return _text(f.get("2"), 40) or "Unnamed", text


def check(name, text):
    """What this engine makes of a level -> {"simulated", "objects", "missing": [...]}"""
    level = Level(RawLevel(name, text, "online"))
    rep = level.report
    simulated = len(level.objects)
    ids = {}
    for part in text.split(";")[1:]:
        if part.startswith("1,"):
            oid = _int(part[2:].split(",", 1)[0])
            if oid in MISSING:
                ids[MISSING[oid]] = True
    total = simulated + sum(rep.skipped.values()) + sum(rep.ignored.values())
    return {"simulated": simulated, "objects": total, "missing": sorted(ids)}


def fetch(level_id, folder=None):
    """Download a level into the cache (savedata/online) -> (path, name, check result)."""
    level_id = _int(level_id)
    name, text = download(level_id)
    report = check(name, text)
    folder = folder or cache_folder()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%d.gmd" % level_id)
    write_gmd(path, name, text)
    return path, name, report
