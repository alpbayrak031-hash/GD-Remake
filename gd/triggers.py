"""Triggers: groups of objects that move, switch off and on, and teleports (GD 1.9 - 2.2).

What is simulated
  move      (901)   moves every object in its target group by (Offset X, Offset Y) over
                    `Duration` seconds along an easing curve, or with the player
                    ("Lock to Player X / Y")
  toggle    (1049)  switches every object in its target group off (no collision, not
                    drawn) or back on
  spawn     (1268)  after `Spawn Delay` seconds, fires every trigger in its target group
  teleport  (3022)  puts the player on the first object of its target group
  teleport portal (747, gd/objects.py)  handled in gd/sim.py
How a trigger fires
  when the player passes its x position; or, with "Touch Triggered", when the player
  touches it; or, with "Spawn Triggered", only when a spawn trigger fires its group.
  "Multi Trigger" lets a touched or spawned trigger fire again.

Sources
  * Level-string keys: Wyliemaster/gddocs "level string" (10 duration, 11 touch triggered,
    28/29 offset x/y, 30 easing, 51 target group, 56 activate group, 57 groups,
    58/59 lock to player x/y, 62 spawn triggered, 63 spawn delay, 85 easing rate,
    87 multi trigger) and its easing list (0 none ... 18 back out).
  * Offsets are stored in game units (30 per block): SPWN's standard library writes
    MOVE_X as "units (10 per grid square) * 3" (Spu7Nix/SPWN-language, std/group.spwn).
  * The easing curves are cocos2d-x's (CCActionEase / CCTweenFunction), the engine GD is
    built on.  The easing rate is the exponent of the In/Out curves and the period of the
    elastic ones.
ESTIMATES - nothing here is decompiled:
  * a passed trigger fires at the end of the step in which the player's x reaches the
    trigger's x; spawned triggers fire in level order; moves advance once per 1/240 s
    step, starting on the step they fire on (a move of 0 seconds lands at once) and
    always end exactly on their offset; a
    spawn with no delay fires on the same step, but what it spawns waits for the next
    one (so a spawn loop cannot run forever within one step); a group spawned twice for
    the same step fires once, and past MAX_WAITING spawns / MAX_MOVES moves at once
    more are dropped - a safety valve against runaway loops, not GD behaviour
  * a touch-triggered trigger is touched when the player's box meets a 30 x 30 box
    (times the trigger's scale) around it
  * an object in several groups is moved by every move aimed at any of them, and is
    on or off according to the most recent toggle aimed at any of them; a trigger
    inside a group that is switched off does not fire
  * lock to player X/Y follows the player's own movement and ignores that offset; a
    negative duration counts as zero
  * moving blocks do not carry the player along: the player is only stopped or killed by
    them where they are on each step
  * the default easing rate, when a level does not store one, is 2
Not simulated: rotate, follow, alpha (fading does not change collision), stop, pulse,
colour, count and collision triggers, toggle orbs, 2.2 area and keyframe triggers.
"""
import bisect
import math

from .constants import SECTION_SIZE

MOVE, TOGGLE, SPAWN, TELEPORT = 901, 1049, 1268, 3022
TRIGGER_IDS = (MOVE, TOGGLE, SPAWN, TELEPORT)
NAMES = {MOVE: "move", TOGGLE: "toggle", SPAWN: "spawn", TELEPORT: "teleport"}
TPS = 240
TOUCH_SIZE = 30.0                    # EST: the box a touch-triggered trigger is touched with
MAX_WAITING = 4096                   # spawns waiting at once; more are dropped (a runaway spawn loop)
MAX_MOVES = 4096                     # moves running at once; more are dropped


# ---------------------------------------------------------------------- easing (cocos2d-x)
def _bounce(t):
    if t < 1 / 2.75:
        return 7.5625 * t * t
    if t < 2 / 2.75:
        t -= 1.5 / 2.75
        return 7.5625 * t * t + 0.75
    if t < 2.5 / 2.75:
        t -= 2.25 / 2.75
        return 7.5625 * t * t + 0.9375
    t -= 2.625 / 2.75
    return 7.5625 * t * t + 0.984375


def _elastic_in(t, period):
    if t in (0.0, 1.0):
        return t
    s = period / 4.0
    t -= 1.0
    return -(2.0 ** (10.0 * t)) * math.sin((t - s) * math.pi * 2.0 / period)


def _elastic_out(t, period):
    if t in (0.0, 1.0):
        return t
    s = period / 4.0
    return 2.0 ** (-10.0 * t) * math.sin((t - s) * math.pi * 2.0 / period) + 1.0


