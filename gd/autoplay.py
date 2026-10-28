"""The bot: finds a sequence of button presses that finishes a level.

Depth-first search over "hold or release for the next N ticks" decisions, using the
simulation's snapshot/restore (which also covers what triggers have moved, so levels
with triggers can be searched).  "Button up" is tried first at every decision, so the
bot only presses when it has to.  Positions that are nearly the same (height and speed
rounded, the same mode, gravity, speed, size and button state) are assumed to have the
same fate and are only explored once; when the search runs out of positions to try,
that is a proof (on this grid) that no route exists.

What keeps it quick:
  * a cube, ball or spider in mid-air can only use the button at an orb (or, when
    buffered jumps are allowed, by holding it into the landing), so away from orbs the
    bot does not try pressing there
  * the icon's angle is not worked out while searching (sim.visual = False) unless the
    level has objects at odd angles, where the angle changes the hitbox
  * Player.used is shared between snapshots instead of copied
Tried and measured worse, so not used: a layer-by-layer (beam) search, which drops the
one position a tight cube jump needs; steering ship/wave towards the middle of the gap,
and "same as before first" in flying modes, which each made some levels need several
times more positions.  How long a search may run is the caller's choice - gd/timing.py
gives long levels a budget in proportion to their length.
"""
from .macros import InputSequence
from .player import BALL, CUBE, SPIDER
from . import objects as O

_TAP_MODES = (CUBE, BALL, SPIDER)        # in the air, the button does nothing away from orbs
_BOTH = (False, True)


def _options(sim, last, strict):
    """The presses worth trying from the current position, button-up first."""
    reach = 0.0
    for q in (sim.player, sim.player2):
        if q is None:
            continue
        if q.mode not in _TAP_MODES or q.can_jump or q.is_dashing or q.is_on_slope:
            return _BOTH
        reach = max(reach, q.x_vel * 0.25 * 64 + 60.0)
    p = sim.player
    for o in sim.query(p.x - 40.0, p.x + reach, -1e9, 1e9):
        if o.kind == O.ORB:
            return _BOTH
    if last and not strict:
        return _BOTH                          # holding on into the landing is a buffered jump
    return (False,)


def solve(sim, chunk=6, max_nodes=600000, progress=None, bucket_y=None, bucket_v=None,
          prefer_release=True, phase=0, strict=False, check=None, allow_push=True):
    """Returns an InputSequence that finishes the level, or None.

    prefer_release: try "button up" first at every decision, so the bot only presses
    when it has to (otherwise it tries "same as before" first, which is faster to
    search but produces long pointless holds).
    strict: forbid buffered jumps and the gravity-flip grace (see Player.strict_taps);
    used when rating a level.
    allow_push: False forbids only the gravity-flip grace (constants.FLIP_GRACE_PUSH) -
    held jumps stay allowed.
    check: called every few positions; it may block (to pause the search) or raise (to
    abandon it) - that is how a search running in the background stays out of the way."""
    # finer decisions need a finer notion of "the same state", or tight gaps get pruned away
    if bucket_y is None:
        bucket_y = max(0.5, chunk / 4.0)
    if bucket_v is None:
        bucket_v = max(0.05, chunk / 32.0)
    # the icon's angle only matters next to objects placed at an odd angle (see gd/sim.py)
    angled = any(o.corners is not None for o in sim.level.objects)
    sim.reset()
    sim.player.strict_taps = strict
    sim.player.no_flip_push = not allow_push
    sim.visual = False
    try:
        for _ in range(phase):                # shift the decision grid by `phase` ticks
            sim.step(False)
        return _dfs(sim, chunk, max_nodes, progress, bucket_y, bucket_v, prefer_release, strict, check, angled)
    finally:
        sim.visual = True                     # a game view gets a simulation that turns the icon again


def _dfs(sim, chunk, max_nodes, progress, bucket_y, bucket_v, prefer_release, strict, check, angled):
    def order(last):
        options = _options(sim, last, strict)
        if len(options) == 2 and not prefer_release and last:
            return (True, False)              # "the same as before" first
        return options

    # each frame: [snapshot, options in the order to try them, next option index, chosen press]
    stack = [[sim.snapshot(), order(False), 0, False]]
    seen = set()
    nodes = 0
    best_x = 0.0
    sim.solver_reach = 0.0                    # furthest x seen; tells you where a failed search got stuck
    while stack:
        frame = stack[-1]
        options = frame[1]
        if frame[2] >= len(options):
            stack.pop()
            continue
        press = options[frame[2]]
        frame[2] += 1
        frame[3] = press
        sim.restore(frame[0])
        for _ in range(chunk):
            sim.step(press)
            if sim.dead or sim.finished:
                break
        nodes += 1
        if check is not None and not nodes & 15:
            check()
        p = sim.player
        if p.x > sim.solver_reach:
            sim.solver_reach = p.x
        if nodes > max_nodes:
            return None
        if sim.dead:
            continue
        if sim.finished:
            events, state = [], False
            for snap, _options_, _idx, chosen in stack:
                if chosen != state:
                    events.append((snap[0], chosen))
                    state = chosen
            return InputSequence(events, {"solver_nodes": nodes, "chunk": chunk})
        # states that are nearly the same are assumed to have the same fate; that is
        # what keeps the search from re-exploring every hopeless variation
        q, trig = sim.player2, sim.trig       # dual mode: the second icon is part of the state too
        key = (sim.tick, round(p.y / bucket_y), round(p.y_vel / bucket_v), int(p.mode), p.upside_down, press,
               p.can_jump, p.jump_unused, p.holding_jump, p.speed, p.mini, p.is_dashing,
               round(p.rotation / 15.0) % 24 if angled else 0,
               None if q is None else (round(q.y / bucket_y), round(q.y_vel / bucket_v), int(q.mode),
                                       q.upside_down, q.can_jump),
               None if trig is None else trig.fired)          # which touch/spawn triggers have gone off
        if key in seen:
            continue
        seen.add(key)
        if progress and p.x > best_x + 300:
            best_x = p.x
            progress(p.x, nodes)
        stack.append([sim.snapshot(), order(press), 0, False])
    return None
