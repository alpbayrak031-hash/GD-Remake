"""Tests for the Online Levels code (gd/online.py) - with canned server replies, no network."""
import atexit
import base64
import gzip
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "GD_REMAKE_HOME" not in os.environ:
    os.environ["GD_REMAKE_HOME"] = tempfile.mkdtemp(prefix="gdhome")
    atexit.register(shutil.rmtree, os.environ["GD_REMAKE_HOME"], True)

from gd import online                                      # noqa: E402
from gd.level import LevelBuilder                          # noqa: E402
from gd.levelio import load_level_file                     # noqa: E402

LIST_REPLY = ("1:10565740:2:Bloodbath:3:%s:5:3:6:503085:8:10:9:50:10:194181551:12:0:13:21:14:5664134:17:1:43:6:"
              "25::18:10:19:3206:42:0:45:24746:15:3|"
              "1:128:2:1st level:3::5:1:6:7:8:0:9:0:10:12:12:0:13:1:14:3:17::43:0:25::18:0:19:0:42:0:45:0:15:0|"
              "1:99:2:Plat:3::5:1:6:7:8:10:9:30:10:5:12:0:13:22:14:1:17::43:0:25::18:4:19:0:42:0:45:900:15:5"
              "#503085:Riot:37415|7:Someone:1#songs#36055:0:10#hash"
              % base64.urlsafe_b64encode(b"Whose blood will be spilt?").decode())


def level_reply(level_id, name, text):
    data = base64.urlsafe_b64encode(gzip.compress(text.encode())).decode()
    return "1:%d:2:%s:3::4:%s:5:1#hash#hash" % (level_id, name, data)


class Replies(unittest.TestCase):
    def setUp(self):
        self.sent = []
        old = online._post
        self.addCleanup(setattr, online, "_post", old)

    def answer(self, reply):
        def fake(path, fields, timeout=online.TIMEOUT):
            self.sent.append((path, fields))
            if isinstance(reply, Exception):
                raise reply
            return reply
        online._post = fake

    def test_a_page_of_levels(self):
        self.answer(LIST_REPLY)
        levels, total = online.browse("trending", page=2)
        self.assertEqual(self.sent, [("/database/getGJLevels21.php", {"type": 3, "page": 2})])
        self.assertEqual(total, 36055)
        bb, first, plat = levels
        self.assertEqual((bb["id"], bb["name"], bb["creator"], bb["difficulty"], bb["stars"], bb["length"]),
                         (10565740, "Bloodbath", "Riot", "Extreme Demon", 10, "Long"))
        self.assertEqual((bb["downloads"], bb["likes"], bb["objects"], bb["featured"]), (194181551, 5664134, 24746, True))
        self.assertEqual(bb["description"], "Whose blood will be spilt?")
        self.assertEqual((first["difficulty"], first["creator"], first["platformer"]), ("Unrated", "Someone", False))
        self.assertEqual((plat["difficulty"], plat["platformer"], plat["length"]), ("Hard", True, "Platformer"))

    def test_search_and_nothing_found(self):
        self.answer("")
        self.assertEqual(online.browse(query="  no such level  "), ([], 0))
        self.assertEqual(self.sent[0][1], {"type": 0, "str": "no such level", "page": 0})

    def test_difficulty_names(self):
        d = online.difficulty
        self.assertEqual(d({"25": "1"}), "Auto")
        self.assertEqual([d({"17": "1", "43": str(k)}) for k in (3, 4, 0, 5, 6)],
                         ["Easy Demon", "Medium Demon", "Hard Demon", "Insane Demon", "Extreme Demon"])
        self.assertEqual([d({"8": "10", "9": str(n)}) for n in (10, 20, 30, 40, 50)],
                         ["Easy", "Normal", "Hard", "Harder", "Insane"])
        self.assertEqual(d({"8": "0", "9": "30"}), "Unrated")

    def test_a_level_is_downloaded_checked_and_cached(self):
        b = LevelBuilder()
        for col in (12, 13):
            b.add(8, 15 + 30 * col, 15)
        b.add(901, 600, 300)                                   # a move trigger: the engine says it lacks it
        b.add(286, 300, 45)                                    # dual: the engine has it now
        self.answer(level_reply(42, "Twin", b.string()))
        folder = os.path.join(os.environ["GD_REMAKE_HOME"], "online-test")
        path, report = online.fetch({"id": 42, "creator": "Kerem"}, folder)
        self.assertEqual(os.path.basename(path), "42.gmd")
        self.assertEqual(load_level_file(path).name, "Twin")
        self.assertEqual(load_level_file(path).level_string, b.string())
        self.assertEqual(report["missing"], ["moving objects (move triggers)"])
        self.assertEqual((report["simulated"], report["objects"]), (3, 4))

    def test_problems_are_explained(self):
        self.answer("1:7:2:Gone")                              # no level data
        with self.assertRaises(online.OnlineError):
            online.download(7)
        self.answer("1:8:2:Wrong id:4:abc")
        with self.assertRaises(online.OnlineError):
            online.download(9)
        with self.assertRaises(online.OnlineError):
            online.download("not a number")
        self.answer("1:5:2:Junk:4:AAAAAAAA")                  # base64 of zeros: not a compressed level
        with self.assertRaises(online.OnlineError):
            online.download(5)

    def test_text_from_the_server_is_cleaned(self):
        self.answer("1:5:2:Bad\x00Name\x07 that is far too long for anything sensible at all:3::6:1:45:10#1:x\x1b[31m:2#s#1:0:10")
        levels, _total = online.browse()
        self.assertEqual(levels[0]["name"], "Bad Name that is far too long for anythi")
        self.assertNotIn("\x1b", levels[0]["creator"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
