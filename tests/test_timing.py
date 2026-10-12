"""Tests for the "tightest jump" measurement shown on the My Levels screen."""
import atexit
import json
import os
import shutil
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

from gd import timing                                      # noqa: E402
from gd.analysis import rate                               # noqa: E402
from gd.autoplay import solve                              # noqa: E402
from gd.level import LevelBuilder                          # noqa: E402
from gd.levelio import write_gmd                           # noqa: E402
from gd.sim import Simulation                              # noqa: E402

SPIKE, BLOCK, ORB = 8, 1, 36


def built(build=None):
    b = LevelBuilder()
    b.add(ORB, 900, 200)             # out of reach: only there to give the level some length
    if build:
        build(b)
    return b


def spikes(n, column=12):
    return lambda b: [b.add(SPIKE, 15 + 30 * (column + i), 15) for i in range(n)]


def wall(b):
    for row in range(12):
        b.block(15, row)


def needle():
    """A level a player reported the bot could not solve although it can be done: at
    half speed, a small spike on the floor and, one column on, a small spike hanging
    from a pillar.  Exactly one of the 240 steps in a second works for the jump."""
    b = LevelBuilder()
    b.add(200, 15, 45)                               # 0.5x speed portal, touched at the start
    b.add(39, 225, 7)                                # small spike on the floor
    for row in range(2, 6):
        b.block(8, row)                              # the pillar
    b.add(39, 255, 53, rot=180)                      # small spike under it
    return b.level("needle")


