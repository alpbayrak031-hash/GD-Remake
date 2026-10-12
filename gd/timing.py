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

from .analysis import rate
from .autoplay import solve
from .constants import TPS
from .level import Level
from .levelio import LevelFormatError, load_level_file
from .paths import SAVEDATA
from .sim import Simulation

DEFAULT_PATH = os.path.join(SAVEDATA, "timing.json")
VERSION = 5                      # raise it when the method changes: old results are then measured again
# input grids tried, coarsest first, in ticks of 1/240 s: 200 ms ... 4 ms (a single physics step)
CHUNKS = (48, 40, 32, 28, 24, 20, 16, 14, 12, 10, 8, 7, 6, 5, 4, 3, 2, 1)
MAX_NODES = 150000               # search budget per attempt
MAX_NODES_FINE = 400000          # ... and for the tick-by-tick attempts, whose positions are cheap but many
BUDGET_SECONDS = 45.0            # ... and per level, counting only time actually spent searching
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


def search(level, wait=None, chunks=CHUNKS, max_nodes=MAX_NODES, budget=BUDGET_SECONDS):
    """-> (result, InputSequence that finishes the level or None).  The result is one of
          {"state": "ok", "ticks": n, "ms": ms, "capped": bool}   the tightest input is about `ms`;
                                                                   capped: even the coarsest grid worked;
                                                                   "hold": True is added when the level can
                                                                   only be finished with a held jump
          {"state": "free"}                                        the level needs no input at all
          {"state": "glitch"}                                      only finishable with the gravity-flip grace
          {"state": "unsolved", "reason": "search" | "time"}       the bot found no way through

    `wait` is called regularly during the search; it may block or raise Cancelled."""
    spent, mark = [0.0], [time.perf_counter()]

    def check():
        spent[0] += time.perf_counter() - mark[0]
        if wait is not None:
            wait()                                  # time spent paused in here is not counted
        mark[0] = time.perf_counter()
        if spent[0] > budget:
            raise _OutOfTime()

    coarse = tuple(c for c in chunks if c > 1)
    try:
        chunk, seq = rate(level, chunks=coarse, max_nodes=max_nodes, check=check) if coarse else (None, None)
        if seq is None and 1 in chunks:
            # every moment the physics has: this finds a jump that works on one step only
            chunk, seq = rate(level, chunks=(1,), max_nodes=max(max_nodes, MAX_NODES_FINE), check=check)
            if seq is None:
                fine = max(max_nodes, MAX_NODES_FINE)
                # ... then the same measurement with held jumps allowed
                for part, nodes in ((coarse, max_nodes), ((1,), fine)):
                    if part and seq is None:
                        chunk, seq = rate(level, chunks=part, max_nodes=nodes, check=check, strict=False,
                                          allow_push=False, phases=HOLD_PHASES)
                if seq is not None:
                    return {"state": "ok", "ticks": chunk, "ms": to_ms(chunk), "capped": chunk == chunks[0],
                            "hold": True}, seq
                # ... and only if that fails too, any route at all, the glitch included
                seq = solve(Simulation(level), chunk=1, max_nodes=fine, check=check)
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

    def _work(self):
        while not self._stop:
            self._run.wait()
            with self._lock:
                path = next((p for p in self._queue if self.timings.lookup(p) is None), None)
                self.current = path
                if path is None:
                    self._queue = []
                    self._thread = None
                    return
            digest = self.timings.digest(path)
            result = None
            try:
                if digest is not None:
                    result = measure(Level(load_level_file(path)), wait=self._wait)
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
