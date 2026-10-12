"""Sharing levels with other players on the same Wi-Fi.

One player HOSTS a room; the others JOIN it with the room's 4-digit code.  Anyone in
the room can put one of their levels on the room's shelf, and anyone can take a copy
of a level from the shelf.  Nothing arrives on your computer unless you ask for it.

How it is kept safe (it is still meant for a network you trust - home, school):

  * Local network only.  Connections and discovery replies from addresses that are
    not private (192.168.x.x, 10.x.x.x, 172.16-31.x.x, link-local, this computer)
    are dropped.
  * The code.  A device on the same Wi-Fi cannot enter a room without the code on
    the host's screen; five wrong codes from one address and it is locked out.
  * What travels is plain level text - the decoded level string ("kA13,0,...;1,8,2,
    615,3,15;...") and a name - never a file, a path or a compressed blob.  The
    receiver checks its size, runs it through this program's own level parser and
    then writes a fresh .gmd itself, under a name it chooses, in the player's levels
    folder only.  Nothing received is ever executed, unpacked or overwritten.
  * Limits on everything: message size, people in a room, levels on the shelf.
  * No encryption (the app carries no TLS): anyone able to listen in on the network
    could read the levels and the code.  That is why the screen says "a network you
    trust".

Wire format: one JSON object per line over TCP.  Rooms are found with a UDP
broadcast ("GDREMAKE?1") that hosts answer; if a network blocks broadcasts, the
host's address can be typed in instead.

This module has no window code.  Its objects are updated by background threads and
read by the menu, which polls `version` (see gd/menu_tk.py).
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time

from .level import Level
from .levelio import RawLevel, load_level_file, write_gmd
from .paths import SAVEDATA, unique_path

PROTOCOL = 1
PORT = 47811                     # TCP: the room (the next nine are tried if it is taken)
DISCOVERY_PORT = 47810           # UDP: "is there a room on this network?"
PROBE = b"GDREMAKE?1"
MAX_LEVEL_CHARS = 8_000_000      # one level, decoded (a big 2.2 level is a few million characters)
MAX_LINE = MAX_LEVEL_CHARS + 65536
MAX_PEOPLE = 12                  # host included
MAX_SHELF = 40                   # levels on a room's shelf
MAX_SHELF_CHARS = 64_000_000     # ... and their total size (they are kept in the host's memory)
MAX_WRONG_CODES = 5
NAME_LIMIT = 24                  # a player's name
TITLE_LIMIT = 40                 # a level's name
HANDSHAKE_SECONDS = 8.0


class ShareError(Exception):
    """Something the player should be told in plain words."""


# ---------------------------------------------------------------------------- small helpers
def clean(text, limit):
    """Text from a stranger made safe to show and to use in a name: printable
    characters only, single spaces, at most `limit` characters."""
    if not isinstance(text, str):
        return ""
    text = "".join(c if c.isprintable() else " " for c in text)
    return " ".join(text.split())[:limit].strip()


def is_local(address):
    """Is this an address on the local network (or this computer)?"""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or (ip.is_private and not ip.is_unspecified and not ip.is_multicast)


def local_address():
    """This computer's address on the network ("127.0.0.1" when it has none)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))          # no packet is sent: this only picks the interface
        address = probe.getsockname()[0]
    except OSError:
        address = "127.0.0.1"
    finally:
        probe.close()
    return address if is_local(address) else "127.0.0.1"


def default_name():
    """A first guess at what to call this player: the first name of the computer's user."""
    try:
        import pwd
        entry = pwd.getpwuid(os.getuid())
        name = clean((entry.pw_gecos or "").split(",")[0], NAME_LIMIT).split(" ")[0] or entry.pw_name
    except Exception:
        name = os.environ.get("USER", "")
    return clean(name, NAME_LIMIT) or "Player"


def _settings_path():
    return os.path.join(SAVEDATA, "sharing.json")


def load_name():
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            name = clean(json.load(fh).get("name"), NAME_LIMIT)
    except (OSError, ValueError, AttributeError):
        name = ""
    return name or default_name()


def save_name(name):
    name = clean(name, NAME_LIMIT)
    if not name:
        return
    try:
        os.makedirs(SAVEDATA, exist_ok=True)
        tmp = _settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"name": name}, fh)
        os.replace(tmp, _settings_path())
    except OSError:
        pass


