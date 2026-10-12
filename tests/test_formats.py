"""Tests for the level and macro importers."""
import base64
import gzip
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gd import levelio, macros                                  # noqa: E402
from gd.level import Level, LevelBuilder                        # noqa: E402
from gd.levelio import (LevelFormatError, decode_level_string, encode_level_string,   # noqa: E402
                        list_levels, load_level_file, parse_plist)
from gd.macros import InputSequence, MacroFormatError, load_macro, write_gdr2          # noqa: E402

RAW = LevelBuilder(mode=1, speed=2, mini=True, flipped=True).block(3, 0).add(8, 315, 15).string()
BARE = "<d><k>kCEK</k><i>4</i><k>k2</k><s>Bare &amp; Co</s><k>k4</k><s>%s</s><k>k5</k><s>me</s></d>"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gdfmt")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)
        return path

    def check(self, path, name=None):
        raw = load_level_file(path)
        self.assertEqual(raw.level_string, RAW)
        if name:
            self.assertEqual(raw.name, name)
        return raw


class LevelString(Base):
    def test_round_trip(self):
        enc = encode_level_string(RAW)
        self.assertTrue(enc.startswith("H4sI"))
        self.assertNotIn("+", enc)
        self.assertNotIn("/", enc)
        self.assertEqual(decode_level_string(enc), RAW)
        self.assertEqual(decode_level_string(RAW), RAW)                 # already decoded
        self.assertEqual(decode_level_string(enc.rstrip("=")), RAW)     # missing padding

    def test_zlib_and_raw_deflate_variants(self):
        z = base64.urlsafe_b64encode(zlib.compress(RAW.encode())).decode()
        self.assertEqual(decode_level_string(z), RAW)
        co = zlib.compressobj(wbits=-15)
        d = base64.urlsafe_b64encode(co.compress(RAW.encode()) + co.flush()).decode()
        self.assertEqual(decode_level_string(d), RAW)

    def test_garbage_is_rejected(self):
        with self.assertRaises(LevelFormatError):
            decode_level_string("!!!! not a level !!!!")

    def test_header_and_objects(self):
        lv = Level(RAW)
        self.assertEqual((lv.start_mode, lv.start_speed, lv.start_mini, lv.start_flipped), (1, 2, True, True))
        self.assertEqual([o.id for o in lv.objects], [1, 8])
        self.assertEqual((lv.objects[0].x, lv.objects[0].y), (105.0, 15.0))

    def test_speed_header_mapping(self):
        # the game stores 1x as 0 and 0.5x as 1
        for code, index in ((0, 1), (1, 0), (2, 2), (3, 3), (4, 4)):
            self.assertEqual(Level("kA4,%d;1,1,2,15,3,15;" % code).start_speed, index)

    def test_rotation_scale_and_groups(self):
        lv = Level("kA2,0;1,8,2,100,3,15,6,90;1,8,2,200,3,15,32,2;1,8,2,300,3,15,6,45;"
                   "1,1,2,400,3,15,57,3.14.15;1,8,2,500,3,15,128,3,129,0.5;")
        a, b, c, d, e = lv.objects
        self.assertEqual((a.w, a.h), (12.0, 6.0))                # quarter turn swaps the 6 x 12 spike box
        self.assertEqual((b.w, b.h), (12.0, 24.0))
        self.assertIsNotNone(c.corners)                          # 45 degrees -> oriented box
        self.assertAlmostEqual(c.maxx - c.minx, (6 + 12) / 2 ** 0.5, places=6)
        self.assertEqual(d.groups, (3, 14, 15))
        self.assertEqual((e.w, e.h), (18.0, 6.0))

    def test_unknown_ids_are_skipped_and_reported(self):
        lv = Level("kA2,0;1,1,2,15,3,15;1,901,2,0,3,0;1,901,2,5,3,0;1,99999,2,0,3,0;1,45,2,0,3,0;")
        self.assertEqual(len(lv.objects), 1)
        self.assertEqual(lv.report.total, 5)
        self.assertEqual(dict(lv.report.skipped), {901: 2, 99999: 1})
        text = lv.report.summary()
        self.assertIn("901 (move trigger) x2", text)
        self.assertIn("99999 x1", text)
        self.assertIn("mirror portal", text)

    def test_no_touch_and_passable_flags(self):
        lv = Level("kA2,0;1,1,2,15,3,15,121,1;1,1,2,45,3,15,134,1;1,1,2,75,3,15;")
        self.assertEqual([(o.x, o.passable) for o in lv.objects], [(45.0, True), (75.0, False)])
        self.assertIn("No Touch", lv.report.summary())

    def test_slope_orientation(self):
        lv = Level("kA2,0;1,289,2,15,3,15;1,289,2,15,3,15,6,90;1,289,2,15,3,15,4,1;"
                   "1,289,2,15,3,15,5,1;1,289,2,15,3,15,4,1,5,1;1,291,2,30,3,15,6,90;")
        self.assertEqual([o.orient for o in lv.objects], [0, 1, 1, 3, 2, 1])
        self.assertEqual((lv.objects[5].w, lv.objects[5].h), (30.0, 60.0))


