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

    def test_a_level_is_downloaded_checked_and_cached(self):
        b = LevelBuilder()
        for col in (12, 13):
            b.add(8, 15 + 30 * col, 15)
        b.add(1346, 600, 300)                                  # a rotate trigger: the engine says it lacks it
        b.add(286, 300, 45)                                    # dual: the engine has it now
        self.answer(level_reply(42, "Twin", b.string()))
        folder = os.path.join(os.environ["GD_REMAKE_HOME"], "online-test")
        path, name, report = online.fetch("42", folder)
        self.assertEqual(self.sent, [("/database/downloadGJLevel22.php", {"levelID": 42})])
        self.assertEqual((os.path.basename(path), name), ("42.gmd", "Twin"))
        self.assertEqual(load_level_file(path).name, "Twin")
        self.assertEqual(load_level_file(path).level_string, b.string())
        self.assertEqual(report["missing"], ["rotating objects"])
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
        b = LevelBuilder()
        b.add(8, 45, 15)
        self.answer(level_reply(5, "Bad\x00Name\x07 that is far too long for anything sensible at all", b.string()))
        name, _text = online.download(5)
        self.assertEqual(name, "Bad Name that is far too long for anythi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
