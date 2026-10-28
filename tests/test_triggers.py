"""Tests for gd/triggers.py: groups that move, switch off and on, spawn, and teleports."""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gd import triggers as T                           # noqa: E402
from gd.level import LevelBuilder                      # noqa: E402
from gd.sim import Simulation                          # noqa: E402

WALL_COL = 20                                          # x = 615: a wall of blocks the cube cannot jump


def build(extra=None, wall_group=None):
    b = LevelBuilder()
    for row in range(12):
        if wall_group is None:
            b.block(WALL_COL, row)
        else:
            b.add(1, 15 + 30 * WALL_COL, 15 + 30 * row, k57=str(wall_group))
    b.block(80, 11)                                    # the level goes on past the wall (out of the way)
    if extra:
        extra(b)
    return Simulation(b.level())


def run(sim, ticks, pressed=False):
    for _ in range(ticks):
        if sim.dead or sim.finished:
            break
        sim.step(pressed)
    return sim.player


class Easing(unittest.TestCase):
    def test_every_curve_starts_at_0_and_ends_at_1(self):
        for kind in range(19):
            self.assertAlmostEqual(T.ease(kind, 2.0, 0.0), 0.0, delta=0.0005, msg=kind)    # expo in-out: 0.0005
            self.assertAlmostEqual(T.ease(kind, 2.0, 1.0), 1.0, delta=0.0011, msg=kind)   # expo in ends at 0.999

    def test_cocos2d_values(self):
        self.assertAlmostEqual(T.ease(0, 2.0, 0.3), 0.3)
        self.assertAlmostEqual(T.ease(1, 2.0, 0.25), 0.125)               # ease in out: 0.5 * (2t)^rate
        self.assertAlmostEqual(T.ease(2, 3.0, 0.5), 0.125)                # ease in: t^rate
        self.assertAlmostEqual(T.ease(3, 2.0, 0.25), 0.5)                 # ease out: t^(1/rate)
        self.assertAlmostEqual(T.ease(9, 2.0, 0.5), 0.765625)             # bounce out
        self.assertAlmostEqual(T.ease(15, 2.0, 0.5), 0.7071067811865476)  # sine out
        self.assertLess(T.ease(17, 2.0, 0.2), 0.0)                        # back in pulls back first


class Moves(unittest.TestCase):
    def test_without_triggers_the_wall_stops_the_player(self):
        p = run(build(), 1200)
        self.assertTrue(p.dead)
        self.assertLess(p.x, 615)

    def test_a_move_trigger_lifts_the_wall_out_of_the_way(self):
        sim = build(lambda b: b.add(T.MOVE, 150, 200, k51=5, k29=600, k10=0.5), wall_group=5)
        self.assertIsNotNone(sim.trig)
        p = run(sim, 1200)
        self.assertFalse(p.dead, p.death_cause)
        self.assertGreater(p.x, 700)
        self.assertEqual(sim.trig.moved[5][0], 0.0)
        self.assertAlmostEqual(sim.trig.moved[5][1], 600.0, places=9)

    def test_a_timed_move_follows_its_curve_and_ends_exactly(self):
        sim = build(lambda b: b.add(T.MOVE, 0, 200, k51=5, k28=60, k10=1.0, k30=2, k85=2.0), wall_group=5)
        run(sim, 120)                                   # fired on the first step: half of its 240 steps done
        self.assertAlmostEqual(sim.trig.moved[5][0], 60 * T.ease(2, 2.0, 0.5), places=6)
        run(sim, 200)
        self.assertAlmostEqual(sim.trig.moved[5][0], 60.0, places=9)
        self.assertEqual(sim.trig.moves, ())

    def test_an_instant_move_puts_a_block_in_the_way(self):
        def extra(b):
            b.add(1, 315, 615, k57="8")                 # a block high above the path ...
            b.add(T.MOVE, 30, 300, k51=8, k29=-600)     # ... dropped onto it at once
        sim = build(extra)
        p = run(sim, 600)
        self.assertTrue(p.dead)
        self.assertLess(p.x, 320)

    def test_moved_objects_are_found_where_they_are(self):
        sim = build(lambda b: b.add(T.MOVE, 0, 200, k51=5, k29=600), wall_group=5)
        run(sim, 2)
        self.assertEqual([o for o in sim.query(600, 630, 0, 300) if 5 in o.groups], [])
        moved = [o for o in sim.query(600, 630, 600, 900) if 5 in o.groups]
        self.assertEqual(len(moved), 11)                # rows 0..11 moved up by 600: those touching y <= 900
        self.assertTrue(all(o.y >= 615 for o in moved))
        base = [o for o in sim.level.objects if 5 in o.groups]
        self.assertTrue(all(o.y < 360 for o in base), "the level's own objects never move")

    def test_lock_to_player_x(self):
        sim = build(lambda b: b.add(T.MOVE, 0, 200, k51=5, k58=1, k10=2.0), wall_group=5)
        p = run(sim, 240)
        self.assertAlmostEqual(sim.trig.moved[5][0], p.x, delta=2 * p.x_vel)