class Containers(Base):
    def test_gmd_full_plist(self):
        path = self.write("a.gmd", levelio.gmd_document("Full Plist", RAW))
        self.check(path, "Full Plist")

    def test_gmd_bare_dictionary(self):
        path = self.write("b.gmd", BARE % encode_level_string(RAW))
        raw = self.check(path, "Bare & Co")
        self.assertEqual(raw.meta["creator"], "me")

    def test_gmd_long_tag_names(self):
        doc = ("<?xml version=\"1.0\"?><plist version=\"1.0\"><dict><key>k2</key><string>Long</string>"
               "<key>k13</key><true/><key>k4</key><string>%s</string></dict></plist>" % encode_level_string(RAW))
        self.check(self.write("c.gmd", doc), "Long")

    def test_gmd2_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("level.meta", json.dumps({"compression": "none"}))
            zf.writestr("song.mp3", b"\x00" * 64)
            zf.writestr("level.data", BARE % encode_level_string(RAW))
        self.check(self.write("d.gmd2", buf.getvalue()), "Bare & Co")

    def test_lvl_compressed(self):
        body = (BARE % encode_level_string(RAW)).encode()
        self.check(self.write("e.lvl", zlib.compress(body)))
        self.check(self.write("f.lvl", gzip.compress(body)))

    def test_bare_level_strings(self):
        self.check(self.write("g.txt", encode_level_string(RAW) + "\n"))
        self.check(self.write("h.txt", RAW))

    def test_json_and_server_reply(self):
        self.check(self.write("i.json", json.dumps({"name": "J", "id": 5, "data": encode_level_string(RAW)})), "J")
        reply = "1:4284013:2:Server Level:5:3:6:1:4:%s:13:21#hash#hash" % encode_level_string(RAW)
        raw = self.check(self.write("j.txt", reply), "Server Level")
        self.assertEqual(raw.meta["id"], "4284013")

    def _save_plist(self):
        level = "<k>k2</k><s>%s</s><k>k4</k><s>%s</s><k>k5</k><s>x</s>"
        other = LevelBuilder().block(1, 0).string()
        return ("<?xml version=\"1.0\"?><plist version=\"1.0\" gjver=\"2.0\"><dict><k>LLM_01</k><d>"
                "<k>_isArr</k><t /><k>k_0</k><d><k>kCEK</k><i>4</i>" + level % ("First", encode_level_string(other)) +
                "<k>kI6</k><d><k>1</k><s>0</s></d></d><k>k_1</k><d><k>kCEK</k><i>4</i>" +
                level % ("Second One", encode_level_string(RAW)) + "</d></d><k>LLM_02</k><i>35</i></dict></plist>")

    def test_windows_save_file(self):
        packed = base64.urlsafe_b64encode(gzip.compress(self._save_plist().encode()))
        path = self.write("CCLocalLevels.dat", bytes(b ^ 11 for b in packed))
        self.assertEqual([lv.name for lv in list_levels(path)], ["First", "Second One"])
        self.assertEqual(load_level_file(path, 1).level_string, RAW)
        self.assertEqual(load_level_file(path, "second").level_string, RAW)
        with self.assertRaises(LevelFormatError):
            load_level_file(path, 7)
        with self.assertRaises(LevelFormatError):
            load_level_file(path, "nope")

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_macos_save_file(self):
        plain = self._save_plist().encode()
        pad = 16 - len(plain) % 16
        enc = subprocess.run(["openssl", "enc", "-aes-256-ecb", "-nopad", "-K", levelio._MAC_SAVE_KEY.hex()],
                             input=plain + bytes([pad]) * pad, capture_output=True, check=True).stdout
        path = self.write("CCLocalLevels.dat", enc)
        self.assertEqual([lv.name for lv in list_levels(path)], ["First", "Second One"])

    def test_not_a_level(self):
        with self.assertRaises(LevelFormatError):
            list_levels(self.write("x.bin", os.urandom(333)))
        with self.assertRaises(LevelFormatError) as ctx:
            list_levels(self.write("m.gdr2", b"GDR\x02\x00"))
        self.assertIn("macro", str(ctx.exception))

    def test_plist_parser(self):
        d = parse_plist("<d><k>a</k><i>3</i><k>b</k><r>1.5</r><k>c</k><t /><k>d</k><d><k>e</k><s>x &lt; y</s></d>"
                        "<k>f</k><s></s></d>")
        self.assertEqual(d["_0"], {"a": 3, "b": 1.5, "c": True, "d": {"e": "x < y"}, "f": ""})


