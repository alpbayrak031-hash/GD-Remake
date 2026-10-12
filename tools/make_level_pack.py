#!/usr/bin/env python3
"""Builds the level pack in levels/ (easy -> very hard), and proves each level.

    python3 tools/make_level_pack.py            # build everything
    python3 tools/make_level_pack.py flip      # only levels whose name contains "flip"

Every level is assembled from the section builders below, then checked:
  * a solver must finish it on a COARSE input grid (see gd/analysis.rate) - the
    coarser the grid a level tolerates, the more forgiving its timing;
  * pad sections are verified hands-off (no input may be needed to survive a pad);
  * the solution is centred in its timing windows and the tightest window measured.
A level that misses its difficulty band is rebuilt with the next seed.
The solution is saved next to the level as a .gdr2 replay ("Watch bot" in the menu).
"""
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gd import analysis                              # noqa: E402
from gd.level import Level, LevelBuilder             # noqa: E402
from gd.levelio import write_gmd                     # noqa: E402
from gd.macros import write_gdr2                     # noqa: E402
from gd.sim import Simulation                        # noqa: E402

CUBE, SHIP, BALL, UFO, WAVE, ROBOT, SPIDER, SWING = range(8)
PORTAL = {CUBE: 12, SHIP: 13, BALL: 47, UFO: 111, WAVE: 660, ROBOT: 745, SPIDER: 1331, SWING: 1933}
SPEED_PORTAL = {0: 200, 1: 201, 2: 202, 3: 203, 4: 1334}
PAD = {"yellow": (35, 2.0), "pink": (140, 2.5), "red": (1332, 3.0)}
SPIKE, SMALL_SPIKE, ORB = 8, 39, 36
GRAV_UP, GRAV_DOWN, MINI, BIG = 11, 10, 101, 99
# horizontal speed relative to 1x: distances that should take the same TIME are scaled by this
PACE = {0: 0.806, 1: 1.0, 2: 1.243, 3: 1.502, 4: 1.849}
AIRBORNE = (SHIP, UFO, WAVE, SWING, BALL, SPIDER)       # modes that are not guaranteed to be on the floor
_pad_cache = {}


def cx(col):
    return 15.0 + 30.0 * col


def pad_spikes(kind, speed):
    """How many spikes can follow a pad (starting 2 columns after it) and still be
    cleared with NO input.  Found by simulation; one spike of margin is kept."""
    key = (kind, speed)
    if key not in _pad_cache:
        oid, y = PAD[kind]
        best = 0
        for n in range(1, 16):
            b = LevelBuilder(speed=speed)
            b.block(120, 0)
            b.add(oid, cx(20), y)
            for i in range(n):
                b.add(SPIKE, cx(22 + i), 15)
            sim = Simulation(b.level())
            while not sim.dead and sim.player.x < cx(22 + n + 12):
                sim.step(False)
            if sim.dead:
                break
            best = n
        _pad_cache[key] = max(1, best - 1)
    return _pad_cache[key]