# ---------------------------------------------------------------------------- levels in and out
def check_level(title, level_string):
    """Everything a level from the network has to pass.  -> (title, level string, objects)."""
    if not isinstance(level_string, str) or not level_string:
        raise ShareError("That was not a level.")
    if len(level_string) > MAX_LEVEL_CHARS:
        raise ShareError("That level is too big to share (%d MB at most)." % (MAX_LEVEL_CHARS // 1_000_000))
    if ";" not in level_string or "\x00" in level_string:
        raise ShareError("That was not a level.")
    title = clean(title, TITLE_LIMIT) or "Shared level"
    try:
        level = Level(RawLevel(title, level_string, "shared"))
        objects = len(level.objects)
    except Exception:                                 # whatever a hostile "level" breaks, it is just refused
        raise ShareError("That level could not be read.")
    return title, level_string, objects


def read_level(path):
    """One of the player's own level files -> (title, level string), ready to share."""
    raw = load_level_file(path)
    title = raw.name if raw.name and raw.name not in ("New level", "Unnamed") else os.path.splitext(os.path.basename(path))[0]
    title, level_string, _objects = check_level(title.replace("_", " "), raw.level_string)
    return title, level_string


def save_level(folder, title, level_string):
    """Write a received level into the player's levels folder as a new file -> its path."""
    title, level_string, _objects = check_level(title, level_string)
    os.makedirs(folder, exist_ok=True)
    path = unique_path(folder, title)
    write_gmd(path, title, level_string)
    return path


# ---------------------------------------------------------------------------- the wire
def _send(sock, lock, message):
    data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
    with lock:
        sock.sendall(data)


def _read(stream):
    """The next message from a socket's file object: a dict, or None when it closed.
    Raises ShareError for anything oversized or malformed."""
    line = stream.readline(MAX_LINE + 1)
    if not line:
        return None
    if len(line) > MAX_LINE or not line.endswith(b"\n"):
        raise ShareError("message too large")
    try:
        message = json.loads(line)
    except ValueError:
        raise ShareError("not a message")
    if not isinstance(message, dict) or not isinstance(message.get("t"), str):
        raise ShareError("not a message")
    return message


def _close(sock):
    for step in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
        try:
            step()
        except OSError:
            pass


class Session:
    """What the menu sees of a room, whether this player hosts it or joined it."""
    role = ""

    def __init__(self, me):
        self.me = clean(me, NAME_LIMIT) or "Player"
        self.room = ""
        self.people = []                 # names, host first
        self.shelf = []                  # [{"id", "title", "from", "objects", "chars"}]
        self.messages = []               # the last few things that happened, newest last
        self.inbox = []                  # [(shelf id, title, level string)] levels this player asked for
        self.waiting = set()             # shelf ids asked for and not arrived yet
        self.version = 0
        self.running = False
        self.ended = ""                  # why the session is over ("" while it is running)
        self._lock = threading.RLock()

    def _say(self, text):
        with self._lock:
            self.messages.append(text)
            del self.messages[:-6]
            self.version += 1

    note = _say                          # the menu adds its own lines ("Saved ... to My Levels.")

    def _changed(self):
        with self._lock:
            self.version += 1

    def take_inbox(self):
        with self._lock:
            items, self.inbox = self.inbox, []
        return items

    def snapshot(self):
        """A consistent copy of what should be on screen."""
        with self._lock:
            return {"people": list(self.people), "shelf": [dict(i) for i in self.shelf],
                    "messages": list(self.messages), "waiting": set(self.waiting)}


# ---------------------------------------------------------------------------- hosting
class Room(Session):
    role = "host"

    def __init__(self, me, port=PORT, discovery_port=DISCOVERY_PORT, code=None):
        Session.__init__(self, me)
        self.room = "%s's room" % self.me
        self.code = code if code is not None else "%04d" % secrets.randbelow(10000)
        self.address = local_address()
        self.port = port
        self.discovery_port = discovery_port
        self.token = secrets.token_hex(6)        # tells finders that two replies are the same room
        self.people = [self.me]
        self._guests = {}                        # id -> {"name", "sock", "lock"}
        self._files = {}                         # shelf id -> level string
        self._digests = {}                       # (owner, sha1) -> shelf id: sharing twice adds nothing
        self._strikes = {}                       # address -> wrong codes
        self._next = 1
        self._listener = None
        self._responder = None

    # -- life cycle
    def start(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        last = None
        for port in ([self.port] if self.port == 0 else range(self.port, self.port + 10)):
            try:
                listener.bind(("", port))
                break
            except OSError as exc:
                last = exc
        else:
            listener.close()
            raise ShareError("Could not open a room: %s." % (last.strerror or last))
        listener.listen(MAX_PEOPLE)
        self.port = listener.getsockname()[1]
        self._listener = listener
        self.running = True
        try:
            responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            responder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                responder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            responder.bind(("", self.discovery_port))
            self.discovery_port = responder.getsockname()[1]
            self._responder = responder
            threading.Thread(target=self._answer_probes, name="share-discovery", daemon=True).start()
        except OSError:
            self._responder = None               # still joinable by typing the address
        threading.Thread(target=self._accept, name="share-accept", daemon=True).start()
        self._say("Room open. Tell your friends the code %s." % self.code)
        return self

    def leave(self):
        if not self.running:
            return
        self.running = False
        self.ended = "You closed the room."
        with self._lock:
            guests = list(self._guests.values())
            self._guests.clear()
        for guest in guests:
            try:
                _send(guest["sock"], guest["lock"], {"t": "bye"})
            except OSError:
                pass
            _close(guest["sock"])
        for sock in (self._listener, self._responder):
            if sock is not None:
                _close(sock)
        self._changed()

    # -- what the host does in its own room
    def share(self, title, level_string):
        self._add(self.me, title, level_string)

    def request(self, item_id):
        with self._lock:
            level = self._files.get(item_id)
            item = next((i for i in self.shelf if i["id"] == item_id), None)
            if level is not None and item is not None:
                self.inbox.append((item_id, item["title"], level))
                self.version += 1

    # -- the shelf
    def _add(self, owner, title, level_string):
        title, level_string, objects = check_level(title, level_string)
        digest = hashlib.sha1(level_string.encode("utf-8", "replace")).hexdigest()
        with self._lock:
            if (owner, digest) in self._digests:
                return self._digests[(owner, digest)]
            if len(self.shelf) >= MAX_SHELF or sum(i["chars"] for i in self.shelf) + len(level_string) > MAX_SHELF_CHARS:
                raise ShareError("The room's shelf is full.")
            item_id, self._next = self._next, self._next + 1
            self._files[item_id] = level_string
            self._digests[(owner, digest)] = item_id
            self.shelf.append({"id": item_id, "title": title, "from": owner, "objects": objects,
                               "chars": len(level_string)})
        self._say("%s shared \"%s\"." % ("You" if owner == self.me else owner, title))
        self._broadcast_state()
        return item_id

    def _broadcast_state(self):
        with self._lock:
            message = {"t": "state", "people": list(self.people), "shelf": [dict(i) for i in self.shelf]}
            guests = list(self._guests.values())
        for guest in guests:
            try:
                _send(guest["sock"], guest["lock"], message)
            except OSError:
                pass                             # its own thread notices and removes it

    # -- guests
    def _accept(self):
        while self.running:
            try:
                sock, address = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock, address[0]), name="share-guest", daemon=True).start()

    def _refuse(self, sock, lock, why):
        try:
            _send(sock, lock, {"t": "no", "why": why})
        except OSError:
            pass
        _close(sock)

    def _serve(self, sock, address):
        lock = threading.Lock()
        if not is_local(address):
            _close(sock)                         # not from this network: not a word
            return
        guest_id = None
        name = ""
        try:
            sock.settimeout(HANDSHAKE_SECONDS)
            stream = sock.makefile("rb")
            hello = _read(stream)
            if hello is None or hello.get("t") != "hello":
                return _close(sock)
            if hello.get("v") != PROTOCOL:
                return self._refuse(sock, lock, "That player has a different version of the game.")
            with self._lock:
                locked_out = self._strikes.get(address, 0) >= MAX_WRONG_CODES
            if locked_out:
                return self._refuse(sock, lock, "Too many wrong codes. Ask the host to open the room again.")
            if not hmac.compare_digest(str(hello.get("code", "")).encode("utf-8"), self.code.encode("utf-8")):
                with self._lock:
                    self._strikes[address] = self._strikes.get(address, 0) + 1
                return self._refuse(sock, lock, "Wrong code.")
            with self._lock:
                if len(self.people) >= MAX_PEOPLE:
                    full = True
                else:
                    full = False
                    wanted = clean(hello.get("name"), NAME_LIMIT) or "Player"
                    name, n = wanted, 2
                    while name in self.people:   # two Alps: the second is "Alp 2"
                        name = "%s %d" % (wanted[:NAME_LIMIT - 3], n)
                        n += 1
                    guest_id, self._next = self._next, self._next + 1
                    self._guests[guest_id] = {"name": name, "sock": sock, "lock": lock}
                    self.people.append(name)
                    welcome = {"t": "welcome", "room": self.room, "you": name, "people": list(self.people),
                               "shelf": [dict(i) for i in self.shelf]}
            if full:
                return self._refuse(sock, lock, "The room is full.")
            sock.settimeout(None)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            _send(sock, lock, welcome)
            self._say("%s joined." % name)
            self._broadcast_state()
            while self.running:
                message = _read(stream)
                if message is None or message["t"] == "bye":
                    break
                if message["t"] == "share":
                    try:
                        self._add(name, message.get("title"), message.get("level"))
                    except ShareError as exc:
                        _send(sock, lock, {"t": "error", "why": str(exc)})
                elif message["t"] == "get":
                    with self._lock:
                        level = self._files.get(message.get("id"))
                        item = next((i for i in self.shelf if i["id"] == message.get("id")), None)
                    if level is None or item is None:
                        _send(sock, lock, {"t": "error", "why": "That level is no longer on the shelf."})
                    else:
                        _send(sock, lock, {"t": "file", "id": item["id"], "title": item["title"], "level": level})
        except (OSError, ShareError):
            pass                                 # a broken or misbehaving connection is simply dropped
        finally:
            _close(sock)
            if guest_id is not None:
                with self._lock:
                    gone = self._guests.pop(guest_id, None)
                    if gone is not None and name in self.people:
                        self.people.remove(name)
                if gone is not None and self.running:
                    self._say("%s left." % name)
                    self._broadcast_state()

    # -- being found
    def _answer_probes(self):
        while self.running:
            try:
                data, sender = self._responder.recvfrom(64)
            except OSError:
                return
            if data != PROBE or not is_local(sender[0]):
                continue
            with self._lock:
                reply = {"gd": "room", "v": PROTOCOL, "id": self.token, "room": self.room, "host": self.me,
                         "port": self.port, "people": len(self.people)}
            try:
                self._responder.sendto(json.dumps(reply).encode("utf-8"), sender)
            except OSError:
                pass


# ---------------------------------------------------------------------------- joining
class Guest(Session):
    role = "guest"

    def __init__(self, me):
        Session.__init__(self, me)
        self.address = ""
        self.port = 0
        self._sock = None
        self._send_lock = threading.Lock()

    def join(self, address, port, code, timeout=HANDSHAKE_SECONDS):
        """Enter a room.  Raises ShareError with the reason when that fails."""
        address = clean(address, 64)
        if not is_local(address):
            raise ShareError("That is not an address on this network.")
        try:
            sock = socket.create_connection((address, int(port)), timeout=timeout)
        except (OSError, ValueError, OverflowError):
            raise ShareError("No room answered at %s. Is the host still hosting, on the same Wi-Fi?" % address)
        try:
            _send(sock, self._send_lock, {"t": "hello", "v": PROTOCOL, "name": self.me, "code": clean(code, 16)})
            stream = sock.makefile("rb")
            reply = _read(stream)
        except (OSError, ShareError):
            _close(sock)
            raise ShareError("The room did not answer properly.")
        if reply is None or reply["t"] not in ("welcome", "no"):
            _close(sock)
            raise ShareError("The room did not answer properly.")
        if reply["t"] == "no":
            _close(sock)
            raise ShareError(clean(reply.get("why"), 120) or "The room refused.")
        sock.settimeout(None)
        self._sock = sock
        self.address, self.port = address, int(port)
        self.room = clean(reply.get("room"), NAME_LIMIT + 10) or "Room"
        self.me = clean(reply.get("you"), NAME_LIMIT) or self.me
        self._take_state(reply)
        self.running = True
        threading.Thread(target=self._listen, args=(stream,), name="share-room", daemon=True).start()
        self._say("You joined %s." % self.room)
        return self

    def _take_state(self, message):
        people = [clean(p, NAME_LIMIT) for p in message.get("people", []) if isinstance(p, str)][:MAX_PEOPLE]
        shelf = []
        for item in message.get("shelf", [])[:MAX_SHELF]:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                shelf.append({"id": item["id"], "title": clean(item.get("title"), TITLE_LIMIT) or "Shared level",
                              "from": clean(item.get("from"), NAME_LIMIT) or "?",
                              "objects": item["objects"] if isinstance(item.get("objects"), int) else 0,
                              "chars": item["chars"] if isinstance(item.get("chars"), int) else 0})
        with self._lock:
            self.people, self.shelf = people, shelf
            self.version += 1

    def _listen(self, stream):
        reason = "The connection to the room was lost."
        try:
            while self.running:
                message = _read(stream)
                if message is None:
                    break
                kind = message["t"]
                if kind == "state":
                    self._take_state(message)
                elif kind == "file":
                    item_id = message.get("id")
                    with self._lock:
                        asked = item_id in self.waiting
                        self.waiting.discard(item_id)
                    if asked:                    # only what this player asked for is ever kept
                        try:
                            title, level, _objects = check_level(message.get("title"), message.get("level"))
                        except ShareError as exc:
                            self._say(str(exc))
                        else:
                            with self._lock:
                                self.inbox.append((item_id, title, level))
                                self.version += 1
                elif kind == "error":
                    with self._lock:
                        self.waiting.clear()
                    self._say(clean(message.get("why"), 120) or "The room reported a problem.")
                elif kind == "bye":
                    reason = "The host closed the room."
                    break
        except (OSError, ShareError):
            pass
        if self.running:
            self.running = False
            self.ended = reason
            _close(self._sock)
            self._say(reason)

    def share(self, title, level_string):
        title, level_string, _objects = check_level(title, level_string)
        self._post({"t": "share", "title": title, "level": level_string})

    def request(self, item_id):
        with self._lock:
            if item_id in self.waiting:
                return
            self.waiting.add(item_id)
            self.version += 1
        self._post({"t": "get", "id": item_id})

    def _post(self, message):
        if not self.running:
            raise ShareError("You are not in a room any more.")
        try:
            _send(self._sock, self._send_lock, message)
        except OSError:
            raise ShareError("The connection to the room was lost.")

    def leave(self):
        if not self.running:
            return
        self.running = False
        self.ended = "You left the room."
        try:
            _send(self._sock, self._send_lock, {"t": "bye"})
        except OSError:
            pass
        _close(self._sock)
        self._changed()


# ---------------------------------------------------------------------------- finding rooms
def broadcast_targets():
    """Where to shout "is there a room?": the whole network, this subnet, this computer."""
    targets = ["255.255.255.255"]
    address = local_address()
    if address != "127.0.0.1":
        targets.append(address.rsplit(".", 1)[0] + ".255")     # the usual home network (/24)
    targets.append("127.0.0.1")
    return targets


def find_rooms(wait=0.6, port=DISCOVERY_PORT, targets=None):
    """Ask the network once.  -> [{"room", "host", "address", "port", "people"}]"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    found = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        sock.settimeout(0.1)
        for target in (targets if targets is not None else broadcast_targets()):
            try:
                sock.sendto(PROBE, (target, port))
            except OSError:
                pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                data, sender = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if not is_local(sender[0]):
                continue
            try:
                reply = json.loads(data)
            except ValueError:
                continue
            if not isinstance(reply, dict) or reply.get("gd") != "room" or reply.get("v") != PROTOCOL:
                continue
            tcp = reply.get("port")
            if not isinstance(tcp, int) or not 0 < tcp < 65536:
                continue
            key = clean(reply.get("id"), 32) or "%s:%d" % (sender[0], tcp)
            people = reply.get("people")
            room = {"room": clean(reply.get("room"), NAME_LIMIT + 10) or "Room",
                    "host": clean(reply.get("host"), NAME_LIMIT) or "?", "address": sender[0], "port": tcp,
                    "people": people if isinstance(people, int) and 0 < people <= MAX_PEOPLE else 1}
            # the same room can answer on two addresses (this computer's own): keep the network one
            if key not in found or found[key]["address"].startswith("127."):
                found[key] = room
    finally:
        sock.close()
    return sorted(found.values(), key=lambda r: (r["room"].lower(), r["address"]))


class Finder:
    """Keeps looking for rooms in the background while the "join" list is on screen."""

    def __init__(self, port=DISCOVERY_PORT, every=2.0):
        self.rooms = []
        self.version = 0
        self.searched = False            # at least one look has finished
        self._port, self._every = port, every
        self._stop = threading.Event()
        threading.Thread(target=self._look, name="share-finder", daemon=True).start()

    def _look(self):
        while not self._stop.is_set():
            try:
                rooms = find_rooms(port=self._port)
            except OSError:
                rooms = []
            if rooms != self.rooms or not self.searched:
                self.rooms, self.searched = rooms, True
                self.version += 1
            self._stop.wait(self._every)

    def stop(self):
        self._stop.set()