class Measure(unittest.TestCase):
    def test_a_level_with_nothing_to_do_needs_no_input(self):
        self.assertEqual(timing.measure(built().level()), {"state": "free"})

    def test_one_spike_is_as_forgiving_as_it_gets(self):
        r = timing.measure(built(spikes(1)).level())
        self.assertEqual((r["state"], r["ticks"], r["capped"]), ("ok", timing.CHUNKS[0], True))
        self.assertEqual(timing.describe(r), ("200+ ms", "easy"))

    def test_more_spikes_in_a_row_are_tighter(self):
        two = timing.measure(built(spikes(2)).level())
        three = timing.measure(built(spikes(3)).level())
        self.assertEqual((two["state"], three["state"]), ("ok", "ok"))
        self.assertLess(three["ms"], two["ms"])
        # a triple spike leaves roughly 50 ms: the level pack's "medium" (gd/analysis.py)
        self.assertTrue(40 <= three["ms"] <= 67, three)
        self.assertEqual(three["ms"], timing.to_ms(three["ticks"]))

    def test_an_impossible_level_is_reported_not_guessed(self):
        r = timing.measure(built(wall).level())
        self.assertEqual(r, {"state": "unsolved", "reason": "search"})
        self.assertEqual(timing.describe(r), ("no way through found", None))

    def test_a_jump_that_works_on_one_physics_step_only_is_found(self):
        level = needle()
        result, inputs = timing.search(level)
        self.assertEqual(result, {"state": "ok", "ticks": 1, "ms": 4, "capped": False})
        self.assertEqual(timing.describe(result), ("~4 ms", "very hard"))
        presses = [tick for tick, down in inputs.events if down]
        self.assertEqual(len(presses), 1)
        sim = Simulation(level).run(inputs)
        self.assertTrue(sim.finished, sim.player.death_cause)
        for shift in (-1, 1):                         # one step earlier or later fails: it really is that tight
            moved = type(inputs)([(tick + shift, down) for tick, down in inputs.events], {})
            self.assertTrue(Simulation(level).run(moved).dead)
        # and this is why a grid of 2 ticks (8 ms) or more cannot find it
        self.assertEqual(rate(level, chunks=(6, 3, 2)), (None, None))

    def test_a_level_that_needs_a_held_jump_still_gets_a_number(self):
        # Reported by a player (it used to read "needs a held jump"): at half speed, a saw
        # with two small spikes on it, a spike and a second saw.  The second jump has to
        # start on the very step the cube lands, which only a held button does exactly.
        b = LevelBuilder()
        b.add(200, 15, 45)
        b.add(89, 240, 15)
        b.add(39, 240, 37)
        b.add(39, 255, 37)
        b.add(SPIKE, 270, 15)
        b.add(89, 315, 75, rot=180)
        level = b.level("held")
        self.assertEqual(rate(level, chunks=(1,)), (None, None), "taps alone: no route, even step by step")
        result, inputs = timing.search(level)
        self.assertEqual(result, {"state": "ok", "ticks": 2, "ms": 8, "capped": False, "hold": True})
        self.assertEqual(timing.describe(result), ("~8 ms", "very hard"))
        sim = Simulation(level).run(inputs)
        self.assertTrue(sim.finished, sim.player.death_cause)
        self.assertEqual([text for _tick, text in sim.log if "pushed" in text], [], "no glitch involved")

    def step_level(self, spikes_overhead):
        """A ball, a one-block step ahead and a roof.  With spikes under the roof the only
        way on is the push out of a block that follows a gravity switch."""
        b = LevelBuilder(mode=2)
        b.blocks(5, 13, 0)
        b.blocks(0, 19, 8)
        if spikes_overhead:
            for col in range(20):
                b.add(SPIKE, 15 + 30 * col, 225, rot=180)
        return b.level()

    def test_the_bot_does_not_climb_a_step_with_the_gravity_flip_push(self):
        level = self.step_level(spikes_overhead=False)        # the roof is a way round
        result, inputs = timing.search(level)
        self.assertEqual(result["state"], "ok")
        sim = Simulation(level).run(inputs)
        self.assertTrue(sim.finished, sim.player.death_cause)
        self.assertEqual([text for _tick, text in sim.log if "pushed" in text], [])

    def test_the_glitch_is_the_last_resort_and_then_it_is_used(self):
        level = self.step_level(spikes_overhead=True)
        result, inputs = timing.search(level)
        self.assertEqual(result, {"state": "glitch"})
        self.assertEqual(timing.describe(result), ("glitch route only", None))
        sim = Simulation(level).run(inputs)                   # the inputs it hands back do work
        self.assertTrue(sim.finished, sim.player.death_cause)
        self.assertTrue(any("pushed" in text for _tick, text in sim.log))
        # every search before the last one had it switched off: no route without it exists
        for strict in (True, False):
            self.assertIsNone(solve(Simulation(level), chunk=1, strict=strict, allow_push=False))

    def test_without_the_push_only_held_jumps_are_allowed_extra(self):
        level = self.step_level(spikes_overhead=True)
        sim = Simulation(level)
        solve(sim, chunk=1, max_nodes=50, allow_push=False)
        self.assertEqual((sim.player.strict_taps, sim.player.no_flip_push), (False, True))
        solve(sim, chunk=1, max_nodes=50, strict=True)
        self.assertEqual((sim.player.strict_taps, sim.player.no_flip_push), (True, False))

    def test_the_time_budget_ends_a_search(self):
        r = timing.measure(built(wall).level(), budget=0.0)
        self.assertEqual(r, {"state": "unsolved", "reason": "time"})

    def test_wait_can_abandon_a_search(self):
        def wait():
            raise timing.Cancelled()
        with self.assertRaises(timing.Cancelled):
            timing.measure(built(wall).level(), wait=wait)

    def test_words_and_bands(self):
        self.assertEqual([timing.to_ms(t) for t in (48, 24, 12, 4, 2, 1)], [200, 100, 50, 17, 8, 4])
        self.assertEqual(timing.CHUNKS[-1], 1, "the search must end at single physics steps")
        self.assertEqual(timing.describe({"state": "glitch"}), ("glitch route only", None))
        self.assertEqual(timing.describe({"state": "ok", "ticks": 2, "ms": 8, "capped": False, "hold": True}),
                         ("~8 ms", "very hard"))
        self.assertEqual([timing.band(ms) for ms in (200, 100, 99, 50, 49, 30, 29, 8)],
                         ["easy", "easy", "medium", "medium", "hard", "hard", "very hard", "very hard"])
        self.assertEqual(timing.describe(None), ("measuring...", None))
        self.assertEqual(timing.describe({"state": "ok", "ticks": 20, "ms": 83, "capped": False}), ("~83 ms", "medium"))
        self.assertEqual(timing.describe({"state": "free"}), ("no input needed", "easy"))
        self.assertEqual(timing.CHUNKS, tuple(sorted(timing.CHUNKS, reverse=True)), "coarsest first")


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gdtiming")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cache = os.path.join(self.dir, "save", "timing.json")

    def level(self, name, build=None):
        path = os.path.join(self.dir, name + ".gmd")
        write_gmd(path, name, built(build).string())
        return path

    def wait_for(self, measurer, version, seconds=240.0):      # generous: a busy Mac searches several times slower
        end = time.time() + seconds
        while measurer.version < version and time.time() < end:
            time.sleep(0.01)
        self.assertGreaterEqual(measurer.version, version, "the measurer never finished")