class Track:
    """A level under construction: a cursor column plus the current game state."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.b = LevelBuilder()
        self.c = 10                      # nothing before column 10: time to get ready
        self.speed, self.mode, self.mini = 1, CUBE, False
        self.top = 9                     # highest row inside the current mode's ceiling
        self.marks = []

    # -- small helpers -----------------------------------------------------------
    def n(self, cols):
        """`cols` columns at 1x, stretched for the current speed."""
        return max(1, int(round(cols * PACE[self.speed])))

    def mark(self, name):
        self.marks.append((self.c, name))

    def rest(self, cols):
        self.c += self.n(cols)

    def spike(self, col, row=0, down=False):
        self.b.add(SPIKE, cx(col), 15 + 30 * row, rot=180 if down else 0)

    def pillar(self, col, r0, r1):
        for r in range(r0, r1 + 1):
            self.b.block(col, r)

    def _stack(self, oid, step):
        """One portal on the ground, or a floor-to-ceiling column of them while flying."""
        if self.mode in AIRBORNE:
            y = step / 2.0
            while y < (self.top + 1) * 30:
                self.b.add(oid, cx(self.c), y)
                y += step
        else:
            self.b.add(oid, cx(self.c), 45)
        self.c += 1

    # -- state changes -------------------------------------------------------------
    def enter(self, mode, rest=8):
        """Mode portal.  Coming out of a flying mode the portal sits in a low doorway,
        so the play area of the next mode always starts at the ground."""
        self.mark("-> %s" % ("cube ship ball ufo wave robot spider swing".split()[mode]))
        if self.mode in AIRBORNE:
            for col in range(self.c, self.c + 3):
                self.pillar(col, 3, self.top)
            self.b.add(PORTAL[mode], cx(self.c + 1), 45)
            self.c += 3
        else:
            self.b.add(PORTAL[mode], cx(self.c), 45)
            self.c += 1
        self.mode = mode
        self.top = 7 if mode in (BALL, SPIDER) else 9
        self.rest(rest)

    def set_speed(self, speed, rest=7):
        self.mark("speed %d" % speed)
        self._stack(SPEED_PORTAL[speed], 60)
        self.speed = speed
        self.rest(rest)

    def set_mini(self, mini, rest=6):
        self.mark("mini" if mini else "big")
        self._stack(MINI if mini else BIG, 90)
        self.mini = mini
        self.rest(rest)

    # -- cube sections ---------------------------------------------------------------
    def spikes(self, groups, rest=10):
        """Groups of ground spikes.  A group is a number of spikes, or (big, small): that
        many full spikes followed by small ones, which are lower and so easier to clear -
        "2 + 1 small" sits between a double (137 ms window) and a triple (about 45 ms)."""
        self.mark("spikes")
        for group in groups:
            big, small = group if isinstance(group, tuple) else (group, 0)
            for i in range(big):
                self.spike(self.c + i)
            for i in range(small):
                self.b.add(SMALL_SPIKE, cx(self.c + big + i), 7)
            self.c += big + small
            self.rest(rest)

    def steps(self, count, rest=9, width=3):
        """A staircase: one jump per step, then a drop back to the ground."""
        self.mark("steps")
        width = self.n(width)
        for k in range(count):
            for col in range(self.c, self.c + width):
                self.pillar(col, 0, k)
            self.c += width
        self.rest(rest)

    def bridge(self, count, gap=2, drift=1, width=4, rest=9):
        """Floating platforms over a bed of spikes."""
        self.mark("bridge")
        row, start, ground = 0, self.c, set()
        last = start
        for i in range(count):
            w = self.n(width)
            for col in range(self.c, self.c + w):
                self.b.block(col, row)
                if row == 0:
                    ground.add(col)
            last = self.c
            self.c += w
            if i < count - 1:
                new_row = max(0, min(4, row + self.rng.randint(-drift, drift)))
                g = self.n(gap)
                if new_row > row:
                    g = max(1, g - 1)            # a shorter leap when the next platform is higher
                self.c += g
                row = new_row
        for col in range(start + self.n(width), last):
            if col not in ground:
                self.spike(col)
        self.rest(rest)

    def orb_pit(self, orbs=1, rest=10):
        """A spike pit too wide to jump: leap in, tap each yellow orb, land beyond it."""
        self.mark("orb pit")
        pace = PACE[self.speed]
        # the first orb sits early in the pit: the earlier it is, the wider the window
        # for the jump that carries the cube to it
        width = int((2.2 + 4.5 * (orbs - 1) + 2.8) * pace)
        for i in range(width):
            self.spike(self.c + i)
        for k in range(orbs):
            self.b.add(ORB, cx(self.c) + (2.2 + 4.5 * k) * 30 * pace, 75)
        self.c += width
        self.rest(rest)

    def pad(self, kind="yellow", rest=9):
        self.mark("%s pad" % kind)
        oid, y = PAD[kind]
        count = pad_spikes(kind, self.speed)
        self.b.add(oid, cx(self.c), y)
        for i in range(count):
            self.spike(self.c + 2 + i)
        self.c += 2 + count
        self.rest(rest)

    def gravity_run(self, groups=(1, 1), spacing=8, rest=9):
        """Flip onto a ceiling, hop over spikes hanging from it, flip back."""
        self.mark("gravity run")
        start = self.c
        self.b.add(GRAV_UP, cx(start), 45)
        cur = start + self.n(spacing)
        for count in groups:
            for i in range(count):
                self.spike(cur + i, row=4, down=True)
            cur += count + self.n(spacing)
        for col in range(start - 1, cur + 2):
            self.b.block(col, 5)
        self.b.add(GRAV_DOWN, cx(cur), 105)
        self.c = cur + 1
        self.rest(rest)

    # -- flying sections ---------------------------------------------------------------
    def gates(self, count, gap, period=8, drift=1, thick=1, start=1, spiked=False):
        """Walls with an opening `gap` rows tall (ship, ufo, swing, wave).

        Plain block walls are forgiving: a ship may slide along a block.  With
        `spiked` the opening is lined with spikes top and bottom, so it has to be
        flown through cleanly - `thick` then makes it a corridor to hold steady in."""
        self.mark("%sgates gap %d" % ("spiked " if spiked else "", gap))
        lo = start
        for _ in range(count):
            lo = max(0, min(self.top + 1 - gap, lo))
            for col in range(self.c, self.c + thick):
                if lo > 0:
                    self.pillar(col, 0, lo - 1)
                if lo + gap <= self.top:
                    self.pillar(col, lo + gap, self.top)
                if spiked:
                    self.spike(col, row=lo)
                    self.spike(col, row=lo + gap - 1, down=True)
            self.c += thick + self.n(period)
            lo += self.rng.randint(-drift, drift)

    def tunnel(self, length, gap, lo=1):
        """A zig-zag corridor one block per column - made for the wave."""
        self.mark("tunnel gap %d" % gap)
        direction, run = 1, 0
        for i in range(length):
            wide = max(0, 3 - i)                     # the mouth of the tunnel is wider
            floor_top = max(0, lo - wide)
            ceil_bottom = lo + gap + wide
            col = self.c + i
            if floor_top > 0:
                self.pillar(col, 0, floor_top - 1)
            if ceil_bottom <= self.top:
                self.pillar(col, ceil_bottom, self.top)
            if run <= 0:
                direction = self.rng.choice((-1, 0, 1)) if i > 3 else 1
                run = self.rng.randint(3, 6)
            run -= 1
            nxt = lo + direction
            if nxt < 0 or nxt + gap > self.top + 1:
                direction = -direction
                nxt = lo + direction
            lo = nxt
        # room to come back down to the doorway of the next portal: the wave drops one
        # row per column whatever the speed, so this is plain columns, not n()
        self.c += length + lo + 3

    def alternate(self, count, run=3, spacing=9):
        """Floor spikes, then ceiling spikes, then floor... (ball and spider)."""
        self.mark("floor/ceiling")
        for i in range(count):
            for k in range(run):
                self.spike(self.c + k, row=self.top if i % 2 else 0, down=bool(i % 2))
            self.c += run
            self.rest(spacing)

    def walls(self, heights, spacing=9):
        """Walls of different heights: the robot's jump depends on how long you hold."""
        self.mark("walls")
        for h in heights:
            self.pillar(self.c, 0, h - 1)
            self.c += 1
            self.rest(spacing)

    def finish(self):
        self.rest(6)
        self.b.block(self.c, 0)                       # a last step to hop over
        return self.b.string()