def _elastic_in_out(t, period):
    if t in (0.0, 1.0):
        return t
    period = period or 0.45
    s = period / 4.0
    t = t * 2.0 - 1.0
    if t < 0:
        return -0.5 * 2.0 ** (10.0 * t) * math.sin((t - s) * math.pi * 2.0 / period)
    return 2.0 ** (-10.0 * t) * math.sin((t - s) * math.pi * 2.0 / period) * 0.5 + 1.0


def _back_in_out(t):
    o = 1.70158 * 1.525
    t *= 2.0
    if t < 1.0:
        return t * t * ((o + 1.0) * t - o) / 2.0
    t -= 2.0
    return t * t * ((o + 1.0) * t + o) / 2.0 + 1.0


def ease(kind, rate, t):
    """How far along (0..1, may overshoot) a move is at time t (0..1)."""
    if kind == 1:
        t *= 2.0
        return 0.5 * t ** rate if t < 1.0 else 1.0 - 0.5 * (2.0 - t) ** rate
    if kind == 2:
        return t ** rate
    if kind == 3:
        return t ** (1.0 / rate) if rate else t
    if kind == 4:
        return _elastic_in_out(t, rate)
    if kind == 5:
        return _elastic_in(t, rate)
    if kind == 6:
        return _elastic_out(t, rate)
    if kind == 7:
        return (1.0 - _bounce(1.0 - t * 2.0)) * 0.5 if t < 0.5 else _bounce(t * 2.0 - 1.0) * 0.5 + 0.5
    if kind == 8:
        return 1.0 - _bounce(1.0 - t)
    if kind == 9:
        return _bounce(t)
    if kind == 10:
        t *= 2.0
        return 0.5 * 2.0 ** (10.0 * (t - 1.0)) if t < 1.0 else 0.5 * (2.0 - 2.0 ** (-10.0 * (t - 1.0)))
    if kind == 11:
        return 0.0 if t == 0.0 else 2.0 ** (10.0 * (t - 1.0)) - 0.001
    if kind == 12:
        return 1.0 if t == 1.0 else 1.0 - 2.0 ** (-10.0 * t)
    if kind == 13:
        return -0.5 * (math.cos(math.pi * t) - 1.0)
    if kind == 14:
        return 1.0 - math.cos(t * math.pi / 2.0)
    if kind == 15:
        return math.sin(t * math.pi / 2.0)
    if kind == 16:
        return _back_in_out(t)
    if kind == 17:
        o = 1.70158
        return t * t * ((o + 1.0) * t - o)
    if kind == 18:
        o = 1.70158
        t -= 1.0
        return t * t * ((o + 1.0) * t + o) + 1.0
    return t


# ---------------------------------------------------------------------- level side
class Trigger:
    __slots__ = ("uid", "id", "x", "y", "groups", "target", "how", "multi", "dx", "dy", "ticks",
                 "easing", "rate", "lock_x", "lock_y", "on", "delay", "half")

    def __repr__(self):
        return "<%s trigger #%d at x=%.0f -> group %d>" % (NAMES[self.id], self.uid, self.x, self.target)


def parse(raw, oid, x, y, groups, num):
    """A trigger from a level-string object, or None if it does nothing here."""
    t = Trigger()
    t.id, t.x, t.y, t.groups = oid, x, y, groups
    t.target = int(num(raw, 51))
    if t.target <= 0:
        return None
    t.how = "spawn" if num(raw, 62) else "touch" if num(raw, 11) else "x"
    t.multi = bool(num(raw, 87))
    t.dx, t.dy = num(raw, 28), num(raw, 29)
    t.ticks = max(0, int(round(num(raw, 10) * TPS))) if oid == MOVE else 0
    t.easing = int(num(raw, 30))
    t.rate = num(raw, 85, 2.0) or 2.0
    t.lock_x, t.lock_y = bool(num(raw, 58)), bool(num(raw, 59))
    t.on = bool(num(raw, 56))
    t.delay = max(0, int(round(num(raw, 63) * TPS)))
    t.half = TOUCH_SIZE / 2.0 * (abs(num(raw, 32, 1.0)) or 1.0)
    return t


