"""How tight is the tightest input of a level, in milliseconds?  My Levels shows it.

The number is gd.analysis.rate() on a fine list of input grids: the COARSEST grid on
which a search can still finish the level (on three grid phases, without buffered
jumps).  If a level can be finished when the button may only change every 20 ticks,
no input in it needs better timing than about 20 / 240 s = 83 ms - so that is, to the
resolution of the list below, the window of its tightest jump.  It is a measurement
of this engine's level, made by a bot: an estimate, not a promise.

The list ends at a grid of ONE tick, i.e. every moment the physics has.  A level
whose only way through is a single 1/240 s step ("frame perfect") is therefore still
found and reads ~4 ms.  Only when no such route exists does the bot fall back, in
this order:
  1. routes that hold the button through a landing (a buffered jump: the next jump
     starts by itself on the first step the player is back on the ground).  The same
     grid measurement is run with holding allowed, on six grid phases instead of three,
     and its number is shown like any other - the result carries "hold": True.
  2. last of all, a route that uses the gravity-flip grace - the "glitch" that lifts a
     ball onto a block (constants.FLIP_GRACE_PUSH).  No number is given for those.
The glitch is therefore only ever used when the bot can find no other way, and "no way
through found" is only said when even that fails.

search()    one level -> a result dict and the bot's inputs
measure()   one level -> just the result dict
Timings     results remembered in savedata/timing.json, per file contents
Measurer    works through a queue of level files on a background thread, and steps
            aside (pauses) whenever the game or the editor needs the processor
"""
import hashlib
import json
import os
import threading
import time

from . import constants as C
from . import objects as O
from .analysis import rate
from .autoplay import solve
from .constants import TPS
from .level import Level
from .levelio import LevelFormatError, load_level_file
from .paths import SAVEDATA
from .sim import Simulation

DEFAULT_PATH = os.path.join(SAVEDATA, "timing.json")
VERSION = 7                      # raise it when the method changes: old results are then measured again
# input grids tried, coarsest first, in ticks of 1/240 s: 200 ms ... 4 ms (a single physics step)
CHUNKS = (48, 40, 32, 28, 24, 20, 16, 14, 12, 10, 8, 7, 6, 5, 4, 3, 2, 1)
MAX_NODES = 150000               # search budget per attempt
MAX_NODES_FINE = 400000          # ... and for the tick-by-tick attempts, whose positions are cheap but many
BUDGET_SECONDS = 45.0            # ... and per level, counting only time actually spent searching
# A longer level needs more: a route is a decision every few ticks all the way through, and
# the search has to find each of them.  The budgets above are for a level of up to
# SHORT_LEVEL seconds and grow in proportion beyond that, up to LONGEST times as much.
SHORT_LEVEL = 30.0
LONGEST = 8.0
# A grid that does not work almost always shows it the same way: the search gets stuck at
# one spot and spends its whole budget trying every variation before it.  So a search
# gives up after this many physics steps without getting a block further.  That alone
# could turn a "yes" into a "no": on the pack, searches that went on to succeed sometimes
# went up to about 410 000 steps without progress (Overclock, Zero G).  So a grid that
# only gave up this way is searched again in full whenever the grid below it works (see
# analysis.rate): the number shown stays what a full search finds - it only comes much
# sooner, because the grids far from the answer are no longer searched to the end.
STALL_STEPS = 250000
SEARCH_SHARE = 0.9               # of a progress bar: the search; what is left is for preparing a replay
HOLD_PHASES = 6                  # grid phases a route with held jumps must work on (see analysis.rate)
# The level pack's difficulty bands (tools/make_level_pack.py), as milliseconds
BANDS = ((100, "easy"), (50, "medium"), (30, "hard"), (0, "very hard"))


class Cancelled(Exception):
    """Raised inside a search to abandon it."""


class _OutOfTime(Exception):
    pass


def to_ms(ticks):
    return int(round(ticks * 1000.0 / TPS))


def band(ms):
    for floor, name in BANDS:
        if ms >= floor:
            return name
    return BANDS[-1][1]


def level_seconds(level):
    """About how long a level takes to play: its length at the speed of each stretch
    between speed portals (at the start speed until the first one)."""
    portals = sorted((o.x, o.arg) for o in level.objects if o.kind == O.PORTAL_SPEED)
    x, speed, seconds = 0.0, level.start_speed, 0.0
    for px, arg in portals + [(level.end_x, None)]:
        if px > x:
            row = C.SPEED_TABLE[speed]
            seconds += (min(px, level.end_x) - x) / (row[0] * row[3] * C.DT * TPS)
            x = px
        if arg is not None:
            speed = arg
    return seconds


