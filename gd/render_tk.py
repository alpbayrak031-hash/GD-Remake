"""The game view (tkinter - ships with Python, nothing to install).

It only draws and feeds the button state to the simulation; all game state lives in
Simulation.  The loop measures real time, runs however many fixed 1/240 s steps are
due, then redraws - rendering never feeds back into the physics.

GameView lives inside any Tk window (the menu embeds it); Viewer wraps it in a
window of its own for `python3 main.py level.gmd`.

Keys   Space / Up / W / click  jump (hold)        Esc or P  pause menu
       R  restart     Z / X  add / remove a practice checkpoint
       H  hitboxes    O  debug info    F  FPS counter    T  trail    N  single step
       - / =  speed
"""
import math
import sys
import time
import tkinter as tk

from . import constants as C
from . import icons, settings
from . import objects as O
from . import ui
from .effects import Effects, blend
from .fps import FrameStats
from .macros import InputSequence
from .player import FLYING, WAVE

STEP = 1.0 / C.TPS
BG = ui.BG
COL_SOLID, COL_SOLID_FILL = "#6F9BFF", "#1D2947"
COL_HAZARD, COL_HAZARD_FILL = "#FF5A5A", "#6E1B24"
COL_SLOPE = "#86B4FF"
# The hitbox view uses the colours of GD's own "Show Hitboxes": blue for what is solid,
# red for what kills, green for what can be touched safely (orbs, pads).  On the player:
# blue = the small box tested against blocks, red = the outer box, dark red = the same
# box turned with the icon (only tested against objects placed at an odd angle).
HIT_SOLID, HIT_HAZARD, HIT_SPECIAL, HIT_ROTATED = "#3B8BFF", "#FF2F2F", "#45F27A", "#B5282E"
ORB_COLORS = {"yellow": "#FFE14D", "pink": "#FF7AD9", "red": "#FF4040", "blue": "#4DC3FF",
              "green": "#59E36B", "black": "#9AA3B5", "dash": "#3CFF9B",
              "gravity_dash": "#FF5CF2", "spider": "#B07CFF"}
PAD_COLORS = {"yellow": "#FFE14D", "pink": "#FF7AD9", "red": "#FF4040", "blue": "#4DC3FF",
              "spider": "#B07CFF"}
SPEED_COLORS = {0: "#FFB84D", 1: "#4DC3FF", 2: "#59E36B", 3: "#FF7AD9", 4: "#FF4040"}
# rough art sizes for the classic spikes, purely cosmetic (width, height)
SPIKE_ART = {8: (30, 30), 144: (30, 30), 177: (30, 30), 216: (30, 30),
             39: (30, 14), 205: (30, 14), 217: (30, 14),
             103: (20, 18), 145: (20, 18), 218: (20, 18)}
# background tint per difficulty, so a level's mood is visible at a glance
THEMES = {"easy": "#0E1A2E", "medium": "#161A33", "hard": "#231528", "very hard": "#260F18"}
AUTO_CHECKPOINT_TICKS = 300          # practice mode: one checkpoint every 1.25 s when safe
FPS_CAP = 240                        # frames a second at most: one per physics step - more only costs battery
CHUNK = 600.0                        # world units: level objects are drawn and dropped this many at a time
VIEW_MARGIN = 360.0                  # ... and drawn this far beyond the screen's edges, so nothing pops in


