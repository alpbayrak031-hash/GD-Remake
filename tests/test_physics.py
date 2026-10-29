"""Physics regression tests.

Expected numbers are written out from the SOURCES (decompiled constants and
gd-sim's measured tables), not copied from this engine's own output, so a test
failing means the engine drifted away from the reference.

    python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gd import constants as C                          # noqa: E402
from gd.level import LevelBuilder                      # noqa: E402
from gd.macros import InputSequence                    # noqa: E402
from gd.player import Mode, round3                     # noqa: E402
from gd.sim import Simulation                          # noqa: E402

SPIKE, BLOCK = 8, 1
FAR = 400            # a block this many columns away keeps the level from ending early


def make(mode=0, speed=1, mini=False, flipped=False, build=None, header=None):
    b = LevelBuilder(mode=mode, speed=speed, mini=mini, flipped=flipped, extra_header=header)
    b.block(FAR, 0)
    if build:
        build(b)
    return Simulation(b.level())


def steps(sim, n, pressed=False):
    fn = pressed if callable(pressed) else (lambda t: pressed)
    for _ in range(n):
        sim.step(fn(sim.tick))
    return sim.player


def run_until(case, sim, done, pressed=False, limit=3000):
    """Step until done(sim) is true; fail (instead of hanging) if that never happens."""
    for _ in range(limit):
        if done(sim):
            return sim.player
        if sim.dead or sim.finished:
            break
        sim.step(pressed)
    if done(sim):
        return sim.player
    case.fail("condition never met (tick %d, dead=%s %s, finished=%s)"
              % (sim.tick, sim.dead, sim.player.death_cause, sim.finished))


def settle(sim, n=6):
    """Let the player come to rest on the ground."""
    return steps(sim, n)


def airborne(sim, x, y, y_vel=0.0):
    """Put the player in mid-air (used to meet an orb or portal in a known state)."""
    p = sim.player
    p.x = p.last_x = float(x)
    p.y = p.last_y = float(y)
    p.y_vel = y_vel
    p.can_jump = p.on_ground = p.is_jumping = False
    return p


class Constants(unittest.TestCase):
    def test_horizontal_speeds(self):
        # gd-sim Player.hpp player_speeds, units per second (agreement is ~1e-8 relative;
        # the last digits depend on where the game rounds to single precision)
        expected = (251.16007972276924, 311.580093712804, 387.42014039710523,
                    468.0001388338566, 576.00020058307177)
        for i, want in enumerate(expected):
            speed, _, _, mult = C.SPEED_TABLE[i]
            self.assertAlmostEqual(speed * mult * 60.0, want, places=4)

    def test_distance_per_step(self):
        sim = make()
        steps(sim, 240)
        self.assertAlmostEqual(sim.player.x, 311.58, delta=0.01)     # one second at 1x

    def test_velocity_rounding(self):
        # PlayerObject::setYVelocity keeps three decimals, rounding half away from zero
        self.assertEqual(round3(11.180031776), 11.18)
        self.assertEqual(round3(-2.7185), -2.719)
        self.assertEqual(round3(2.9996), 3.0)
        self.assertEqual(round3(7.0), 7.0)


class CubeJump(unittest.TestCase):
    # gd-sim Vehicle.cpp jumpHeights / 54: the velocity a jump starts with
    START = (10.62, 11.18, 11.42, 11.23, 11.23)
    # gravity per step, to three decimals: round(m_gravity * 0.225, 3)
    GRAV_STEP = (0.212, 0.216, 0.215, 0.216, 0.216)

    def test_held_jump_keeps_full_velocity_on_first_step(self):
        for speed in range(5):
            sim = make(speed=speed)
            steps(sim, 2, True)                     # step 1 lands, step 2 jumps (button already held)
            p = sim.player
            self.assertEqual(p.y_vel, self.START[speed], C.SPEED_NAMES[speed])

    def test_fresh_click_loses_one_step_of_gravity(self):
        # gd-sim's measured first-step velocities (units/s): yStart - one gravity step
        want = (562.032, 592.056, 605.07, 594.756, 594.756)
        for speed in range(5):
            sim = make(speed=speed)
            settle(sim)
            p = steps(sim, 1, True)
            self.assertAlmostEqual(p.y_vel * 54.0, want[speed], places=6, msg=C.SPEED_NAMES[speed])

    def test_jump_height_matches_closed_form(self):
        for speed in range(5):
            v0, g = self.START[speed], self.GRAV_STEP[speed]
            # held jump: velocities v0, v0-g, v0-2g ... while positive, each moving v * 0.225
            n = int(v0 / g)
            apex = sum(v0 - g * k for k in range(n + 1)) * 0.225
            sim = make(speed=speed)
            top = 0.0
            for _ in range(200):
                sim.step(True if sim.tick < 4 else False)
                top = max(top, sim.player.y)
            self.assertAlmostEqual(top - 15.0, apex, delta=2e-3, msg=C.SPEED_NAMES[speed])
            if speed == 1:
                self.assertAlmostEqual(apex, 66.3624, places=4)      # 2.21 blocks

    def test_jump_height_1x_in_blocks(self):
        sim = make()
        top = max(steps(sim, 1, sim.tick < 4).y for _ in range(200))
        self.assertTrue(2.1 < (top - 15.0) / 30.0 < 2.25)

    def test_lands_exactly_on_ground_and_air_time(self):
        sim = make()
        settle(sim)
        sim.step(True)
        start = sim.tick
        run_until(self, sim, lambda s: s.player.can_jump)
        ticks = sim.tick - start + 1
        self.assertEqual(sim.player.y, 15.0)
        self.assertEqual(sim.player.y_vel, 0.0)
        # 2 * 10.964 / 0.216 = 101.5 steps of flight
        self.assertIn(ticks, (102, 103))

    def test_mini_jump(self):
        sim2 = make(mini=True)
        settle(sim2, 40)                        # the mini cube first drops 6 units to the ground
        self.assertEqual(sim2.player.y, 9.0)
        p2 = steps(sim2, 1, True)
        # 0.8 * 11.180032 = 8.944 (gd-sim: 482.976 u/s), then one 0.216 gravity step
        self.assertEqual(p2.y_vel, 8.728)
        self.assertEqual(p2.half_w, 9.0)

    def test_terminal_velocity(self):
        sim = make(build=lambda b: b.add(1332, 200, 2))          # red pad: 20 * 0.225 per step
        lowest = min(steps(sim, 1).y_vel for _ in range(700))
        self.assertEqual(lowest, -15.0)

    def test_inner_hitbox_decides_wall_deaths(self):
        # a wall one block high at x = 300..330: the 30-unit cube overlaps it from
        # x = 285 but only dies when its 9-unit inner box reaches it at x = 295.5
        sim = make(build=lambda b: b.add(BLOCK, 315, 15))
        run_until(self, sim, lambda s: s.dead)
        self.assertGreaterEqual(sim.player.x + 4.5, 300.0)
        self.assertLess(sim.player.last_x + 4.5, 300.0)

    def test_passable_block_does_not_kill(self):
        sim = make(build=lambda b: b.add(BLOCK, 315, 15, k134=1))
        steps(sim, 400)
        self.assertFalse(sim.player.dead)

    def test_spike_uses_small_hitbox(self):
        # spike 8 at x = 315: hitbox is 6 wide (312..318), so death comes at x + 15 >= 312
        sim = make(build=lambda b: b.add(SPIKE, 315, 15))
        run_until(self, sim, lambda s: s.dead)
        self.assertGreaterEqual(sim.player.x + 15.0, 312.0)
        self.assertLess(sim.player.last_x + 15.0, 312.0)

    def test_lands_on_block_top(self):
        sim = make(build=lambda b: b.blocks(8, 20, 0))           # platform top at y = 30
        landed = False
        for _ in range(500):
            sim.step(170 <= sim.tick < 175)
            p = sim.player
            if p.x > 300 and p.can_jump:
                landed = True
                self.assertEqual(p.y, 45.0)
                break
        self.assertTrue(landed)
        self.assertFalse(sim.player.dead)


class Orbs(unittest.TestCase):
    def hit(self, orb_id, mode=0, mini=False, press=True, y_vel=0.0):
        sim = make(mode=mode, mini=mini, build=lambda b: b.add(orb_id, 600, 150))
        sim.ceiling = None if mode in (0, 5) else sim.ceiling
        airborne(sim, 598, 150, y_vel)
        sim.step(press)
        return sim.player

    def test_needs_a_click(self):
        p = self.hit(36, press=False)
        self.assertLess(p.y_vel, 0.0)
        self.assertEqual(len(p.used), 0)

    def test_yellow(self):
        p = self.hit(36)
        self.assertEqual(p.y_vel, 11.18)                         # gd-sim 603.72 u/s
        self.assertAlmostEqual(p.y_vel * 54, 603.72, places=6)

    def test_pink(self):
        self.assertAlmostEqual(self.hit(141).y_vel * 54, 434.7, places=6)    # gd-sim table

    def test_red(self):
        self.assertEqual(self.hit(1333).y_vel, round3(11.180032 * 1.38))     # OSGD ringJump

    def test_blue_flips_and_pulls_to_new_floor(self):
        p = self.hit(84)
        self.assertTrue(p.upside_down)
        self.assertAlmostEqual(p.y_vel * 54, 241.488, places=6)  # gd-sim: 241.488 towards the new floor

    def test_green_flips_and_jumps(self):
        p = self.hit(1022)
        self.assertTrue(p.upside_down)
        self.assertEqual(p.y_vel, -11.18)

    def test_black(self):
        self.assertEqual(self.hit(1330).y_vel, -15.0)            # gd-sim -810 u/s

    def test_mini_yellow(self):
        self.assertAlmostEqual(self.hit(36, mini=True).y_vel * 54, 482.976, places=6)

    def test_ball_yellow(self):
        self.assertAlmostEqual(self.hit(36, mode=2).y_vel * 54, 422.604, places=3)   # gd-sim 422.60399

    def test_single_use_and_fresh_click_required(self):
        sim = make(build=lambda b: (b.add(36, 600, 150), b.add(36, 640, 170)))
        airborne(sim, 598, 150)
        sim.step(True)
        self.assertEqual(len(sim.player.used), 1)
        first = sim.player.y_vel
        steps(sim, 25, True)                    # still holding while passing the second orb
        self.assertEqual(len(sim.player.used), 1, "a held button must not fire a second orb")
        self.assertLess(sim.player.y_vel, first)

    def test_ground_jump_consumes_the_click(self):
        # jump from the ground, keep holding, fly through an orb: it must not fire
        ref = make()
        settle(ref)
        steps(ref, 30, True)
        ox, oy = ref.player.x, ref.player.y
        sim = make(build=lambda b: b.add(36, ox, oy))
        settle(sim)
        steps(sim, 45, True)
        self.assertEqual(len(sim.player.used), 0)
        # release and press again just before it: now it fires
        sim = make(build=lambda b: b.add(36, ox, oy))
        settle(sim)
        steps(sim, 45, lambda t: t < 20 or t >= 26)
        self.assertEqual(len(sim.player.used), 1)


class Pads(unittest.TestCase):
    def launch(self, pad_id, mode=0, mini=False, y=2):
        sim = make(mode=mode, mini=mini, build=lambda b: b.add(pad_id, 150, y))
        return run_until(self, sim, lambda s: bool(s.player.used))

    def test_yellow(self):
        self.assertEqual(self.launch(35).y_vel, 16.0)            # gd-sim 864 u/s

    def bounces(self, multi):
        """Blue pads on a floor and a ceiling that follow the player (a move trigger locked
        to its X for 8 blocks): how often a pad sends it the other way."""
        from gd import triggers as T
        b = LevelBuilder()
        b.add(291, 150, 15)                                    # a slope up onto the floor block
        b.add(291, 150, 195, rot=180, flip_x=True)
        extra = {"k99": "1"} if multi else {}
        b.add(1, 195, 15, rot=180, flip_x=True, k57="1")
        b.add(67, 195, 33, k57="1", **extra)
        b.add(67, 195, 177, rot=180, k57="1", **extra)
        b.add(1, 195, 195, rot=180, k57="1")
        b.add(T.MOVE, 210, 105, k10="0.77", k11="1", k51="1", k58="1")
        sim = Simulation(b.level())
        while not sim.dead and not sim.finished and sim.tick < 3000:
            sim.step(False)
        self.assertTrue(sim.finished)
        return sum(1 for _t, text in sim.log if text == "blue pad")

    def test_a_pad_works_once_unless_it_is_multi_activate(self):
        self.assertEqual(self.bounces(False), 2, "floor pad, ceiling pad, then they are spent")
        self.assertGreater(self.bounces(True), 4, "they keep sending it back and forth")

    def test_pink(self):
        self.assertEqual(self.launch(140).y_vel, 10.4)           # gd-sim 561.6 u/s

    def test_red(self):
        self.assertEqual(self.launch(1332).y_vel, 20.0)          # gd-sim 1080 u/s

    def test_mini_yellow(self):
        self.assertEqual(self.launch(35, mini=True).y_vel, 12.8)  # gd-sim 691.2 u/s

    def test_ball_yellow(self):
        self.assertAlmostEqual(self.launch(35, mode=2).y_vel * 54, 518.4, places=4)

    def test_blue_flips_gravity(self):
        p = self.launch(67, y=3)
        self.assertTrue(p.upside_down)
        self.assertAlmostEqual(p.y_vel * 54, 345.6, places=6)    # gd-sim: 345.6 towards the new floor

    def test_yellow_pad_height(self):
        sim = make(build=lambda b: b.add(35, 150, 2))
        top = max(steps(sim, 1).y for _ in range(400))
        # 16 -> 0 in 0.216 steps: sum of (16 - 0.216 k) * 0.225 for k = 1..74
        want = sum(16.0 - 0.216 * k for k in range(1, 75)) * 0.225
        self.assertAlmostEqual(top - 15.0, want, delta=0.01)


class Portals(unittest.TestCase):
    def test_gravity_portal_halves_velocity(self):
        sim = make(build=lambda b: b.add(11, 600, 150))
        airborne(sim, 590, 150, -4.0)
        p = steps(sim, 1)
        self.assertTrue(p.upside_down)
        self.assertAlmostEqual(p.y_vel, (-4.0 - 0.216) * 0.5, places=9)   # OSGD flipGravity
        sim.step(False)
        self.assertGreater(sim.player.y_vel, -2.108)             # gravity now pulls upward

    def test_ship_portal_and_bounds(self):
        for portal_y, floor in ((45, 0.0), (345, 180.0)):
            sim = make(build=lambda b: b.add(13, 600, portal_y))
            airborne(sim, 590, portal_y, 6.0)
            p = steps(sim, 1)
            self.assertEqual(p.mode, Mode.SHIP)
            self.assertAlmostEqual(p.y_vel, (6.0 - 0.216) * 0.5, places=9)
            self.assertEqual((sim.floor, sim.ceiling), (floor, floor + 300.0))   # gd-sim VehiclePortal

    def test_ball_portal_bounds(self):
        sim = make(build=lambda b: b.add(47, 200, 45))
        steps(sim, 200)
        self.assertEqual(sim.player.mode, Mode.BALL)
        self.assertEqual((sim.floor, sim.ceiling), (0.0, 240.0))

    def test_ship_to_ufo_quarters_velocity(self):
        sim = make(mode=1, build=lambda b: b.add(111, 600, 150))
        airborne(sim, 590, 150, 4.0)
        before = sim.player.y_vel
        p = steps(sim, 1)
        self.assertEqual(p.mode, Mode.UFO)
        # one ship step while still rising (0.958199 * 0.225 * 1.2 * 0.4 = 0.1035), then
        # toggleFlyMode(off) and toggleBirdMode(on) each halve it (OSGD)
        self.assertAlmostEqual(p.y_vel, round3(before - 0.1034855) / 4.0, places=9)

    def test_cube_portal_removes_ceiling(self):
        sim = make(mode=1, build=lambda b: b.add(12, 200, 45))
        steps(sim, 200)
        self.assertEqual(sim.player.mode, Mode.CUBE)
        self.assertIsNone(sim.ceiling)

    def test_size_portals(self):
        sim = make(build=lambda b: (b.add(101, 200, 45), b.add(99, 500, 45)))
        steps(sim, 200)
        self.assertTrue(sim.player.mini)
        self.assertEqual(sim.player.half_h, 9.0)
        self.assertEqual(sim.player.y, 9.0)
        steps(sim, 240)
        self.assertFalse(sim.player.mini)
        self.assertEqual(sim.player.y, 15.0)

    def test_speed_portals(self):
        ids = {200: 251.16, 201: 311.58, 202: 387.42, 203: 468.0, 1334: 576.0}
        for oid, units_per_s in ids.items():
            sim = make(speed=0 if oid == 201 else 1, build=lambda b: b.add(oid, 200, 45))
            steps(sim, 260)
            x0 = sim.player.x
            steps(sim, 240)
            self.assertAlmostEqual(sim.player.x - x0, units_per_s, delta=0.02, msg=str(oid))

    def test_portal_is_single_use(self):
        sim = make(build=lambda b: b.add(2926, 200, 45))         # gravity toggle
        steps(sim, 190)
        self.assertTrue(sim.player.upside_down)


class Modes(unittest.TestCase):
    def test_ship_acceleration_and_clamps(self):
        sim = make(mode=1)
        settle(sim)
        p = steps(sim, 1, True)
        self.assertEqual(p.y_vel, 0.108)                 # 0.958199 * 0.225 * 0.5  (gd-sim 1397.05 u/s^2)
        top = max(steps(sim, 1, True).y_vel for _ in range(150))
        self.assertEqual(top, 8.0)                       # gd-sim clamp 432 u/s
        steps(sim, 60, True)                             # now pinned to the ceiling
        self.assertEqual(sim.player.y, 285.0)
        low = min(steps(sim, 1, False).y_vel for _ in range(150))
        self.assertEqual(low, -6.4)                      # gd-sim clamp -345.6 u/s

    def test_mini_ship_clamps(self):
        sim = make(mode=1, mini=True)
        top = max(steps(sim, 1, True).y_vel for _ in range(200))
        self.assertAlmostEqual(top * 54, 508.248, places=6)      # gd-sim
        steps(sim, 120, True)
        low = min(steps(sim, 1, False).y_vel for _ in range(200))
        self.assertAlmostEqual(low * 54, -406.566, places=6)     # gd-sim

    def test_ufo_click(self):
        sim = make(mode=3)
        settle(sim)
        self.assertAlmostEqual(steps(sim, 1, True).y_vel * 54, 371.034, places=6)   # gd-sim
        sim = make(mode=3, mini=True)
        settle(sim, 20)
        self.assertAlmostEqual(steps(sim, 1, True).y_vel * 54, 358.992, places=6)   # gd-sim

    def test_ufo_needs_a_new_click_each_time(self):
        sim = make(mode=3)
        settle(sim)
        steps(sim, 300, True)
        self.assertEqual(sim.player.y, 15.0)             # one hop, then back on the floor

    def test_ball_switch(self):
        sim = make(mode=2)
        settle(sim)
        p = steps(sim, 1, True)
        self.assertTrue(p.upside_down)
        # gd-sim: 181.116 u/s right after the switch (3.354), plus one 0.129 gravity step
        self.assertEqual(p.y_vel, 3.483)
        steps(sim, 400, True)                            # holding does not switch again
        self.assertTrue(sim.player.upside_down)
        self.assertEqual(sim.player.y, 225.0)            # resting on the 240 ceiling

    def test_wave_is_45_degrees_and_mini_is_steeper(self):
        for mini, slope in ((False, 1.0), (True, 2.0)):
            sim = make(mode=4, mini=mini)
            steps(sim, 5, True)
            p = sim.player
            x0, y0 = p.x, p.y
            steps(sim, 20, True)
            self.assertAlmostEqual((p.y - y0) / (p.x - x0), slope, places=4)

    def test_wave_dies_on_blocks(self):
        sim = make(mode=4, build=lambda b: b.add(BLOCK, 315, 15))
        steps(sim, 300)
        self.assertTrue(sim.player.dead)

    def test_robot_tap_and_hold(self):
        heights = {}
        for name, hold in (("tap", 1), ("hold", 400)):
            sim = make(mode=5)
            settle(sim)
            start = sim.tick
            heights[name] = max(steps(sim, 1, sim.tick < start + hold).y for _ in range(400)) - 15.0
        # hold: 67 boosted steps at 5.59 (OSGD: charge += 0.0225 until 1.5), then gravity 0.194/step
        want_hold = 67 * 5.59 * 0.225 + sum(5.59 - 0.194 * k for k in range(1, 29)) * 0.225
        self.assertAlmostEqual(heights["hold"], want_hold, delta=0.05)
        self.assertLess(heights["tap"], 30.0)
        self.assertGreater(heights["hold"], 3 * 30.0)

    def test_robot_boost_does_not_resume(self):
        sim = make(mode=5)
        settle(sim)
        start = sim.tick
        steps(sim, 30, lambda t: t < start + 10 or t >= start + 15)   # tap, release, press again
        self.assertLess(sim.player.y_vel, 5.59)

    def test_spider_teleports_to_ceiling(self):
        sim = make(mode=6)
        settle(sim, 20)
        self.assertEqual(sim.player.y, 13.5)             # 27-unit hitbox (OSGD toggleSpiderMode)
        p = steps(sim, 2, True)
        self.assertTrue(p.upside_down)
        self.assertEqual(p.y, 240.0 - 13.5)

    def test_spider_stops_at_block(self):
        sim = make(mode=6, build=lambda b: b.blocks(0, 30, 4))   # underside at y = 120
        settle(sim, 20)
        p = steps(sim, 2, True)
        self.assertEqual(p.y, 120.0 - 13.5)
        self.assertFalse(p.dead)

    def test_swing_flip(self):
        sim = make(mode=7)
        settle(sim)
        steps(sim, 60, lambda t: t < 8)                  # flip up, drift upwards
        v = sim.player.y_vel
        self.assertGreater(v, 0)
        p = steps(sim, 1, True)                          # flip back
        self.assertFalse(p.upside_down)
        # OSGD: velocity * 0.8, then one step of 0.4 gravity (0.958199 * 0.225 * 0.4 = 0.086)
        self.assertEqual(p.y_vel, round3(round3(v * 0.8) - 0.0862379))

    def test_flipped_gravity_cube_dies_without_ceiling(self):
        sim = make(flipped=True)
        steps(sim, 2000)
        self.assertTrue(sim.player.dead)

    def test_upside_down_coyote_quirk(self):
        # legacy gravity bug (OSGD playerIsFallingBugged): flipped, the cube can still jump
        # for several steps after leaving a ledge; right side up it cannot (gd-sim: < 10 steps)
        def frames_of_coyote(flipped):
            def build(b):
                b.blocks(0, 6, 5)                        # ceiling ledge ending at x = 210
            sim = make(flipped=flipped, build=build if flipped else (lambda b: b.blocks(0, 6, 0)))
            if not flipped:
                sim.player.y = sim.player.last_y = 45.0          # start standing on the ledge
            run_until(self, sim, lambda s: s.player.x - 15.0 > 210.0)
            n = 0
            while sim.player.can_jump and n < 50:
                sim.step(False)
                n += 1
            return n
        self.assertEqual(frames_of_coyote(False), 0)
        self.assertIn(frames_of_coyote(True), range(7, 10))


class Slopes(unittest.TestCase):
    def test_climbs_and_is_launched(self):
        def build(b):
            b.add(289, 315, 15)                          # 45 degree slope, x 300..330
            b.blocks(11, 20, 0)                          # platform continuing at the top
        sim = make(build=build)
        ys = []
        for _ in range(600):
            if sim.player.x >= 420:
                break
            sim.step(False)
            ys.append((sim.player.x, sim.player.y))
            self.assertFalse(sim.player.dead, sim.player.death_cause)
        on_slope = [y for x, y in ys if 300 < x < 318]
        self.assertTrue(all(b > a for a, b in zip(on_slope, on_slope[1:])), "should rise along the slope")
        self.assertGreater(max(y for _, y in ys), 46.0, "should be thrown above the platform")
        self.assertTrue(any("launched" in text for _, text in sim.log))

    def test_slope_spikes_kill(self):
        sim = make(build=lambda b: b.add(363, 315, 15))
        steps(sim, 300)
        self.assertTrue(sim.player.dead)


class Determinism(unittest.TestCase):
    def test_same_inputs_same_trace(self):
        from tools.make_test_level import build
        from gd.level import Level
        level = Level(build(), "t")
        seq = InputSequence([(t, i % 2 == 0) for i, t in enumerate(range(10, 3000, 37))])
        digests = []
        for _ in range(2):
            sim = Simulation(level)
            trace = []
            sim.run(seq, 3000, trace)
            digests.append(sim.digest(trace))
        self.assertEqual(digests[0], digests[1])
        other = Simulation(level)
        trace = []
        other.run(InputSequence([(11, True)]), 3000, trace)
        self.assertNotEqual(other.digest(trace), digests[0])

    def test_snapshot_restore(self):
        sim = make(build=lambda b: b.add(35, 300, 2))
        steps(sim, 100, lambda t: t % 50 < 5)
        snap = sim.snapshot()
        a = [steps(sim, 1, sim.tick % 40 < 3).y for _ in range(300)]
        sim.restore(snap)
        b = [steps(sim, 1, sim.tick % 40 < 3).y for _ in range(300)]
        self.assertEqual(a, b)

    def test_bundled_replay_completes_the_test_level(self):
        from gd.level import Level
        from gd.levelio import load_level_file
        from gd.macros import load_macro
        gmd = os.path.join(ROOT, "levels", "test_level.gmd")
        if not os.path.exists(gmd):
            self.skipTest("run tools/make_test_level.py first")
        for replay in ("test_level.gdr2", "test_level.replay.json"):
            sim = Simulation(Level(load_level_file(gmd)))
            sim.run(load_macro(os.path.join(ROOT, "levels", replay)))
            self.assertTrue(sim.finished, "%s: %s" % (replay, sim.player.death_cause))
            seen = " ".join(text for _, text in sim.log)
            for word in ("yellow orb", "yellow pad", "pink pad", "ship", "ball", "ufo", "wave",
                         "robot", "spider", "swing", "mini", "2x", "0.5x", "flipped"):
                self.assertIn(word, seen)


class Hitboxes(unittest.TestCase):
    """The player's three boxes, and the rotation that turns one of them."""

    def test_the_three_boxes(self):
        sim = make()
        p = airborne(sim, 300, 150)
        self.assertEqual(p.outer_rect(), (285.0, 315.0, 135.0, 165.0))          # 30 x 30
        self.assertEqual(p.inner_rect(), (295.5, 304.5, 145.5, 154.5))          # 0.3 x: 9 x 9
        p.rotation = 0.0
        xs, ys = zip(*p.oriented_box())
        self.assertEqual((min(xs), max(xs), min(ys), max(ys)), p.outer_rect())  # unturned: the same box
        p.rotation = 45.0
        xs, ys = zip(*p.oriented_box())
        self.assertAlmostEqual(max(xs) - 300.0, 15.0 * 2 ** 0.5, places=9)      # a diamond: the corners stick out
        self.assertAlmostEqual(max(ys) - 150.0, 15.0 * 2 ** 0.5, places=9)

    def test_mini_and_wave_boxes_shrink_together(self):
        p = airborne(make(mini=True), 300, 150)
        self.assertAlmostEqual(p.outer_rect()[1] - p.outer_rect()[0], 18.0)
        self.assertAlmostEqual(p.inner_rect()[1] - p.inner_rect()[0], 5.4)
        p = airborne(make(mode=4), 300, 150)
        self.assertAlmostEqual(p.outer_rect()[1] - p.outer_rect()[0], 10.0)

    def test_cube_turns_half_a_turn_every_26_frames_in_the_air(self):
        # OSGD runNormalRotation: 180 degrees per 26/60 s (mini: 20/60 s) at every speed
        for speed in (0, 1, 4):
            for mini, seconds in ((False, 26.0 / 60.0), (True, 20.0 / 60.0)):
                sim = make(speed=speed, mini=mini)
                p = settle(sim, 90)                   # (a mini cube first drops the 6 units from its spawn point)
                self.assertFalse(p.is_rotating)
                before = p.rotation
                self.assertAlmostEqual(before, 0.0, delta=0.1)
                sim.step(True)
                steps(sim, 24)                        # 25 steps in the air so far
                self.assertTrue(p.is_rotating)
                self.assertAlmostEqual(p.rotation - before, 180.0 / seconds * 25 / C.TPS, places=6)

    def test_cube_settles_flat_after_landing(self):
        sim = make()
        settle(sim)
        sim.step(True)
        run_until(self, sim, lambda s: s.player.on_ground and s.tick > 30)
        p = sim.player
        self.assertFalse(p.is_rotating)
        self.assertTrue(170.0 < p.rotation < 180.0, p.rotation)   # it lands a little short of the half turn
        steps(sim, 60)
        self.assertAlmostEqual(p.rotation, 180.0, places=2)       # ... and is eased onto the nearest side

    def test_flipped_cube_turns_the_other_way(self):
        sim = make(flipped=True, build=lambda b: b.blocks(0, 30, 5))
        p = run_until(self, sim, lambda s: s.player.is_rotating)   # "falling" up to the blocks overhead
        self.assertAlmostEqual(p.rotation_speed, -180.0 / C.ROTATE_TIME)
        run_until(self, sim, lambda s: s.player.on_ground)
        steps(sim, 60)
        self.assertEqual(p.rotation_speed, 0.0)
        sim.step(True)                                             # a jump off the ceiling
        steps(sim, 10)
        self.assertAlmostEqual(p.rotation_speed, -180.0 / C.ROTATE_TIME)

    def test_walking_off_an_edge_starts_the_turn(self):
        sim = make(build=lambda b: b.blocks(2, 5, 0))             # a platform; its top is at y = 30
        airborne(sim, 70, 46)
        p = run_until(self, sim, lambda s: s.player.on_ground)
        steps(sim, 8)
        self.assertEqual((p.y, p.is_rotating), (45.0, False))
        run_until(self, sim, lambda s: s.player.is_rotating)
        self.assertLess(p.y_vel, C.ROTATE_FALL_START)
        self.assertGreater(p.x - 15.0, 6 * 30)                    # only once the whole cube is past the last block

    def test_other_modes(self):
        p = steps(make(mode=5), 60, pressed=lambda t: 10 <= t < 30)        # robot: never spins
        self.assertEqual((p.rotation_speed, round(p.rotation, 6)), (0.0, 0.0))
        p = steps(make(mode=2), 30)                                        # ball: rolls at 600 degrees a second
        self.assertAlmostEqual(p.rotation_speed, 600.0)
        p = steps(make(mode=1), 40, pressed=True)                          # ship climbing: nose up
        self.assertLess(p.rotation, -5.0)
        p = steps(make(mode=4), 100, pressed=True)                         # wave: 45 degrees
        self.assertAlmostEqual(p.rotation, -45.0, delta=0.5)

    def angled_spike(self, rotation):
        """A spike turned 45 degrees, up and to the right of a cube hanging in the air.
        The spike's box reaches to 18.04 units from the cube's centre along the diagonal;
        the cube's corner is 21.21 away, the middle of its side only 15."""
        sim = make(build=lambda b: b.add(SPIKE, 317, 167, rot=45))
        p = airborne(sim, 300, 150)
        p.rotation = rotation
        sim._collide(p)
        return p

    def test_angled_hazard_is_tested_against_the_turned_box(self):
        self.assertTrue(self.angled_spike(0.0).dead, "corner towards the spike: hit")
        self.assertFalse(self.angled_spike(45.0).dead, "flat side towards the spike: the turned box misses")
        self.assertTrue(self.angled_spike(90.0).dead)

    def test_upright_hazard_ignores_the_turn(self):
        for rotation in (0.0, 45.0):
            sim = make(build=lambda b: b.add(SPIKE, 318, 150))          # hitbox 6 wide: starts at x = 315
            p = airborne(sim, 300, 150)
            p.rotation = rotation
            sim._collide(p)
            self.assertTrue(p.dead, "the outer box touches it whatever the angle")
            sim = make(build=lambda b: b.add(SPIKE, 319, 150))          # ... and at 316 it does not
            p = airborne(sim, 300, 150)
            p.rotation = rotation                                       # (a 45 degree turn would reach 321)
            sim._collide(p)
            self.assertFalse(p.dead)

    def test_solid_blocks_use_the_small_box(self):
        # a wall straight ahead: fatal only once the 9 x 9 inner box reaches it
        sim = make(build=lambda b: b.block(12, 0))
        p = settle(sim)
        run_until(self, sim, lambda s: s.dead)
        self.assertAlmostEqual(p.inner_rect()[1], 12 * 30, delta=p.x_vel * C.DT + 1e-6)
        self.assertGreater(p.outer_rect()[1], 12 * 30 + 9)            # the outer box is already deep inside

    def test_box_overlap_and_angle_helpers(self):
        from gd.geometry import obb_overlaps_obb, obb_corners, slerp2d
        a = obb_corners(0, 0, 30, 30, 0)
        self.assertTrue(obb_overlaps_obb(a, obb_corners(30, 0, 30, 30, 0)))       # touching counts, as in the game
        self.assertFalse(obb_overlaps_obb(a, obb_corners(30.01, 0, 30, 30, 0)))
        self.assertFalse(obb_overlaps_obb(a, obb_corners(34, 34, 30, 30, 45)))    # bounding boxes overlap, shapes do not
        self.assertTrue(obb_overlaps_obb(a, obb_corners(25, 25, 30, 30, 45)))
        self.assertAlmostEqual(slerp2d(0.0, 1.0, 0.25), 0.25)
        self.assertAlmostEqual(abs(slerp2d(3.0, -3.0, 0.5)), 3.14159265, places=6)   # the short way round