class Toggles(unittest.TestCase):
    def test_switching_the_wall_off(self):
        sim = build(lambda b: b.add(T.TOGGLE, 300, 200, k51=5, k56=0), wall_group=5)
        p = run(sim, 1200)
        self.assertFalse(p.dead, p.death_cause)
        self.assertGreater(p.x, 700)

    def test_switching_it_back_on(self):
        def extra(b):
            b.add(T.TOGGLE, 100, 200, k51=5, k56=0)
            b.add(T.TOGGLE, 200, 200, k51=5, k56=1)
        p = run(build(extra, wall_group=5), 1200)
        self.assertTrue(p.dead)

    def test_a_trigger_in_a_switched_off_group_does_not_fire(self):
        def extra(b):
            b.add(T.TOGGLE, 100, 200, k51=9, k56=0)                       # switches group 9 off ...
            b.add(T.TOGGLE, 300, 200, k51=5, k56=0, k57="9")              # ... which holds this one
        p = run(build(extra, wall_group=5), 1200)
        self.assertTrue(p.dead)


class Spawns(unittest.TestCase):
    def test_a_spawn_fires_its_group_after_the_delay(self):
        def extra(b):
            b.add(T.SPAWN, 60, 200, k51=9, k63=0.5)
            b.add(T.TOGGLE, 3000, 200, k51=5, k56=0, k57="9", k62=1)     # only ever fired by the spawn
        sim = build(extra, wall_group=5)
        run(sim, 100)
        self.assertEqual(sim.trig.toggles, {}, "not yet")
        run(sim, 120)
        self.assertIn(5, sim.trig.toggles)
        p = run(sim, 1200)
        self.assertFalse(p.dead, p.death_cause)

    def test_a_spawn_loop_cannot_hang_the_game(self):
        def extra(b):
            for x in (60, 90):
                b.add(T.SPAWN, 30, 200, k51=9)                            # starts the loop
                b.add(T.SPAWN, x, 260, k51=9, k57="9", k62=1, k87=1)      # two that spawn each other
        sim = build(extra)
        t = time.time()
        run(sim, 400)
        self.assertLess(time.time() - t, 5.0)
        self.assertLessEqual(len(sim.trig.spawns), T.MAX_WAITING)


class Touch(unittest.TestCase):
    def test_touched_only_when_the_player_meets_it(self):
        def extra(b):
            b.add(T.TOGGLE, 300, 15, k51=5, k56=0, k11=1)                 # on the path
        p = run(build(extra, wall_group=5), 1200)
        self.assertFalse(p.dead, p.death_cause)

        def missed(b):
            b.add(T.TOGGLE, 300, 300, k51=5, k56=0, k11=1)                # far above it
        p = run(build(missed, wall_group=5), 1200)
        self.assertTrue(p.dead)


