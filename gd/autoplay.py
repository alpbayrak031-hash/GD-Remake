"""A small brute-force player used to produce demo replays.

Depth-first search over "hold or release for the next N ticks" decisions, using the
simulation's snapshot/restore.  It is not clever - it exists so the bundled test
level ships with an input sequence that provably completes it, and to show that the
engine is deterministic enough to search.
"""
from .macros import InputSequence


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
    check: called every few nodes; it may block (to pause the search) or raise (to
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
    for _ in range(phase):                 # shift the decision grid by `phase` ticks
        sim.step(False)
    # each frame: [snapshot, next option index, chosen press]
    stack = [[sim.snapshot(), 0, False]]
    seen = set()
    nodes = 0
    best_x = 0.0
    sim.solver_reach = 0.0                 # furthest x seen; tells you where a failed search got stuck
    while stack:
        frame = stack[-1]
        prev_press = stack[-2][2] if len(stack) > 1 else False
        options = (False, True) if prefer_release else (prev_press, not prev_press)
        if frame[1] >= 2:
            stack.pop()
            continue
        press = options[frame[1]]
        frame[1] += 1
        frame[2] = press
        sim.restore(frame[0])
        for _ in range(chunk):
            sim.step(press)
            if sim.dead or sim.finished:
                break
        nodes += 1
        if check is not None and not nodes & 15:
            check()
        if sim.player.x > sim.solver_reach:
            sim.solver_reach = sim.player.x
        if nodes > max_nodes:
            return None
        if sim.dead:
            continue
        if sim.finished:
            events, tick, state = [], 0, False
            for snap, _idx, chosen in stack:
                if chosen != state:
                    events.append((snap[0], chosen))
                    state = chosen
            return InputSequence(events, {"solver_nodes": nodes, "chunk": chunk})
        p = sim.player
        # states that are nearly the same are assumed to have the same fate; that is
        # what keeps the search from re-exploring every hopeless variation
        key = (sim.tick, round(p.y / bucket_y), round(p.y_vel / bucket_v), int(p.mode),
               p.upside_down, press, p.can_jump, p.jump_unused, p.holding_jump,
               round(p.rotation / 15.0) % 24 if angled else 0)
        if key in seen:
            continue
        seen.add(key)
        if progress and p.x > best_x + 300:
            best_x = p.x
            progress(p.x, nodes)
        stack.append([sim.snapshot(), 0, False])
    return None