class FlipGrace(unittest.TestCase):
    """camila314/gdp collidedWithObjectInternal, last part: within 0.1 s of a gravity flip
    a collision that would kill pushes the player out of the block instead, provided the
    block is on the player's head side with the feet side at least 10 units clear.
    Reported by a player as "the ball goes up a block": a ball rolling towards a
    one-block step that switches gravity shortly before it is lifted onto the step."""

    CONTACT = 113          # the step in which the ball's small box reaches the block at x = 150

    def step_ahead(self, press, strict=False):
        sim = make(mode=2, build=lambda b: b.blocks(5, 13, 0))
        sim.player.strict_taps = strict
        steps(sim, self.CONTACT, pressed=lambda t: press <= t < press + 4)
        return sim

    def test_the_push(self):
        sim = self.step_ahead(press=98)                 # 15 steps (62 ms) before contact
        p = sim.player
        self.assertFalse(p.dead, p.death_cause)
        self.assertEqual((p.y, p.y_vel, p.upside_down, p.can_jump), (45.0, 0.0, True, True))   # on top: 30 + 15
        self.assertIn("gravity flip grace", sim.log[-1][1])

    def test_too_late_a_switch_still_kills(self):
        # 7 steps before contact the ball has risen about 6 units: its top is not yet
        # 10 above the block's, so the rule does not apply
        sim = self.step_ahead(press=106)
        self.assertTrue(sim.dead)
        self.assertIn("block", sim.player.death_cause)

    def test_no_switch_no_push(self):
        self.assertTrue(self.step_ahead(press=10 ** 6).dead)

    def test_it_can_be_switched_off(self):
        old = C.FLIP_GRACE_PUSH
        C.FLIP_GRACE_PUSH = False
        try:
            self.assertTrue(self.step_ahead(press=98).dead)
        finally:
            C.FLIP_GRACE_PUSH = old

    def test_the_rating_search_may_not_use_it(self):
        self.assertTrue(self.step_ahead(press=98, strict=True).dead)

    def test_it_can_be_forbidden_on_its_own(self):
        sim = make(mode=2, build=lambda b: b.blocks(5, 13, 0))
        sim.player.no_flip_push = True
        steps(sim, self.CONTACT, pressed=lambda t: 98 <= t < 102)
        self.assertTrue(sim.dead)