# ====================================================================================
# The levels.  Each recipe gets a fresh Track.
# ====================================================================================
def first_steps(t):
    t.spikes([1, 1, 1], 12)
    t.steps(2, 11)
    t.spikes([1, 2, 1], 12)
    t.steps(3, 11)
    t.spikes([2, 1, 2, 1], 12)
    t.pad("pink", 11)
    t.spikes([1, 1, 2], 12)
    t.steps(3, 11)
    t.spikes([2, 1, 1, 2], 12)


def stepping_stones(t):
    t.spikes([1, 2], 11)
    t.bridge(3, gap=2, drift=0, width=5, rest=11)
    t.steps(3, 11)
    t.pad("yellow", 11)
    t.spikes([2, 1], 11)
    t.bridge(4, gap=2, drift=1, width=5, rest=11)
    t.pad("pink", 11)
    t.spikes([1, 2, 2], 11)
    t.bridge(4, gap=2, drift=1, width=5, rest=11)
    t.pad("yellow", 11)
    t.spikes([2, 2], 11)


def lift_off(t):
    t.spikes([1, 2, 1], 11)
    t.steps(2, 10)
    t.enter(SHIP, 10)
    t.gates(7, gap=6, period=10, drift=1)
    t.enter(CUBE, 9)
    t.spikes([2, 1], 11)
    t.pad("yellow", 11)
    t.enter(SHIP, 10)
    t.gates(8, gap=5, period=10, drift=1)
    t.enter(CUBE, 9)
    t.spikes([1, 2, 2], 11)


