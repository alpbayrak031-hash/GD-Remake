#!/usr/bin/env python3
"""Builds levels/test_level.gmd - a short level that touches every mechanic - and
searches for an input sequence that completes it (saved as .gdr2 and .json).

    python3 tools/make_test_level.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gd import analysis                          # noqa: E402
from gd.level import Level, LevelBuilder         # noqa: E402
from gd.levelio import write_gmd                 # noqa: E402
from gd.macros import write_gdr2                 # noqa: E402
from gd.sim import Simulation                    # noqa: E402

SPIKE, BLOCK = 8, 1
ORB_YELLOW, PAD_YELLOW, PAD_PINK = 36, 35, 140
P_CUBE, P_SHIP, P_BALL, P_UFO, P_WAVE, P_ROBOT, P_SPIDER, P_SWING = 12, 13, 47, 111, 660, 745, 1331, 1933
P_GRAV_DOWN, P_GRAV_UP, P_BIG, P_MINI = 10, 11, 99, 101
P_HALF, P_1X, P_2X = 200, 201, 202


def build():
    b = LevelBuilder()

    def cx(col):
        return 15 + 30 * col

    def spikes(c0, c1, row=0, ceiling=False):
        for c in range(c0, c1 + 1):
            b.add(SPIKE, cx(c), 15 + 30 * row, rot=180 if ceiling else 0)

    def pillar(col, r0, r1):
        for r in range(r0, r1 + 1):
            b.block(col, r)

    def doorway(col, top):
        """A low opening around a portal, so the mode before it cannot fly or roll past
        above the portal (the first version let a ball or spider skip whole sections)."""
        for c in (col - 1, col, col + 1):
            pillar(c, 3, top)

    def exit_to_cube(col):
        for y in (45, 131, 217, 290):               # a wall of cube portals, floor to ceiling
            b.add(P_CUBE, cx(col), y)

    # -- cube: single spike, double spike, step up, floating platform
    spikes(12, 12)
    spikes(20, 21)
    b.blocks(28, 31, 0)
    spikes(32, 34)
    b.blocks(35, 38, 1)
    # -- yellow orb over a spike pit that is too wide to jump
    spikes(44, 49)
    b.add(ORB_YELLOW, cx(47), 75)
    # -- pads
    b.add(PAD_YELLOW, cx(56), 2)
    spikes(58, 60)                                  # the pad carries the cube 6 columns: 3 spikes fit, 4 do not
    b.add(PAD_PINK, cx(67), 2.5)
    spikes(68, 69)
    # -- ship
    b.add(P_SHIP, cx(76), 45)
    pillar(84, 0, 3)
    pillar(92, 5, 9)
    exit_to_cube(100)
    # -- gravity portals with a ceiling to run on
    b.add(P_GRAV_UP, cx(108), 45)
    b.blocks(106, 122, 5)
    spikes(114, 114, row=4, ceiling=True)
    b.add(P_GRAV_DOWN, cx(120), 105)
    # -- mini, then speed changes
    b.add(P_MINI, cx(128), 45)
    spikes(133, 133)
    b.add(P_BIG, cx(137), 45)
    b.add(P_2X, cx(141), 45)
    spikes(148, 148)
    b.add(P_HALF, cx(154), 45)
    spikes(159, 159)
    b.add(P_1X, cx(164), 45)
    # -- ball: floor spikes then ceiling spikes (ceiling is at y = 240)
    b.add(P_BALL, cx(170), 45)
    spikes(176, 178)
    spikes(184, 186, row=7, ceiling=True)
    # -- ufo
    doorway(192, 7)
    b.add(P_UFO, cx(192), 45)
    pillar(198, 0, 2)
    # -- wave
    doorway(204, 9)
    b.add(P_WAVE, cx(204), 45)
    pillar(212, 0, 3)
    pillar(219, 6, 9)
    exit_to_cube(226)
    # -- robot: a wall only a held jump clears
    b.add(P_ROBOT, cx(234), 45)
    pillar(241, 0, 2)
    # -- spider
    b.add(P_SPIDER, cx(248), 45)
    spikes(254, 256)
    spikes(262, 264, row=7, ceiling=True)
    # -- swing
    doorway(270, 7)
    b.add(P_SWING, cx(270), 45)
    pillar(278, 0, 3)
    pillar(286, 6, 9)
    exit_to_cube(294)
    b.block(300, 0)
    return b.string()


def main():
    out_dir = os.path.join(ROOT, "levels")
    os.makedirs(out_dir, exist_ok=True)
    string = build()
    gmd = os.path.join(out_dir, "test_level.gmd")
    write_gmd(gmd, "Physics Test", string)
    level = Level(string, "Physics Test")
    print("wrote", gmd)
    print(level.report.summary())

    t0 = time.time()
    chunk, seq = analysis.rate(level)
    if seq is None:
        print("solver gave up - no replay written")
        return 1
    an = analysis.Analyzer(level, seq)
    seq = an.centre()
    print("solved in %.1f s: fine on a %d ms input grid, %d presses" % (
        time.time() - t0, round(chunk * 1000 / 240), an.report()["presses"]))
    sim = Simulation(level)
    sim.run(seq)
    assert sim.finished, "replay does not reproduce!"
    seq.meta["level"] = "Physics Test"
    seq.save(os.path.join(out_dir, "test_level.replay.json"))
    write_gdr2(os.path.join(out_dir, "test_level.gdr2"), seq, "Physics Test")
    print("wrote levels/test_level.replay.json and levels/test_level.gdr2 (%d ticks)" % sim.tick)
    return 0


if __name__ == "__main__":
    sys.exit(main())