class FollowStopAlpha(unittest.TestCase):
    def test_a_group_follows_another_ones_moves(self):
        def extra(b):
            b.add(1, 900, 615, k57="6")                                    # what follows (out of the way)
            b.add(T.FOLLOW, 0, 200, k51=6, k71=5, k72=0.5, k73=-1, k10=5)
            b.add(T.MOVE, 30, 230, k51=5, k28=60, k29=90, k10=0.5)
        sim = build(extra, wall_group=5)
        run(sim, 240)
        self.assertAlmostEqual(sim.trig.moved[5][0], 60.0, places=6)
        self.assertAlmostEqual(sim.trig.moved[6][0], 30.0, places=6)
        self.assertAlmostEqual(sim.trig.moved[6][1], -90.0, places=6)

    def test_a_stop_trigger_halts_a_move_and_a_waiting_spawn(self):
        def extra(b):
            b.add(T.MOVE, 0, 200, k51=5, k29=600, k10=2.0, k57="20")       # started by a trigger in group 20
            b.add(T.SPAWN, 0, 260, k51=21, k63=1.0, k57="20")
            b.add(T.TOGGLE, 3000, 300, k51=5, k56=0, k57="21", k62=1)
            b.add(T.STOP, 120, 200, k51=20)
        sim = build(extra, wall_group=5)
        run(sim, 400)
        self.assertEqual(sim.trig.moves, ())
        self.assertEqual(sim.trig.spawns, ())
        self.assertLess(sim.trig.moved[5][1], 600.0 * 0.5, "stopped part of the way up")
        self.assertNotIn(5, sim.trig.toggles, "the spawn never happened")

    def test_alpha_fades_the_drawing_not_the_collision(self):
        def extra(b):
            b.add(T.ALPHA, 0, 200, k51=5, k35=0, k10=0.5)
        sim = build(extra, wall_group=5)
        run(sim, 60)
        k = sim.level.triggers.group_clusters[5][0]
        self.assertAlmostEqual(sim.trig.opacity(5), 1.0 - 60 / 120.0, delta=0.02)
        self.assertFalse(sim.trig.hidden(k))
        run(sim, 100)
        self.assertTrue(sim.trig.hidden(k), "faded out: not drawn")
        p = run(sim, 1200)
        self.assertTrue(p.dead, "but still solid")


class Teleports(unittest.TestCase):
    def test_the_portal_puts_the_player_at_its_exit(self):
        sim = build(lambda b: b.add(747, 300, 45, k54=400))
        while not any(text == "teleport" for _tick, text in sim.log) and sim.tick < 400:
            sim.step(False)
        self.assertAlmostEqual(sim.player.y, 445.0, places=3)
        self.assertLess(sim.player.x, 320)

    def test_the_trigger_sends_the_player_to_its_target(self):
        def extra(b):
            b.add(211, 1215, 315, k57="7")                                 # the target (decoration): past the wall
            b.add(T.TELEPORT, 300, 200, k51=7)
        sim = build(extra)
        p = run(sim, 300)
        self.assertGreater(p.x, 1200)
        p = run(sim, 600)
        self.assertFalse(p.dead, p.death_cause)


class Snapshots(unittest.TestCase):
    def test_restore_brings_back_where_everything_was(self):
        sim = build(lambda b: (b.add(T.MOVE, 0, 200, k51=5, k29=600, k10=2.0),
                               b.add(T.TOGGLE, 100, 200, k51=6, k56=0)), wall_group=5)
        run(sim, 120)
        snap = sim.snapshot()
        moved, toggles = dict(sim.trig.moved), dict(sim.trig.toggles)
        ys = sorted(o.y for o in sim.query(600, 630, -1e9, 1e9))
        run(sim, 240)
        self.assertNotEqual(sim.trig.moved, moved)
        sim.restore(snap)
        self.assertEqual(sim.trig.moved, moved)
        self.assertEqual(sim.trig.toggles, toggles)
        self.assertEqual(sorted(o.y for o in sim.query(600, 630, -1e9, 1e9)), ys)
        sim.reset()
        self.assertEqual(sim.trig.moved, {})

    def test_the_bot_finds_the_way_a_trigger_opens(self):
        from gd.autoplay import solve

        def extra(b):
            b.add(T.TOGGLE, 300, 15, k51=5, k56=0, k11=1)                 # touched on the ground ...
            for col in range(9, 12):
                b.block(col, 0, 8)                                         # ... behind three spikes
        sim = build(extra, wall_group=5)
        seq = solve(sim, chunk=4, max_nodes=200000)
        self.assertIsNotNone(seq)
        sim.reset()
        sim.run(seq)
        self.assertTrue(sim.finished, sim.player.death_cause)


if __name__ == "__main__":
    unittest.main(verbosity=2)