class Macros(Base):
    SEQ = InputSequence([(10, True), (25, False), (300, True), (301, False), (4000, True), (4100, False)])

    def test_input_sequence(self):
        s = InputSequence([(5, True), (5, True), (9, False), (20, True)])
        self.assertEqual(s.events, [(5, True), (9, False), (20, True)])
        self.assertEqual([s.pressed_at(t) for t in (0, 5, 8, 9, 19, 20)], [False, True, True, False, False, True])
        it = s.iter_states()
        self.assertEqual([next(it) for _ in range(10)], [False] * 5 + [True] * 4 + [False])

    def test_gdr2_round_trip(self):
        path = os.path.join(self.dir, "run.gdr2")
        write_gdr2(path, self.SEQ, "Level")
        got = load_macro(path)
        self.assertEqual(got.events, self.SEQ.events)
        self.assertEqual(got.meta["format"], "gdr2")
        self.assertEqual(got.meta["levelName"], "Level")
        self.assertEqual(got.meta["framerate"], 240.0)
        self.assertEqual(load_macro(path, offset=2).events[0], (12, True))

    def test_gdr2_reference_layout(self):
        # built byte by byte from gdr.hpp: 480 fps, platformer, tagged inputs, one player-2 event
        def s(text):
            return bytes([len(text)]) + text.encode()
        blob = (b"GDR" + b"\x02" + s("tag") + s("me") + s("") + struct.pack(">f", 1.5) + b"\xba\xac\x01" +
                struct.pack(">d", 480.0) + b"\x00\x00\x00\x01" + s("bot") + b"\x03" + b"\x80\x01" + s("lvl") +
                b"\x02\xaa\xbb" +              # replay extension (2 bytes, skipped)
                b"\x02\x05\x03" +              # two deaths at frames 5 and 8
                b"\x04\x03")                   # 4 inputs, 3 for player 1
        def packed(delta, button, down):
            return bytes([(delta << 3) | (button << 1) | int(down)])
        blob += packed(4, 1, True) + b"\x01\x00"       # frame 4 jump down   (+1 byte input extension)
        blob += packed(2, 2, True) + b"\x00"           # frame 6 "left" down -> dropped
        blob += packed(6, 1, False) + b"\x00"          # frame 12 jump up
        blob += packed(9, 1, True) + b"\x00"           # player 2, frame 9   -> dropped
        got = load_macro(self.write("p.gdr2", blob))
        self.assertEqual(got.events, [(2, True), (6, False)])          # 480 fps frames -> 240 Hz ticks
        self.assertEqual(got.meta["deaths"], [5, 8])
        self.assertEqual(got.meta["gameVersion"], 22074)
        self.assertEqual(got.meta["levelID"], 128)
        self.assertEqual(got.meta["dropped_events"], 2)
        self.assertTrue(got.meta["platformer"])

    def test_gdr2_truncated(self):
        with self.assertRaises(MacroFormatError):
            load_macro(self.write("t.gdr2", b"GDR\x02\x05ab"))

    GDR1 = {"gameVersion": 2.204, "author": "a", "framerate": 60.0, "bot": {"name": "b", "version": "1"},
            "inputs": [{"frame": 3, "btn": 1, "2p": False, "down": True},
                       {"frame": 5, "btn": 1, "2p": True, "down": True},
                       {"frame": 9, "btn": 1, "2p": False, "down": False}]}

    def test_gdr1_json(self):
        got = load_macro(self.write("a.gdr.json", json.dumps(self.GDR1)))
        self.assertEqual(got.events, [(12, True), (36, False)])        # 60 fps -> x4
        self.assertEqual(got.meta["dropped_events"], 1)

    def test_gdr1_msgpack(self):
        def pack(v):
            if isinstance(v, bool):
                return b"\xc3" if v else b"\xc2"
            if isinstance(v, int):
                return bytes([v]) if 0 <= v < 128 else b"\xcd" + struct.pack(">H", v)
            if isinstance(v, float):
                return b"\xcb" + struct.pack(">d", v)
            if isinstance(v, str):
                b = v.encode()
                return bytes([0xA0 | len(b)]) + b
            if isinstance(v, list):
                return bytes([0x90 | len(v)]) + b"".join(pack(x) for x in v)
            if isinstance(v, dict):
                return bytes([0x80 | len(v)]) + b"".join(pack(k) + pack(x) for k, x in v.items())
            raise TypeError(v)
        got = load_macro(self.write("a.gdr", pack(self.GDR1)))
        self.assertEqual(got.events, [(12, True), (36, False)])
        self.assertEqual(got.meta["format"], "gdr1")

    def test_other_json_macros(self):
        mhr = {"_": "Mega Hack v7.1 Replay", "meta": {"fps": 240},
               "events": [{"frame": 7, "down": True, "x": 1.0}, {"frame": 8, "x": 2.0},
                          {"frame": 9, "down": False}, {"frame": 9, "down": True, "p2": True}]}
        self.assertEqual(load_macro(self.write("m.mhr.json", json.dumps(mhr))).events, [(7, True), (9, False)])
        tas = {"fps": 120, "macro": [{"frame": 2, "player_1": {"click": 1}, "player_2": {"click": 0}},
                                     {"frame": 6, "player_1": {"click": 2}, "player_2": {"click": 0}}]}
        self.assertEqual(load_macro(self.write("t.json", json.dumps(tas))).events, [(4, True), (12, False)])
        echo = {"FPS": 240, "Echo Replay": [{"Frame": 1, "Hold": True, "Player 2": False},
                                            {"Frame": 4, "Hold": False, "Player 2": False}]}
        self.assertEqual(load_macro(self.write("e.echo", json.dumps(echo))).events, [(1, True), (4, False)])

    def test_plain_text_and_own_format(self):
        got = load_macro(self.write("p.txt", "60\n# comment\n2 1 0\n5 0 0\n7 1 1\n"))
        self.assertEqual(got.events, [(8, True), (20, False)])
        path = os.path.join(self.dir, "own.json")
        self.SEQ.save(path)
        self.assertEqual(load_macro(path).events, self.SEQ.events)

    def test_unknown_macro(self):
        with self.assertRaises(MacroFormatError):
            load_macro(self.write("u.json", json.dumps({"hello": 1})))
        with self.assertRaises(MacroFormatError):
            load_macro(self.write("u.bin", b"\x00\x01\xff\xfe" * 9))


if __name__ == "__main__":
    unittest.main(verbosity=2)
