"""The one window of the program: menu, game and editor.

A menu screen is an object with draw() and key(); the app clears its canvas and asks
the current screen to draw whenever something changes.  Playing a level swaps the
menu canvas for a GameView, editing swaps it for the Editor, and play-testing from
the editor swaps the editor for a GameView and back - always inside this window, so
there is a single program (and a single dock icon) however deep you go.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from . import icons, online, paths, share, ui
from .level import Level
from .levelio import LevelFormatError, load_level_file, write_gmd
from .macros import MacroFormatError, load_macro
from .paths import safe_stem, unique_path
from .progress import Progress, level_key
from .render_tk import GameView
from .sim import Simulation
from .timing import Measurer, describe

PACK = paths.PACK_LEVELS              # the built-in levels, shipped with the program
LEVELS = paths.USER_LEVELS            # the player's own: Documents/GD Remake/levels
LEVEL_EXTENSIONS = (".gmd", ".gmd2", ".lvl")
PER_PAGE = 6


# ------------------------------------------------------------------ level catalogue
def load_index():
    path = os.path.join(PACK, "index.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            return json.load(fh).get("levels", [])
    except (OSError, ValueError):
        return []


_info_cache = {}


def level_info(path):
    """Name, object count, modes used and a rough length for any level file (cached)."""
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    hit = _info_cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        raw = load_level_file(path)
        level = Level(raw)
    except (LevelFormatError, OSError, ValueError):
        info = None
    else:
        modes = [level.start_mode]
        for o in sorted((o for o in level.objects if o.kind == "portal_mode"), key=lambda o: o.x):
            if o.arg not in modes:
                modes.append(o.arg)
        stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        name = raw.name if raw.name and raw.name not in ("New level", "Unnamed") else stem
        info = {"name": name, "objects": len(level.objects), "skipped": sum(level.report.skipped.values()),
                "modes": modes, "seconds": level.end_x / 311.58}
    _info_cache[path] = (stamp, info)
    return info


def pack_levels():
    out = []
    for e in load_index():
        path = os.path.join(PACK, e["file"])
        if os.path.exists(path):
            replay = os.path.join(PACK, e["replay"]) if e.get("replay") else None
            out.append(dict(e, path=path, replay=replay if replay and os.path.exists(replay) else None))
    test = os.path.join(PACK, "test_level.gmd")
    if os.path.exists(test):
        replay = os.path.join(PACK, "test_level.gdr2")
        out.append({"name": "Physics Test", "difficulty": "test", "path": test, "seconds": 30.0,
                    "description": "Every mode and portal once, in order. A tour, not a challenge.",
                    "replay": replay if os.path.exists(replay) else None})
    return out


def my_levels():
    """The level files in the player's levels folder, newest first."""
    out = []
    try:
        names = sorted(os.listdir(LEVELS), key=str.lower)
    except OSError:                           # no folder yet, or no permission to read it
        return out
    for name in names:
        path = os.path.join(LEVELS, name)
        if name.lower().endswith(LEVEL_EXTENSIONS) and os.path.isfile(path):
            out.append(path)
    out.sort(key=lambda p: -os.path.getmtime(p))
    return out


def tidy_level_files(progress=None, timings=None):
    """Name every file in the player's levels folder (and its trash) after the level in it,
    so the folder shows the same names as the game: a level called "Sky High" lives in
    "Sky High.gmd".  A level takes its first file name from whatever it was called when it
    was first saved - usually "New level" - and renaming the level used to leave the file
    behind.  Nothing is ever overwritten (a taken name gets " 2", " 3", ...), and the
    level's progress and measured timing move with it.  Returns [(old path, new path)]."""
    moved = []
    for folder in (LEVELS, os.path.join(LEVELS, "trash")):
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            continue
        for name in names:
            old = os.path.join(folder, name)
            stem, ext = os.path.splitext(name)
            if ext.lower() not in LEVEL_EXTENSIONS or not os.path.isfile(old):
                continue
            try:
                title = load_level_file(old).name
            except (LevelFormatError, OSError, ValueError):
                continue                                   # not readable: leave it alone
            if not title or title in ("New level", "Unnamed"):
                continue                                   # never named: the file name is its name
            want = safe_stem(title)
            if stem == want or (stem.startswith(want + " ") and stem[len(want) + 1:].isdigit()):
                continue
            new = unique_path(folder, want, ext)
            try:
                os.rename(old, new)
            except OSError:
                continue
            moved.append((old, new))
            if progress is not None:
                progress.rename(level_key(os.path.join(LEVELS, name)), level_key(os.path.join(LEVELS, os.path.basename(new))))
            if timings is not None and folder == LEVELS:
                timings.rename(old, new)
    if moved:
        _info_cache.clear()
        if progress is not None:
            progress.save()
    return moved


# ------------------------------------------------------------------ screens
class Screen:
    def __init__(self, app):
        self.app = app

    def draw(self, cv, w, h):
        pass

    def key(self, k):
        pass

    def header(self, cv, w, title, right=None):
        ui.Button(cv, 20, 18, 104, 38, "Back", self.app.home, "quiet", 13, "Esc")
        cv.create_text(w / 2, 37, text=title, fill=ui.TEXT, font=ui.font(20, True))
        if right:
            cv.create_text(w - 24, 37, text=right, anchor="e", fill=ui.MUTED, font=ui.font(13))


class Home(Screen):
    def draw(self, cv, w, h):
        app = self.app
        cy = h * 0.2
        cv.create_text(w / 2, cy, text="GD REMAKE", fill=ui.TEXT, font=ui.font(56, True))
        cv.create_text(w / 2, cy + 50, text="a physics recreation of Geometry Dash", fill=ui.MUTED, font=ui.font(15))
        span = 84
        for m in range(8):
            icons.draw_icon(cv, m, w / 2 + (m - 3.5) * span, cy + 122, 22)
        y = cy + 190
        ui.Button(cv, w / 2 - 150, y, 300, 72, "PLAY", app.show_levels, "primary", 26, "Enter", radius=16)
        ui.Button(cv, w / 2 - 232, y + 92, 224, 56, "My Levels", app.show_mine, "normal", 16, "M")
        ui.Button(cv, w / 2 + 8, y + 92, 224, 56, "Online Levels", app.show_online, "normal", 16, "O")
        ui.Button(cv, w / 2 - 232, y + 162, 224, 44, "Level Editor", app.new_level, "normal", 14, "E")
        ui.Button(cv, w / 2 + 8, y + 162, 224, 44, "Share on Wi-Fi", app.show_share, "normal", 14, "W")
        ui.Button(cv, w / 2 - 232, y + 218, 224, 38, "Progress", app.show_stats, "quiet", 13, "S")
        ui.Button(cv, w / 2 + 8, y + 218, 224, 38, "Quit", app.quit, "quiet", 13, "Q")
        if app.session is not None:                           # never let an open room be forgotten
            room = app.session
            cv.create_text(w / 2, 26, fill=ui.CYAN, font=ui.font(13, True),
                           text=("Your room is open  -  code %s" % room.code) if room.role == "host"
                           else "You are in %s" % room.room)
        pack = [e for e in pack_levels() if e["difficulty"] != "test"]
        t = app.progress.totals([level_key(e["path"]) for e in pack])
        everything = app.progress.totals()
        cv.create_text(w / 2, h - 26, fill=ui.MUTED, font=ui.font(13),
                       text="%d of %d levels completed      %d attempts      %s played" % (
                           t["completed"], t["levels"], everything["attempts"], ui.format_time(everything["seconds"])))

    def key(self, k):
        app = self.app
        {"return": app.show_levels, "space": app.show_levels, "m": app.show_mine, "e": app.new_level,
         "s": app.show_stats, "w": app.show_share, "o": app.show_online, "q": app.quit,
         "escape": app.quit}.get(k, lambda: None)()


