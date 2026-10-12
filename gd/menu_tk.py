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
import tkinter as tk
from tkinter import filedialog, messagebox

from . import icons, paths, ui
from .level import Level
from .levelio import LevelFormatError, load_level_file, write_gmd
from .macros import MacroFormatError, load_macro
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


def unique_path(folder, stem, ext=".gmd"):
    stem = "".join(c if c.isalnum() or c in " -_" else "_" for c in stem).strip() or "level"
    path = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, "%s %d%s" % (stem, n, ext))
        n += 1
    return path


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
        ui.Button(cv, w / 2 + 8, y + 92, 224, 56, "Level Editor", app.new_level, "normal", 16, "E")
        ui.Button(cv, w / 2 - 232, y + 162, 224, 44, "Progress", app.show_stats, "quiet", 14, "S")
        ui.Button(cv, w / 2 + 8, y + 162, 224, 44, "Quit", app.quit, "quiet", 14, "Q")
        pack = [e for e in pack_levels() if e["difficulty"] != "test"]
        t = app.progress.totals([level_key(e["path"]) for e in pack])
        everything = app.progress.totals()
        cv.create_text(w / 2, h - 26, fill=ui.MUTED, font=ui.font(13),
                       text="%d of %d levels completed      %d attempts      %s played" % (
                           t["completed"], t["levels"], everything["attempts"], ui.format_time(everything["seconds"])))

    def key(self, k):
        app = self.app
        {"return": app.show_levels, "space": app.show_levels, "m": app.show_mine, "e": app.new_level,
         "s": app.show_stats, "q": app.quit, "escape": app.quit}.get(k, lambda: None)()


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
        self.root.after(TIMING_POLL_MS, self._timing_tick)

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
        self._set(MyLevels(self))

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