def bounce_house(t):
    t.pad("yellow", 11)
    t.spikes([1, 2], 11)
    t.orb_pit(1, 12)
    t.pad("pink", 11)
    t.enter(BALL, 10)
    t.alternate(7, run=3, spacing=11)
    t.enter(CUBE, 9)
    t.orb_pit(1, 12)
    t.pad("red", 12)
    t.spikes([2, 1, 2], 11)
    t.orb_pit(1, 12)
    t.pad("yellow", 11)
    t.spikes([2, 2], 11)


def flip_side(t):
    t.spikes([1, 2], 11)
    t.gravity_run((1, 1), spacing=9, rest=11)
    t.steps(3, 10)
    t.enter(UFO, 10)
    t.gates(7, gap=6, period=10, drift=1)
    t.enter(CUBE, 9)
    t.gravity_run((1, 2, 1), spacing=9, rest=11)
    t.spikes([2, 2], 11)
    t.gravity_run((2, 1, 2), spacing=9, rest=11)
    t.spikes([1, 2], 11)


def double_time(t):
    t.spikes([2, (2, 1), 2], 8)
    t.bridge(4, gap=3, drift=1, width=4, rest=8)
    t.set_speed(2)
    t.spikes([3, (3, 1), 2, 3], 8)
    t.pad("yellow", 8)
    t.orb_pit(2, 9)
    t.steps(4, 8, width=4)
    t.spikes([3, 3], 8)
    t.set_speed(1)
    t.bridge(5, gap=3, drift=1, width=4, rest=8)
    t.orb_pit(2, 9)
    t.spikes([(2, 1), 2, (1, 2)], 8)


def night_flight(t):
    t.spikes([2, (2, 1)], 8)
    t.enter(SHIP, 9)
    t.gates(8, gap=4, period=8, drift=1, thick=2, spiked=True)
    t.enter(UFO, 8)
    t.gates(7, gap=4, period=8, drift=1, thick=3, spiked=True)
    t.enter(BALL, 8)
    t.alternate(7, run=3, spacing=8)
    t.enter(CUBE, 8)
    t.set_speed(2)
    t.spikes([3, (3, 1), 3], 8)
    t.enter(SHIP, 9)
    t.gates(8, gap=3, period=7, drift=1, thick=2, spiked=True)
    t.enter(WAVE, 8)
    t.tunnel(40, gap=4)
    t.enter(CUBE, 8)
    t.spikes([3, (3, 1)], 8)


def switchback(t):
    t.gravity_run((2, 1, 2), spacing=7, rest=8)
    t.enter(ROBOT, 8)
    t.walls([1, 2, 3, 1, 3, 2], 8)
    t.enter(SPIDER, 8)
    t.alternate(8, run=3, spacing=6)
    t.enter(CUBE, 8)
    t.set_mini(True)
    t.spikes([1, 2, 1, 1, 2], 9)
    t.set_mini(False)
    t.gravity_run((2, 2, 1, 2), spacing=7, rest=8)
    t.orb_pit(2, 9)
    t.spikes([(2, 1), (1, 2), (2, 1)], 8)


def needle_thread(t):
    t.set_speed(2, 6)
    t.spikes([3, 4, 3, 4], 6)
    t.enter(SHIP, 8)
    t.gates(8, gap=3, period=7, drift=1, thick=3, spiked=True)
    t.enter(WAVE, 7)
    t.tunnel(46, gap=3)
    t.enter(CUBE, 7)
    t.bridge(6, gap=3, drift=1, width=3, rest=6)
    t.enter(SHIP, 8)
    t.gates(8, gap=3, period=6, drift=1, thick=4, spiked=True)
    t.enter(WAVE, 7)
    t.tunnel(50, gap=2)
    t.enter(CUBE, 7)
    t.spikes([4, 3, 4], 6)


def clockwork(t):
    t.set_speed(2, 6)
    t.spikes([4, 3, 4], 6)
    t.enter(ROBOT, 7)
    t.walls([2, 3, 1, 3, 3, 2, 1, 3], 6)
    t.enter(SPIDER, 7)
    t.alternate(12, run=3, spacing=4)
    t.enter(BALL, 7)
    t.alternate(8, run=4, spacing=6)
    t.enter(UFO, 7)
    t.gates(7, gap=4, period=7, drift=1, thick=4, spiked=True)
    t.enter(CUBE, 7)
    t.set_mini(True, 5)
    t.spikes([2, 3, 2, 3], 6)
    t.set_mini(False, 5)
    t.set_speed(3, 6)
    t.spikes([4, (4, 1), 3, (3, 2)], 5)
    t.orb_pit(3, 6)
    t.bridge(5, gap=3, drift=1, width=3, rest=5)
    t.spikes([4, 4], 5)