class Cache(Files):
    def test_a_result_belongs_to_the_exact_file_contents(self):
        path = self.level("a", spikes(3))
        t = timing.Timings(self.cache)
        self.assertIsNone(t.lookup(path))
        self.assertFalse(os.path.exists(self.cache), "nothing is written until something is measured")
        t.store(path, t.digest(path), {"state": "ok", "ticks": 12, "ms": 50, "capped": False})
        self.assertEqual(timing.Timings(self.cache).lookup(path)["ms"], 50)      # survives a restart
        write_gmd(path, "a", built(spikes(2)).string())                          # the level is edited
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertIsNone(timing.Timings(self.cache).lookup(path))
        self.assertIsNone(t.lookup(path))
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.cache)) if n.endswith(".tmp")], [])

    def test_a_damaged_or_outdated_cache_is_ignored(self):
        path = self.level("a")
        os.makedirs(os.path.dirname(self.cache))
        for text in ("{ not json", "[]", json.dumps({"version": timing.VERSION + 1, "levels": {
                "a.gmd": {"sha1": timing.file_digest(path), "result": {"state": "free"}}}})):
            with open(self.cache, "w") as fh:
                fh.write(text)
            self.assertIsNone(timing.Timings(self.cache).lookup(path))

    def test_a_missing_file_has_no_result(self):
        t = timing.Timings(self.cache)
        self.assertIsNone(t.digest(os.path.join(self.dir, "gone.gmd")))
        self.assertIsNone(t.lookup(os.path.join(self.dir, "gone.gmd")))


class Background(Files):
    def measurer(self):
        m = timing.Measurer(timing.Timings(self.cache))
        self.addCleanup(m.stop)
        return m

    def test_levels_are_measured_and_remembered(self):
        easy, hard, bad = self.level("easy", spikes(1)), self.level("hard", spikes(3)), self.level("bad", wall)
        m = self.measurer()
        self.assertIsNone(m.get(hard))
        m.want([hard, easy, bad])
        self.wait_for(m, 3)
        self.assertEqual(m.get(easy)["ms"], 200)
        self.assertLess(m.get(hard)["ms"], 100)
        self.assertEqual(m.get(bad)["state"], "unsolved")
        self.assertFalse(m.busy)
        again = timing.Measurer(timing.Timings(self.cache))                      # the next start of the game
        self.assertEqual(again.get(hard), m.get(hard))
        again.want([hard, easy, bad])
        self.assertFalse(again.busy, "nothing is measured twice")

    def test_paused_means_no_work(self):
        path = self.level("hard", spikes(3))
        m = self.measurer()
        m.pause()
        m.want([path])
        time.sleep(0.4)
        self.assertEqual((m.version, m.get(path)), (0, None))
        m.resume()
        self.wait_for(m, 1)
        self.assertEqual(m.get(path)["state"], "ok")

    def test_an_unreadable_file_is_not_retried_forever(self):
        path = os.path.join(self.dir, "junk.gmd")
        with open(path, "w") as fh:
            fh.write("this is not a level")
        m = self.measurer()
        m.want([path])
        self.wait_for(m, 1)
        self.assertEqual(m.get(path), {"state": "unreadable"})
        self.assertEqual(timing.describe(m.get(path)), ("", None))

    def test_a_level_that_is_no_longer_wanted_is_dropped(self):
        slow = self.level("slow", wall)
        m = self.measurer()
        m.pause()
        m.want([slow])
        m.want([])                       # the level was deleted, say, before its turn came
        m.resume()
        time.sleep(0.3)
        self.assertIsNone(m.get(slow))
        self.assertFalse(m.busy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