class Cluster:
    """The objects that share the same moving/toggled groups: they always move together."""
    __slots__ = ("index", "groups", "objects", "sections", "minx", "maxx", "miny", "maxy")

    def query(self, minx, maxx, miny, maxy, stamp):
        out = []
        for s in range(int(minx // SECTION_SIZE), int(maxx // SECTION_SIZE) + 1):
            for o in self.sections.get(s, ()):
                if o.stamp != stamp:
                    o.stamp = stamp
                    if not (o.maxx < minx or maxx < o.minx or o.maxy < miny or maxy < o.miny):
                        out.append(o)
        return out


class Setup:
    """Everything about a level's triggers that never changes while it is played."""

    def __init__(self, triggers, objects, anchors):
        triggers.sort(key=lambda t: (t.x, t.uid))
        self.triggers = triggers
        self.passed = [t for t in triggers if t.how == "x"]
        self.passed_x = [t.x for t in self.passed]
        self.touched = sorted((t for t in triggers if t.how == "touch"), key=lambda t: t.x - t.half)
        self.touched_x = [t.x - t.half for t in self.touched]
        self.reach = max([t.half * 2.0 for t in self.touched] + [0.0])
        self.by_group = {}                     # group -> triggers in it (what a spawn trigger fires)
        for t in triggers:
            for g in t.groups:
                self.by_group.setdefault(g, []).append(t)
        self.anchors = anchors                 # group -> (x, y, groups) of its first object
        dynamic = {t.target for t in triggers if t.id in (MOVE, TOGGLE)}
        self.static = []
        clusters = {}
        for o in objects:
            key = tuple(sorted(g for g in o.groups if g in dynamic))
            if key:
                clusters.setdefault(key, []).append(o)
            else:
                self.static.append(o)
        self.clusters = []
        self.group_clusters = {}               # group -> indices of the clusters it moves
        for key, objs in clusters.items():
            c = Cluster()
            c.index, c.groups, c.objects = len(self.clusters), key, objs
            c.sections = {}
            for o in objs:
                for s in range(int(o.minx // SECTION_SIZE), int(o.maxx // SECTION_SIZE) + 1):
                    c.sections.setdefault(s, []).append(o)
            c.minx, c.maxx = min(o.minx for o in objs), max(o.maxx for o in objs)
            c.miny, c.maxy = min(o.miny for o in objs), max(o.maxy for o in objs)
            self.clusters.append(c)
            for g in key:
                self.group_clusters.setdefault(g, []).append(c.index)
        self.cluster_of = {o.uid: c.index for c in self.clusters for o in c.objects}


# ---------------------------------------------------------------------- one play-through
def _shifted(base):
    o = base.__class__()
    for name in base.__slots__:
        setattr(o, name, getattr(base, name))
    o.stamp = -1
    return o


class Run:
    """The triggers' state in one Simulation: what has fired, how far every group has
    moved, what is switched off.  snapshot()/restore() make it part of the simulation's
    own snapshots, so the bot can search levels with triggers too."""

    def __init__(self, sim, setup):
        self.sim, self.setup = sim, setup
        self.copies = {}                       # uid -> this run's moved copy of an object
        self.reset()

    def reset(self):
        n = len(self.setup.clusters)
        self.cursor = 0                        # passed triggers fired so far
        self.moved = {}                        # group -> (dx, dy) moved so far
        self.offsets = [(0.0, 0.0)] * n        # per cluster
        self.toggles = {}                      # group -> (order, on)
        self.order = 0
        self.off = [False] * n                 # per cluster: switched off
        self.moves = ()                        # running moves: (trigger, ticks done)
        self.spawns = ()                       # (tick, trigger) waiting to fire, in tick order
        self.fired = frozenset()               # uids of touch/spawn triggers that may not fire again
        self.last = None                       # the player's position on the previous step

    def snapshot(self):
        return (self.cursor, dict(self.moved), list(self.offsets), dict(self.toggles), self.order,
                list(self.off), self.moves, self.spawns, self.fired, self.last)

    def restore(self, snap):
        (self.cursor, moved, offsets, toggles, self.order, off, self.moves, self.spawns, self.fired,
         self.last) = snap
        self.moved, self.offsets, self.toggles, self.off = dict(moved), list(offsets), dict(toggles), list(off)

    # -- where things are now --------------------------------------------------------
    def query(self, minx, maxx, miny, maxy, out):
        """Add the moving objects that touch the rect to `out` (the level's query gives
        the ones that never move)."""
        setup = self.setup
        stamp = self.sim.level.next_stamp()
        for c in setup.clusters:
            if self.off[c.index]:
                continue
            dx, dy = self.offsets[c.index]
            if c.maxx + dx < minx or maxx < c.minx + dx or c.maxy + dy < miny or maxy < c.miny + dy:
                continue
            for base in c.query(minx - dx, maxx - dx, miny - dy, maxy - dy, stamp):
                o = self.copies.get(base.uid)
                if o is None:
                    o = self.copies[base.uid] = _shifted(base)
                if o.x != base.x + dx or o.y != base.y + dy:
                    o.x, o.y = base.x + dx, base.y + dy
                    o.minx, o.maxx, o.miny, o.maxy = base.minx + dx, base.maxx + dx, base.miny + dy, base.maxy + dy
                    if base.corners is not None:
                        o.corners = [(cx + dx, cy + dy) for cx, cy in base.corners]
                out.append(o)

    def offset(self, groups):
        dx = dy = 0.0
        for g in groups:
            m = self.moved.get(g)
            if m is not None:
                dx += m[0]
                dy += m[1]
        return dx, dy

    def enabled(self, groups):
        latest = None
        for g in groups:
            t = self.toggles.get(g)
            if t is not None and (latest is None or t[0] > latest[0]):
                latest = t
        return latest is None or latest[1]

    # -- each step ---------------------------------------------------------------------
    def step(self, p):
        """After the player has moved: fire what the player reached, then move groups."""
        setup, sim = self.setup, self.sim
        passed = setup.passed
        while self.cursor < len(passed) and passed[self.cursor].x <= p.x:
            t = passed[self.cursor]
            self.cursor += 1
            if self.enabled(t.groups):
                self.fire(t, p)
        if setup.touched:
            hw, hh = p.half_w, p.half_h
            stop = bisect.bisect_right(setup.touched_x, p.x + hw)
            start = bisect.bisect_left(setup.touched_x, p.x - hw - setup.reach)
            for t in setup.touched[start:stop]:
                if (t.uid not in self.fired and abs(t.x - p.x) <= hw + t.half and abs(t.y - p.y) <= hh + t.half
                        and self.enabled(t.groups)):
                    self.fire(t, p)
        if self.spawns and self.spawns[0][0] <= sim.tick:
            # only what was already due: a spawn loop with no delay goes round once per step
            due = [s for s in self.spawns if s[0] <= sim.tick]
            self.spawns = self.spawns[len(due):]
            for _tick, group in due:
                for t in setup.by_group.get(group, ()):
                    if t.uid not in self.fired and self.enabled(t.groups):
                        self.fire(t, p)
        if self.moves:
            self._advance(p)
        self.last = (p.x, p.y)

    def fire(self, t, p):
        if t.how != "x" and not t.multi:
            self.fired = self.fired | {t.uid}
        if t.id == MOVE:
            if t.ticks == 0 and not (t.lock_x or t.lock_y):
                self._move(t.target, t.dx, t.dy)
            elif len(self.moves) < MAX_MOVES:
                self.moves = self.moves + ((t, 0),)
        elif t.id == TOGGLE:
            self.order += 1
            self.toggles[t.target] = (self.order, t.on)
            for i in self.setup.group_clusters.get(t.target, ()):
                self.off[i] = not self.enabled(self.setup.clusters[i].groups)
        elif t.id == SPAWN:
            due = (self.sim.tick + t.delay, t.target)
            if due not in self.spawns and len(self.spawns) < MAX_WAITING:
                spawns = list(self.spawns)
                bisect.insort(spawns, due)
                self.spawns = tuple(spawns)
        elif t.id == TELEPORT:
            anchor = self.setup.anchors.get(t.target)
            if anchor is not None:
                dx, dy = self.offset(anchor[2])
                self.sim.teleport(p, anchor[0] + dx, anchor[1] + dy)
        self.sim._note("%s trigger -> group %d" % (NAMES[t.id], t.target))

    def _advance(self, p):
        running = []
        last = self.last or (p.x, p.y)
        for t, done in self.moves:
            done += 1
            total = max(1, t.ticks)
            if t.lock_x or t.lock_y:
                self._move(t.target, p.x - last[0] if t.lock_x else 0.0, p.y - last[1] if t.lock_y else 0.0)
            else:
                # the curve is pinned to 0 and 1 at the ends, so a move lands exactly on its offset
                # (cocos2d's exponential curves stop 0.05 - 0.1 % short of them)
                a = 0.0 if done == 1 else ease(t.easing, t.rate, (done - 1) / float(total))
                b = 1.0 if done >= total else ease(t.easing, t.rate, done / float(total))
                self._move(t.target, t.dx * (b - a), t.dy * (b - a))
            if done < total:
                running.append((t, done))
        self.moves = tuple(running)

    def _move(self, group, dx, dy):
        if not dx and not dy:
            return
        m = self.moved.get(group, (0.0, 0.0))
        self.moved[group] = (m[0] + dx, m[1] + dy)
        clusters = self.setup.clusters
        for i in self.setup.group_clusters.get(group, ()):
            self.offsets[i] = self.offset(clusters[i].groups)