def zero_g(t):
    t.set_speed(2, 6)
    t.gravity_run((3, 4, 3), spacing=6, rest=6)
    t.enter(SWING, 9)
    t.gates(7, gap=4, period=8, drift=1, thick=3, spiked=True)
    t.enter(SHIP, 8)
    t.set_mini(True, 5)
    t.gates(8, gap=3, period=6, drift=1, thick=2, spiked=True)
    t.set_mini(False, 5)
    t.enter(WAVE, 7)
    t.tunnel(50, gap=2)
    t.enter(CUBE, 7)
    t.gravity_run((4, 3, 4), spacing=5, rest=6)
    t.spikes([4, 3, 4], 5)


def event_horizon(t):
    t.set_speed(3, 5)
    t.spikes([4, 5, 4, 5], 5)
    t.enter(SHIP, 7)
    t.gates(9, gap=3, period=6, drift=1, thick=4, spiked=True)
    t.enter(WAVE, 6)
    t.tunnel(60, gap=2)
    t.enter(UFO, 6)
    t.gates(8, gap=3, period=7, drift=1, thick=1, spiked=True)
    t.enter(BALL, 6)
    t.alternate(10, run=4, spacing=5)
    t.enter(CUBE, 6)
    t.set_speed(4, 5)
    t.spikes([5, 6, 5, 6], 4)
    t.orb_pit(3, 5)
    t.enter(WAVE, 6)
    t.tunnel(64, gap=2)
    t.enter(CUBE, 6)
    t.spikes([6, 5], 4)


def overclock(t):
    t.set_speed(3, 5)
    t.gravity_run((4, 5, 4), spacing=5, rest=5)
    t.enter(ROBOT, 6)
    t.walls([3, 2, 3, 1, 3, 3, 2, 3], 5)
    t.enter(SPIDER, 6)
    t.alternate(14, run=3, spacing=3)
    t.enter(SWING, 8)
    t.gates(8, gap=3, period=7, drift=1, thick=3, spiked=True)
    t.enter(SHIP, 7)
    t.set_mini(True, 4)
    t.gates(9, gap=3, period=6, drift=1, thick=3, spiked=True)
    t.set_mini(False, 4)
    t.enter(WAVE, 6)
    t.set_mini(True, 4)
    t.tunnel(56, gap=3)
    t.set_mini(False, 4)
    t.enter(CUBE, 6)
    t.set_speed(4, 5)
    t.spikes([6, 5, 6, 5], 4)


# (recipe, title, difficulty, blurb)
LEVELS = [
    (first_steps, "First Steps", "easy", "Cube only. Single and double spikes, a few stairs."),
    (stepping_stones, "Stepping Stones", "easy", "Platforms over spikes and your first jump pads."),
    (lift_off, "Lift Off", "easy", "Two gentle ship flights through wide gates."),
    (bounce_house, "Bounce House", "easy", "Pads, a yellow orb and a relaxed ball section."),
    (flip_side, "Flip Side", "easy", "Run on the ceiling, then hop a UFO through big gaps."),
    (double_time, "Double Time", "medium", "Spike groups with small spikes, orb chains and a 2x stretch."),
    (night_flight, "Night Flight", "medium", "Spike-lined ship and UFO gates, a ball section and a wave tunnel."),
    (switchback, "Switchback", "medium", "Gravity flips, robot walls, spider and a mini run."),
    (needle_thread, "Needle Thread", "hard", "2x speed. Four-spike jumps, ship corridors and a two-block wave tunnel."),
    (clockwork, "Clockwork", "hard", "Robot, spider, ball and UFO at 2x, a mini run, then a 3x cube sprint."),
    (zero_g, "Zero G", "hard", "Upside-down runs, swing, mini ship and a two-block wave tunnel at 2x."),
    (event_horizon, "Event Horizon", "very hard", "3x and 4x. Five- and six-spike jumps, spiked corridors, long wave tunnels."),
    (overclock, "Overclock", "very hard", "Every mode at 3x, with mini ship and mini wave, and a 4x finish."),
]
# Difficulty bands: the coarsest input grid (in 1/240 s ticks) a level must and may
# tolerate, i.e. roughly its tightest timing window: easy >= 100 ms, medium 50-67 ms,
# hard 33-42 ms, very hard 17-25 ms.  For scale: a triple spike at 1x is about 45 ms.
BAND = {"easy": (24, 999), "medium": (12, 16), "hard": (8, 10), "very hard": (4, 6)}
CHUNKS = (32, 24, 16, 12, 10, 8, 6, 4)


