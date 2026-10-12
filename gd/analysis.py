"""How forgiving is a level?

A solver proving "some input sequence finishes the level" says nothing about whether
a person can do it - a search happily uses frame-perfect or unintuitive tricks (the
first version of the bundled test level had a pad jump that was only possible that
way).  Two measurements are used instead:

rate()     the COARSEST input grid on which the level is still solvable.  If a level
           can be finished when the button may only change every 24 ticks (0.1 s), on
           three different grid phases, then no input in it needs better timing than
           roughly that.  Buffered jumps are forbidden during this search: holding
           the button so the cube re-jumps the instant it lands makes the jump time
           depend on the PREVIOUS jump instead of on the grid, which lets a search hit
           arbitrarily narrow windows and makes a level look far more lenient than it
           is (a triple spike has a 50 ms window; without this rule it rated 133 ms).
           The gravity-flip grace (constants.FLIP_GRACE_PUSH) is switched off as well:
           it lets a ball that switches gravity just before a one-block step be lifted
           onto it, and a rating - or a bot run - built on that is not how a level is
           meant to be played.

Analyzer   for one concrete solution: how far each press can move earlier or later
           (together with its release), and how far each release can move, before
           the run fails.  `centre()` moves every input to the middle of its window,
           which removes the solver's "last possible moment" bias and makes the bot's
           replay look like something a person would do.
"""
from .autoplay import solve
from .macros import InputSequence
from .sim import Simulation

MAX_SHIFT = 96          # ticks (0.4 s); wider windows are reported as this
last_reach = 0.0        # how far the solver got in the most recent rate() call (for diagnostics)
SNAP_EVERY = 240


def _signature(sim):
    p = sim.player
    return (p.x, p.y, p.y_vel, p.mode, p.upside_down, p.mini, p.speed, p.can_jump, p.on_ground,
            p.is_jumping, p.jump_unused, p.holding_jump, p.velocity_uncapped, p.robot_boost_charge,
            p.robot_boost_invalidated, p.is_dashing, p.dash_y, p.is_on_slope, p.slope, p.snap_obj,
            p.snap_distance, p.last_flip_tick, p.last_spider_tick, sim.floor, sim.ceiling,
            len(p.used), len(sim.destroyed))


def _states_from(events, start):
    """Button state for ticks start, start+1, ... given sorted (tick, down) events."""
    i, n, state = 0, len(events), False
    while i < n and events[i][0] < start:
        state = events[i][1]
        i += 1
    tick = start
    while True:
        while i < n and events[i][0] <= tick:
            state = events[i][1]
            i += 1
        yield state
        tick += 1