def scale_for(level):
    """How many times the standard budgets a level gets (see SHORT_LEVEL)."""
    return max(1.0, min(LONGEST, level_seconds(level) / SHORT_LEVEL))


def search(level, wait=None, chunks=CHUNKS, max_nodes=None, budget=None, progress=None):
    """-> (result, InputSequence that finishes the level or None).  The result is one of
          {"state": "ok", "ticks": n, "ms": ms, "capped": bool}   the tightest input is about `ms`;
                                                                   capped: even the coarsest grid worked;
                                                                   "hold": True is added when the level can
                                                                   only be finished with a held jump
          {"state": "free"}                                        the level needs no input at all
          {"state": "glitch"}                                      only finishable with the gravity-flip grace
          {"state": "unsolved", "reason": "search" | "time"}       the bot found no way through

    `wait` is called regularly during the search; it may block or raise Cancelled.
    max_nodes / budget: per attempt / per level; by default the standard ones, scaled up
    for a long level (scale_for).
    progress(fraction, text): how far the measurement has got (it only goes up, and stops
    at SEARCH_SHARE: the rest is the caller's - the editor uses it to prepare the replay)
    and what it is doing, e.g. (0.58, "trying a 17 ms grid")."""
    scale = scale_for(level)
    if max_nodes is None:
        max_nodes = int(MAX_NODES * scale)
    if budget is None:
        budget = BUDGET_SECONDS * scale
    spent, mark = [0.0], [time.perf_counter()]

    def check():
        spent[0] += time.perf_counter() - mark[0]
        if wait is not None:
            wait()                                  # time spent paused in here is not counted
        mark[0] = time.perf_counter()
        if spent[0] > budget:
            raise _OutOfTime()

    best = [0.0]

    def report(text, x):
        if progress is not None:
            best[0] = max(best[0], min(1.0, x / level.end_x))
            progress(best[0] * SEARCH_SHARE, text)

    def on_grid(stage):
        def hook(chunk, _phase, x, note="first"):
            grid = "a %d ms grid" % to_ms(chunk) if chunk > 1 else "every physics step"
            if note == "again":
                text = "found a route - checking %s from another start" % grid
            elif note == "recheck":
                text = "double-checking %s" % grid
            else:
                text = "%s %s" % (stage, grid)
            report(text, x)
        return hook

    def on_solve(text):
        return lambda x, _nodes: report(text, x)

    tries = on_grid("trying")
    report("starting", 0.0)
    coarse = tuple(c for c in chunks if c > 1)
    try:
        chunk, seq = rate(level, chunks=coarse, max_nodes=max_nodes, check=check, stall=STALL_STEPS,
                          progress=tries) if coarse else (None, None)
        if seq is None and 1 in chunks:
            # every moment the physics has: this finds a jump that works on one step only
            fine = max(max_nodes, int(MAX_NODES_FINE * scale))
            chunk, seq = rate(level, chunks=(1,), max_nodes=fine, check=check, stall=STALL_STEPS, progress=tries)
            if seq is None:
                # ... then with held jumps allowed.  First the one question that settles it -
                # is there any such route, step by step? - so that an impossible level does
                # not cost a whole second pass over every grid; only then how coarse it can be.
                seq = solve(Simulation(level), chunk=1, max_nodes=fine, check=check, allow_push=False,
                            stall=STALL_STEPS, progress=on_solve("trying held jumps"))
                if seq is not None:
                    chunk, held = 1, seq
                    if coarse:
                        chunk, seq = rate(level, chunks=coarse, max_nodes=max_nodes, check=check, strict=False,
                                          allow_push=False, phases=HOLD_PHASES, stall=STALL_STEPS,
                                          progress=on_grid("held jumps:"))
                    if seq is None:
                        chunk, seq = 1, held
                    return {"state": "ok", "ticks": chunk, "ms": to_ms(chunk), "capped": chunk == chunks[0],
                            "hold": True}, seq
                # ... and only if that fails too, any route at all, the glitch included
                seq = solve(Simulation(level), chunk=1, max_nodes=fine, check=check, stall=STALL_STEPS,
                            progress=on_solve("trying the glitch"))
                if seq is not None:
                    return {"state": "glitch"}, seq
    except _OutOfTime:
        return {"state": "unsolved", "reason": "time"}, None
    if seq is None:
        return {"state": "unsolved", "reason": "search"}, None
    if not any(down for _tick, down in seq.events):
        return {"state": "free"}, seq
    return {"state": "ok", "ticks": chunk, "ms": to_ms(chunk), "capped": chunk == chunks[0]}, seq


def measure(level, **options):
    """search() without the inputs."""
    return search(level, **options)[0]


