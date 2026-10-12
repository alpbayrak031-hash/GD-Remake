"""The level editor.  It is a screen of the main window (gd/menu_tk.py), not a
window of its own: `python3 editor.py [level file]` opens the app straight on it.

Layout: a toolbar on top (close, file, undo, level name, start settings, Play), the
level in the middle, and a build bar at the bottom - object tabs with picture tiles,
like GD's own editor - next to the tools and view options.

Saving is automatic: about a second after any change the level is written to its
file (a new level gets a file in the player's levels folder, Documents/GD Remake/levels,
on its first change).  The first time a session overwrites a file that already
existed, the old version is copied to savedata/backups/ in that folder first.

Mouse   left click / drag   place the selected object (snaps to the grid)
        right click / drag  erase            (also: Shift + left, or the Erase tool)
        Alt/Option + click  pick the object under the cursor as the brush
        wheel / trackpad    scroll           (arrow keys and A / D also scroll)
        Space + drag        pan
Keys    R rotate   F flip X   V flip Y   E erase tool   G half grid   H hitboxes
        1-9, 0 pick a tile   Tab next tab   - / = zoom   Home / End   ? help
        P play   B watch the bot   Ctrl/Cmd + O / N / Z, Shift+Z redo
        Ctrl/Cmd + W close the editor   Ctrl/Cmd + S save right now
"""
import os
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from . import constants as C
from . import icons, paths, ui
from . import objects as O
from .editor_model import GRID, PALETTE, EdObject, LevelDocument, describe, snap_position
from .levelio import LevelFormatError, list_levels
from .render_tk import BG, SPEED_COLORS, Painter

LEVELS = paths.USER_LEVELS            # where the player's levels are kept (Documents/GD Remake/levels)
BACKUPS = paths.BACKUPS
AUTOSAVE_MS = 1000            # how often the editor checks for changes to write
MOD = "Command" if sys.platform == "darwin" else "Control"
ZOOMS = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
TOP_H, BAR_H, TILE = 56, 150, 62
KEYS = "GDEditorKeys"         # a bind tag: the editor's shortcuts live on its own widgets, not on the window
BAR_BG = "#10151F"            # the toolbars are a shade darker than panels, so quiet buttons still show
HELP = """Building
  left click / drag        place the selected object
  right click / drag       erase   (or Shift + left, or the Erase tool: E)
  Alt / Option + click     pick the object under the cursor
  R   F   V                rotate 90,  flip X,  flip Y
  1 - 9, 0                 choose a tile          Tab   next tab
  G                        half grid (15 units)

Moving around
  wheel / trackpad, arrows, A / D        scroll
  Space + drag                           pan
  - / =                                  zoom
  Home / End                             jump to the start / the finish

Testing and files
  P        play the level            B   let the bot try it
  H        show / hide hitboxes
  Cmd/Ctrl + Z, Shift + Z            undo, redo
  Cmd/Ctrl + O, N                    open, new
  Cmd/Ctrl + W                       close the editor

Your level is saved automatically as you build.

Press ? or Esc to close this help."""


class TilePainter(Painter):
    """Draws palette tiles on the build bar."""

    def __init__(self, cv):
        self.cv = cv
        self.zoom = 1.0