class DualMode(unittest.TestCase):
    """Dual portals (286 on, 287 off).  An ESTIMATE of the game - see Simulation._dual."""
    DUAL_ON, DUAL_OFF = 286, 287

    def level(self, build=None, off_col=40):
        def b(lv):
            lv.add(self.DUAL_ON, 315, 45)
            lv.add(self.DUAL_OFF, 15 + 30 * off_col, 45)
            if build:
                build(lv)
        return make(build=b)

    def test_a_second_icon_with_the_opposite_gravity(self):
        sim = self.level()
        run_until(self, sim, lambda s: s.player2 is not None)
        p, q = sim.player, sim.player2
        self.assertEqual((q.upside_down, p.upside_down, q.mode, q.x), (True, False, p.mode, p.x))
        self.assertEqual((sim.floor, sim.ceiling), (0.0, C.BOUNDS_SHIP), "the play area gets a ceiling")
        run_until(self, sim, lambda s: s.player2.can_jump)          # it falls up and lands on the ceiling
        self.assertAlmostEqual(q.y, C.BOUNDS_SHIP - 15.0)
        self.assertIn("dual mode on", [t for _, t in sim.log])

    def test_one_button_drives_both(self):
        sim = self.level()
        run_until(self, sim, lambda s: s.player2 is not None and s.player2.can_jump)
        p, q = sim.player, sim.player2
        y1, y2 = p.y, q.y
        steps(sim, 20, pressed=True)
        self.assertGreater(p.y, y1 + 20, "the first icon jumps up")
        self.assertLess(q.y, y2 - 20, "the second jumps down, towards its own floor")

    def test_losing_either_icon_ends_the_attempt(self):
        sim = self.level(lambda lv: lv.spike(20, row=9, down=True) if hasattr(lv, "spike") else
                         lv.add(SPIKE, 15 + 30 * 20, 285, rot=180))
        run_until(self, sim, lambda s: s.dead, limit=4000)
        self.assertTrue(sim.player.dead)
        self.assertIn("second icon", sim.player.death_cause)
        self.assertLess(sim.player.x, 30 * 21)

    def test_a_mode_portal_one_icon_takes_does_not_move_the_floor_and_ceiling(self):
        # only the first icon (on the floor) meets this ship portal, low down
        sim = self.level(lambda lv: lv.add(13, 15 + 30 * 20, 45))
        run_until(self, sim, lambda s: s.player2 is not None)
        bounds = (sim.floor, sim.ceiling)
        run_until(self, sim, lambda s: s.player.x > 30 * 21)
        self.assertEqual(sim.player.mode, Mode.SHIP)
        self.assertEqual(sim.player2.mode, Mode.CUBE, "only the icon that touched it")
        self.assertEqual((sim.floor, sim.ceiling), bounds, "the play area stays where the dual portal put it")

    def test_a_mode_portal_stacked_on_the_dual_portal_changes_only_the_first_icon(self):
        # level order: dual portal, then a ship portal at the same spot (as levels stack them)
        def b(lv):
            lv.add(self.DUAL_ON, 315, 45)
            lv.add(13, 315, 45)
            lv.add(self.DUAL_OFF, 15 + 30 * 40, 45)
        sim = make(build=b)
        run_until(self, sim, lambda s: s.player2 is not None)
        steps(sim, 30)
        self.assertEqual(sim.player.mode, Mode.SHIP)
        self.assertEqual(sim.player2.mode, Mode.CUBE, "the second icon keeps the mode it was copied with")

    def test_dual_off(self):
        sim = self.level(off_col=25)
        run_until(self, sim, lambda s: s.player2 is not None)
        run_until(self, sim, lambda s: s.player2 is None)
        self.assertEqual((sim.floor, sim.ceiling), (0.0, None))
        self.assertIn("dual mode off", [t for _, t in sim.log])
        self.assertFalse(sim.dead)

    def test_snapshots_keep_the_second_icon(self):
        sim = self.level()
        run_until(self, sim, lambda s: s.player2 is not None)
        snap = sim.snapshot()
        y = sim.player2.y
        steps(sim, 30)
        sim.restore(snap)
        self.assertEqual(sim.player2.y, y)
        sim.reset()
        self.assertIsNone(sim.player2)

    def test_the_bot_can_play_dual(self):
        from gd.autoplay import solve
        sim = self.level(lambda lv: [lv.add(SPIKE, 15 + 30 * 20, 285, rot=180), lv.add(SPIKE, 15 + 30 * 30, 15)])
        seq = solve(sim, chunk=6, max_nodes=200000)
        self.assertIsNotNone(seq)
        sim.reset()
        sim.run(seq, max_ticks=1600)
        self.assertFalse(sim.dead, sim.player.death_cause)