class LevelSelect(Screen):
    """One big card per level, paged left and right - the way GD shows its levels."""

    def __init__(self, app, index=0):
        Screen.__init__(self, app)
        self.levels = pack_levels()
        self.index = max(0, min(index, len(self.levels) - 1))

    def move(self, step):
        if self.levels:
            self.index = (self.index + step) % len(self.levels)
            self.app.redraw()

    def current(self):
        return self.levels[self.index] if self.levels else None

    def play(self, practice=False):
        e = self.current()
        if e:
            self.app.play(e["path"], practice=practice, theme=e["difficulty"])

    def watch(self):
        e = self.current()
        if e and e.get("replay"):
            self.app.play(e["path"], replay=e["replay"], theme=e["difficulty"])

    def edit(self):
        e = self.current()
        if e:
            self.app.open_editor(e["path"], copy=True)

    def draw(self, cv, w, h):
        app = self.app
        if not self.levels:
            self.header(cv, w, "Levels")
            cv.create_text(w / 2, h / 2, fill=ui.MUTED, font=ui.font(15),
                           text="No level pack found.\nRun:  python3 tools/make_level_pack.py")
            return
        e = self.current()
        diff = e["difficulty"]
        colour = icons.DIFFICULTY_COLORS.get(diff, "#5D8DFF")
        rec = app.progress.get(level_key(e["path"]))
        done = sum(1 for x in self.levels if app.progress.completed(level_key(x["path"])))
        self.header(cv, w, "Level %d of %d" % (self.index + 1, len(self.levels)), "%d completed" % done)

        cw, ch = min(780.0, w - 260.0), 236.0
        cx0, cy0 = (w - cw) / 2.0, 84.0 + max(0.0, (h - 660.0) * 0.35)
        ui.rounded(cv, cx0 - 4, cy0 - 4, cx0 + cw + 4, cy0 + ch + 4, 24, fill=colour, outline="")
        card = ui.rounded(cv, cx0, cy0, cx0 + cw, cy0 + ch, 20, fill=ui.PANEL, outline="", tags="card")
        icons.draw_face(cv, diff, cx0 + 84, cy0 + 86, 46, tags="card")
        cv.create_text(cx0 + 84, cy0 + 158, text=diff.upper(), fill=colour, font=ui.font(12, True), tags="card")
        cv.create_text(cx0 + 168, cy0 + 52, text=e["name"], anchor="w", fill=ui.TEXT, font=ui.font(34, True), tags="card")
        cv.create_text(cx0 + 170, cy0 + 96, text=e.get("description", ""), anchor="w", fill=ui.MUTED,
                       font=ui.font(14), width=cw - 200, tags="card")
        info = level_info(e["path"]) or {"modes": [0], "objects": e.get("objects", 0)}
        for i, mode in enumerate(info["modes"]):
            icons.draw_icon(cv, mode, cx0 + 186 + i * 48, cy0 + 150, 13, tags="card")
        facts = ["%d s" % round(e.get("seconds", info.get("seconds", 0)))]
        if e.get("input_grid_ms"):
            facts.append("timing %d ms" % e["input_grid_ms"])
        facts.append("%d objects" % info.get("objects", 0))
        cv.create_text(cx0 + cw - 26, cy0 + 204, text="      ".join(facts), anchor="e", fill=ui.MUTED,
                       font=ui.font(13), tags="card")
        stats = "Attempts %d" % rec["attempts"]
        if rec["completions"]:
            stats += "      Best time %.2f s" % rec["best_time"]
        cv.create_text(cx0 + 26, cy0 + 204, text=stats, anchor="w", fill=ui.MUTED, font=ui.font(13), tags="card")
        if rec["completions"]:
            bx, by = cx0 + cw - 44, cy0 + 44
            cv.create_oval(bx - 22, by - 22, bx + 22, by + 22, fill=ui.GREEN, outline="", tags="card")
            cv.create_line(bx - 10, by, bx - 3, by + 8, bx + 11, by - 8, fill="#06220D", width=4,
                           capstyle="round", joinstyle="round", tags="card")
        cv.tag_bind("card", "<ButtonRelease-1>", lambda ev: self.play())
        cv.tag_bind("card", "<Enter>", lambda ev: (cv.itemconfigure(card, fill=ui.PANEL_HI), cv.configure(cursor="hand2")))
        cv.tag_bind("card", "<Leave>", lambda ev: (cv.itemconfigure(card, fill=ui.PANEL), cv.configure(cursor="")))

        mid = cy0 + ch / 2.0
        for side, step in ((cx0 - 70, -1), (cx0 + cw + 70, 1)):
            tag = "arrow%d" % step
            ui.rounded(cv, side - 28, mid - 44, side + 28, mid + 44, 14, fill=ui.PANEL, outline="", tags=tag)
            cv.create_polygon(side - 9 * step, mid - 20, side + 11 * step, mid, side - 9 * step, mid + 20,
                              fill=ui.TEXT, outline="", tags=tag)
            cv.tag_bind(tag, "<ButtonRelease-1>", lambda ev, s=step: self.move(s))
            cv.tag_bind(tag, "<Enter>", lambda ev: cv.configure(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda ev: cv.configure(cursor=""))

        bw = cw * 0.78
        bx = (w - bw) / 2.0
        y = cy0 + ch + 26
        for label, field, col in (("Normal Mode", "best", ui.GREEN), ("Practice Mode", "practice", ui.CYAN)):
            cv.create_text(w / 2, y, text=label, fill=ui.TEXT, font=ui.font(13, True))
            ui.bar(cv, bx, y + 12, bw, 22, rec[field] / 100.0, col)
            cv.create_text(w / 2, y + 23, text="%d%%" % int(rec[field]), fill="#FFFFFF", font=ui.font(12, True))
            y += 56
        y += 2
        ui.Button(cv, w / 2 - 130, y, 260, 62, "PLAY", self.play, "primary", 22, "Enter", radius=14)
        ui.Button(cv, w / 2 - 316, y + 8, 170, 46, "Practice", lambda: self.play(True), "accent", 14, "P")
        ui.Button(cv, w / 2 + 146, y + 8, 170, 46, "Watch bot", self.watch, "normal", 14, "B",
                  enabled=bool(e.get("replay")))
        ui.Button(cv, w - 190, h - 54, 168, 36, "Edit a copy", self.edit, "quiet", 12, "E")
        n = len(self.levels)
        for i in range(n):
            x = w / 2 + (i - (n - 1) / 2.0) * 18
            r = 5 if i == self.index else 3
            lvl = self.levels[i]
            fill = ui.TEXT if i == self.index else (ui.GREEN if app.progress.completed(level_key(lvl["path"])) else ui.LINE)
            cv.create_oval(x - r, h - 30 - r, x + r, h - 30 + r, fill=fill, outline="")

    def key(self, k):
        if k in ("left", "a"):
            self.move(-1)
        elif k in ("right", "d"):
            self.move(1)
        elif k in ("return", "space"):
            self.play()
        elif k == "p":
            self.play(True)
        elif k == "b":
            self.watch()
        elif k == "e":
            self.edit()
        elif k == "escape":
            self.app.home()


# the difficulty colours, lightened where needed to stay readable as text on a dark panel
TIMING_COLORS = {"easy": "#59E36B", "medium": "#FFC83D", "hard": "#FF8552", "very hard": "#FF5C93"}


class MyLevels(Screen):
    def __init__(self, app):
        Screen.__init__(self, app)
        self.page = 0
        self.selected = 0

    def rows(self):
        return my_levels()

    def _chosen(self):
        rows = self.rows()
        i = self.page * PER_PAGE + self.selected
        return rows[i] if 0 <= i < len(rows) else None

    def delete(self, path):
        info = level_info(path) or {"name": os.path.basename(path)}
        if not messagebox.askyesno("Delete level", "Move %r to the trash folder inside your levels folder?"
                                   % info["name"]):
            return
        trash = os.path.join(LEVELS, "trash")
        os.makedirs(trash, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(path))
        shutil.move(path, unique_path(trash, stem, ext))
        self.app.redraw()

    def draw(self, cv, w, h):
        app = self.app
        rows = self.rows()
        self.header(cv, w, "My Levels", "%d level%s" % (len(rows), "" if len(rows) == 1 else "s"))
        ui.Button(cv, w - 370, 64, 170, 40, "New level", app.new_level, "primary", 14, "N")
        ui.Button(cv, w - 190, 64, 166, 40, "Import a file", app.import_level, "normal", 14, "I")
        ui.Button(cv, w - 542, 64, 160, 40, "Show in Finder" if sys.platform == "darwin" else "Open folder",
                  app.reveal_levels, "quiet", 13)
        app.home_error = paths.ensure_home()
        note = "Levels you made or imported. They are kept in %s." % app.levels_place()
        if app.home_error:                              # e.g. macOS was told to keep the game out of Documents
            note = "Cannot use %s (%s). Nothing can be saved until that is fixed." % (app.levels_place(), app.home_error)
        cv.create_text(24, 84, anchor="w", fill=ui.RED if app.home_error else ui.MUTED, font=ui.font(13),
                       text=ui.fit(note, ui.font(13), w - 542 - 24 - 16))
        if not rows:
            cv.create_text(w / 2, h / 2, fill=ui.MUTED, font=ui.font(16), justify="center",
                           text="Nothing here yet.\nPress N to open the editor, or I to import a .gmd file.")
            return
        pages = (len(rows) - 1) // PER_PAGE + 1
        self.page = max(0, min(self.page, pages - 1))
        shown = rows[self.page * PER_PAGE:(self.page + 1) * PER_PAGE]
        self.selected = max(0, min(self.selected, len(shown) - 1))
        # the bot works out each level's tightest timing in the background; this page first
        app.timing.want(shown + [p for p in rows if p not in shown])
        # columns, placed from the right: buttons, progress, tightest jump; the name gets the rest
        widths = (92, 100, 80, 88) if w >= 1120 else (76, 84, 64, 72)
        x0, x1 = 24, w - 24
        bx = x1 - 16 - sum(widths) - 3 * 8
        px = bx - 250
        tx = px - 140
        room = tx - (x0 + 78) - 24
        y = 122
        for i, path in enumerate(shown):
            info = level_info(path)
            rec = app.progress.get(level_key(path))
            ui.rounded(cv, x0, y, x1, y + 70, 14, fill=ui.PANEL, outline=ui.CYAN if i == self.selected else "",
                       width=2)
            if info is None:
                cv.create_text(x0 + 24, y + 35, anchor="w", fill=ui.RED, font=ui.font(14),
                               text="%s  -  not a readable level" % os.path.basename(path))
            else:
                icons.draw_face(cv, "custom", x0 + 40, y + 35, 22)
                cv.create_text(x0 + 78, y + 24, anchor="w", fill=ui.TEXT, font=ui.font(17, True),
                               text=ui.fit(info["name"], ui.font(17, True), room))
                note = "%d objects      about %d s" % (info["objects"], round(info["seconds"]))
                if info["skipped"]:
                    note += "      %d not simulated" % info["skipped"]
                note += "      " + os.path.basename(path)
                cv.create_text(x0 + 78, y + 49, anchor="w", fill=ui.MUTED, font=ui.font(12),
                               text=ui.fit(note, ui.font(12), room))
                text, band = describe(app.timing.get(path))
                cv.create_text(tx, y + 22, anchor="w", fill=ui.MUTED, font=ui.font(10, True), text="TIGHTEST JUMP")
                cv.create_text(tx, y + 46, anchor="w", text=text,
                               fill=TIMING_COLORS.get(band, ui.MUTED), font=ui.font(17 if band else 12, bool(band)))
                for j, mode in enumerate(info["modes"][:8]):
                    icons.draw_icon(cv, mode, px + 10 + j * 30, y + 24, 8)
                ui.bar(cv, px, y + 42, 190, 16, rec["best"] / 100.0, ui.GREEN)
                cv.create_text(px + 198, y + 50, anchor="w", fill=ui.TEXT, font=ui.font(12, True), text="%d%%" % int(rec["best"]))
                x = bx
                for width, (label, cmd, style) in zip(widths, (
                        ("Play", lambda p=path: app.play(p), "primary"),
                        ("Practice", lambda p=path: app.play(p, practice=True), "accent"),
                        ("Edit", lambda p=path: app.open_editor(p), "normal"))):
                    ui.Button(cv, x, y + 15, width, 40, label, cmd, style, 13 if label == "Practice" else 14)
                    x += width + 8
            ui.Button(cv, x1 - 16 - widths[3], y + 15, widths[3], 40, "Delete", lambda p=path: self.delete(p), "danger", 13)
            y += 80
        if pages > 1:
            ui.Button(cv, w / 2 - 150, h - 54, 90, 36, "Previous", lambda: self._page(-1), "quiet", 12, enabled=self.page > 0)
            cv.create_text(w / 2, h - 36, text="Page %d of %d" % (self.page + 1, pages), fill=ui.MUTED, font=ui.font(13))
            ui.Button(cv, w / 2 + 60, h - 54, 90, 36, "Next", lambda: self._page(1), "quiet", 12, enabled=self.page < pages - 1)
        cv.create_text(24, h - 36, anchor="w", fill=ui.MUTED, font=ui.font(12),
                       text="Up / Down select      Enter play      P practice      E edit")

    def _page(self, step):
        self.page += step
        self.selected = 0
        self.app.redraw()

    def key(self, k):
        app = self.app
        path = self._chosen()
        if k == "escape":
            app.home()
        elif k == "n":
            app.new_level()
        elif k == "i":
            app.import_level()
        elif k in ("up", "down"):
            self.selected += -1 if k == "up" else 1
            app.redraw()
        elif k in ("left", "right"):
            self._page(-1 if k == "left" else 1)
        elif path and k in ("return", "space"):
            app.play(path)
        elif path and k == "p":
            app.play(path, practice=True)
        elif path and k == "e":
            app.open_editor(path)
        elif path and k in ("delete", "backspace"):
            self.delete(path)


class Online(Screen):
    """Public levels from Geometry Dash's own level server (gd/online.py does the talking):
    the game's lists, a search, and for each level Play and Save to My Levels.  Requests
    run on a background thread so the window never freezes."""

    def __init__(self, app):
        Screen.__init__(self, app)
        self.list_key, self.query, self.page = "featured", "", 0
        self.levels, self.total = [], 0
        self.busy, self.error = "", ""
        self.fetched = {}                         # level id -> (cached file, what the engine makes of it)
        self.saved = {}                           # level id -> file in My Levels
        self.search = tk.StringVar()
        self.entry = tk.Entry(app.cv, textvariable=self.search, relief="flat", bg="#0A0D15", fg=ui.TEXT,
                              insertbackground=ui.TEXT, font=ui.font(14), highlightthickness=1,
                              highlightbackground=ui.LINE, highlightcolor=ui.CYAN)
        self.entry.bind("<Return>", lambda e: self.do_search())
        self.entry.bind("<Escape>", lambda e: app.cv.focus_set())
        self.load()

    def close(self):
        self.entry.destroy()

    # ------------------------------------------------------------------ talking to the server
    def _run(self, label, work, done):
        """`work` on a background thread; `done(result)` back on the window afterwards."""
        self.busy, self.error = label, ""
        box = {}

        def run():
            try:
                box["ok"] = work()
            except online.OnlineError as exc:
                box["err"] = str(exc)
            except Exception as exc:              # a bug or a strange reply: say so, do not crash
                box["err"] = "Something went wrong: %s" % exc

        thread = threading.Thread(target=run, name="online", daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                self.app.root.after(100, poll)
                return
            self.busy = ""
            if "err" in box:
                self.error = box["err"]
            else:
                done(box["ok"])
            if self.app.screen is self and self.app.game is None:
                self.app.redraw()

        self.app.root.after(100, poll)
        self.app.redraw()

    def load(self):
        def got(result):
            self.levels, self.total = result
        self._run("Loading levels...", lambda: online.browse(self.list_key, self.page, self.query), got)

    def choose(self, key):
        self.list_key, self.query, self.page = key, "", 0
        self.search.set("")
        self.load()

    def do_search(self):
        query = self.search.get().strip()
        if query and not self.busy:
            self.query, self.page = query, 0
            self.app.cv.focus_set()
            self.load()

    def turn(self, step):
        if not self.busy:
            self.page = max(0, self.page + step)
            self.load()

    def _then(self, level, action):
        """Download the level (once) and then `action(level)`."""
        if self.busy:
            return
        if level["id"] in self.fetched:
            action(level)
            return

        def got(result):
            self.fetched[level["id"]] = result
            action(level)
        self._run('Downloading "%s"...' % level["name"], lambda: online.fetch(level), got)

    def play(self, level):
        self._then(level, lambda lv: self.app.play(self.fetched[lv["id"]][0]))

    def save(self, level):
        def keep(lv):
            path = self.fetched[lv["id"]][0]
            try:
                raw = load_level_file(path)
                os.makedirs(LEVELS, exist_ok=True)
                target = unique_path(LEVELS, raw.name)
                write_gmd(target, raw.name, raw.level_string, creator=lv["creator"])
                self.saved[lv["id"]] = target
                _info_cache.clear()
            except (OSError, LevelFormatError) as exc:
                self.error = "Could not save it: %s" % (getattr(exc, "strerror", None) or exc)
        self._then(level, keep)

    # ------------------------------------------------------------------ drawing
    def draw(self, cv, w, h):
        self.header(cv, w, "Online Levels", "from the Geometry Dash level server")
        x = 24
        for key, label, _code in online.LISTS:
            width = 24 + 8 * len(label)
            active = key == self.list_key and not self.query
            ui.Button(cv, x, 70, width, 34, label, lambda k=key: self.choose(k), "accent" if active else "quiet", 12,
                      radius=8)
            x += width + 6
        ex = max(x + 96, w - 330)
        cv.create_text(ex - 8, 87, anchor="e", text="Name or ID", fill=ui.MUTED, font=ui.font(12))
        cv.create_window(ex, 87, window=self.entry, anchor="w", width=210, height=32)
        ui.Button(cv, w - 112, 70, 88, 34, "Search", self.do_search, "accent" if self.query else "normal", 12, radius=8)

        top, row_h = 118, max(44, min(54, (h - 118 - 70) / 10.0))
        if self.query:
            cv.create_text(24, top + 6, anchor="w", fill=ui.MUTED, font=ui.font(12),
                           text='Search: "%s"' % self.query)
            top += 18
        y = top
        for level in self.levels:
            self._row(cv, level, 24, y, w - 24, row_h - 6)
            y += row_h
        if self.busy or self.error or not self.levels:
            text = self.busy or self.error or "No levels found."
            cv.create_text(w / 2, h - 82 if self.levels else h / 2, text=text, font=ui.font(14, bool(self.error)),
                           fill=ui.YELLOW if self.error else ui.MUTED)
        pages = max(1, (self.total + 9) // 10)
        ui.Button(cv, w / 2 - 170, h - 50, 100, 34, "Previous", lambda: self.turn(-1), "quiet", 12,
                  enabled=self.page > 0 and not self.busy, radius=8)
        cv.create_text(w / 2, h - 33, fill=ui.MUTED, font=ui.font(12),
                       text="Page %d of %s" % (self.page + 1, "{:,}".format(pages)) if self.total else "")
        ui.Button(cv, w / 2 + 70, h - 50, 100, 34, "Next", lambda: self.turn(1), "quiet", 12,
                  enabled=self.page + 1 < pages and not self.busy, radius=8)
        cv.create_text(24, h - 33, anchor="w", fill=ui.MUTED, font=ui.font(11),
                       text=ui.fit("Most new levels use triggers this game does not have yet; "
                                   "each level says what it misses once downloaded.", ui.font(11), w / 2 - 200))

    @staticmethod
    def _face(name):
        if name.endswith("Demon"):
            return "very hard"
        return {"Auto": "easy", "Easy": "easy", "Normal": "easy", "Hard": "medium", "Harder": "medium",
                "Insane": "hard"}.get(name, "custom")

    def _row(self, cv, level, x0, y0, x1, height):
        ui.rounded(cv, x0, y0, x1, y0 + height, 12, fill=ui.PANEL, outline="")
        mid = y0 + height / 2.0
        icons.draw_face(cv, self._face(level["difficulty"]), x0 + 26, mid, height * 0.36)
        name_w = max(160, (x1 - x0) * 0.30)
        cv.create_text(x0 + 52, mid - 9, anchor="w", fill=ui.TEXT, font=ui.font(15, True),
                       text=ui.fit(level["name"], ui.font(15, True), name_w))
        cv.create_text(x0 + 52, mid + 11, anchor="w", fill=ui.MUTED, font=ui.font(11),
                       text=ui.fit("by %s      ID %d" % (level["creator"], level["id"]), ui.font(11), name_w))
        sx = x0 + 64 + name_w
        colour = icons.DIFFICULTY_COLORS.get(self._face(level["difficulty"]), ui.MUTED)
        cv.create_text(sx, mid - 9, anchor="w", fill=colour, font=ui.font(13, True),
                       text="%s%s" % (level["difficulty"], "   %d stars" % level["stars"] if level["stars"] else ""))
        got = self.fetched.get(level["id"])
        if got:
            report = got[1]
            note = "runs %s of %s objects" % ("{:,}".format(report["simulated"]), "{:,}".format(report["objects"]))
            if report["missing"]:
                note += "  -  missing: " + ", ".join(report["missing"])
            fill = ui.YELLOW if report["missing"] else ui.GREEN
        else:
            note = "%s downloads    %s likes    %s    %s objects" % (
                "{:,}".format(level["downloads"]), "{:,}".format(level["likes"]), level["length"],
                "?" if not level["objects"] else "{:,}".format(level["objects"]) if level["objects"] < 65535
                else "65,535+")
            fill = ui.MUTED
        buttons = 196
        cv.create_text(sx, mid + 11, anchor="w", fill=fill, font=ui.font(11),
                       text=ui.fit(note, ui.font(11), x1 - buttons - sx - 12))
        bx = x1 - buttons
        if level["platformer"]:
            cv.create_text(x1 - 16, mid, anchor="e", fill=ui.MUTED, font=ui.font(12),
                           text="platformer: not supported")
            return
        ui.Button(cv, bx, mid - 17, 82, 34, "Play", lambda lv=level: self.play(lv), "primary", 13,
                  enabled=not self.busy, radius=8)
        if level["id"] in self.saved:
            cv.create_text(x1 - 16, mid, anchor="e", fill=ui.GREEN, font=ui.font(12, True), text="Saved")
        else:
            ui.Button(cv, bx + 90, mid - 17, 92, 34, "Save", lambda lv=level: self.save(lv), "normal", 13,
                      enabled=not self.busy, radius=8)

    def key(self, k):
        if k == "escape":
            self.app.home()
        elif k in ("left", "right"):
            self.turn(-1 if k == "left" else 1)


class Share(Screen):
    """Share levels with players on the same Wi-Fi.

    Two states.  Outside a room: "Host a room" on the left, the rooms found on the
    network on the right.  Inside a room: the code and who is here on top, your levels to
    SEND on the left, the levels shared in the room to RECEIVE on the right.  All the
    networking is in gd/share.py; this only draws it and passes on the clicks."""

    def __init__(self, app):
        Screen.__init__(self, app)
        self.send_page = self.shelf_page = 0
        self.name = tk.StringVar(value=share.load_name())
        self.entry = tk.Entry(app.cv, textvariable=self.name, relief="flat", bg="#0A0D15", fg=ui.TEXT,
                              insertbackground=ui.TEXT, font=ui.font(15), highlightthickness=1,
                              highlightbackground=ui.LINE, highlightcolor=ui.CYAN)
        for key in ("<Return>", "<Escape>"):
            self.entry.bind(key, lambda e: app.cv.focus_set())
        app.look_for_rooms(app.session is None)

    def close(self):
        """Called when the menu moves to another screen.  A room stays open; the search stops."""
        share.save_name(self.name.get())
        self.entry.destroy()
        self.app.look_for_rooms(False)

    def my_name(self):
        return share.clean(self.name.get(), share.NAME_LIMIT) or share.default_name()

    # ------------------------------------------------------------------ drawing
    def draw(self, cv, w, h):
        if self.app.session is None:
            self._draw_lobby(cv, w, h)
        else:
            self._draw_room(cv, w, h, self.app.session)

    @staticmethod
    def _card(cv, x0, y0, x1, y1, caption, title):
        ui.rounded(cv, x0, y0, x1, y1, 16, fill=ui.PANEL, outline="")
        cv.create_text(x0 + 24, y0 + 26, anchor="w", text=caption, fill=ui.CYAN, font=ui.font(11, True))
        cv.create_text(x0 + 24, y0 + 54, anchor="w", text=title, fill=ui.TEXT, font=ui.font(19, True))

    def _draw_lobby(self, cv, w, h):
        app = self.app
        self.header(cv, w, "Share on Wi-Fi")
        cv.create_text(24, 92, anchor="w", text="Your name", fill=ui.MUTED, font=ui.font(13))
        cv.create_window(104, 92, window=self.entry, anchor="w", width=220, height=32)
        cv.create_text(338, 92, anchor="w", text="This is what the other players see.", fill=ui.MUTED, font=ui.font(12))
        if app.share_note:                                    # why the last room ended
            cv.create_text(w - 24, 92, anchor="e", text=app.share_note, fill=ui.YELLOW, font=ui.font(13, True))
        top, bottom, mid = 122, h - 84, w / 2.0

        # --- host
        x0, x1 = 24, mid - 8
        self._card(cv, x0, top, x1, bottom, "HOST A ROOM", "Open a room for your friends")
        steps = ("1.   Press Start hosting.",
                 "2.   A 4-digit code appears. Tell it to your friends.",
                 "3.   They choose your room on their screen and type the code.",
                 "4.   Everyone can then send levels and pick up the ones they want.")
        for i, text in enumerate(steps):
            cv.create_text(x0 + 24, top + 100 + i * 30, anchor="w", text=ui.fit(text, ui.font(14), x1 - x0 - 48),
                           fill=ui.TEXT, font=ui.font(14))
        ui.Button(cv, x0 + 24, top + 100 + len(steps) * 30 + 16, 240, 56, "Start hosting", self.host, "primary", 18,
                  "H", radius=14)

        # --- join
        x0, x1 = mid + 8, w - 24
        self._card(cv, x0, top, x1, bottom, "JOIN A ROOM", "Rooms on this Wi-Fi")
        finder = app.finder
        rooms = list(finder.rooms) if finder is not None else []
        y = top + 86
        for room in rooms[:max(1, int((bottom - y - 70) // 62))]:
            ui.rounded(cv, x0 + 16, y, x1 - 16, y + 54, 12, fill=ui.PANEL_HI, outline="")
            cv.create_text(x0 + 32, y + 18, anchor="w", fill=ui.TEXT, font=ui.font(15, True),
                           text=ui.fit(room["room"], ui.font(15, True), x1 - x0 - 190))
            cv.create_text(x0 + 32, y + 39, anchor="w", fill=ui.MUTED, font=ui.font(11),
                           text="%d %s      %s" % (room["people"], "person" if room["people"] == 1 else "people",
                                                    room["address"]))
            ui.Button(cv, x1 - 126, y + 8, 98, 38, "Join", lambda r=room: self.join(r["address"], r["port"]),
                      "accent", 14, radius=10)
            y += 62
        if not rooms:
            looking = finder is None or not finder.searched
            cv.create_text(x0 + 24, y + 14, anchor="w", fill=ui.MUTED, font=ui.font(14),
                           text="Looking for rooms..." if looking else "No room found yet.")
            if not looking:
                cv.create_text(x0 + 24, y + 42, anchor="w", fill=ui.MUTED, font=ui.font(12),
                               text=ui.fit("Ask the host to press Start hosting. The list updates by itself.",
                                           ui.font(12), x1 - x0 - 48))
        ui.Button(cv, x0 + 24, bottom - 54, 270, 36, "Type the host's address instead", self.join_by_address,
                  "normal", 12, radius=10)

        for i, text in enumerate((
                "Use this only on a network you trust, such as your home Wi-Fi: levels are sent without encryption.",
                "If macOS asks whether GD Remake may accept incoming connections or find devices on your local "
                "network, choose Allow.")):
            cv.create_text(24, h - 58 + i * 22, anchor="w", fill=ui.MUTED, font=ui.font(12),
                           text=ui.fit(text, ui.font(12), w - 48))

    def _pager(self, cv, x1, y, name, count, rows):
        """Small previous / next buttons at the top right of a list that does not fit."""
        pages = max(1, (count - 1) // rows + 1)
        page = max(0, min(getattr(self, name), pages - 1))
        setattr(self, name, page)
        if pages > 1:
            def turn(step):
                setattr(self, name, page + step)
                self.app.redraw()
            ui.Button(cv, x1 - 100, y, 36, 30, "<", lambda: turn(-1), "quiet", 13, enabled=page > 0, radius=8)
            cv.create_text(x1 - 50, y + 15, text="%d / %d" % (page + 1, pages), fill=ui.MUTED, font=ui.font(11))
            ui.Button(cv, x1 - 36, y, 36, 30, ">", lambda: turn(1), "quiet", 13, enabled=page < pages - 1, radius=8)
        return page

    def _draw_room(self, cv, w, h, session):
        app = self.app
        state = session.snapshot()
        hosting = session.role == "host"
        self.header(cv, w, session.room)
        ui.Button(cv, w - 204, 18, 180, 38, "Stop hosting" if hosting else "Leave room", app.leave_room, "danger", 13)

        # --- the room: code, address, who is here
        ui.rounded(cv, 24, 70, w - 24, 150, 16, fill=ui.PANEL, outline="")
        x = 48
        if hosting:
            cv.create_text(x, 92, anchor="w", text="ROOM CODE", fill=ui.CYAN, font=ui.font(11, True))
            cv.create_text(x, 124, anchor="w", text="  ".join(session.code), fill=ui.TEXT, font=ui.font(30, True))
            cv.create_text(x + 190, 92, anchor="w", text="ADDRESS", fill=ui.MUTED, font=ui.font(11, True))
            cv.create_text(x + 190, 120, anchor="w", text=session.address, fill=ui.TEXT, font=ui.font(17, True))
            cv.create_text(x + 190, 139, anchor="w", text="only needed if a friend cannot see the room",
                           fill=ui.MUTED, font=ui.font(10))
            x += 470
        people = state["people"]
        names = ["%s%s" % (name, " (host)" if i == 0 else "") + (" - you" if name == session.me else "")
                 for i, name in enumerate(people)]
        cv.create_text(x, 92, anchor="w", text="IN THE ROOM  (%d)" % len(people), fill=ui.CYAN if not hosting else ui.MUTED,
                       font=ui.font(11, True))
        cv.create_text(x, 122, anchor="w", fill=ui.TEXT, font=ui.font(14),
                       text=ui.fit("     ".join(names), ui.font(14), w - 48 - x))

        top, bottom, mid = 164, h - 56, w / 2.0
        rows = max(2, int((bottom - top - 78) // 56))

        # --- send
        x0, x1 = 24, mid - 8
        self._card(cv, x0, top, x1, bottom, "SEND", "Your levels")
        mine = my_levels()
        page = self._pager(cv, x1 - 16, top + 38, "send_page", len(mine), rows)
        y = top + 82
        for path in mine[page * rows:(page + 1) * rows]:
            info = level_info(path)
            if info is None:
                continue
            ui.rounded(cv, x0 + 16, y, x1 - 16, y + 48, 12, fill=ui.PANEL_HI, outline="")
            cv.create_text(x0 + 32, y + 24, anchor="w", fill=ui.TEXT, font=ui.font(14, True),
                           text=ui.fit(info["name"], ui.font(14, True), x1 - x0 - 200))
            if path in app.shared:
                cv.create_text(x1 - 34, y + 24, anchor="e", text="Shared", fill=ui.GREEN, font=ui.font(13, True))
            else:
                ui.Button(cv, x1 - 138, y + 6, 110, 36, "Share", lambda p=path: app.share_level(p), "primary", 13,
                          radius=10)
            y += 56
        if not mine:
            cv.create_text(x0 + 24, y + 10, anchor="w", fill=ui.MUTED, font=ui.font(13),
                           text="You have no levels of your own yet. Build one in the Level Editor.")

        # --- receive
        x0, x1 = mid + 8, w - 24
        self._card(cv, x0, top, x1, bottom, "RECEIVE", "Shared in this room")
        shelf = state["shelf"]
        page = self._pager(cv, x1 - 16, top + 38, "shelf_page", len(shelf), rows)
        y = top + 82
        for item in shelf[page * rows:(page + 1) * rows]:
            ui.rounded(cv, x0 + 16, y, x1 - 16, y + 48, 12, fill=ui.PANEL_HI, outline="")
            cv.create_text(x0 + 32, y + 16, anchor="w", fill=ui.TEXT, font=ui.font(14, True),
                           text=ui.fit(item["title"], ui.font(14, True), x1 - x0 - 250))
            own = item["from"] == session.me
            cv.create_text(x0 + 32, y + 35, anchor="w", fill=ui.MUTED, font=ui.font(11),
                           text="from %s      %d objects" % ("you" if own else item["from"], item["objects"]))
            if own:
                cv.create_text(x1 - 34, y + 24, anchor="e", text="yours", fill=ui.MUTED, font=ui.font(13))
            elif item["id"] in app.saved:
                cv.create_text(x1 - 34, y + 24, anchor="e", text="Saved to My Levels", fill=ui.GREEN,
                               font=ui.font(13, True))
            elif item["id"] in state["waiting"]:
                cv.create_text(x1 - 34, y + 24, anchor="e", text="Getting it...", fill=ui.MUTED, font=ui.font(13))
            else:
                ui.Button(cv, x1 - 188, y + 6, 160, 36, "Save to My Levels", lambda i=item["id"]: app.take_level(i),
                          "accent", 13, radius=10)
            y += 56
        if not shelf:
            cv.create_text(x0 + 24, y + 10, anchor="w", fill=ui.MUTED, font=ui.font(13),
                           text=ui.fit("Nothing has been shared yet. Levels appear here when someone presses Share.",
                                       ui.font(13), x1 - x0 - 48))

        if state["messages"]:
            cv.create_text(24, h - 28, anchor="w", fill=ui.MUTED, font=ui.font(13),
                           text=ui.fit(state["messages"][-1], ui.font(13), w - 48))

    # ------------------------------------------------------------------ actions
    def host(self):
        self.app.host_room(self.my_name())

    def join(self, address, port):
        self.app.join_room(self.my_name(), address, port)

    def join_by_address(self):
        address = simpledialog.askstring("Host's address", "Type the address on the host's screen\n"
                                         "(four numbers, for example 192.168.1.23):", parent=self.app.root)
        if address:
            self.join(address.strip(), share.PORT)

    def key(self, k):
        if k == "escape":
            self.app.home()
        elif k == "h" and self.app.session is None:
            self.host()


class Stats(Screen):
    def reset(self):
        if messagebox.askyesno("Reset progress", "Erase all saved progress for every level?"):
            self.app.progress.reset()
            self.app.redraw()

    def draw(self, cv, w, h):
        app = self.app
        self.header(cv, w, "Progress")
        pack = [e for e in pack_levels() if e["difficulty"] != "test"]
        t = app.progress.totals([level_key(e["path"]) for e in pack])
        everything = app.progress.totals()
        tiles = (("%d / %d" % (t["completed"], t["levels"]), "levels completed"), (str(everything["attempts"]), "attempts"),
                 (str(everything["jumps"]), "jumps"), (ui.format_time(everything["seconds"]), "played"))
        tw = (w - 48 - 3 * 14) / 4.0
        for i, (value, label) in enumerate(tiles):
            x = 24 + i * (tw + 14)
            ui.rounded(cv, x, 72, x + tw, 150, 14, fill=ui.PANEL, outline="")
            cv.create_text(x + tw / 2, 100, text=value, fill=ui.TEXT, font=ui.font(24, True))
            cv.create_text(x + tw / 2, 130, text=label, fill=ui.MUTED, font=ui.font(12))
        y = 172
        cols = (24 + 44, w * 0.27, w * 0.52, w * 0.85, w - 30)
        for x, label, anchor in ((cols[0], "Level", "w"), (cols[1], "Normal", "w"), (cols[2], "Practice", "w"),
                                 (cols[3], "Attempts", "e"), (cols[4], "Best time", "e")):
            cv.create_text(x, y, text=label, anchor=anchor, fill=ui.MUTED, font=ui.font(12, True))
        row_h = min(30.0, (h - 268) / max(1, len(pack)))
        y += 22
        bw = w * 0.18
        for e in pack:
            rec = app.progress.get(level_key(e["path"]))
            icons.draw_face(cv, e["difficulty"], 24 + 16, y, 10)
            cv.create_text(cols[0], y, text=e["name"], anchor="w", fill=ui.TEXT, font=ui.font(13, True))
            for x, field, col in ((cols[1], "best", ui.GREEN), (cols[2], "practice", ui.CYAN)):
                ui.bar(cv, x, y - 7, bw, 14, rec[field] / 100.0, col)
                cv.create_text(x + bw + 8, y, text="%d%%" % int(rec[field]), anchor="w", fill=ui.TEXT, font=ui.font(12))
            cv.create_text(cols[3], y, text=str(rec["attempts"]), anchor="e", fill=ui.TEXT, font=ui.font(13))
            cv.create_text(cols[4], y, anchor="e", fill=ui.TEXT if rec["best_time"] else ui.MUTED, font=ui.font(13),
                           text="%.2f s" % rec["best_time"] if rec["best_time"] else "-")
            y += row_h
        ui.Button(cv, w - 214, h - 56, 190, 38, "Reset all progress", self.reset, "danger", 13)
        cv.create_text(24, h - 37, anchor="w", fill=ui.MUTED, font=ui.font(12),
                       text="Saved in the savedata folder next to your levels. A bot's run never counts.")

    def key(self, k):
        if k == "escape":
            self.app.home()


# ------------------------------------------------------------------ the application
MENU_MIN, EDITOR_MIN = (1000, 640), (1240, 720)
TIMING_POLL_MS = 300


class App:
    def __init__(self):
        self.root = root = tk.Tk()
        root.title("GD Remake")
        root.geometry("1180x700")
        root.minsize(*MENU_MIN)
        root.configure(bg=ui.BG)
        self.cv = tk.Canvas(root, bg=ui.BG, highlightthickness=0, takefocus=1)
        self.cv.pack(fill="both", expand=True)
        self.home_error = paths.ensure_home()         # the player's folder in Documents ("" = fine)
        self.progress = Progress()
        self.timing = Measurer()                      # works out "tightest jump" for My Levels
        self._timing_seen = 0
        self.game = None
        self.editor = None
        self.session = None                           # the sharing room this player hosts or is in
        self.finder = None                            # looks for rooms while the list is on screen
        self.shared, self.saved = set(), {}           # my level files sent / shelf items saved, this room
        self.share_note = ""                          # why the last room ended
        self._share_seen = None
        self.tidy_levels()                            # level files are named after their levels
        self.quit_after_game = False
        self.remember_index = 0
        self.screen = Home(self)
        self._pending = None
        self.cv.bind("<Configure>", lambda e: self._schedule())
        root.bind("<KeyPress>", self._key)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.createcommand("::tk::mac::Quit", self.quit)      # Cmd+Q and the Dock's Quit: save first
        if os.environ.get("GD_REMAKE_APP"):                   # the app has no terminal to print errors to
            root.report_callback_exception = self._callback_error
        root.after(TIMING_POLL_MS, self._timing_tick)

    # -- drawing
    def _schedule(self):
        if self._pending is None:
            self._pending = self.root.after(20, self.redraw)

    def redraw(self):
        self._pending = None
        if self.game is not None or self.editor is not None:
            return
        cv = self.cv
        cv.delete("all")
        cv.configure(cursor="")
        w, h = max(cv.winfo_width(), 200), max(cv.winfo_height(), 200)
        self.screen.draw(cv, w, h)

    def _timing_tick(self):
        """The measuring thread cannot touch the window, so the window asks it for news.
        It only gets the processor while a menu is showing: playing and editing stay smooth."""
        in_menu = self.game is None and self.editor is None
        if in_menu:
            self.timing.resume()
            if self.timing.version != self._timing_seen:
                self._timing_seen = self.timing.version
                if isinstance(self.screen, MyLevels):
                    self.redraw()
        else:
            self.timing.pause()
        self._share_tick()
        self.root.after(TIMING_POLL_MS, self._timing_tick)

    # -- sharing over the local network (the networking itself is gd/share.py)
    def _share_tick(self):
        """Background threads run the room; the window collects what they have for it."""
        session = self.session
        if session is not None:
            for item_id, title, text in session.take_inbox():     # only levels this player asked for
                try:
                    self.saved[item_id] = share.save_level(LEVELS, title, text)
                    session.note('Saved "%s" to My Levels.' % title)
                    _info_cache.clear()
                except (share.ShareError, OSError) as exc:
                    session.note('Could not save "%s": %s' % (title, getattr(exc, "strerror", None) or exc))
            if not session.running:                               # the host closed it, or the Wi-Fi dropped
                self._end_session(session.ended)
        seen = (id(self.session), self.session.version if self.session else 0,
                self.finder.version if self.finder else 0)
        if seen != self._share_seen:
            self._share_seen = seen
            if isinstance(self.screen, (Share, Home)) and self.game is None and self.editor is None:
                self.redraw()

    def _end_session(self, why=""):
        if self.session is not None:
            self.session.leave()
        self.session, self.share_note = None, why
        self.shared, self.saved = set(), {}
        self.look_for_rooms(isinstance(self.screen, Share))

    def look_for_rooms(self, on):
        if on and self.finder is None:
            self.finder = share.Finder()
        elif not on and self.finder is not None:
            self.finder.stop()
            self.finder = None

    def show_share(self):
        self._set(Share(self))

    def show_online(self):
        self._set(Online(self))

    def host_room(self, name):
        if self.session is not None:
            return
        share.save_name(name)
        try:
            self.session = share.Room(name).start()
        except share.ShareError as exc:
            messagebox.showerror("Cannot host a room", str(exc))
            return
        self.share_note = ""
        self.look_for_rooms(False)
        self.redraw()

    def join_room(self, name, address, port):
        if self.session is not None:
            return
        code = simpledialog.askstring("Room code", "Type the 4-digit code on the host's screen:", parent=self.root)
        if not code:
            return
        share.save_name(name)
        try:
            self.session = share.Guest(name).join(address, port, code.strip(), timeout=4.0)
        except share.ShareError as exc:
            messagebox.showerror("Cannot join", str(exc))
            return
        self.share_note = ""
        self.look_for_rooms(False)
        self.redraw()

    def leave_room(self):
        if self.session is None:
            return
        hosting = self.session.role == "host"
        others = len(self.session.snapshot()["people"]) - 1
        if hosting and others > 0 and not messagebox.askyesno(
                "Stop hosting", "Close the room? %d other player%s will be disconnected."
                % (others, "" if others == 1 else "s")):
            return
        self._end_session("")
        self.redraw()

    def share_level(self, path):
        if self.session is None:
            return
        try:
            title, text = share.read_level(path)
            self.session.share(title, text)
            self.shared.add(path)
        except (share.ShareError, LevelFormatError, OSError) as exc:
            messagebox.showerror("Cannot share this level", str(exc))
        self.redraw()

    def take_level(self, item_id):
        if self.session is None:
            return
        try:
            self.session.request(item_id)
        except share.ShareError as exc:
            messagebox.showerror("Cannot get this level", str(exc))
        self.redraw()

    def _callback_error(self, kind, value, trace):
        """Inside the app: keep the latest error where the player can find it, and go on."""
        import traceback
        try:
            os.makedirs(paths.SAVEDATA, exist_ok=True)
            with open(os.path.join(paths.SAVEDATA, "last_error.txt"), "w", encoding="utf-8") as fh:
                fh.write("".join(traceback.format_exception(kind, value, trace)))
        except OSError:
            pass

    def _key(self, e):
        # the game and the editor handle their own keys; the menu only listens when it is showing
        if self.game is not None or self.editor is not None or isinstance(e.widget, (tk.Entry, tk.Text)):
            return
        self.screen.key(e.keysym.lower())

    def _set(self, screen):
        if isinstance(self.screen, LevelSelect):
            self.remember_index = self.screen.index
        if hasattr(self.screen, "close"):
            self.screen.close()
        self.screen = screen
        self.redraw()

    def _show_menu(self):
        self.root.title("GD Remake")
        self.cv.pack(fill="both", expand=True)
        self.cv.focus_set()
        self.redraw()

    # -- navigation
    def home(self):
        self._set(Home(self))

    def show_levels(self):
        self._set(LevelSelect(self, self.remember_index))

    def show_mine(self):
        self.tidy_levels()
        self._set(MyLevels(self))

    def tidy_levels(self):
        """Keep the files in the levels folder named after their levels (never while one is open)."""
        if self.game is None and self.editor is None:
            tidy_level_files(self.progress, self.timing.timings)

    def show_stats(self):
        self._set(Stats(self))

    def quit(self):
        """Close the window - from anywhere.  The editor saves its level first."""
        if self.game is not None:
            self.game.on_exit = None
            self.game.exit()
            self.game = None
            if self.editor is not None:
                self.editor.resume()
        if self.editor is not None and not self.editor.flush():
            return
        self.timing.stop()
        self.look_for_rooms(False)
        if self.session is not None:
            self.session.leave()                      # tells the others, closes the room
        if hasattr(self.screen, "close"):
            self.screen.close()
        self.root.destroy()

    # -- playing
    def play(self, path, practice=False, replay=None, theme=None, standalone=False, save=True, **view_options):
        """Play a level file.  `standalone`: the program was started just for this level,
        so leaving the level closes the window instead of showing the menu."""
        if self.game is not None:
            return False
        select = view_options.pop("select", None)
        offset = view_options.pop("replay_offset", 0)
        try:
            level = Level(load_level_file(path, select))
            inputs = load_macro(replay, offset) if replay else None
        except (LevelFormatError, MacroFormatError, OSError) as exc:
            messagebox.showerror("Cannot open level", str(exc))
            return False
        info = level_info(path)
        if info:
            level.name = info["name"]
        self.quit_after_game = standalone
        self.timing.pause()
        self.cv.pack_forget()
        self.root.title("GD Remake - %s" % level.name)
        self.game = GameView(self.root, Simulation(level), inputs, practice=practice,
                             progress=self.progress if save else None, key=level_key(path),
                             on_exit=self._game_closed, theme=theme, **view_options)
        self.game.start()
        return True

    def play_level(self, level, inputs=None, hitboxes=False):
        """Play-test a level straight from the editor (nothing is saved to progress)."""
        if self.game is not None or self.editor is None:
            return
        self.editor.suspend()
        self.root.title("GD Remake - testing %s" % level.name)
        self.game = GameView(self.root, Simulation(level), inputs, show_hitboxes=hitboxes,
                             on_exit=self._game_closed)
        self.game.start()

    def _game_closed(self):
        self.game = None
        if self.editor is not None:
            self.editor.resume()
        elif self.quit_after_game:
            self.timing.stop()
            self.root.destroy()
        else:
            self._show_menu()

    # -- editing
    def open_editor(self, path=None, copy=False):
        if self.game is not None or self.editor is not None:
            return
        from .editor_tk import Editor
        self.timing.pause()
        self.cv.pack_forget()
        self.root.minsize(*EDITOR_MIN)
        self.root.update_idletasks()
        if self.root.winfo_width() < 1300 or self.root.winfo_height() < 780:
            self.root.geometry("1320x820")           # the editor's toolbars need the room
        self.editor = Editor(self.root, self, path, copy)
        self.editor.start()

    def new_level(self):
        self.open_editor()

    def editor_closed(self):
        self.editor = None
        self.root.minsize(*MENU_MIN)
        _info_cache.clear()                           # levels may have been added or changed
        self.tidy_levels()                            # ... or renamed: the file takes the new name
        self.screen = MyLevels(self)
        self._show_menu()

    @staticmethod
    def levels_place():
        """The player's levels folder as a person would say it: Documents/GD Remake/levels."""
        home = os.path.expanduser("~")
        return os.path.relpath(LEVELS, home) if paths.inside(LEVELS, home) else LEVELS

    def reveal_levels(self):
        """Open the player's levels folder in Finder (or the system's file manager)."""
        problem = paths.ensure_home()
        if problem:
            messagebox.showerror("Cannot open the folder", "%s: %s" % (LEVELS, problem))
            return
        opener = {"darwin": ["open"], "win32": ["explorer"]}.get(sys.platform, ["xdg-open"])
        try:
            subprocess.Popen(opener + [LEVELS])
        except OSError as exc:
            messagebox.showerror("Cannot open the folder", str(exc))

    def import_level(self):
        path = filedialog.askopenfilename(title="Import a level file")
        if not path:
            return
        try:
            raw = load_level_file(path)
        except (LevelFormatError, OSError) as exc:
            messagebox.showerror("Cannot import", str(exc))
            return
        stem = raw.name if raw.name not in ("Unnamed", "New level") else os.path.splitext(os.path.basename(path))[0]
        try:
            os.makedirs(LEVELS, exist_ok=True)
            write_gmd(unique_path(LEVELS, stem), stem, raw.level_string)
        except OSError as exc:
            messagebox.showerror("Cannot import", "%s: %s" % (LEVELS, exc.strerror or exc))
            return
        self.show_mine()

    def run(self):
        self.root.lift()
        self.root.focus_force()
        if self.game is None and self.editor is None:
            self.cv.focus_set()
            self.redraw()
        self.root.mainloop()


Menu = App          # the name main.py used before the redesign