def describe(result):
    """-> (text, difficulty band or None) for a result, e.g. ("~83 ms", "medium")."""
    if result is None:
        return "measuring...", None
    state = result.get("state")
    if state == "ok":
        ms = result["ms"]
        return ("%d+ ms" % ms if result.get("capped") else "~%d ms" % ms), band(ms)
    if state == "free":
        return "no input needed", "easy"
    if state == "glitch":
        return "glitch route only", None
    if state == "unsolved":
        return "no way through found", None
    return "", None


def file_digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


class Timings:
    """Results by file name, each valid only for the exact file contents it was measured on."""

    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        self.levels = {}
        self._stamps = {}                 # path -> (mtime, size, digest): avoids re-reading unchanged files
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("version") == VERSION and isinstance(data.get("levels"), dict):
                self.levels = {k: v for k, v in data["levels"].items()
                               if isinstance(v, dict) and isinstance(v.get("result"), dict)}
        except (OSError, ValueError, AttributeError):
            pass                          # no file yet, or a damaged one: everything is simply measured again

    def digest(self, level_path):
        try:
            st = os.stat(level_path)
            hit = self._stamps.get(level_path)
            if hit and hit[:2] == (st.st_mtime, st.st_size):
                return hit[2]
            digest = file_digest(level_path)
        except OSError:
            return None
        self._stamps[level_path] = (st.st_mtime, st.st_size, digest)
        return digest

    def lookup(self, level_path):
        entry = self.levels.get(os.path.basename(level_path))
        if entry and entry.get("sha1") == self.digest(level_path):
            return entry["result"]
        return None

    def store(self, level_path, digest, result):
        self.levels[os.path.basename(level_path)] = {"sha1": digest, "result": result}
        self.save()

    def rename(self, old_path, new_path):
        """The file was renamed: its result goes with it."""
        entry = self.levels.pop(os.path.basename(old_path), None)
        self._stamps.pop(old_path, None)
        if entry is not None:
            self.levels[os.path.basename(new_path)] = entry
            self.save()

    def save(self):
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": VERSION, "levels": self.levels}, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass                          # a cache: losing it only costs a re-measure


class Measurer:
    """Measures level files one at a time on a background thread.

    The thread never touches the window.  The owner calls want() with the files it is
    showing, reads results with get(), watches `version` to learn that something new
    arrived, and calls pause() / resume() around anything that must stay smooth."""

    def __init__(self, timings=None):
        self.timings = timings if timings is not None else Timings()
        self.version = 0
        self.current = None
        self.progress = 0.0                         # how far into `current` the bot has got (0..1)
        self._queue = []
        self._lock = threading.Lock()
        self._run = threading.Event()
        self._run.set()
        self._stop = False
        self._thread = None

    # ------------------------------------------------------------------ owner's side
    def get(self, path):
        """The remembered result for this file as it is now, or None (not measured yet)."""
        return self.timings.lookup(path)

    def want(self, paths):
        """These files should have a result; the first ones are done first."""
        todo = [p for p in paths if self.timings.lookup(p) is None]
        with self._lock:
            self._queue = todo
            if todo and (self._thread is None or not self._thread.is_alive()) and not self._stop:
                self._thread = threading.Thread(target=self._work, name="timing", daemon=True)
                self._thread.start()

    def pause(self):
        self._run.clear()

    def resume(self):
        self._run.set()

    def stop(self):
        self._stop = True
        self._run.set()

    @property
    def busy(self):
        with self._lock:
            return self.current is not None or bool(self._queue)

    # ------------------------------------------------------------------ the thread
    def _wait(self):
        self._run.wait()
        if self._stop:
            raise Cancelled()
        with self._lock:
            if self.current not in self._queue:      # no longer wanted (deleted, or another screen)
                raise Cancelled()

    def _progressed(self, fraction, _text):
        self.progress = fraction

    def _work(self):
        while not self._stop:
            self._run.wait()
            with self._lock:
                path = next((p for p in self._queue if self.timings.lookup(p) is None), None)
                self.current = path
                self.progress = 0.0
                if path is None:
                    self._queue = []
                    self._thread = None
                    return
            digest = self.timings.digest(path)
            result = None
            try:
                if digest is not None:
                    result = measure(Level(load_level_file(path)), wait=self._wait, progress=self._progressed)
            except Cancelled:
                result = None
            except (LevelFormatError, OSError, ValueError):
                result = {"state": "unreadable"}
            except Exception as exc:                  # a bug must not kill the thread silently
                result = {"state": "unsolved", "reason": "error: %s" % type(exc).__name__}
            with self._lock:
                self.current = None
                if result is None or digest is None or self.timings.digest(path) != digest:
                    # cancelled, vanished, or edited while it was being measured
                    self._queue = [p for p in self._queue if p != path]
                else:
                    self.timings.store(path, digest, result)
                self.version += 1
