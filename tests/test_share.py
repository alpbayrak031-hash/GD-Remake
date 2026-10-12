"""Tests for sharing levels over the local network (gd/share.py).

Everything here talks to this computer only (127.0.0.1) on ports the system picks."""
import atexit
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# the player's folder (normally Documents/GD Remake) must never be touched by a test
if "GD_REMAKE_HOME" not in os.environ:
    os.environ["GD_REMAKE_HOME"] = tempfile.mkdtemp(prefix="gdhome")
    atexit.register(shutil.rmtree, os.environ["GD_REMAKE_HOME"], True)

from gd import share                                       # noqa: E402
from gd.level import LevelBuilder                          # noqa: E402
from gd.levelio import load_level_file                     # noqa: E402

LOCAL = "127.0.0.1"


def level_text(spikes=3):
    b = LevelBuilder()
    for i in range(spikes):
        b.add(8, 615 + 30 * i, 15)
    return b.string()


def wait_until(condition, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class Net(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gdshare")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.sessions = []
        self.addCleanup(lambda: [s.leave() for s in self.sessions])

    def room(self, name="Host", code="4821"):
        room = share.Room(name, port=0, discovery_port=0, code=code).start()
        self.sessions.append(room)
        return room

    def guest(self, room, name="Guest", code=None):
        guest = share.Guest(name)
        self.sessions.append(guest)
        return guest.join(LOCAL, room.port, room.code if code is None else code)

    def raw(self, room, hello=None):
        """A bare socket that has (optionally) said hello - for sending what the game never would."""
        sock = socket.create_connection((LOCAL, room.port), timeout=5)
        self.addCleanup(sock.close)
        stream = sock.makefile("rb")
        if hello is not None:
            sock.sendall((json.dumps(hello) + "\n").encode())
        return sock, stream

    def patch(self, name, value):
        old = getattr(share, name)
        setattr(share, name, value)
        self.addCleanup(setattr, share, name, old)


class Joining(Net):
    def test_the_code_lets_you_in(self):
        room = self.room("Alp")
        self.assertEqual((room.room, room.people, room.code, room.running), ("Alp's room", ["Alp"], "4821", True))
        guest = self.guest(room, "Kerem")
        self.assertEqual((guest.room, guest.me, guest.people), ("Alp's room", "Kerem", ["Alp", "Kerem"]))
        self.assertTrue(wait_until(lambda: room.people == ["Alp", "Kerem"]))
        self.assertIn("Kerem joined.", room.messages)

    def test_a_wrong_code_does_not(self):
        room = self.room()
        with self.assertRaises(share.ShareError) as caught:
            self.guest(room, code="0000")
        self.assertEqual(str(caught.exception), "Wrong code.")
        self.assertEqual(room.people, ["Host"])

    def test_guessing_codes_locks_you_out(self):
        room = self.room()
        for _ in range(share.MAX_WRONG_CODES):
            with self.assertRaises(share.ShareError):
                self.guest(room, code="1111")
        with self.assertRaises(share.ShareError) as caught:
            self.guest(room)                                  # even the right code now
        self.assertIn("Too many wrong codes", str(caught.exception))

    def test_a_random_code_is_four_digits(self):
        codes = {share.Room("x").code for _ in range(20)}
        self.assertTrue(all(len(c) == 4 and c.isdigit() for c in codes))
        self.assertGreater(len(codes), 1)

    def test_two_players_with_the_same_name(self):
        room = self.room("Alp")
        self.assertEqual(self.guest(room, "Alp").me, "Alp 2")
        self.assertEqual(self.guest(room, "Alp").me, "Alp 3")
        self.assertTrue(wait_until(lambda: room.people == ["Alp", "Alp 2", "Alp 3"]))

    def test_a_full_room(self):
        self.patch("MAX_PEOPLE", 2)
        room = self.room()
        self.guest(room, "One")
        with self.assertRaises(share.ShareError) as caught:
            self.guest(room, "Two")
        self.assertEqual(str(caught.exception), "The room is full.")

    def test_another_version_of_the_game(self):
        room = self.room()
        _sock, stream = self.raw(room, {"t": "hello", "v": share.PROTOCOL + 1, "name": "x", "code": room.code})
        self.assertEqual(json.loads(stream.readline())["t"], "no")

    def test_nobody_home(self):
        room = self.room()
        port = room.port
        room.leave()
        with self.assertRaises(share.ShareError):
            share.Guest("x").join(LOCAL, port, "4821", timeout=2)

    def test_leaving(self):
        room = self.room()
        one, two = self.guest(room, "One"), self.guest(room, "Two")
        one.leave()
        self.assertTrue(wait_until(lambda: room.people == ["Host", "Two"] and two.people == ["Host", "Two"]))
        self.assertEqual((one.running, one.ended), (False, "You left the room."))
        room.leave()
        self.assertTrue(wait_until(lambda: not two.running))
        self.assertEqual(two.ended, "The host closed the room.")
        with self.assertRaises(share.ShareError):
            two.share("x", level_text())


class Levels(Net):
    def test_a_level_goes_from_one_player_to_another(self):
        room = self.room("Alp")
        kerem, dara = self.guest(room, "Kerem"), self.guest(room, "Dara")
        text = level_text(3)
        kerem.share("Triple: Spike!", text)
        self.assertTrue(wait_until(lambda: len(room.shelf) == 1 and len(dara.shelf) == 1 and len(kerem.shelf) == 1))
        item = dara.shelf[0]
        self.assertEqual((item["title"], item["from"], item["objects"], item["chars"]),
                         ("Triple: Spike!", "Kerem", 3, len(text)))
        self.assertEqual(dara.inbox, [], "seeing a level on the shelf does not download it")
        dara.request(item["id"])
        self.assertTrue(wait_until(lambda: dara.inbox))
        self.assertEqual(dara.take_inbox(), [(item["id"], "Triple: Spike!", text)])
        self.assertEqual(dara.take_inbox(), [])
        room.request(item["id"])                               # the host takes a copy too
        self.assertEqual(room.take_inbox(), [(item["id"], "Triple: Spike!", text)])

    def test_the_host_shares_too_and_latecomers_see_the_shelf(self):
        room = self.room("Alp")
        room.share("Mine", level_text(2))
        room.share("Mine", level_text(2))                      # sharing the same level twice adds nothing
        self.assertEqual(len(room.shelf), 1)
        late = self.guest(room, "Late")
        self.assertEqual([(i["title"], i["from"]) for i in late.shelf], [("Mine", "Alp")])
        late.request(late.shelf[0]["id"])
        self.assertTrue(wait_until(lambda: late.inbox))
        self.assertEqual(late.inbox[0][2], level_text(2))

    def test_nothing_arrives_that_was_not_asked_for(self):
        room = self.room()
        guest = self.guest(room)
        self.assertTrue(wait_until(lambda: room._guests))
        pushy = list(room._guests.values())[0]
        share._send(pushy["sock"], pushy["lock"], {"t": "file", "id": 7, "title": "Surprise", "level": level_text()})
        room.share("Marker", level_text(1))                    # ... and something that does arrive, after it
        self.assertTrue(wait_until(lambda: guest.shelf))
        self.assertEqual(guest.inbox, [])

    def test_a_guest_cannot_put_rubbish_on_the_shelf(self):
        room = self.room()
        sock, stream = self.raw(room, {"t": "hello", "v": share.PROTOCOL, "name": "Bad\x00\nGuy" + "x" * 99,
                                       "code": room.code})
        welcome = json.loads(stream.readline())
        self.assertEqual(welcome["t"], "welcome")
        self.assertEqual(welcome["you"], ("Bad Guy" + "x" * 99)[:share.NAME_LIMIT], "names are cleaned and cut")
        for level in ("no semicolon here", "", 42, None, "a;b\x00c;", ["1,1;"]):
            sock.sendall((json.dumps({"t": "share", "title": "x", "level": level}) + "\n").encode())
        errors = 0
        sock.settimeout(5)
        while errors < 6:
            reply = json.loads(stream.readline())
            errors += reply["t"] == "error"
        self.assertEqual(room.shelf, [])
        sock.sendall(b'{"t":"get","id":"../../etc/passwd"}\n')
        while True:
            reply = json.loads(stream.readline())
            if reply["t"] == "error":
                break
        self.assertIn("no longer on the shelf", reply["why"])

    def test_an_oversized_or_garbled_message_ends_the_connection(self):
        self.patch("MAX_LINE", 2000)
        room = self.room()
        for junk in (b"x" * 5000 + b"\n", b"this is not json\n", b"[1,2,3]\n"):
            sock, stream = self.raw(room, {"t": "hello", "v": share.PROTOCOL, "name": "x", "code": room.code})
            self.assertEqual(json.loads(stream.readline())["t"], "welcome")
            sock.sendall(junk)
            sock.settimeout(5)
            while True:                                        # state updates may still be in flight
                line = stream.readline()
                if not line:
                    break
            self.assertTrue(wait_until(lambda: room.people == ["Host"]))

    def test_shelf_limits(self):
        self.patch("MAX_SHELF", 1)
        room = self.room()
        guest = self.guest(room)
        room.share("One", level_text(1))
        with self.assertRaises(share.ShareError):
            room.share("Two", level_text(2))
        guest.share("Three", level_text(3))
        self.assertTrue(wait_until(lambda: any("shelf is full" in m for m in guest.messages)))
        self.assertEqual(len(room.shelf), 1)

    def test_what_counts_as_a_level(self):
        title, text, objects = share.check_level("  A\tname\nwith   gaps  " + "!" * 80, level_text(2))
        self.assertEqual((title, objects), (("A name with gaps " + "!" * 80)[:share.TITLE_LIMIT].strip(), 2))
        self.assertEqual(share.check_level(None, level_text(1))[0], "Shared level")
        self.patch("MAX_LEVEL_CHARS", 100)
        with self.assertRaises(share.ShareError) as caught:
            share.check_level("big", level_text(30))
        self.assertIn("too big", str(caught.exception))
        for bad in ("", "nothing", b"1,1;", "x\x00;"):
            with self.assertRaises(share.ShareError):
                share.check_level("x", bad)

    def test_saving_never_overwrites_and_never_leaves_the_folder(self):
        folder = os.path.join(self.dir, "levels")
        first = share.save_level(folder, "../../Escape: attempt", level_text(2))
        second = share.save_level(folder, "../../Escape: attempt", level_text(3))
        self.assertEqual(sorted(os.listdir(folder)), ["______Escape_ attempt 2.gmd", "______Escape_ attempt.gmd"])
        self.assertEqual({os.path.dirname(first), os.path.dirname(second)}, {folder})
        self.assertEqual(load_level_file(first).level_string, level_text(2))
        self.assertEqual(share.read_level(second), ("../../Escape: attempt", level_text(3)))
        self.assertEqual(os.listdir(self.dir), ["levels"], "nothing was written anywhere else")


class Network(Net):
    def test_only_addresses_on_the_local_network(self):
        for address in ("192.168.1.5", "10.0.0.7", "172.16.4.4", "172.31.255.1", "169.254.10.1", "127.0.0.1", "::1"):
            self.assertTrue(share.is_local(address), address)
        for address in ("8.8.8.8", "1.2.3.4", "172.32.0.1", "0.0.0.0", "224.0.0.1", "::ffff:8.8.8.8",
                        "2001:4860:4860::8888", "example.com", "", None):
            self.assertFalse(share.is_local(address), address)
        with self.assertRaises(share.ShareError) as caught:
            share.Guest("x").join("8.8.8.8", 80, "0000")      # refused before any connection is made
        self.assertIn("not an address on this network", str(caught.exception))
        self.assertTrue(share.is_local(share.local_address()))

    def test_rooms_can_be_found(self):
        room = self.room("Alp")
        self.guest(room, "Kerem")
        self.assertTrue(wait_until(lambda: len(room.people) == 2))
        found = share.find_rooms(wait=0.5, port=room.discovery_port, targets=[LOCAL])
        self.assertEqual(found, [{"room": "Alp's room", "host": "Alp", "address": LOCAL, "port": room.port,
                                  "people": 2}])
        self.assertNotIn("4821", json.dumps(found), "the code is never given out")
        finder = share.Finder(port=room.discovery_port, every=0.1)
        self.addCleanup(finder.stop)
        self.assertTrue(wait_until(lambda: finder.rooms))
        room.leave()
        self.assertEqual(share.find_rooms(wait=0.3, port=room.discovery_port, targets=[LOCAL]), [])

    def test_the_finder_ignores_impostors(self):
        fake = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        fake.bind((LOCAL, 0))
        fake.settimeout(2)
        self.addCleanup(fake.close)
        import threading

        def answer():
            for reply in (b"garbage", b'{"gd":"room","v":99,"port":1}', b'{"gd":"room","v":1,"port":"80"}',
                          b'{"gd":"room","v":1,"port":70000}', b'[1]'):
                try:
                    _data, sender = fake.recvfrom(64)
                    fake.sendto(reply, sender)
                except OSError:
                    return
        threading.Thread(target=answer, daemon=True).start()
        for _ in range(5):
            self.assertEqual(share.find_rooms(wait=0.2, port=fake.getsockname()[1], targets=[LOCAL]), [])

    def test_names(self):
        self.assertEqual(share.clean("  A\x00l\np  ", 10), "A l p")
        self.assertEqual(share.clean("x" * 50, 5), "xxxxx")
        self.assertEqual(share.clean(None, 5), "")
        self.assertTrue(share.default_name())
        share.save_name("  Alp\tGiray  ")
        self.assertEqual(share.load_name(), "Alp Giray")
        share.save_name("")                                    # an empty name is not saved over a good one
        self.assertEqual(share.load_name(), "Alp Giray")


if __name__ == "__main__":
    unittest.main(verbosity=2)
