"""Small preferences that should survive quitting the game, kept in
savedata/settings.json in the player's folder (see gd/paths.py)."""
import json
import os

from .paths import SAVEDATA                   # (read when called, so tests can point it elsewhere)

DEFAULTS = {"show_fps": False, "show_deco": True}


def _path():
    return os.path.join(SAVEDATA, "settings.json")


def load():
    values = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            values.update({k: v for k, v in saved.items() if k in DEFAULTS and type(v) is type(DEFAULTS[k])})
    except (OSError, ValueError):
        pass                              # no file yet, or a damaged one: the defaults
    return values


def get(key):
    return load()[key]


def put(key, value):
    """Remember one setting.  Failing to write it only means it is forgotten."""
    values = load()
    values[key] = value
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(values, fh, indent=1, sort_keys=True)
        os.replace(tmp, _path())
    except OSError:
        pass