class ClickBetweenFrames(unittest.TestCase):
    """CBF: a click part-way into a step splits that step (syzzi's mod, src/main.cpp)."""

    def ground_cube(self):
        sim = Simulation(LevelBuilder().level())
        for _ in range(5):
            sim.step(False)
        self.assertTrue(sim.player.on_ground)
        return sim

    def test_a_click_half_way_through_a_step_jumps_half_a_step_later(self):
        heights = []
        for at in (None, 0.5):
            sim = self.ground_cube()
            sim.step(True, at)
            heights.append(sim.player.y)
        start = self.ground_cube().player.y
        self.assertGreater(heights[1], start, "it still leaves the ground in that step ...")
        self.assertLess(heights[1], heights[0], "... but rises for half the step only")

    def test_an_icon_in_mid_air_gets_the_click_at_the_start_of_the_step(self):
        a, b = self.ground_cube(), self.ground_cube()
        for sim in (a, b):
            sim.step(True)
            sim.step(False)
            sim.step(False)
            self.assertFalse(sim.player.on_ground)
        a.step(True)
        b.step(True, 0.5)                               # a cube in the air is "buffering": not split
        self.assertEqual(a.state(), b.state())

    def test_no_change_no_split(self):
        a, b = self.ground_cube(), self.ground_cube()
        a.step(False)
        b.step(False, 0.5)
        self.assertEqual(a.state(), b.state())

    def test_a_ship_splits_too(self):
        heights = []
        for at in (None, 0.25):
            sim = Simulation(LevelBuilder(mode=1).level())
            for _ in range(20):
                sim.step(False)
            sim.step(True, at)
            sim.step(True)
            heights.append(sim.player.y_vel)
        self.assertNotEqual(heights[0], heights[1])


