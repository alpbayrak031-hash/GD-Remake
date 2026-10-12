"""Where things live.

ROOT         the program: the gd package and levels/, which holds the built-in level
             pack.  Inside the macOS app this is Contents/Resources/app.  Nothing is
             ever written here while the game runs.
HOME         the player's folder, ~/Documents/GD Remake.  Everything the game writes:
               levels/      the levels you build or import (deleted ones: levels/trash/)
               savedata/    progress.json, timing.json and the editor's backups/
             Set the environment variable GD_REMAKE_HOME to use another folder (the
             tests do, so they never touch the real one).
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_LEVELS = os.path.join(ROOT, "levels")

HOME = os.environ.get("GD_REMAKE_HOME") or os.path.join(os.path.expanduser("~"), "Documents", "GD Remake")
USER_LEVELS = os.path.join(HOME, "levels")
SAVEDATA = os.path.join(HOME, "savedata")
BACKUPS = os.path.join(SAVEDATA, "backups")


def ensure_home():
    """Create the player's folders.  Returns "" on success, else why it failed (for
    instance when macOS was told not to let the game into Documents)."""
    try:
        for folder in (USER_LEVELS, SAVEDATA):
            os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        return exc.strerror or str(exc)
    return ""


def inside(path, folder):
    """Is `path` in `folder` (or a folder below it)?"""
    path, folder = os.path.abspath(path), os.path.abspath(folder)
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:                       # different drives on Windows
        return False