def build_one(index, recipe, title, difficulty, blurb, out_dir, max_seeds=4):
    need, most = BAND[difficulty]
    best = None
    for seed in range(1, max_seeds + 1):
        track = Track(seed * 7919 + index)
        recipe(track)
        string = track.finish()
        level = Level(string, title)
        info = {}
        chunk, seq = analysis.rate(level, CHUNKS, max_nodes=400000, info=info)
        if seq is None:
            col = (info.get("limit_x") or 0.0) / 30.0
            where = [name for c, name in track.marks if c <= col]
            print("    seed %d: not solved (stuck near column %.0f, %s)" % (seed, col, where[-1] if where else "start"), flush=True)
            continue
        fits = need <= chunk <= most
        if best is None or fits:
            best = (seed, track, string, level, chunk, seq, info)
        limit = info.get("limit_x")
        where = [name for c, name in track.marks if limit is not None and c <= limit / 30.0]
        print("    seed %d: solvable on a %d-tick grid%s%s" % (
            seed, chunk, "" if fits else "  (outside the %s band)" % difficulty,
            "  - tightest spot: %s near column %.0f" % (where[-1] if where else "start", limit / 30.0) if limit else ""),
            flush=True)
        if fits:
            break
    if best is None:
        raise SystemExit("could not build a solvable %r" % title)
    seed, track, string, level, chunk, seq, info = best
    an = analysis.Analyzer(level, seq)
    seq = an.centre(passes=2)
    assert an.completes, "centred replay no longer completes"
    rep = an.report(worst=3)

    def section_at(column):
        name = "start"
        for col, label in track.marks:
            if col <= column:
                name = label
        return name

    slug = "%02d_%s" % (index + 1, title.lower().replace(" ", "_"))
    write_gmd(os.path.join(out_dir, slug + ".gmd"), title, string)
    seq.meta.update({"level": title})
    write_gdr2(os.path.join(out_dir, slug + ".gdr2"), seq, title)
    entry = {
        "file": slug + ".gmd", "replay": slug + ".gdr2", "name": title, "difficulty": difficulty,
        "description": blurb, "seconds": round(an.ticks / 240.0, 1), "objects": len(level.objects),
        "input_grid_ms": round(chunk * 1000 / 240), "press_window_ms": rep["press_window_ms"],
        "release_window_ms": rep["release_window_ms"], "presses": rep["presses"], "seed": seed,
    }
    limit = info.get("limit_x")
    entry["tightest_section"] = section_at(limit / 30.0) if limit else ""
    print("  %-16s %-9s %5.1f s  %4d objects  timing %3d ms  %s" % (
        title, difficulty, entry["seconds"], entry["objects"], entry["input_grid_ms"],
        "(tightest: %s)" % entry["tightest_section"] if limit else ""), flush=True)
    return entry


def main(argv):
    out_dir = os.path.join(ROOT, "levels")
    os.makedirs(out_dir, exist_ok=True)
    index_path = os.path.join(out_dir, "index.json")
    entries = {}
    if os.path.exists(index_path):
        with open(index_path) as fh:
            entries = {e["name"]: e for e in json.load(fh)["levels"]}
    only = [a.lower() for a in argv]
    t0 = time.time()
    for i, (recipe, title, difficulty, blurb) in enumerate(LEVELS):
        if only and not any(word in title.lower() for word in only):
            continue
        print("%s (%s)" % (title, difficulty))
        entries[title] = build_one(i, recipe, title, difficulty, blurb, out_dir)
    ordered = [entries[title] for _, title, _, _ in LEVELS if title in entries]
    with open(index_path, "w") as fh:
        json.dump({"levels": ordered}, fh, indent=1)
    print("wrote %d levels to %s in %.0f s" % (len(ordered), out_dir, time.time() - t0))


if __name__ == "__main__":
    main(sys.argv[1:])
