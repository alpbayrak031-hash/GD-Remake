"""Tests for the level pack, the fairness analysis and the editor's document model."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gd import analysis                                        # noqa: E402
from gd.editor_model import LevelDocument, snap_position      # noqa: E402
from gd.level import Level, LevelBuilder                       # noqa: E402
from gd.levelio import load_level_file                         # noqa: E402
from gd.macros import InputSequence, load_macro                # noqa: E402
from gd.sim import Simulation                                  # noqa: E402

LEVELS = os.path.join(ROOT, "levels")
INDEX = os.path.join(LEVELS, "index.json")
# the coarsest input grid (ms) each difficulty has to tolerate / may tolerate
BANDS = {"easy": (100, 10 ** 6), "medium": (50, 67), "hard": (33, 42), "very hard": (17, 25)}


def hands_off(pad_id, pad_y, spikes, speed=1):
    """Does a cube survive a pad followed by `spikes` spikes without any input?"""
    b = LevelBuilder(speed=speed)
    b.block(120, 0)
    b.add(pad_id, 15 + 30 * 20, pad_y)
    for i in range(spikes):
        b.add(8, 15 + 30 * (22 + i), 15)
    sim = Simulation(b.level())
    for _ in range(1200):
        sim.step(False)
        if sim.dead:
            return False
    return True


class PadsAreHandsOff(unittest.TestCase):
    def test_yellow_pad_clears_three_spikes_not_four(self):
        # the bug report: the test level used to put FOUR spikes after its first pad
        self.assertTrue(hands_off(35, 2, 3))
        self.assertFalse(hands_off(35, 2, 4))

    def test_pack_helper_keeps_a_margin(self):
        from tools.make_level_pack import PAD, pad_spikes
        for kind in ("yellow", "pink", "red"):
            for speed in (1, 2, 3):
                n = pad_spikes(kind, speed)
                oid, y = PAD[kind]
                self.assertTrue(hands_off(oid, y, n, speed), (kind, speed))
                self.assertTrue(hands_off(oid, y, n + 1, speed) or n == 1, "one spike of margin: %s %d" % (kind, speed))

    def test_test_level_pad_needs_no_input(self):
        raw = load_level_file(os.path.join(LEVELS, "test_level.gmd"))
        level = Level(raw)
        pad = next(o for o in level.objects if o.kind == "pad" and o.arg == "yellow")
        after = sorted(o.x for o in level.objects if o.kind == "hazard" and pad.x < o.x < pad.x + 240)
        self.assertEqual(len(after), 3)


@unittest.skipUnless(os.path.exists(INDEX), "run tools/make_level_pack.py first")
class LevelPack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(INDEX) as fh:
            cls.entries = json.load(fh)["levels"]

    def test_has_the_requested_spread(self):
        count = {}
        for e in self.entries:
            count[e["difficulty"]] = count.get(e["difficulty"], 0) + 1
        self.assertGreaterEqual(count.get("easy", 0), 5)
        self.assertGreaterEqual(count.get("medium", 0), 3)
        self.assertGreaterEqual(count.get("hard", 0), 3)
        self.assertGreaterEqual(count.get("very hard", 0), 1)
        self.assertEqual(len({e["name"] for e in self.entries}), len(self.entries))

    def test_every_level_loads_fully_and_its_replay_finishes_it(self):
        for e in self.entries:
            with self.subTest(level=e["name"]):
                level = Level(load_level_file(os.path.join(LEVELS, e["file"])))
                self.assertEqual(sum(level.report.skipped.values()), 0, "every object must be simulated")
                self.assertEqual(level.name, e["name"])
                sim = Simulation(level)
                sim.run(load_macro(os.path.join(LEVELS, e["replay"])))
                self.assertTrue(sim.finished, "died at x=%.0f: %s" % (sim.player.x, sim.player.death_cause))
                self.assertAlmostEqual(sim.time, e["seconds"], delta=0.2)

    def test_measured_difficulty_matches_the_label(self):
        for e in self.entries:
            with self.subTest(level=e["name"]):
                low, high = BANDS[e["difficulty"]]
                self.assertTrue(low <= e["input_grid_ms"] <= high,
                                "%s is labelled %s but tolerates a %d ms grid" % (e["name"], e["difficulty"], e["input_grid_ms"]))

    def test_difficulty_never_decreases_through_the_pack(self):
        order = ["easy", "medium", "hard", "very hard"]
        ranks = [order.index(e["difficulty"]) for e in self.entries]
        self.assertEqual(ranks, sorted(ranks))

    def test_easy_levels_stay_at_normal_speed_and_start_gently(self):
        for e in self.entries:
            if e["difficulty"] != "easy":
                continue
            level = Level(load_level_file(os.path.join(LEVELS, e["file"])))
            self.assertFalse([o for o in level.objects if o.kind == "portal_speed"], e["name"])
            self.assertGreaterEqual(min(o.x for o in level.objects), 300.0, "first obstacle too early in " + e["name"])


class Analysis(unittest.TestCase):
    def level(self, spikes):
        b = LevelBuilder()
        for i in range(spikes):
            b.add(8, 15 + 30 * (20 + i), 15)
        b.block(50, 0)
        return b.level()

    def test_single_spike_is_forgiving_and_quad_is_impossible(self):
        chunk, seq = analysis.rate(self.level(1))
        self.assertEqual(chunk, 32)
        self.assertIsNotNone(seq)
        tight, _ = analysis.rate(self.level(3))
        self.assertIsNotNone(tight)
        self.assertLessEqual(tight, 12)                     # a triple spike: about a 45 ms window
        self.assertEqual(analysis.rate(self.level(4), max_nodes=20000), (None, None))   # 4 at 1x cannot be jumped

    def test_buffered_jumps_do_not_count(self):
        # holding through a landing re-jumps at once; a search can abuse that to hit any
        # window, so the rating forbids it - without the rule this triple rates "easy"
        from gd.autoplay import solve
        level = self.level(3)
        loose = [c for c in (32, 24) if all(solve(Simulation(level), chunk=c, phase=p) for p in (0, c // 3, 2 * c // 3))]
        strict = [c for c in (32, 24) if all(solve(Simulation(level), chunk=c, phase=p, strict=True)
                                             for p in (0, c // 3, 2 * c // 3))]
        self.assertTrue(loose)
        self.assertFalse(strict)

    def test_windows_and_centring(self):
        level = self.level(1)
        _, seq = analysis.rate(level)
        an = analysis.Analyzer(level, seq)
        self.assertTrue(an.completes)
        centred = an.centre()
        self.assertTrue(an.completes)
        rep = an.report()
        self.assertGreaterEqual(rep["presses"], 1)
        self.assertGreater(rep["press_window_ms"], 100)     # one spike at 1x: a wide window
        sim = Simulation(level)
        sim.run(centred)
        self.assertTrue(sim.finished)

    def test_analyzer_agrees_with_plain_simulation(self):
        level = self.level(2)
        _, seq = analysis.rate(level)
        an = analysis.Analyzer(level, seq)
        for shift in (-40, -8, 0, 8, 40):
            trial = [(t + shift, d) for t, d in an.events[:2]] + an.events[2:]
            ticks = [t for t, _ in an.events[:2]] + [t for t, _ in trial[:2]]
            sim = Simulation(level)
            sim.run(InputSequence(trial))
            self.assertEqual(an._try(trial, min(ticks), max(ticks)), sim.finished, shift)


class EditorModel(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gdedit")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_snapping(self):
        self.assertEqual(snap_position(1, 100, 20)[:2], (105.0, 15.0))
        self.assertEqual(snap_position(1, 100, 20, half=True)[:2], (105.0, 15.0))
        self.assertEqual(snap_position(1, 93, 37, half=True)[:2], (90.0, 30.0))
        self.assertEqual(snap_position(35, 100, 20)[:2], (105.0, 2.0))             # pad rests on the cell floor
        self.assertEqual(snap_position(35, 100, 140, rot=180)[:2], (105.0, 148.0))  # ...or hangs from its ceiling
        self.assertEqual(snap_position(39, 100, 20)[:2], (105.0, 7.0))             # small spike
        self.assertEqual(snap_position(291, 100, 20)[:2], (120.0, 15.0))           # long slope spans two cells
        self.assertEqual(snap_position(1, 101.4, 52, step=7.5)[:2], (105.0, 52.5))  # the finer grids
        self.assertEqual(snap_position(1, 101.4, 52, step=3.75)[:2], (101.25, 52.5))

    def test_place_erase_undo_redo(self):
        doc = LevelDocument("t")
        self.assertIsNotNone(doc.place_in_cell(1, 3, 0))
        self.assertIsNone(doc.place_in_cell(1, 3, 0), "no exact duplicates")
        doc.place_in_cell(8, 5, 0)
        doc.place_in_cell(35, 7, 0)
        self.assertEqual(doc.pick(105, 10).id, 1)
        self.assertEqual(doc.pick(225, 1).id, 35)                # thin pad still pickable
        self.assertIsNone(doc.pick(500, 500))
        self.assertTrue(doc.erase(doc.pick(165, 15)))
        self.assertEqual([o.id for o in doc.objects], [1, 35])
        self.assertTrue(doc.undo())
        self.assertEqual([o.id for o in doc.objects], [1, 8, 35])
        self.assertTrue(doc.redo())
        self.assertEqual([o.id for o in doc.objects], [1, 35])
        for _ in range(10):
            doc.undo()
        self.assertEqual(doc.objects, [])
        self.assertFalse(doc.undo())

    def test_save_and_reload(self):
        doc = LevelDocument("My Level")
        doc.set_start(mode=1, speed=2, mini=True, flipped=True)
        doc.place_in_cell(1, 3, 0)
        doc.place_in_cell(8, 5, 4, rot=180)
        doc.place_in_cell(289, 9, 0, flip_x=True)
        path = os.path.join(self.dir, "mine.gmd")
        doc.save(path)
        self.assertFalse(doc.dirty)
        again = LevelDocument.load(path)
        self.assertEqual(again.name, "My Level")
        self.assertEqual(again.get_start(), {"mode": 1, "speed": 2, "mini": True, "flipped": True})
        self.assertEqual(again.to_string(), doc.to_string())
        level = Level(load_level_file(path))
        self.assertEqual((level.start_mode, level.start_speed, level.start_mini, level.start_flipped), (1, 2, True, True))
        self.assertEqual([(o.id, o.rot) for o in level.objects], [(1, 0.0), (8, 180.0), (289, 0.0)])
        self.assertEqual(level.objects[2].orient, 1)             # flipped slope runs downhill

    def test_objects_the_engine_cannot_simulate_survive_a_round_trip(self):
        source = "kA2,0,kA4,0;1,1,2,15,3,15;1,901,2,0,3,0,51,7,28,30,10,0.5;1,1007,2,45,3,60,57,1.2;"
        doc = LevelDocument.from_string(source, "x")
        self.assertEqual(doc.counts(), (1, 2))
        doc.place_in_cell(8, 4, 0)
        out = doc.to_string()
        self.assertIn("1,901,2,0,3,0,10,0.5,28,30,51,7", out)
        self.assertIn("1,1007,2,45,3,60,57,1.2", out)
        self.assertEqual(len(Level(out).objects), 2)

    def test_real_pack_level_opens_in_the_editor_model(self):
        path = os.path.join(LEVELS, "test_level.gmd")
        doc = LevelDocument.load(path)
        self.assertEqual(doc.counts()[1], 0)
        self.assertEqual(len(doc.to_level().objects), len(doc.objects))


if __name__ == "__main__":
    unittest.main(verbosity=2)