class Analyzer:
    def __init__(self, level, sequence):
        self.level = level
        self.sim = Simulation(level)
        self.meta = dict(sequence.meta)
        self.set_events(list(sequence.events))

    # ------------------------------------------------------------------ reference run
    def set_events(self, events):
        self.events = events
        sim = self.sim
        sim.reset()
        self.snaps, self.sigs = {}, []
        states = _states_from(events, 0)
        while not sim.dead and not sim.finished:
            if sim.tick % SNAP_EVERY == 0:
                self.snaps[sim.tick] = sim.snapshot()
            sim.step(next(states))
            self.sigs.append(_signature(sim))
        self.completes = sim.finished
        self.ticks = sim.tick

    def _try(self, trial, first, last):
        """Does `trial` (differing from the reference only in ticks first..last) finish?"""
        start = (max(0, first - 1) // SNAP_EVERY) * SNAP_EVERY
        while start not in self.snaps and start > 0:
            start -= SNAP_EVERY
        sim = self.sim
        sim.restore(self.snaps[start])
        states = _states_from(trial, start)
        sigs = self.sigs
        while not sim.dead and not sim.finished:
            sim.step(next(states))
            t = sim.tick
            if t > last + 1 and t <= len(sigs) and _signature(sim) == sigs[t - 1]:
                return True              # back on the reference trajectory: same future
        return sim.finished

    # ------------------------------------------------------------------ windows
    def _shifted(self, idxs, shift):
        trial = list(self.events)
        for i in idxs:
            trial[i] = (trial[i][0] + shift, trial[i][1])
        ticks = [self.events[i][0] for i in idxs] + [trial[i][0] for i in idxs]
        return trial, min(ticks), max(ticks)

    def _limit(self, idxs, direction, room):
        room = max(0, min(room, MAX_SHIFT))

        def ok(shift):
            return self._try(*self._shifted(idxs, direction * shift))

        if room == 0 or not ok(1):
            return 0
        if ok(room):
            return room
        lo, hi = 1, room
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        return lo

    def _groups(self):
        """(kind, indices, room earlier, room later) for every press (moved together with
        its release) and every release (moved alone)."""
        ev = self.events
        n = len(ev)
        out = []
        for i, (tick, down) in enumerate(ev):
            prev_tick = ev[i - 1][0] if i > 0 else -1
            if down:
                j = i + 1 if i + 1 < n and not ev[i + 1][1] else i
                after = ev[j + 1][0] if j + 1 < n else None
                out.append(("press", list(range(i, j + 1)), tick - prev_tick - 1,
                            MAX_SHIFT if after is None else after - ev[j][0] - 1))
            else:
                after = ev[i + 1][0] if i + 1 < n else None
                out.append(("release", [i], tick - prev_tick - 1,
                            MAX_SHIFT if after is None else after - tick - 1))
        return out

    def windows(self):
        """-> list of dicts: kind, tick, earlier, later, ticks (window size)."""
        out = []
        for kind, idxs, room_early, room_late in self._groups():
            early = self._limit(idxs, -1, room_early)
            late = self._limit(idxs, +1, room_late)
            out.append({"kind": kind, "tick": self.events[idxs[0]][0], "earlier": early,
                        "later": late, "ticks": early + late + 1})
        return out

    def centre(self, passes=2):
        for _ in range(passes):
            moved = False
            for g in range(len(self._groups())):
                kind, idxs, room_early, room_late = self._groups()[g]
                early = self._limit(idxs, -1, room_early)
                late = self._limit(idxs, +1, room_late)
                shift = (late - early) // 2
                if shift:
                    trial, first, last = self._shifted(idxs, shift)
                    if self._try(trial, first, last):
                        self.set_events(trial)
                        moved = True
            if not moved:
                break
        return InputSequence(self.events, self.meta)

    def report(self, worst=5):
        """Tightest press window (the headline number), tightest release window, and
        where the `worst` tightest inputs are."""
        win = self.windows()
        presses = [w for w in win if w["kind"] == "press"]
        releases = [w for w in win if w["kind"] == "release"]
        ranked = sorted(win, key=lambda w: w["ticks"])[:worst]
        xs = {}
        for w in ranked:
            snap_tick = (w["tick"] // SNAP_EVERY) * SNAP_EVERY
            self.sim.restore(self.snaps.get(snap_tick, self.snaps[0]))
            states = _states_from(self.events, self.sim.tick)
            while self.sim.tick < w["tick"] and not self.sim.dead and not self.sim.finished:
                self.sim.step(next(states))
            xs[w["tick"]] = self.sim.player.x

        def ms(ticks):
            return int(round(ticks * 1000.0 / 240.0))

        cap = MAX_SHIFT * 2 + 1
        press_min = min([w["ticks"] for w in presses] or [cap])
        release_min = min([w["ticks"] for w in releases] or [cap])
        return {"presses": len(presses), "press_window_ticks": press_min, "press_window_ms": ms(press_min),
                "release_window_ticks": release_min, "release_window_ms": ms(release_min),
                "tightest": [{"kind": w["kind"], "ms": ms(w["ticks"]), "tick": w["tick"],
                              "column": round(xs[w["tick"]] / 30.0, 1)} for w in ranked]}


def rate(level, chunks=(32, 24, 16, 12, 8, 6, 4), max_nodes=250000, info=None, check=None,
         strict=True, allow_push=True, phases=3):
    """-> (chunk, InputSequence) for the coarsest input grid that solves the level on
    three different phases, or (None, None).  Bigger chunk = more forgiving level.

    Requiring several phases matters: a single lucky grid alignment can make one tight
    jump look easy, three cannot.  If `info` is a dict it receives "limit_x": how far
    the search got on the last grid that was too coarse, i.e. where the hardest spot is.
    `check` is handed to the solver (see autoplay.solve).

    strict=False allows held (buffered) jumps.  That is only meant for levels that cannot
    be finished without one: a chain of held jumps lets the search land on a narrow window
    from a coarse grid, so the result can look more forgiving than the level is.  Asking
    for more `phases` makes such luck less likely.  allow_push: see autoplay.solve.
    """
    global last_reach
    sim = Simulation(level)
    last_reach = 0.0
    limit_x = None
    for chunk in chunks:
        result = None
        # evenly spread grid offsets (grids of only a few ticks have fewer distinct ones)
        for phase in sorted({(i * chunk) // phases for i in range(phases)}):
            seq = solve(sim, chunk=chunk, max_nodes=max_nodes, phase=phase, strict=strict, check=check,
                        allow_push=allow_push)
            last_reach = max(last_reach, sim.solver_reach)
            if seq is None:
                limit_x = sim.solver_reach
                result = None
                break
            result = result or seq
        if result is not None:
            if info is not None:
                info["limit_x"] = limit_x
            return chunk, result
    if info is not None:
        info["limit_x"] = limit_x
    return None, None