class Editor(Painter):
    """`host` is the application: it provides play_level(level, inputs, hitboxes),
    editor_closed() and quit(), and owns the window this editor lives in."""

    def __init__(self, parent, host, path=None, copy=False):
        self.host = host
        self.doc = LevelDocument()
        self.zoom = 1.5
        self.left, self.top = -90.0, -520.0          # canvas coordinates of the view's corner
        self.brush, self.rot, self.flip_x, self.flip_y = 1, 0.0, False, False
        self.tool = "place"
        self.grid = GRID
        self.show_hit = True
        self.tab = 0
        self.busy = False
        self.help_open = False
        self.note = "Pick an object below, then click in the level to place it.   Press ? for all shortcuts."
        self._stroke = None
        self._pan = None
        self._space = False
        self._drawn = set()
        self.save_error = ""                         # why the last save failed ("" = it worked)
        self._retry_at = 0.0
        self._created = None                         # a file this session made for a new level
        self._backed_up = set()
        self.root = parent.winfo_toplevel()
        self.frame = tk.Frame(parent, bg=BAR_BG)
        self._build_ui()
        self._bind()
        if path:
            self._load(path, copy)

    # ------------------------------------------------------------------ showing and hiding
    def start(self):
        self.frame.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self.refresh_all()
        self.cv.focus_set()
        self.root.after(AUTOSAVE_MS, self._autosave_tick)

    def suspend(self):
        """Step aside while the level is being play-tested."""
        self.frame.pack_forget()

    def resume(self):
        self.frame.pack(fill="both", expand=True)
        self.cv.focus_set()
        self._view_changed()
        self._status()

    # ------------------------------------------------------------------ widgets
    def _build_ui(self):
        root = self.frame
        self.top_bar = tk.Canvas(root, height=TOP_H, bg=BAR_BG, highlightthickness=0)
        self.top_bar.pack(side="top", fill="x")
        self.v_name = tk.StringVar(value=self.doc.name)
        self.name_entry = tk.Entry(self.top_bar, textvariable=self.v_name, width=14, relief="flat",
                                   bg="#0A0D15", fg=ui.TEXT, insertbackground=ui.TEXT, font=ui.font(14),
                                   highlightthickness=1, highlightbackground=ui.LINE, highlightcolor=ui.CYAN)
        self.v_name.trace_add("write", lambda *a: self._renamed())
        self.name_entry.bind("<Return>", lambda e: self.cv.focus_set())
        self.name_entry.bind("<Escape>", lambda e: self.cv.focus_set())

        self.status = tk.Label(root, anchor="w", bg="#0A0D15", fg=ui.MUTED, font=ui.font(12), padx=10, pady=3)
        self.status.pack(side="bottom", fill="x")
        self.bar = tk.Canvas(root, height=BAR_H, bg=BAR_BG, highlightthickness=0)
        self.bar.pack(side="bottom", fill="x")
        self.tiles = TilePainter(self.bar)
        self.hscroll = tk.Scrollbar(root, orient="horizontal", command=self._scrollbar_moved)
        self.hscroll.pack(side="bottom", fill="x")
        self.cv = tk.Canvas(root, bg=BG, highlightthickness=0, cursor="crosshair", takefocus=1)
        self.cv.pack(side="top", fill="both", expand=True)
        self.top_bar.bind("<Configure>", lambda e: self._draw_top())
        self.bar.bind("<Configure>", lambda e: self._draw_bar())

    def _draw_top(self):
        cv = self.top_bar
        cv.delete("all")
        w = max(cv.winfo_width(), 800)
        y, h = 10, 36
        x = 10
        ui.Button(cv, x, y, 76, h, "\u2715 Close", self.close, "danger", 13, radius=8)
        x += 81
        ui.Button(cv, x, y, 48, h, "Quit", self.quit_app, "quiet", 13, radius=8)
        x += 60
        for text, cmd, width in (("New", self.new, 46), ("Open", self.open, 52), ("Save as", self.save_as, 70)):
            ui.Button(cv, x, y, width, h, text, cmd, "normal", 13, radius=8)
            x += width + 5
        x += 8
        for text, cmd in (("Undo", self.undo), ("Redo", self.redo)):
            ui.Button(cv, x, y, 52, h, text, cmd, "quiet", 13, radius=8)
            x += 57
        x += 8
        cv.create_text(x, y + h / 2, text="Name", anchor="w", fill=ui.MUTED, font=ui.font(12))
        cv.create_window(x + 40, y + h / 2, window=self.name_entry, anchor="w", height=30, width=136)
        x += 40 + 146
        st = self.doc.get_start()
        mode = st["mode"] if 0 <= st["mode"] <= 7 else 0
        cv.create_text(x, y + h / 2, text="Start", anchor="w", fill=ui.MUTED, font=ui.font(12))
        x += 38
        ui.Button(cv, x, y, 104, h, icons.MODE_NAMES[mode], lambda bx=x: self._popup_mode(bx), "normal", 13, radius=8,
                  icon=lambda c, ix, iy, tags: icons.draw_icon(c, mode, ix + 2, iy, 9, tags=tags))
        x += 109
        ui.Button(cv, x, y, 52, h, C.SPEED_NAMES[st["speed"]], lambda bx=x: self._popup_speed(bx), "normal", 13, radius=8)
        x += 57
        ui.Button(cv, x, y, 50, h, "Mini", lambda: self._set_start(mini=not st["mini"]),
                  "accent" if st["mini"] else "quiet", 13, radius=8)
        x += 55
        ui.Button(cv, x, y, 66, h, "Flipped", lambda: self._set_start(flipped=not st["flipped"]),
                  "accent" if st["flipped"] else "quiet", 13, radius=8)
        ui.Button(cv, w - 122, y - 2, 112, h + 4, "PLAY", self.play, "primary", 16, "P", radius=10)
        ui.Button(cv, w - 240, y, 112, h, "Watch bot", self.bot, "normal", 13, "B", radius=8, enabled=not self.busy)

    def _popup_mode(self, x):
        menu = tk.Menu(self.root, tearoff=0)
        for i, name in enumerate(icons.MODE_NAMES):
            menu.add_command(label=name, command=lambda m=i: self._set_start(mode=m))
        menu.tk_popup(self.top_bar.winfo_rootx() + int(x), self.top_bar.winfo_rooty() + TOP_H - 8)

    def _popup_speed(self, x):
        menu = tk.Menu(self.root, tearoff=0)
        for i, name in enumerate(C.SPEED_NAMES):
            menu.add_command(label=name, command=lambda s=i: self._set_start(speed=s))
        menu.tk_popup(self.top_bar.winfo_rootx() + int(x), self.top_bar.winfo_rooty() + TOP_H - 8)

    def _draw_bar(self):
        cv = self.bar
        cv.delete("all")
        w = max(cv.winfo_width(), 900)
        # --- tools (left)
        cv.create_text(14, 16, text="TOOL", anchor="w", fill=ui.MUTED, font=ui.font(10, True))
        ui.Button(cv, 12, 28, 84, 36, "Build", lambda: self._set_tool("place"),
                  "primary" if self.tool == "place" else "quiet", 13, radius=8)
        ui.Button(cv, 102, 28, 84, 36, "Erase", lambda: self._set_tool("erase"),
                  "danger" if self.tool == "erase" else "quiet", 13, "E", radius=8)
        ui.Button(cv, 12, 70, 174, 30, "Rotate: %d degrees" % self.rot, self._rotate, "normal", 12, "R", radius=8)
        ui.Button(cv, 12, 106, 84, 30, "Flip X", lambda: self._flip("flip_x"), "accent" if self.flip_x else "quiet",
                  12, "F", radius=8)
        ui.Button(cv, 102, 106, 84, 30, "Flip Y", lambda: self._flip("flip_y"), "accent" if self.flip_y else "quiet",
                  12, "V", radius=8)
        cv.create_line(200, 10, 200, BAR_H - 10, fill=ui.LINE)
        # --- view options (right)
        rx = w - 196
        cv.create_line(rx - 12, 10, rx - 12, BAR_H - 10, fill=ui.LINE)
        cv.create_text(rx, 16, text="VIEW", anchor="w", fill=ui.MUTED, font=ui.font(10, True))
        ui.Button(cv, rx, 28, 88, 34, "Grid %d" % self.grid, self._toggle_grid, "normal", 12, "G", radius=8)
        ui.Button(cv, rx + 94, 28, 90, 34, "Hitboxes", self._toggle_hit, "accent" if self.show_hit else "quiet", 12,
                  "H", radius=8)
        ui.Button(cv, rx, 68, 40, 34, "-", lambda: self._zoom(-1), "normal", 16, radius=8)
        cv.create_text(rx + 66, 85, text="%d%%" % round(self.zoom / 1.5 * 100), fill=ui.TEXT, font=ui.font(12, True))
        ui.Button(cv, rx + 94, 68, 40, 34, "+", lambda: self._zoom(1), "normal", 16, radius=8)
        ui.Button(cv, rx + 140, 68, 44, 34, "?", self.toggle_help, "quiet", 14, radius=8)
        ui.Button(cv, rx, 108, 88, 30, "To start", lambda: self._goto(0.0), "quiet", 11, radius=8)
        ui.Button(cv, rx + 94, 108, 90, 30, "To finish", lambda: self._goto(self.doc.extent()[1]), "quiet", 11, radius=8)
        # --- object tabs and tiles (middle)
        x = 214
        for i, (group, _items) in enumerate(PALETTE):
            width = 30 + 8 * len(group)
            ui.Button(cv, x, 8, width, 28, group, lambda t=i: self._set_tab(t), "accent" if i == self.tab else "quiet",
                      12, radius=8)
            x += width + 6
        self._tile_ids = [oid for oid, _label in PALETTE[self.tab][1]]
        x = 214
        for n, (oid, label) in enumerate(PALETTE[self.tab][1]):
            self._tile(cv, x, 44, oid, label, n)
            x += TILE + 10

    def _tile(self, cv, x, y, oid, label, n):
        tag = "tile%d" % oid
        selected = oid == self.brush and self.tool == "place"
        ui.rounded(cv, x, y, x + TILE, y + TILE, 10, fill="#0A0D15", outline=ui.GREEN if selected else ui.LINE,
                   width=3 if selected else 1, tags=tag)
        ghost = EdObject({1: str(oid), 2: "0", 3: "0"})
        mx, my = x + TILE / 2.0, y + TILE / 2.0
        kind = ghost.phys.kind if ghost.phys is not None else None
        if kind == O.PORTAL_MODE:                       # a portal is easiest to recognise by what it turns you into
            icons.draw_icon(cv, ghost.phys.arg, mx, my, 16, tags=tag)
        elif kind == O.PORTAL_SPEED:
            cv.create_text(mx, my, text=C.SPEED_NAMES[ghost.phys.arg], fill=SPEED_COLORS[ghost.phys.arg],
                           font=ui.font(18, True), tags=tag)
        elif kind == O.PORTAL_GRAVITY:
            col = {"normal": "#4DC3FF", "flipped": "#FFE14D"}.get(ghost.phys.arg, "#59E36B")
            ups = {"normal": (-1,), "flipped": (1,)}.get(ghost.phys.arg, (1, -1))
            for up in ups:
                off = 0 if len(ups) == 1 else 12 * up
                cv.create_polygon(mx - 13, my + (8 * up if len(ups) == 1 else -off + 3 * up) , mx + 13,
                                  my + (8 * up if len(ups) == 1 else -off + 3 * up), mx,
                                  my - (14 * up if len(ups) == 1 else off + 13 * up), fill=col, outline="", tags=tag)
        elif kind == O.PORTAL_SIZE:
            half = 7 if ghost.phys.arg else 16
            cv.create_rectangle(mx - half, my - half, mx + half, my + half, fill="#FF7AD9" if ghost.phys.arg else "#59E36B",
                                outline="", tags=tag)
        elif ghost.phys is not None:
            p = ghost.phys
            span = max(p.maxx - p.minx, p.maxy - p.miny, 30.0)
            if kind == O.SAW:
                span = p.radius * 3.0                   # the drawn blade is wider than its hitbox
            self.tiles.zoom = min(1.5, (TILE - 16) / span)
            cx, cy = (p.minx + p.maxx) / 2.0, (p.miny + p.maxy) / 2.0
            centred = EdObject({1: str(oid), 2: str((x + TILE / 2.0) / self.tiles.zoom - cx),
                                3: str(-(y + TILE / 2.0) / self.tiles.zoom - cy)})
            for item in self.tiles.draw_object(centred.phys, tag):
                tags = cv.gettags(item)
                if "hit" in tags or (cv.type(item) == "text" and "art" in tags):
                    cv.delete(item)
        cv.create_text(x + TILE / 2.0, y + TILE + 11, text=label, fill=ui.TEXT if selected else ui.MUTED,
                       font=ui.font(10), tags=tag)
        cv.create_text(x + 8, y + 9, text=str((n + 1) % 10), fill=ui.MUTED, font=ui.font(9), tags=tag)
        cv.tag_bind(tag, "<ButtonRelease-1>", lambda e, o=oid: self._pick_brush(o))
        cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))

    def _bind(self):
        cv, root = self.cv, self.root
        cv.bind("<Configure>", lambda e: self._view_changed())
        cv.bind("<Motion>", self._motion)
        cv.bind("<Leave>", lambda e: cv.delete("ghost"))
        cv.bind("<ButtonPress-1>", lambda e: self._press(e, erase=bool(e.state & 0x1)))
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<ButtonRelease-1>", self._end_stroke)
        for n in (2, 3):
            cv.bind("<ButtonPress-%d>" % n, lambda e: self._press(e, erase=True))
            cv.bind("<B%d-Motion>" % n, self._drag)
            cv.bind("<ButtonRelease-%d>" % n, self._end_stroke)
        for mod in ("Alt", "Option"):
            try:
                cv.bind("<%s-ButtonPress-1>" % mod, self._eyedrop)
            except tk.TclError:
                pass
        cv.bind("<MouseWheel>", lambda e: self._scroll(0, -self._wheel(e)))
        cv.bind("<Shift-MouseWheel>", lambda e: self._scroll(-self._wheel(e), 0))
        cv.bind("<Button-4>", lambda e: self._scroll(0, -40))
        cv.bind("<Button-5>", lambda e: self._scroll(0, 40))

        # Shortcuts are bound to a tag that only the editor's canvases carry.  They work
        # while the editor has the keyboard, never fire while typing the level name, and
        # are simply gone once the editor's widgets are destroyed.
        for widget in (self.cv, self.top_bar, self.bar):
            widget.bindtags((KEYS,) + widget.bindtags())

            if widget is not self.cv:               # a click on a toolbar ends typing in the name field
                widget.bind("<ButtonPress-1>", lambda e: self.cv.focus_set(), add="+")

        def key(seq, fn):
            root.bind_class(KEYS, seq, lambda e: fn())

        key("<Left>", lambda: self._scroll(-120, 0))
        key("<Right>", lambda: self._scroll(120, 0))
        key("<Up>", lambda: self._scroll(0, -90))
        key("<Down>", lambda: self._scroll(0, 90))
        key("a", lambda: self._scroll(-240, 0))
        key("d", lambda: self._scroll(240, 0))
        key("<Home>", lambda: self._goto(0.0))
        key("<End>", lambda: self._goto(self.doc.extent()[1]))
        key("r", self._rotate)
        key("f", lambda: self._flip("flip_x"))
        key("v", lambda: self._flip("flip_y"))
        key("e", lambda: self._set_tool("place" if self.tool == "erase" else "erase"))
        key("<Escape>", self._escape)
        key("g", self._toggle_grid)
        key("h", self._toggle_hit)
        key("<minus>", lambda: self._zoom(-1))
        key("<equal>", lambda: self._zoom(+1))
        key("p", self.play)
        key("b", self.bot)
        key("<question>", self.toggle_help)
        key("<F1>", self.toggle_help)
        key("<Tab>", lambda: self._set_tab((self.tab + 1) % len(PALETTE)))
        for n in range(10):
            key(str(n), lambda i=(n - 1) % 10: self._pick_tile(i))
        key("<KeyPress-space>", lambda: self._set_space(True))
        key("<KeyRelease-space>", lambda: self._set_space(False))
        for target in (KEYS, None):                     # the file shortcuts also work from the name field
            for seq, fn in (("s", self.save), ("o", self.open), ("n", self.new), ("z", self.undo),
                            ("Z", self.redo), ("y", self.redo), ("w", self.close)):
                full = "<%s-%s>" % (MOD, seq)
                if target:
                    root.bind_class(target, full, lambda e, f=fn: (f(), "break")[1])
                else:
                    self.name_entry.bind(full, lambda e, f=fn: (f(), "break")[1])

    @staticmethod
    def _wheel(e):
        d = e.delta
        if abs(d) >= 120:
            d = d / 120.0 * 3.0
        return d * 12.0

    def _set_space(self, down):
        self._space = down
        self.cv.configure(cursor="fleur" if down else ("crosshair" if self.tool == "place" else "X_cursor"))

    # ------------------------------------------------------------------ coordinates
    def _world(self, e):
        return (e.x + self.left) / self.zoom, -(e.y + self.top) / self.zoom

    def _brush_position(self, wx, wy):
        return snap_position(self.brush, wx, wy, self.rot, half=self.grid != GRID)

    # ------------------------------------------------------------------ drawing
    def _view_changed(self):
        cv = self.cv
        w, h = max(cv.winfo_width(), 10), max(cv.winfo_height(), 10)
        cv.configure(scrollregion=(self.left, self.top, self.left + w, self.top + h))
        cv.xview_moveto(0)
        cv.yview_moveto(0)
        cv.delete("grid")
        z = self.zoom
        x0, x1 = self.left / z, (self.left + w) / z
        y0, y1 = -(self.top + h) / z, -self.top / z
        step = GRID
        gx = int(x0 // step) * step
        while gx <= x1:
            cv.create_line(gx * z, self.top, gx * z, self.top + h,
                           fill="#1A2032" if gx % 300 else "#2C3654", tags="grid")
            gx += step
        gy = int(y0 // step) * step
        while gy <= y1:
            cv.create_line(self.left, -gy * z, self.left + w, -gy * z,
                           fill="#1A2032" if gy % 300 else "#2C3654", tags="grid")
            gy += step
        if y0 < 0:
            cv.create_rectangle(self.left, 0, self.left + w, -y0 * z + 2, fill="#080A11", outline="", tags="grid")
        cv.create_line(self.left, 0, self.left + w, 0, fill="#DDE3F0", width=2, tags="grid")
        cv.create_line(0, self.top, 0, self.top + h, fill=ui.GREEN, dash=(2, 4), tags="grid")
        end = (self.doc.extent()[1] + C.LEVEL_END_PADDING) * z
        cv.create_line(end, self.top, end, self.top + h, fill=ui.GREEN, width=2, dash=(8, 6), tags="grid")
        cv.create_text(end + 8, self.top + 14, anchor="w", fill=ui.GREEN, text="FINISH", tags="grid", font=ui.font(11, True))
        cv.create_text(8, self.top + 14, anchor="w", fill=ui.GREEN, text="START", tags="grid", font=ui.font(11, True))
        cv.tag_lower("grid")
        # the start marker: the icon the level begins with, standing at x = 0
        cv.delete("startmark")
        st = self.doc.get_start()
        half = 15.0 * (C.MINI_SCALE if st["mini"] else 1.0)
        icons.draw_icon(cv, st["mode"] if 0 <= st["mode"] <= 7 else 0, 0, -half * z, half * z, flipped=st["flipped"],
                        tags="startmark")
        self._update_scrollbar(w)
        if self.help_open:
            self._draw_help()

    def _update_scrollbar(self, w):
        total = (self.doc.extent()[1] + C.LEVEL_END_PADDING + 600.0) * self.zoom + 400.0
        first = (self.left + 200.0) / total
        last = (self.left + 200.0 + w) / total
        self.hscroll.set(max(0.0, min(1.0, first)), max(0.0, min(1.0, last)))
        self._scroll_total = total

    def _scrollbar_moved(self, action, value, unit=None):
        if action == "moveto":
            self.left = float(value) * self._scroll_total - 200.0
        else:
            self.left += int(value) * (60 if unit == "units" else self.cv.winfo_width() * 0.8)
        self._view_changed()

    def _draw(self, obj):
        tag = "u%d" % obj.uid
        if obj.phys is not None:
            self.draw_object(obj.phys, tag)
        else:
            z = self.zoom
            self.cv.create_rectangle((obj.x - 6) * z, -(obj.y + 6) * z, (obj.x + 6) * z, -(obj.y - 6) * z,
                                     outline="#6B7280", dash=(2, 2), tags=(tag,))
            if z >= 1.5:
                self.cv.create_text(obj.x * z, -(obj.y + 12) * z, text=str(obj.id), fill="#6B7280",
                                    font=ui.font(8), tags=(tag,))

    def refresh_all(self):
        self.cv.delete("all")
        self._drawn = set()
        for obj in self.doc.objects:
            self._draw(obj)
            self._drawn.add(obj.uid)
        self._apply_hit()
        self._view_changed()
        self._sync_start_widgets()
        self._draw_top()
        self._draw_bar()
        self._status()

    def _sync_objects(self):
        """Bring the canvas in line with the document after an edit, undo or redo."""
        wanted = {o.uid: o for o in self.doc.objects}
        for uid in self._drawn - set(wanted):
            self.cv.delete("u%d" % uid)
        for uid in set(wanted) - self._drawn:
            self._draw(wanted[uid])
        self._drawn = set(wanted)
        self._apply_hit()
        self.cv.tag_lower("grid")
        self.cv.tag_raise("startmark")
        self._status()

    def _apply_hit(self):
        self.cv.itemconfigure("hit", state="normal" if self.show_hit else "hidden")

    def _status(self, where=""):
        sim, other = self.doc.counts()
        length = (self.doc.extent()[1] + C.LEVEL_END_PADDING) / 311.58
        parts = ["%d objects%s" % (sim, " (+%d not simulated)" % other if other else ""),
                 "about %d s at 1x" % round(length)]
        if where:
            parts.append(where)
        parts.append(self.note)
        if self.save_error:
            saved = "NOT SAVED - %s" % self.save_error
        elif self.doc.dirty:
            saved = "Saving..."
        elif self.doc.path:
            saved = "Saved"
        else:
            saved = "Autosave is on"
        self.status.configure(text="      ".join([saved] + parts), fg=ui.RED if self.save_error else ui.MUTED)
        self.root.title("Level editor - %s" % self.doc.name)

    def _say(self, text):
        self.note = text
        self._status()

    # ------------------------------------------------------------------ help
    def toggle_help(self):
        self.help_open = not self.help_open
        self.cv.delete("help")
        if self.help_open:
            self._draw_help()

    def _draw_help(self):
        cv = self.cv
        cv.delete("help")
        w, h = cv.winfo_width(), cv.winfo_height()
        pw, ph = 560, 470
        x0, y0 = self.left + (w - pw) / 2.0, self.top + max(10.0, (h - ph) / 2.0)
        ui.rounded(cv, x0, y0, x0 + pw, y0 + ph, 16, fill=ui.PANEL, outline=ui.LINE, tags="help")
        cv.create_text(x0 + 28, y0 + 34, text="Editor shortcuts", anchor="w", fill=ui.TEXT, font=ui.font(20, True), tags="help")
        cv.create_text(x0 + 28, y0 + 64, text=HELP, anchor="nw", fill=ui.TEXT, font=(ui.MONO, 12), tags="help")
        cv.tag_raise("help")

    def _escape(self):
        if self.help_open:
            self.toggle_help()
        else:
            self._set_tool("place")

    # ------------------------------------------------------------------ mouse
    def _motion(self, e):
        wx, wy = self._world(e)
        self.cv.delete("ghost")
        if self.help_open:
            return
        z = self.zoom
        x, y, cx, cy = self._brush_position(wx, wy)
        if self.tool == "place" and not self._space:
            raw = {1: str(self.brush), 2: str(x), 3: str(y), 6: str(self.rot)}
            if self.flip_x:
                raw[4] = "1"
            if self.flip_y:
                raw[5] = "1"
            ghost = EdObject(raw)
            if ghost.phys is not None:
                for item in self.draw_object(ghost.phys, "ghost"):
                    if "hit" in self.cv.gettags(item) and not self.show_hit:
                        self.cv.itemconfigure(item, state="hidden")
        self.cv.create_rectangle((cx - 15) * z, -(cy + 15) * z, (cx + 15) * z, -(cy - 15) * z,
                                 outline=ui.GREEN if self.tool == "place" else ui.RED, tags="ghost")
        self._status("column %d, row %d" % (wx // GRID, wy // GRID))

    def _press(self, e, erase=False):
        self.cv.focus_set()
        if self.help_open:
            self.toggle_help()
            return
        if self._space:
            self._pan = (e.x, e.y, self.left, self.top)
            return
        self._stroke = "erase" if (erase or self.tool == "erase") else "place"
        self.doc.begin_stroke()
        self._apply_stroke(e)

    def _drag(self, e):
        if self._pan:
            px, py, left, top = self._pan
            self.left, self.top = left - (e.x - px), top - (e.y - py)
            self._view_changed()
        elif self._stroke:
            self._apply_stroke(e)
            self._motion(e)

    def _apply_stroke(self, e):
        wx, wy = self._world(e)
        if self._stroke == "erase":
            obj = self.doc.pick(wx, wy)
            if obj is not None:
                self.doc.erase(obj, checkpoint=False)
                self._sync_objects()
        else:
            x, y, _, _ = self._brush_position(wx, wy)
            if self.doc.place(self.brush, x, y, self.rot, self.flip_x, self.flip_y, checkpoint=False):
                self._sync_objects()

    def _end_stroke(self, _e):
        self._pan = None
        if self._stroke:
            self._stroke = None
            self._view_changed()                 # the finish line may have moved

    def _eyedrop(self, e):
        obj = self.doc.pick(*self._world(e))
        if obj is not None:
            self.rot = obj.rot
            self.flip_x, self.flip_y = obj.raw.get(4) == "1", obj.raw.get(5) == "1"
            self._pick_brush(obj.id)
            self._say("Picked %s." % describe(obj.id))
        return "break"

    def _scroll(self, dx, dy):
        self.left += dx
        self.top += dy
        self._view_changed()

    def _goto(self, x):
        self.left = x * self.zoom - 160.0
        self._view_changed()

    # ------------------------------------------------------------------ brush / view options
    def _pick_brush(self, oid):
        self.brush = oid
        for i, (_group, items) in enumerate(PALETTE):
            if any(o == oid for o, _ in items):
                self.tab = i
        self.tool = "place"
        self.cv.configure(cursor="crosshair")
        self._draw_bar()
        self._say("Placing: %s.   R rotates, right click erases." % describe(oid))

    def _pick_tile(self, index):
        ids = [oid for oid, _ in PALETTE[self.tab][1]]
        if 0 <= index < len(ids):
            self._pick_brush(ids[index])

    def _set_tab(self, tab):
        self.tab = tab
        self._draw_bar()

    def _rotate(self):
        self.rot = (self.rot + 90.0) % 360.0
        self._draw_bar()

    def _flip(self, name):
        setattr(self, name, not getattr(self, name))
        self._draw_bar()

    def _set_tool(self, tool):
        self.tool = tool
        self.cv.configure(cursor="crosshair" if tool == "place" else "X_cursor")
        self._draw_bar()
        self._say("Erasing: click or drag over objects.   E or Esc goes back to building." if tool == "erase"
                  else "Placing: %s." % describe(self.brush))

    def _toggle_grid(self):
        self.grid = GRID / 2 if self.grid == GRID else GRID
        self._draw_bar()

    def _toggle_hit(self):
        self.show_hit = not self.show_hit
        self._apply_hit()
        self._draw_bar()

    def _zoom(self, step):
        i = min(range(len(ZOOMS)), key=lambda k: abs(ZOOMS[k] - self.zoom))
        new = ZOOMS[max(0, min(len(ZOOMS) - 1, i + step))]
        if new != self.zoom:
            w, h = self.cv.winfo_width(), self.cv.winfo_height()
            cx, cy = (self.left + w / 2) / self.zoom, (self.top + h / 2) / self.zoom
            self.zoom = new
            self.left, self.top = cx * new - w / 2, cy * new - h / 2
            self.refresh_all()

    # ------------------------------------------------------------------ start settings
    def _sync_start_widgets(self):
        if self.v_name.get() != self.doc.name:
            self.v_name.set(self.doc.name)

    def _set_start(self, **kw):
        self.doc.set_start(**kw)
        self._draw_top()
        self._view_changed()
        self._status()

    def _renamed(self):
        name = self.v_name.get().strip() or "Unnamed"
        if name != self.doc.name:
            self.doc.name = name
            self.doc.dirty = True
            self._status()

    # ------------------------------------------------------------------ saving
    def autosave(self):
        """Write the level if it has changed since the last save.  False = writing failed."""
        doc = self.doc
        if not doc.dirty:
            return True
        try:
            if not doc.path:                       # a new level: give it a file among "My levels"
                from .menu_tk import unique_path
                os.makedirs(LEVELS, exist_ok=True)
                doc.path = self._created = unique_path(LEVELS, doc.name)
            elif doc.path not in self._backed_up and doc.path != self._created and os.path.isfile(doc.path):
                os.makedirs(BACKUPS, exist_ok=True)
                shutil.copy2(doc.path, os.path.join(BACKUPS, os.path.basename(doc.path)))
            self._backed_up.add(doc.path)
            doc.save()
        except OSError as exc:
            self.save_error = exc.strerror or str(exc)
            self._retry_at = time.monotonic() + 10.0
            return False
        self.save_error = ""
        return True

    def _autosave_tick(self):
        try:
            if not self.frame.winfo_exists():
                return
        except tk.TclError:                                        # the whole window is gone
            return
        try:
            typing = self.root.focus_get() is self.name_entry      # wait until the name is finished
        except KeyError:                                           # Tk's own quirk while a popup menu is open
            typing = True
        if (self.doc.dirty and self._stroke is None and not typing and self.frame.winfo_ismapped()
                and time.monotonic() >= self._retry_at):
            self.autosave()
            self._status()
        self.root.after(AUTOSAVE_MS, self._autosave_tick)

    def flush(self):
        """Save now, before leaving this level.  If that is impossible, ask whether to
        leave anyway.  Returns False when the user chooses to stay."""
        if self.autosave():
            return True
        self._status()
        return messagebox.askyesno("Could not save", "Saving %r failed: %s.\n\nLeave anyway and lose the "
                                   "changes that are not saved?" % (self.doc.name, self.save_error))

    # ------------------------------------------------------------------ files
    def new(self):
        if self.flush():
            self.doc = LevelDocument()
            self._created = None
            self.save_error = ""
            self.left, self.top = -90.0, -520.0
            self.refresh_all()

    def _load(self, path, copy=False):
        try:
            levels = list_levels(path)
            select = None
            if len(levels) > 1:
                select = self._choose([lv.name for lv in levels])
                if select is None:
                    return
            self.doc = LevelDocument.load(path, select)
        except (LevelFormatError, OSError) as exc:
            messagebox.showerror("Cannot open level", str(exc))
            return
        self._created = None
        self.save_error = ""
        if copy or paths.inside(path, paths.PACK_LEVELS):   # a built-in level: never written back over itself
            self.doc.path = None
            self.doc.name += " copy"
            self.note = "This is a copy of a built-in level. It becomes a level of your own once you change it."
        self.left, self.top = -90.0, -520.0
        if self.frame.winfo_ismapped():
            self.refresh_all()

    def _choose(self, names):
        win = tk.Toplevel(self.root)
        win.title("Which level?")
        box = tk.Listbox(win, width=40, height=min(20, len(names)))
        for n in names:
            box.insert("end", n)
        box.pack(padx=8, pady=8)
        box.selection_set(0)
        picked = []
        tk.Button(win, text="Open", command=lambda: (picked.extend(box.curselection()), win.destroy())).pack(pady=6)
        win.grab_set()
        self.root.wait_window(win)
        return picked[0] if picked else None

    def open(self):
        if not self.flush():
            return
        path = filedialog.askopenfilename(title="Open level", initialdir=LEVELS)
        if path:
            self._load(path)

    def save(self):
        """Save right now instead of waiting for the next autosave."""
        self.doc.dirty = True
        self._retry_at = 0.0
        if not self.autosave():
            self._status()
            messagebox.showerror("Cannot save", "Saving %r failed: %s." % (self.doc.name, self.save_error))
            return False
        self._say("Saved to %s." % os.path.basename(self.doc.path))
        return True

    def save_as(self):
        """Continue in a new file; the file edited so far stays as it is."""
        path = filedialog.asksaveasfilename(title="Save level as", defaultextension=".gmd", initialdir=LEVELS,
                                            initialfile=self.doc.name.replace(" ", "_") + ".gmd",
                                            filetypes=[("GDShare level", "*.gmd")])
        if not path:
            return False
        self.autosave()                            # the old file keeps everything done up to here
        self.doc.path = path
        self._created = path                       # the save dialog already asked before replacing a file
        return self.save()

    def undo(self):
        if self.doc.undo():
            self._sync_objects()
            self._draw_top()
            self._view_changed()

    def redo(self):
        if self.doc.redo():
            self._sync_objects()
            self._draw_top()
            self._view_changed()

    # ------------------------------------------------------------------ testing the level
    def play(self):
        self.autosave()
        self._status()
        self.host.play_level(self.doc.to_level(), None, self.show_hit)

    def bot(self):
        """Let the solver try the level (in a thread, so the window stays responsive)."""
        if self.busy:
            return
        self.busy = True
        self._draw_top()
        level = self.doc.to_level()
        result = {}

        def work():
            from .analysis import Analyzer
            from .timing import describe, search
            try:
                found, seq = search(level)               # the same measurement My Levels shows
                if seq is None:
                    result["text"] = None
                    result["why"] = found.get("reason")
                    return
                an = Analyzer(level, seq)
                result["replay"] = an.centre(passes=1)
                presses = an.report(worst=1)["presses"]
                if found["state"] == "ok":
                    tight = "Tightest jump: %s%s." % (describe(found)[0], " (a single physics step - frame perfect)"
                                                      if found["ticks"] == 1 else "")
                    if found.get("hold"):
                        tight += " Taps alone never work here: this route holds the button through a landing."
                elif found["state"] == "glitch":
                    tight = ("There is no normal route, so this run uses the glitch: the push onto a block "
                             "just after a gravity switch.")
                else:
                    tight = "No input is needed."
                result["text"] = "The bot finished it with %d press%s. %s" % (presses, "" if presses == 1 else "es", tight)
            except Exception as exc:                    # report instead of dying silently in a thread
                result["error"] = "%s: %s" % (type(exc).__name__, exc)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self._say("The bot is trying the level...")

        def poll():
            if not self.frame.winfo_exists():
                return
            if thread.is_alive():
                self.root.after(150, poll)
                return
            self.busy = False
            self._draw_top()
            if result.get("error"):
                messagebox.showerror("Bot failed", result["error"])
            elif result.get("text") is None:
                self._say("The bot gave up.")
                if result.get("why") == "time":
                    messagebox.showinfo("Bot", "The bot ran out of time before finding a way through.\n"
                                               "That does not mean there is none - the level is long or precise.")
                else:
                    messagebox.showinfo("Bot", "The bot found no way through, even trying every single physics step.\n"
                                               "The level is most likely impossible as built (very long levels can "
                                               "also exceed its search limit).")
            else:
                self._say(result["text"])
                if self.frame.winfo_ismapped():         # still in the editor: show the run
                    self.host.play_level(level, result["replay"], self.show_hit)

        self.root.after(150, poll)

    # ------------------------------------------------------------------ leaving
    def close(self):
        """Save and go back to the menu.  Returns False if the user stays (a failed save)."""
        if not self.flush():
            return False
        self.frame.destroy()
        self.host.editor_closed()
        return True

    def quit_app(self):
        """Save and close the whole game."""
        self.host.quit()


def main(argv=None):
    """`python3 editor.py [--copy] [level]`: the app, opened on the editor."""
    from .menu_tk import App
    argv = list(sys.argv[1:] if argv is None else argv)
    copy = "--copy" in argv
    if copy:
        argv.remove("--copy")
    app = App()
    app.open_editor(argv[0] if argv else None, copy)
    app.run()