def _chunks(objects):
    """Objects bucketed by where they start -> [(min x, max x, objects)]."""
    buckets = {}
    for o in objects:
        buckets.setdefault(int(o.minx // CHUNK), []).append(o)
    return [(min(o.minx for o in objs), max(o.maxx for o in objs), objs) for _k, objs in sorted(buckets.items())]


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def level_backdrop(level, theme_bg):
    """(background, ground) colours for a level: its own, from the level file, darkened so
    the drawn objects stay readable - or the difficulty theme for levels without colours."""
    bg, ground = level.colours.get(1000), level.colours.get(1001)
    if bg is None:
        return theme_bg, blend(theme_bg, "#000000", 0.45)

    def dim(rgb, limit):
        top = max(rgb)
        return _hex(tuple(int(c * limit / top) for c in rgb)) if top > limit else _hex(rgb)
    return dim(bg, 64), dim(ground or (0, 0, 0), 40)


class Painter:
    """Turns physics objects into canvas items.  Shared by the game view and the editor.

    World units map to canvas coordinates as (x * zoom, -y * zoom); subclasses provide
    `cv` (a tk.Canvas) and `zoom`.  Items tagged "hit" are pure hitbox outlines and
    items tagged "art" are pure decoration, so either can be hidden.
    """
    cv = None
    zoom = 1.5
    _made = None
    _extra = ()                          # tags every new item also gets (draw_object's `tag`)
    block_fill, block_line, block_inner = COL_SOLID_FILL, COL_SOLID, "#33456F"
    hazard_fill, hazard_line, hazard_inner = COL_HAZARD_FILL, COL_HAZARD, "#9C2B36"

    def _track(self, item):
        if self._made is not None:
            self._made.append(item)
        return item

    def draw_object(self, o, tag=None):
        """Draw one object; returns its canvas items (all given `tag` - or every tag in a
        tuple - if one is passed)."""
        self._made = []
        self._extra = (tag,) if isinstance(tag, str) else tuple(tag or ())
        try:
            self._draw_object(o)
        finally:
            self._extra = ()
        items, self._made = self._made, None
        return items

    def _tags(self, kw):
        if self._extra:
            t = kw.get("tags") or ()
            kw["tags"] = ((t,) if isinstance(t, str) else tuple(t)) + self._extra
        return kw

    # ------------------------------------------------------------------ helpers
    def _wx(self, x):
        return x * self.zoom

    def _wy(self, y):
        return -y * self.zoom

    def _poly(self, pts, **kw):
        flat = []
        for x, y in pts:
            flat += [self._wx(x), self._wy(y)]
        return self._track(self.cv.create_polygon(*flat, **self._tags(kw)))

    def _rect(self, minx, maxx, miny, maxy, **kw):
        return self._track(self.cv.create_rectangle(self._wx(minx), self._wy(maxy), self._wx(maxx),
                                                    self._wy(miny), **self._tags(kw)))

    def _oval(self, x, y, r, **kw):
        return self._track(self.cv.create_oval(self._wx(x - r), self._wy(y + r), self._wx(x + r),
                                               self._wy(y - r), **self._tags(kw)))

    def _text(self, x, y, text, colour, size=9, **kw):
        return self._track(self.cv.create_text(self._wx(x), self._wy(y), text=text, fill=colour,
                                               font=ui.font(max(7, int(size * self.zoom / 1.5)), True),
                                               **self._tags(kw)))

    @staticmethod
    def _rot(points, cx, cy, deg):
        a = -math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in points]

    @staticmethod
    def _box(o):
        return o.corners or [(o.minx, o.miny), (o.maxx, o.miny), (o.maxx, o.maxy), (o.minx, o.maxy)]

    # ------------------------------------------------------------------ objects
    def _draw_object(self, o):
        k = o.kind
        if k in (O.SOLID, O.BREAKABLE):
            dash = (3, 3) if k == O.BREAKABLE else None
            width = max(1, int(round(self.zoom)))
            if o.corners:
                self._poly(o.corners, fill=self.block_fill, outline=self.block_line, width=width,
                           tags=("obj%d" % o.uid,))
            else:
                self._rect(o.minx, o.maxx, o.miny, o.maxy, fill=self.block_fill, outline=self.block_line,
                           width=width, dash=dash, tags=("obj%d" % o.uid,))
                inset = 4.0
                if o.maxx - o.minx > 3 * inset and o.maxy - o.miny > 3 * inset and not dash:
                    self._rect(o.minx + inset, o.maxx - inset, o.miny + inset, o.maxy - inset, fill="",
                               outline=self.block_inner, tags=("obj%d" % o.uid, "art"))
        elif k == O.HAZARD:
            art = SPIKE_ART.get(o.id)
            if art:
                aw, ah = art
                base = o.y - (15.0 if ah > 20 else ah / 2.0)
                pts = [(o.x - aw / 2, base), (o.x + aw / 2, base), (o.x, base + ah)]
                self._poly(self._rot(pts, o.x, o.y, o.rot), fill=self.hazard_fill, outline=self.hazard_line,
                           width=max(1, int(round(self.zoom))), tags="art")
                inner = [(o.x - aw * 0.22, base + ah * 0.16), (o.x + aw * 0.22, base + ah * 0.16),
                         (o.x, base + ah * 0.6)]
                self._poly(self._rot(inner, o.x, o.y, o.rot), fill=self.hazard_inner, outline="", tags="art")
            self._poly(self._box(o), fill="" if art else self.hazard_fill,
                       outline=HIT_HAZARD if art else self.hazard_line, width=1, tags="hit" if art else "")
        elif k == O.SAW:
            self._oval(o.x, o.y, o.radius * 1.5, fill=COL_HAZARD_FILL, outline=COL_HAZARD, tags="art")
            self._oval(o.x, o.y, o.radius * 0.5, fill=BG, outline=COL_HAZARD, tags="art")
            self._oval(o.x, o.y, o.radius, fill="", outline=HIT_HAZARD, width=1, tags="hit")
        elif k in (O.SLOPE, O.SLOPE_HAZARD):
            a, b, c, d = (o.minx, o.miny), (o.maxx, o.miny), (o.maxx, o.maxy), (o.minx, o.maxy)
            tri = {0: (a, b, c), 1: (a, b, d), 2: (a, c, d), 3: (b, c, d)}[o.orient]
            hazard = k == O.SLOPE_HAZARD
            self._poly(tri, fill=self.hazard_fill if hazard else self.block_fill,
                       outline=self.hazard_line if hazard else self.block_line, width=max(1, int(round(self.zoom))))
        elif k == O.ORB:
            col = ORB_COLORS.get(o.arg, "#ffffff")
            self._oval(o.x, o.y, 17, fill="", outline=col, width=2, tags="art")
            self._oval(o.x, o.y, 10, fill=col, outline="", tags="art")
            self._poly(self._box(o), fill="", outline=HIT_SPECIAL, dash=(2, 3), tags="hit")
            if o.arg in ("dash", "gravity_dash"):
                tip = self._rot([(o.x + 27, o.y)], o.x, o.y, o.rot + (180 if o.flip_x else 0))[0]
                self._track(self.cv.create_line(self._wx(o.x), self._wy(o.y), self._wx(tip[0]), self._wy(tip[1]),
                                                **self._tags(dict(fill=col, width=2, arrow="last", tags="art"))))
        elif k == O.PAD:
            col = PAD_COLORS.get(o.arg, "#ffffff")
            # the pad itself is only 4-6 units tall: draw a chunkier base so it is easy to see
            y0 = o.y - o.h / 2
            if o.upside:
                base = [(o.x - 14, o.y + o.h / 2), (o.x + 14, o.y + o.h / 2), (o.x + 10, o.y + o.h / 2 - 9),
                        (o.x - 10, o.y + o.h / 2 - 9)]
            else:
                base = [(o.x - 14, y0), (o.x + 14, y0), (o.x + 10, y0 + 9), (o.x - 10, y0 + 9)]
            self._poly(base, fill=col, outline="", tags="art")
            self._poly(self._box(o), fill="", outline=HIT_SPECIAL, tags="hit")
        else:
            self._draw_portal(o)

    # ------------------------------------------------------------------ portals
    portal_bg = BG                       # what portal glows fade into (the game view sets its theme)

    def _ellipse(self, o, rx, ry, dx=0.0, steps=28):
        """Points of an ellipse around the portal's centre, turned with the portal."""
        pts = [(o.x + dx + rx * math.cos(2 * math.pi * i / steps), o.y + ry * math.sin(2 * math.pi * i / steps))
               for i in range(steps)]
        return self._rot(pts, o.x, o.y, o.rot) if o.rot else pts

    def _portal_colour(self, o):
        k = o.kind
        if k == O.PORTAL_MODE:
            return icons.MODE_COLORS[o.arg]
        if k == O.PORTAL_GRAVITY:
            return {"normal": "#4DC3FF", "flipped": "#FFE14D"}.get(o.arg, "#59E36B")
        if k == O.PORTAL_SIZE:
            return "#FF7AD9" if o.arg else "#59E36B"
        if k == O.PORTAL_DUAL:
            return "#FF9F1C" if o.arg else "#3D8BFF"
        if k == O.PORTAL_TELEPORT:
            return "#3D9BFF"
        return SPEED_COLORS.get(o.arg, "#ffffff")

    def _draw_portal(self, o):
        """GD-style portals: a glowing oval ring with a darker frame behind it and what the
        portal does drawn inside it.  Speed portals are chevrons, one per speed step.
        Tk has no transparency, so a glow is a few rings blended towards the background."""
        k, col, z = o.kind, self._portal_colour(o), self.zoom
        dark = blend(col, "#05070C", 0.8)
        light = blend(col, "#FFFFFF", 0.5)
        line = max(1, int(round(2.4 * z)))
        hw, hh = o.w / 2.0, o.h / 2.0
        if o.rot and o.rot % 180 == 90:
            hw, hh = hh, hw                                 # the hitbox was swapped; the art is not
        if k == O.PORTAL_SPEED:
            self._draw_speed_portal(o, col, dark, light, line, hh)
        else:
            rx, ry = hw * 0.62 + 4, hh + 2
            for i, t in ((3, 0.86), (2, 0.74), (1, 0.6)):                       # glow
                self._poly(self._ellipse(o, rx + 3 * i, ry + 4 * i), fill=blend(col, self.portal_bg, t), outline="",
                           tags="art")
            self._poly(self._ellipse(o, rx * 0.95, ry * 0.98, dx=rx * 0.42), fill=blend(col, "#000000", 0.55),
                       outline=blend(col, "#000000", 0.3), width=1, tags="art")    # the frame behind the ring
            self._poly(self._ellipse(o, rx, ry), fill=dark, outline=col, width=line)
            self._poly(self._ellipse(o, rx - 4, ry - 6), fill="", outline=light, width=1, tags="art")
            cx, cy = self._wx(o.x), self._wy(o.y)
            if k == O.PORTAL_MODE:
                for item in icons.draw_icon(self.cv, o.arg, cx, cy, 9.5 * z, tags=self._extra):
                    self._track(item)
                self._text(o.x, o.maxy + 11, icons.MODE_NAMES[o.arg].upper(), col, tags="art")
            elif k == O.PORTAL_DUAL:                    # two icons, one upside down - or just one
                pairs = ((cy - 13 * z, False), (cy + 13 * z, True)) if o.arg else ((cy, False),)
                for iy, flipped in pairs:
                    for item in icons.draw_icon(self.cv, 0, cx, iy, 7.5 * z, flipped=flipped,
                                                colour=light if flipped else col, tags=self._extra):
                        self._track(item)
                self._text(o.x, o.maxy + 11, "DUAL" if o.arg else "DUAL OFF", col, tags="art")
            elif k == O.PORTAL_GRAVITY:
                up = {"normal": -1, "flipped": 1}.get(o.arg, 0)
                arrows = ((o.y - 9 * up, up),) if up else ((o.y + 11, 1), (o.y - 11, -1))
                for base, d in arrows:
                    pts = [(o.x - 8, base - 6 * d), (o.x + 8, base - 6 * d), (o.x, base + 10 * d)]
                    self._poly(self._rot(pts, o.x, o.y, o.rot) if o.rot else pts, fill=light, outline=col, width=1)
                self._text(o.x, o.maxy + 11, "GRAVITY", col, tags="art")
            elif k == O.PORTAL_TELEPORT:
                # the exit, `arg` units above: an orange ring of the same size
                exit_y = o.y + o.arg
                pts = [(o.x + rx * math.cos(2 * math.pi * i / 28), exit_y + ry * math.sin(2 * math.pi * i / 28))
                       for i in range(28)]
                self._poly(pts, fill=blend("#FF9F1C", self.portal_bg, 0.7), outline="#FF9F1C", width=line, tags="art")
                self._text(o.x, o.maxy + 11, "TELEPORT", col, tags="art")
            else:
                half = 4 if o.arg else 9
                self._rect(o.x - half, o.x + half, o.y - half, o.y + half, fill=light, outline=col, width=1)
                self._text(o.x, o.maxy + 11, "MINI" if o.arg else "NORMAL", col, tags="art")
        self._poly(self._box(o), fill="", outline=HIT_SPECIAL, width=1, tags="hit")

    def _draw_speed_portal(self, o, col, dark, light, line, hh):
        count = {0: 1, 1: 1, 2: 2, 3: 3, 4: 4}.get(o.arg, 1)
        back = o.arg == 0                                   # the slow portal points backwards
        size = hh * 0.9
        step, thick = size * 0.55, size * 0.38
        width = step * (count - 1) + size * 0.75
        for i, t in ((2, 0.84), (1, 0.68)):                 # glow
            self._poly(self._ellipse(o, width / 2 + 6 * i + 4, size + 4 * i), fill=blend(col, self.portal_bg, t),
                       outline="", tags="art")
        x0 = o.x - width / 2.0
        for n in range(count):
            left = x0 + n * step
            tip = left + size * 0.75
            pts = [(left, o.y + size), (left + thick, o.y + size), (tip, o.y), (left + thick, o.y - size),
                   (left, o.y - size), (tip - thick, o.y)]
            if back:
                pts = [(2 * o.x - x, y) for x, y in pts]
            self._poly(self._rot(pts, o.x, o.y, o.rot) if o.rot else pts, fill=col, outline=light, width=1)
        self._text(o.x, o.maxy + 11, C.SPEED_NAMES[o.arg], col, tags="art")


class GameView(Painter):
    def __init__(self, parent, sim, inputs=None, record_path=None, zoom=1.5, show_hitboxes=False,
                 debug=False, practice=False, progress=None, key=None, on_exit=None, theme=None):
        self.sim = sim
        self.replay = inputs
        self.record_path = record_path
        self.zoom = zoom
        self.show_hit = show_hitboxes
        self.show_debug = debug
        self.show_fps = settings.get("show_fps")      # remembered between levels and sessions
        self.frames = FrameStats()
        self.slack = 0.0                              # seconds the frame timer is set short by (see _frame)
        self.fps_next = 0.0                           # when the FPS text is next refreshed
        self.show_trail = False
        self.practice = practice and inputs is None
        self.progress = progress if inputs is None else None      # a bot's run never counts
        self.key = key
        self.on_exit = on_exit
        self.bg, self.ground = level_backdrop(sim.level, THEMES.get(theme, BG))
        self.portal_bg = self.bg
        self.block_fill = blend(self.bg, "#000000", 0.55)
        self.block_line = blend(COL_SOLID, "#FFFFFF", 0.35)
        self.block_inner = blend(self.block_fill, self.block_line, 0.28)
        self.paused = False
        self.panel = None                     # None, "pause" or "complete"
        self.rate = 1.0
        self.key_down = False
        self.recorded = []
        self.trail = []
        self.acc = 0.0
        self.dead_since = None
        self.cam_y = 0.0
        self.attempt = 1
        self.jumps = 0
        self.jumps_saved = 0
        self.unsaved_ticks = 0
        self.message = ""
        self.message_until = 0.0
        self.hint_until = 0.0
        self.checkpoints = []                 # practice: [snapshot, x, y, quick deaths, inputs so far]
        self.last_checkpoint_tick = 0
        self.sprite = None
        self.sprite2 = None                   # the second icon in dual mode
        self.running = False
        self._counted = False
        self._pending_release = None
        self._states = inputs.iter_states() if inputs is not None else None
        self.w, self.h = 1120, 640
        self.last = time.perf_counter()

        self.frame = tk.Frame(parent, bg=self.bg)
        self.cv = tk.Canvas(self.frame, bg=self.bg, highlightthickness=0, takefocus=1)
        self.cv.pack(fill="both", expand=True)
        self._build_static()
        self.fx = Effects(self.cv, self.zoom, self.bg)        # trails and fire sit above the level, below the player
        self._build_dynamic()
        self._apply_hitbox_visibility()
        self.frame_dt = 0.0

        cv = self.cv
        cv.bind("<Configure>", self._resized)
        cv.bind("<KeyPress>", self._key_press)
        cv.bind("<KeyRelease>", self._key_release)
        cv.bind("<ButtonPress-1>", self._mouse_press)
        cv.bind("<ButtonRelease-1>", self._release)

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        self.frame.pack(fill="both", expand=True)
        self.cv.focus_set()
        self.running = True
        self.last = time.perf_counter()
        self.hint_until = self.last + 4.0
        self.cv.after(10, self._frame)

    def exit(self):
        """Leave the level: save, tear down, hand control back."""
        if not self.running:
            return
        self.running = False
        self.save_recording()
        self._save_progress()
        callback = self.on_exit
        self.frame.destroy()
        if callback:
            callback()

    # ------------------------------------------------------------------ scene
    def _build_static(self):
        lv, cv = self.sim.level, self.cv
        left, right = -1500.0, lv.end_x + 2500.0
        # the sky gets a little lighter towards the ground, the way GD's backgrounds read
        top = max(1500.0, lv.max_y)
        bands = 7
        for i in range(bands):
            y0 = 60.0 + (top - 60.0) * i / bands
            self._rect(left, right, 0.0 if i == 0 else y0, top + 600.0 if i == bands - 1 else 60.0 + (top - 60.0) * (i + 1) / bands,
                       fill=blend(self.bg, "#FFFFFF", 0.035 * (bands - i) / bands), outline="")
        self._rect(left, right, -900.0, 0.0, fill=self.ground, outline="")
        self._rect(left, right, -60.0, 0.0, fill=blend(self.ground, "#FFFFFF", 0.05), outline="")
        line = blend(self.ground, "#FFFFFF", 0.9)
        cv.create_line(self._wx(left), self._wy(0), self._wx(right), self._wy(0), fill=blend(line, self.bg, 0.6),
                       width=max(3, int(4 * self.zoom)))
        cv.create_line(self._wx(left), self._wy(0), self._wx(right), self._wy(0), fill=line, width=2)
        cv.create_line(self._wx(lv.end_x), self._wy(0), self._wx(lv.end_x), self._wy(top),
                       fill=ui.GREEN, width=2, dash=(8, 6))
        # level objects are drawn near the camera only (_cull) and always stay below this
        self.it_layer = cv.create_line(0, 0, 0, 0, state="hidden")
        tr = lv.triggers
        self.chunks = _chunks(tr.static if tr is not None else lv.objects)
        self.drawn = set()                            # chunk indices on the canvas
        self.cl_chunks = [_chunks(c.objects) for c in tr.clusters] if tr is not None else []
        self.cl_drawn = set()                         # (cluster, chunk) on the canvas
        self.cl_at = [(0.0, 0.0)] * len(self.cl_chunks)   # how far each cluster's items have been moved

    def _cull(self, x0, x1):
        """Draw the chunks of the level that are on (or near) the screen and drop the rest;
        move the groups that triggers have moved."""
        cv, z = self.cv, self.zoom
        made = []
        drawn = self.drawn
        for i, (a, b, objs) in enumerate(self.chunks):
            want = a <= x1 and b >= x0
            if want != (i in drawn):
                tag = "c%d" % i
                if want:
                    for o in objs:
                        self.draw_object(o, tag)
                    drawn.add(i)
                    made.append(tag)
                else:
                    cv.delete(tag)
                    drawn.discard(i)
        trig = self.sim.trig
        if trig is not None:
            for k, chunks in enumerate(self.cl_chunks):
                off = trig.off[k]
                dx, dy = trig.offsets[k]
                at = self.cl_at[k]
                if not off and (dx, dy) != at:
                    cv.move("k%d" % k, (dx - at[0]) * z, (at[1] - dy) * z)
                    self.cl_at[k] = at = (dx, dy)
                for j, (a, b, objs) in enumerate(chunks):
                    want = not off and a + dx <= x1 and b + dx >= x0
                    key = (k, j)
                    if want != (key in self.cl_drawn):
                        tag = "k%d_%d" % key
                        if want:
                            for o in objs:
                                self.draw_object(o, (tag, "k%d" % k))
                            if at != (0.0, 0.0):
                                cv.move(tag, at[0] * z, -at[1] * z)
                            self.cl_drawn.add(key)
                            made.append(tag)
                        else:
                            cv.delete(tag)
                            self.cl_drawn.discard(key)
        for tag in made:
            cv.tag_lower(tag, self.it_layer)
            if not self.show_hit:
                cv.itemconfigure(tag + "&&hit", state="hidden")

    def _build_dynamic(self):
        cv = self.cv
        self.it_floor = cv.create_rectangle(0, 0, 0, 0, fill="#080A11", outline="#DDE3F0", state="hidden")
        self.it_ceil = cv.create_rectangle(0, 0, 0, 0, fill="#080A11", outline="#DDE3F0", state="hidden")
        self.it_trail = cv.create_line(0, 0, 0, 0, fill="#FFFFFF", width=1, state="hidden")
        # the player's three boxes ("phit": drawn over the icon, like GD does)
        both = ("hit", "phit")
        self.it_rot = cv.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill="", outline=HIT_ROTATED, width=2, tags=both)
        self.it_outer = cv.create_rectangle(0, 0, 0, 0, outline=HIT_HAZARD, width=2, tags=both)
        self.it_inner = cv.create_rectangle(0, 0, 0, 0, outline=HIT_SOLID, width=2, tags=both)
        self.it_bar_bg = cv.create_rectangle(0, 0, 0, 0, fill="#05070C", outline=ui.LINE, tags="hud")
        self.it_bar = cv.create_rectangle(0, 0, 0, 0, fill=ui.GREEN, outline="", tags="hud")
        self.it_pct = cv.create_text(0, 0, anchor="w", fill=ui.TEXT, font=ui.font(13, True), tags="hud")
        self.it_title = cv.create_text(0, 0, anchor="nw", fill=ui.MUTED, font=ui.font(12), tags="hud")
        self.it_tag = cv.create_text(0, 0, anchor="ne", fill=ui.CYAN, font=ui.font(12, True), tags="hud")
        self.it_debug = cv.create_text(0, 0, anchor="nw", fill="#D5DBE8", font=(ui.MONO, 11), tags="hud")
        self.it_fps = cv.create_text(0, 0, anchor="ne", fill="#D5DBE8", font=(ui.MONO, 12, "bold"), tags="hud")
        self.it_msg = cv.create_text(0, 0, anchor="center", fill="#FFFFFF", font=ui.font(22, True), tags="hud")
        self.it_hint = cv.create_text(0, 0, anchor="s", fill=ui.MUTED, font=ui.font(11), tags="hud")

    def _apply_hitbox_visibility(self):
        self.cv.itemconfigure("hit", state="normal" if self.show_hit else "hidden")

    # ------------------------------------------------------------------ input
    def _key_press(self, e):
        k = e.keysym.lower()
        if k in ("space", "up", "w"):
            if self.panel == "pause":
                self.resume()
            elif self.panel is None:
                self._press()
            return
        if k in ("escape", "p"):
            if self.panel == "complete":
                self.exit()
            elif self.panel == "pause":
                self.resume()
            else:
                self.pause()
            return
        if k == "return" and self.panel == "complete":
            self.exit()
            return
        if k == "q" and self.panel:
            self.exit()
            return
        if k == "r":
            self.restart()
            return
        if k == "m" and self.panel == "pause":
            self.toggle_practice()
            return
        if k == "h":
            self._toggle("show_hit")
            self._apply_hitbox_visibility()
        elif k == "o":
            self._toggle("show_debug")
        elif k == "f":
            self.toggle_fps()
        elif k == "t":
            self._toggle("show_trail")
        elif k == "z":
            self.add_checkpoint()
        elif k == "x":
            self.remove_checkpoint()
        elif k == "n":
            self.paused = True
            self._advance()
        elif k == "minus":
            self.rate = max(1.0 / 16.0, self.rate / 2)
        elif k in ("equal", "plus"):
            self.rate = min(4.0, self.rate * 2)
        elif k == "s":
            self.save_recording()
        if self.panel == "pause":
            self._show_panel("pause")

    def _key_release(self, e):
        if e.keysym.lower() in ("space", "up", "w"):
            self._release()

    def _mouse_press(self, _e):
        self.cv.focus_set()
        if self.panel is None:
            self._press()

    def _press(self, _event=None):
        if self._pending_release is not None:      # X11 auto-repeat sends release+press pairs
            self.cv.after_cancel(self._pending_release)
            self._pending_release = None
        self.key_down = True

    def _release(self, _event=None):
        if sys.platform == "darwin" or sys.platform.startswith("win"):
            self.key_down = False
        else:
            self._pending_release = self.cv.after(4, self._really_release)

    def _really_release(self):
        self._pending_release = None
        self.key_down = False

    def _toggle(self, name):
        setattr(self, name, not getattr(self, name))

    def _resized(self, event):
        self.w, self.h = event.width, event.height
        if self.panel:
            self._show_panel(self.panel)

    # ------------------------------------------------------------------ pause / complete panels
    def pause(self):
        self.paused = True
        self.key_down = False
        self._show_panel("pause")

    def resume(self):
        self.paused = False
        self._hide_panel()
        self.last = time.perf_counter()
        self.frames.reset()                           # the paused stretch is not what the game runs like

    def _hide_panel(self):
        self.panel = None
        self.cv.delete("panel")
        self.cv.configure(cursor="")

    def _show_panel(self, kind):
        cv = self.cv
        cv.delete("panel")
        self.panel = kind
        left, top = cv.canvasx(0), cv.canvasy(0)
        pw, ph = 380, (446 if kind == "pause" else 330)
        x0, y0 = left + (self.w - pw) / 2.0, top + (self.h - ph) / 2.0
        ui.rounded(cv, x0 - 3, y0 - 3, x0 + pw + 3, y0 + ph + 3, 20, fill="#05070C", outline="", tags="panel")
        ui.rounded(cv, x0, y0, x0 + pw, y0 + ph, 18, fill=ui.PANEL, outline=ui.LINE, tags="panel")
        sim = self.sim

        def button(row, text, command, style="normal", key=None, enabled=True):
            ui.Button(cv, x0 + 30, y0 + 78 + row * 50, pw - 60, 42, text, command, style, 14, key,
                      enabled=enabled, tags=("panel",))

        def on(flag):
            return "on" if flag else "off"

        if kind == "pause":
            cv.create_text(x0 + pw / 2, y0 + 40, text="Paused", fill=ui.TEXT, font=ui.font(24, True), tags="panel")
            button(0, "Resume", self.resume, "primary", "Esc")
            button(1, "Restart", self.restart, key="R")
            if self._states is None:
                button(2, "Practice mode: %s" % on(self.practice), self.toggle_practice, key="M")
            else:
                button(2, "Watching the bot", lambda: None, enabled=False)
            button(3, "Hitboxes: %s" % on(self.show_hit), self._panel_toggle_hit, key="H")
            button(4, "Debug info: %s" % on(self.show_debug), lambda: self._panel_toggle("show_debug"), key="O")
            button(5, "FPS counter: %s" % on(self.show_fps), self.toggle_fps, key="F")
            button(6, "Exit level", self.exit, "danger", "Q")
        else:
            title = "Practice complete!" if self.practice else "Level complete!"
            lines = [("Time", "%.2f s" % sim.time), ("Attempts", str(self.attempt)), ("Jumps", str(self.jumps))]
            if self._states is not None:
                title = "The bot finished"
                lines = [("Time", "%.2f s" % sim.time), ("Inputs", str(len(self.replay)))]
            cv.create_text(x0 + pw / 2, y0 + 42, text=title, fill=ui.GREEN, font=ui.font(24, True), tags="panel")
            for i, (label, value) in enumerate(lines):
                y = y0 + 92 + i * 32
                cv.create_text(x0 + 60, y, text=label, anchor="w", fill=ui.MUTED, font=ui.font(15), tags="panel")
                cv.create_text(x0 + pw - 60, y, text=value, anchor="e", fill=ui.TEXT, font=ui.font(15, True), tags="panel")
            ui.Button(cv, x0 + 30, y0 + ph - 122, pw - 60, 46, "Play again", self.restart, "normal", 15, "R",
                      tags=("panel",))
            ui.Button(cv, x0 + 30, y0 + ph - 66, pw - 60, 46, "Continue", self.exit, "primary", 15, "Enter",
                      tags=("panel",))
        cv.tag_raise("panel")

    def toggle_fps(self):
        self.show_fps = not self.show_fps
        settings.put("show_fps", self.show_fps)
        self.fps_next = 0.0
        if self.panel == "pause":
            self._show_panel("pause")

    def _panel_toggle(self, name):
        self._toggle(name)
        self._show_panel("pause")

    def _panel_toggle_hit(self):
        self._toggle("show_hit")
        self._apply_hitbox_visibility()
        self._show_panel("pause")

    # ------------------------------------------------------------------ practice mode
    def toggle_practice(self):
        if self._states is not None:
            return
        self.practice = not self.practice
        self.restart()

    def add_checkpoint(self, auto=False):
        sim, p = self.sim, self.sim.player
        if not self.practice or sim.dead or sim.finished or sim.tick == 0:
            return
        self.checkpoints.append([sim.snapshot(), p.x, p.y, 0, len(self.recorded)])
        self.last_checkpoint_tick = sim.tick
        cx, cy = self._wx(p.x), self._wy(p.y)
        self.cv.create_polygon(cx, cy - 9, cx + 7, cy, cx, cy + 9, cx - 7, cy, fill=ui.GREEN, outline="#06220D",
                               tags=("cp", "cp%d" % len(self.checkpoints)))
        if not auto:
            self._flash("Checkpoint")

    def remove_checkpoint(self):
        if self.practice and self.checkpoints:
            self.cv.delete("cp%d" % len(self.checkpoints))
            self.checkpoints.pop()
            self._flash("Checkpoint removed")

    def _respawn(self):
        """Practice: back to the last checkpoint (or the start if there is none)."""
        sim = self.sim
        died_at = sim.tick
        while self.checkpoints:
            cp = self.checkpoints[-1]
            if died_at - cp[0][0] < 100:            # died within 0.4 s of it...
                cp[3] += 1
                if cp[3] >= 2:                      # ...twice: it is a bad checkpoint, drop it
                    self.cv.delete("cp%d" % len(self.checkpoints))
                    self.checkpoints.pop()
                    continue
            sim.restore(cp[0])
            del self.recorded[cp[4]:]
            self.last_checkpoint_tick = sim.tick
            break
        else:
            sim.reset()
            self.recorded = []
            self.last_checkpoint_tick = 0
        self.attempt += 1
        self._counted = False
        self.trail = []
        self.fx.reset()
        self.dead_since = None
        self.acc = 0.0
        self.key_down = False

    # ------------------------------------------------------------------ simulation driving
    def restart(self):
        if self.sim.tick > 0:
            self.attempt += 1
        self._save_progress()
        self.sim.reset()
        self.fx.reset()
        self.recorded = []
        self.trail = []
        self.checkpoints = []
        self.cv.delete("cp")
        self.last_checkpoint_tick = 0
        self.dead_since = None
        self.acc = 0.0
        self.paused = False
        self._counted = False
        self._hide_panel()
        self.last = time.perf_counter()
        if self.replay is not None:
            self._states = self.replay.iter_states()

    def _advance(self):
        sim = self.sim
        if sim.dead or sim.finished:
            return
        if not self._counted:                         # an attempt starts with its first step
            self._counted = True
            if self.progress and self.key:
                self.progress.attempt(self.key)
        pressed = next(self._states) if self._states is not None else self.key_down
        clicked = pressed and not sim.button
        if pressed != sim.button:
            self.recorded.append((sim.tick, pressed))
            if pressed:
                self.jumps += 1
        sim.step(pressed)
        self.fx.tick(sim, clicked)
        self.unsaved_ticks += 1
        p = sim.player
        if self.show_trail and sim.tick % 2 == 0:
            self.trail.append((p.x, p.y))
            if len(self.trail) > 500:
                del self.trail[:100]
        if self.practice and sim.tick - self.last_checkpoint_tick >= AUTO_CHECKPOINT_TICKS and not sim.dead \
                and (p.can_jump or p.mode in FLYING):
            self.add_checkpoint(auto=True)
        if sim.dead:
            self._on_death()
        elif sim.finished:
            self._on_complete()

    def _percent(self):
        return max(0.0, min(100.0, 100.0 * self.sim.player.x / self.sim.level.end_x))

    def _on_death(self):
        if self.progress and self.key:
            if self.progress.reached(self.key, self._percent(), self.practice) and not self.practice:
                self._flash("New best  %d%%" % int(self._percent()), 1.2)
            self._save_progress()

    def _on_complete(self):
        if self.progress and self.key:
            self.progress.complete(self.key, self.sim.time, self.practice)
            self._save_progress()
        self.save_recording()
        self._show_panel("complete")

    def _save_progress(self):
        if not (self.progress and self.key):
            return
        if self.unsaved_ticks or self.jumps != self.jumps_saved:
            self.progress.add_play(self.key, self.unsaved_ticks / float(C.TPS), self.jumps - self.jumps_saved)
            self.unsaved_ticks = 0
            self.jumps_saved = self.jumps
        self.progress.save()

    def _flash(self, text, seconds=0.8):
        self.message = text
        self.message_until = time.perf_counter() + seconds

    def save_recording(self):
        if not self.record_path or not self.recorded:
            return
        seq = InputSequence(self.recorded, {"level": self.sim.level.name})
        if self.record_path.lower().endswith(".gdr2"):
            from .macros import write_gdr2
            write_gdr2(self.record_path, seq, self.sim.level.name)
        else:
            seq.save(self.record_path)
        print("saved %d input events to %s" % (len(seq), self.record_path))

    # ------------------------------------------------------------------ main loop
    def _frame(self):
        if not self.running:
            return
        now = time.perf_counter()
        dt, self.last = now - self.last, now
        self.frames.add(now, dt)                      # measured whether or not the counter is shown
        self.frame_dt = 0.0 if (self.paused or self.panel) else min(dt, 0.1) * self.rate
        sim = self.sim
        if not self.paused and self.panel is None:
            self.acc += min(dt, 0.1) * self.rate
            n = 0
            while self.acc >= STEP and n < 40:
                self._advance()
                self.acc -= STEP
                n += 1
            if n == 40:
                self.acc = 0.0
        if sim.dead and self.panel is None:
            if self.dead_since is None:
                self.dead_since = now
            elif now - self.dead_since > (0.45 if self.practice else 0.8):
                if self._states is not None:
                    pass                               # a failed replay just stays where it died
                elif self.practice:
                    self._respawn()
                else:
                    self.save_recording()
                    self.restart()
        self._draw(now)
        self.cv.update_idletasks()                    # paint this frame now, before the next one starts
        # Tk's timers run late by a varying amount (on macOS after(n) waits about 1.3 n ms), so
        # the wait is trimmed by however much the frames have been running over
        period = 1.0 / FPS_CAP
        self.slack = min(period, max(0.0, self.slack + (0.0001 if dt > period else -0.0001)))
        wait = (now + period - self.slack - time.perf_counter()) * 1000.0
        self.cv.after(max(1, int(round(wait))), self._frame)

    def _draw_second(self, q, z):
        """Dual mode: the second icon (and its two boxes when hitboxes are shown)."""
        cv = self.cv
        if q is None:
            if self.sprite2 is not None:
                self.sprite2.destroy()
                self.sprite2 = None
                cv.delete("p2hit")
            return
        mode = int(q.mode)
        if self.sprite2 is None or self.sprite2.mode != mode:
            if self.sprite2 is not None:
                self.sprite2.destroy()
            self.sprite2 = icons.IconSprite(cv, mode)
        self.sprite2.place(self._wx(q.x), self._wy(q.y), 15.0 * (C.MINI_SCALE if q.mini else 1.0) * z,
                           q.rotation, q.upside_down)
        self.sprite2.set_colour("#FF4D4D" if q.dead else blend(icons.MODE_COLORS[mode], "#FFFFFF", 0.35))
        cv.delete("p2hit")
        if self.show_hit:
            for rect, colour in ((q.outer_rect(), HIT_HAZARD), (q.inner_rect(), HIT_SOLID)):
                minx, maxx, miny, maxy = rect
                cv.create_rectangle(self._wx(minx), self._wy(maxy), self._wx(maxx), self._wy(miny), outline=colour,
                                    width=2, tags=("hit", "phit", "p2hit"))

    def _draw(self, now):
        sim, p, cv, z = self.sim, self.sim.player, self.cv, self.zoom
        w, h = self.w, self.h
        # camera: player a third of the way in; vertically centred on the play area
        if sim.ceiling is not None:
            target = (sim.floor + sim.ceiling) / 2.0
        else:
            target = max(p.y, 0.32 * h / z - 60.0)
        self.cam_y += (target - self.cam_y) * 0.12
        left = self._wx(p.x) - w * 0.33
        top = self._wy(self.cam_y) - h * 0.5
        if self.panel is None:
            cv.configure(scrollregion=(left, top, left + w, top + h))
            cv.xview_moveto(0)
            cv.yview_moveto(0)
        else:
            left, top = cv.canvasx(0), cv.canvasy(0)
        self._cull(left / z - VIEW_MARGIN, (left + w) / z + VIEW_MARGIN)

        if sim.ceiling is not None:
            x0, x1 = p.x - 2000, p.x + 3000
            cv.coords(self.it_floor, self._wx(x0), self._wy(sim.floor), self._wx(x1), self._wy(sim.floor - 900))
            cv.coords(self.it_ceil, self._wx(x0), self._wy(sim.ceiling + 900), self._wx(x1), self._wy(sim.ceiling))
            cv.itemconfigure(self.it_floor, state="normal")
            cv.itemconfigure(self.it_ceil, state="normal")
        else:
            cv.itemconfigure(self.it_floor, state="hidden")
            cv.itemconfigure(self.it_ceil, state="hidden")
        for uid in sim.destroyed:
            cv.itemconfigure("obj%d" % uid, state="hidden")

        self.fx.draw(self.frame_dt, now, left / z)

        # --- player
        mode = int(p.mode)
        if self.sprite is None or self.sprite.mode != mode:
            if self.sprite is not None:
                self.sprite.destroy()
            self.sprite = icons.IconSprite(cv, mode)
        visual = 15.0 * (C.MINI_SCALE if p.mini else 1.0) * z
        self.sprite.place(self._wx(p.x), self._wy(p.y), visual, p.rotation, p.upside_down)
        self.sprite.set_colour("#FF4D4D" if p.dead else icons.MODE_COLORS[mode])
        if self.show_hit:
            minx, maxx, miny, maxy = p.outer_rect()
            cv.coords(self.it_outer, self._wx(minx), self._wy(maxy), self._wx(maxx), self._wy(miny))
            minx, maxx, miny, maxy = p.inner_rect()
            cv.coords(self.it_inner, self._wx(minx), self._wy(maxy), self._wx(maxx), self._wy(miny))
            flat = []
            for x, y in p.oriented_box():
                flat += [self._wx(x), self._wy(y)]
            cv.coords(self.it_rot, *flat)
        self._draw_second(sim.player2, z)

        if self.show_trail and len(self.trail) >= 2:
            flat = []
            for x, y in self.trail:
                flat += [self._wx(x), self._wy(y)]
            cv.coords(self.it_trail, *flat)
            cv.itemconfigure(self.it_trail, state="normal")
        else:
            cv.itemconfigure(self.it_trail, state="hidden")

        # --- HUD
        done = self._percent() / 100.0
        bar_w = min(460.0, w * 0.42)
        bx = left + (w - bar_w) / 2.0
        cv.coords(self.it_bar_bg, bx, top + 14, bx + bar_w, top + 26)
        cv.coords(self.it_bar, bx + 2, top + 16, bx + 2 + (bar_w - 4) * done, top + 24)
        cv.itemconfigure(self.it_bar, fill=ui.CYAN if self.practice else ui.GREEN)
        cv.coords(self.it_pct, bx + bar_w + 10, top + 20)
        cv.itemconfigure(self.it_pct, text="%d%%" % int(done * 100))
        cv.coords(self.it_title, left + 16, top + 12)
        cv.itemconfigure(self.it_title, text="%s\nAttempt %d" % (sim.level.name, self.attempt))
        cv.coords(self.it_tag, left + w - 16, top + 12)
        tag = "WATCHING THE BOT" if self._states is not None else ("PRACTICE" if self.practice else "")
        if self.rate != 1.0:
            tag += "   x%.3g" % self.rate
        cv.itemconfigure(self.it_tag, text=tag)
        cv.coords(self.it_fps, left + w - 16, top + (34 if tag else 12))
        if not self.show_fps:
            cv.itemconfigure(self.it_fps, text="")
        elif now >= self.fps_next:                    # four times a second: readable, not flickering
            self.fps_next = now + 0.25
            cv.itemconfigure(self.it_fps, text=self.frames.text(now))

        if self.show_debug:
            vy = p.y_vel * C.VEL_TO_UNITS_PER_SEC
            if p.mode == WAVE:
                vy = (p.y - p.last_y) * C.TPS
            flags = [name for name, on in (("ground", p.can_jump), ("rising", p.is_jumping),
                                           ("slope", p.is_on_slope), ("dash", p.is_dashing),
                                           ("uncapped", p.velocity_uncapped), ("HOLD", sim.button)) if on]
            lines = [
                "tick %-6d t %7.3fs" % (sim.tick, sim.time),
                "pos  x %9.3f  y %8.3f   (block %.2f, %.2f)" % (p.x, p.y, p.x / 30.0, p.y / 30.0),
                "vel  x %8.2f  y %+8.2f u/s   (native y %+.3f)" % (p.x_vel * 60.0, vy, p.y_vel),
                "mode %-6s speed %-4s gravity %-7s size %s" % (p.mode.name, C.SPEED_NAMES[p.speed],
                                                               "FLIPPED" if p.upside_down else "normal",
                                                               "mini" if p.mini else "normal"),
                "state " + (" ".join(flags) or "-"),
            ]
            if sim.ceiling is not None:
                lines.append("bounds floor %.0f  ceiling %.0f" % (sim.floor, sim.ceiling))
            if sim.log:
                lines.append("last  %s @ tick %d" % (sim.log[-1][1], sim.log[-1][0]))
            if self.show_hit:
                lines.append("boxes blue: blocks   red: spikes, orbs, pads, portals")
                lines.append("      dark red: turned %+.0f deg (angled objects only)" % (p.rotation % 360.0))
            cv.itemconfigure(self.it_debug, text="\n".join(lines))
        else:
            cv.itemconfigure(self.it_debug, text="")
        cv.coords(self.it_debug, left + 16, top + 54)

        msg = ""
        if now < self.message_until:
            msg = self.message
        elif p.dead and self._states is not None:
            msg = "The replay died here: %s" % p.death_cause
        cv.coords(self.it_msg, left + w / 2, top + h * 0.24)
        cv.itemconfigure(self.it_msg, text=msg)
        hint = ""
        if now < self.hint_until and self.panel is None:
            hint = "Space or click to jump      Esc: pause and options"
            if self.practice:
                hint += "      Z / X: add / remove checkpoint"
        cv.coords(self.it_hint, left + w / 2, top + h - 14)
        cv.itemconfigure(self.it_hint, text=hint)
        cv.tag_raise("cp")
        cv.tag_raise("fx")
        cv.tag_raise("player")
        cv.tag_raise("phit")
        cv.tag_raise("hit")
        cv.tag_raise("hud")
        cv.tag_raise("panel")


class Viewer:
    """A window of its own around one GameView (used when a level is opened directly)."""

    def __init__(self, sim, inputs=None, record_path=None, zoom=1.5, show_hitboxes=False, **kw):
        self.root = tk.Tk()
        self.root.title("GD physics - %s" % sim.level.name)
        self.root.geometry("1180x680")
        self.root.configure(bg=BG)
        self.view = GameView(self.root, sim, inputs, record_path, zoom, show_hitboxes,
                             on_exit=self.root.destroy, **kw)
        self.root.protocol("WM_DELETE_WINDOW", self.view.exit)

    def run(self):
        self.view.start()
        self.root.lift()
        self.root.focus_force()
        self.view.cv.focus_set()
        self.root.mainloop()
