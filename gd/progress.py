"""Saved progress: best percentage, attempts, completions, per level.

Stored as JSON in savedata/progress.json in the player's folder (see gd/paths.py).
A level is identified by where its file is: "levels/<file>" for the built-in pack,
"mine/<file>" for a level in the player's own levels folder, and the full path for a
file anywhere else - so rebuilding or editing a level keeps its record, and so does
moving the game or the player's folder.
"""
import json
import os
import time

from .paths import ROOT, SAVEDATA, USER_LEVELS, inside

SAVE_PATH = os.path.join(SAVEDATA, "progress.json")
DEFAULTS = {"best": 0.0, "practice": 0.0, "attempts": 0, "completions": 0, "best_time": None,
            "jumps": 0, "seconds": 0.0, "last_played": None}


def level_key(path):
    path = os.path.abspath(path)
    if inside(path, USER_LEVELS):
        return "mine/" + os.path.relpath(path, USER_LEVELS).replace(os.sep, "/")
    try:
        rel = os.path.relpath(path, ROOT)
    except ValueError:                       # different drive on Windows
        rel = path
    return (path if rel.startswith("..") else rel).replace(os.sep, "/")


class Progress:
    def __init__(self, path=SAVE_PATH):
        self.path = path
        self.levels = {}
        self.load()

    # ------------------------------------------------------------------ file
    def load(self):
        self.levels = {}
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            levels = data.get("levels", {})
            if isinstance(levels, dict):
                self.levels = {k: dict(DEFAULTS, **v) for k, v in levels.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            # unreadable save: keep it for inspection, start clean
            try:
                os.replace(self.path, self.path + ".broken")
            except OSError:
                pass

    def save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "levels": self.levels}, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)           # atomic: a crash never leaves half a file

    # ------------------------------------------------------------------ reading
    def get(self, key):
        return dict(DEFAULTS, **self.levels.get(key, {}))

    def completed(self, key):
        return self.get(key)["completions"] > 0

    def totals(self, keys=None):
        entries = [self.get(k) for k in (keys if keys is not None else self.levels)]
        return {"levels": len(entries),
                "completed": sum(1 for e in entries if e["completions"] > 0),
                "attempts": sum(e["attempts"] for e in entries),
                "jumps": sum(e["jumps"] for e in entries),
                "seconds": sum(e["seconds"] for e in entries)}

    # ------------------------------------------------------------------ writing
    def _entry(self, key):
        if key not in self.levels:
            self.levels[key] = dict(DEFAULTS)
        entry = self.levels[key]
        entry["last_played"] = time.strftime("%Y-%m-%d %H:%M")
        return entry

    def attempt(self, key):
        self._entry(key)["attempts"] += 1

    def reached(self, key, percent, practice=False):
        """Record how far an attempt got (0-100).  Returns True for a new best."""
        entry = self._entry(key)
        field = "practice" if practice else "best"
        percent = max(0.0, min(100.0, float(percent)))
        if percent > entry[field]:
            entry[field] = round(percent, 1)
            return True
        return False

    def complete(self, key, seconds, practice=False):
        entry = self._entry(key)
        if practice:
            entry["practice"] = 100.0
            return
        entry["best"] = 100.0
        entry["completions"] += 1
        if entry["best_time"] is None or seconds < entry["best_time"]:
            entry["best_time"] = round(seconds, 2)

    def add_play(self, key, seconds=0.0, jumps=0):
        entry = self._entry(key)
        entry["seconds"] = round(entry["seconds"] + seconds, 1)
        entry["jumps"] += jumps

    def reset(self, key=None):
        if key is None:
            self.levels = {}
        else:
            self.levels.pop(key, None)
        self.save()

    def rename(self, old, new):
        if old in self.levels:
            self.levels[new] = self.levels.pop(old)