class Bot(unittest.TestCase):
    """gd/autoplay.py: what the search skips, and what it proves."""

    def test_options_skip_presses_that_cannot_matter(self):
        from gd.autoplay import _options
        sim = make(build=lambda b: b.add(36, 30 * 30, 75))    # a yellow orb far ahead
        p = settle(sim)
        self.assertEqual(_options(sim, False, True), (False, True), "on the ground: both")
        sim.step(True)
        steps(sim, 3)
        self.assertFalse(p.can_jump)
        self.assertEqual(_options(sim, False, True), (False,), "in the air, no orb near: pressing does nothing")
        self.assertEqual(_options(sim, True, False), (False, True), "unless holding on into a landing is allowed")
        p.x = 30 * 30 - 40.0
        self.assertEqual(_options(sim, False, True), (False, True), "an orb ahead: pressing can matter")
        sim2 = make(mode=1)                                    # ship: always both
        steps(sim2, 5)
        self.assertEqual(_options(sim2, False, True), (False, True))

    def test_no_route_is_proven_quickly(self):
        from gd.autoplay import solve
        import time as clock
        sim = make(build=lambda b: [b.block(15, r) for r in range(12)])    # a wall nobody can pass
        t = clock.time()
        self.assertIsNone(solve(sim, chunk=4, max_nodes=10 ** 7))
        self.assertLess(clock.time() - t, 20.0)

    def test_the_bot_leaves_the_icon_angle_for_the_screen(self):
        from gd.autoplay import solve
        sim = make()
        solve(sim, chunk=8, max_nodes=1000)
        self.assertTrue(sim.visual, "a game view gets a simulation that works out the angle again")


if __name__ == "__main__":
    unittest.main(verbosity=2)
