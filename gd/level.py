"""Turns a decoded level string into physics objects.

Object keys used (Wyliemaster/gddocs "level string"):
  1 id, 2 x, 3 y, 4 flip-x, 5 flip-y, 6 rotation (clockwise degrees),
  32 scale, 128 scale-x, 129 scale-y, 57 group ids ("1.4.9").
  From memory of the 2.2 key list, not re-verified: 99 multi-activate,
  121 "No Touch" (object has no collision), 134 "Passable" (solid from above only).
Header keys (gddocs "level start"):
  kA2 game mode, kA3 mini, kA4 speed, kA8 dual, kA11 flipped gravity,
  kA22 platformer, kA32 fix gravity bug, kA34 fix robot jump, kA39 fix radius collision.
  Colours (only drawn, never simulated): kS38 the colour channels ("1_r_2_g_3_b_..._6_id|...",
  gddocs "color string"; channel 1000 is the background, 1001 the ground), or before 2.0
  kS29 / kS30 (one such entry each), or before 1.9 kS1-kS3 / kS4-kS6 (red, green, blue).
"""
from collections import Counter

from . import objects as O
from . import triggers as T
from .constants import LEVEL_END_PADDING, MAX_Y, SECTION_SIZE
from .geometry import obb_corners
from .levelio import RawLevel, split_level_string

# kA4 is not in speed order: the game stores 1x as 0 and 0.5x as 1 (gd-sim Level.cpp)
_SPEED_FROM_HEADER = {0: 1, 1: 0, 2: 2, 3: 3, 4: 4}


def _num(d, key, default=0.0):
    try:
        return float(d.get(key, default))
    except (TypeError, ValueError):
        return default


class GameObject:
    __slots__ = ("uid", "id", "kind", "arg", "x", "y", "rot", "flip_x", "flip_y", "w", "h",
                 "minx", "maxx", "miny", "maxy", "corners", "radius", "groups", "multi",
                 "orient", "upside", "passable", "note", "stamp")

    def __repr__(self):
        return "<obj #%d id=%d %s at (%.1f, %.1f)>" % (self.uid, self.id, self.kind, self.x, self.y)

    # ---- slope helpers (orient: 0 = floor rising to the right, 1 = floor falling,
    #      2 = ceiling whose surface rises to the right, 3 = ceiling whose surface falls)
    @property
    def slope_is_ceiling(self):
        return self.orient >= 2

    @property
    def slope_rises(self):
        return self.orient in (0, 2)


class ImportReport:
    def __init__(self):
        self.total = 0
        self.by_kind = Counter()
        self.skipped = Counter()          # id -> count, no physics mapping
        self.ignored = Counter()          # reason -> count, mapped but not simulated
        self.assumed = Counter()          # note -> count, simulated with an assumed hitbox
        self.warnings = []

    def summary(self, max_ids=25):
        lines = ["objects in level: %d" % self.total]
        used = sum(self.by_kind.values())
        lines.append("  simulated: %d  (%s)" % (
            used, ", ".join("%s %d" % kv for kv in sorted(self.by_kind.items())) or "none"))
        for note, n in sorted(self.assumed.items()):
            lines.append("  note: %d object(s) use an %s" % (n, note))
        for reason, n in sorted(self.ignored.items()):
            lines.append("  ignored: %d x %s" % (n, reason))
        n_skip = sum(self.skipped.values())
        lines.append("  skipped (no physics mapping): %d objects, %d distinct IDs" % (n_skip, len(self.skipped)))
        if self.skipped:
            top = self.skipped.most_common(max_ids)
            parts = []
            for oid, n in top:
                name = O.UNSUPPORTED_NAMES.get(oid)
                parts.append("%d%s x%d" % (oid, " (%s)" % name if name else "", n))
            more = len(self.skipped) - len(top)
            lines.append("    " + ", ".join(parts) + (" ... +%d more IDs" % more if more > 0 else ""))
        for w in self.warnings:
            lines.append("  WARNING: " + w)
        return "\n".join(lines)


class Level:
    def __init__(self, level_string, name="Unnamed"):
        if isinstance(level_string, RawLevel):
            name, level_string = level_string.name, level_string.level_string
        self.name = name
        self.header, raw_objects = split_level_string(level_string)
        self.report = ImportReport()
        self.objects = []
        self.sections = {}             # section index -> list[GameObject] that never move
        self._stamp = 0
        self.triggers = None           # triggers.Setup, if the level has any (gd/triggers.py)
        self.colours = _colours(self.header)   # colour channel -> (r, g, b); 1000 background, 1001 ground

        h = self.header
        self.start_mode = int(_num(h, "kA2")) if 0 <= int(_num(h, "kA2")) <= 7 else 0
        self.start_mini = _num(h, "kA3") != 0
        self.start_speed = _SPEED_FROM_HEADER.get(int(_num(h, "kA4")), 1)
        self.start_flipped = _num(h, "kA11") != 0
        self.fix_gravity_bug = _num(h, "kA32") != 0
        self.fix_robot_jump = _num(h, "kA34") != 0
        self.fix_radius_collision = _num(h, "kA39") != 0
        if _num(h, "kA22") != 0:
            self.report.warnings.append("platformer level: only classic (auto-scrolling) physics are simulated")
        if _num(h, "kA8") != 0:
            self.report.warnings.append("level starts in dual mode: only player 1 is simulated")
        if _num(h, "kA20") != 0:
            self.report.warnings.append("level starts with reversed gameplay: not simulated")

        max_x, max_y = 0.0, 0.0
        triggers = []
        for raw in raw_objects:
            self.report.total += 1
            x, y = _num(raw, 2), _num(raw, 3)
            max_x, max_y = max(max_x, x), max(max_y, y)
            if _num(raw, 1) in T.TRIGGER_IDS:
                t = T.parse(raw, int(_num(raw, 1)), x, y, _groups(raw), _num)
                if t is not None:
                    t.uid = len(triggers)
                    triggers.append(t)
                    self.report.by_kind["%s trigger" % T.NAMES[t.id]] += 1
                else:
                    self.report.ignored["trigger with no target group"] += 1
                continue
            obj = self._build(raw, x, y)
            if obj is not None:
                obj.uid = len(self.objects)
                obj.stamp = -1
                self.objects.append(obj)
                self.report.by_kind[obj.kind] += 1
                if obj.note:
                    self.report.assumed[obj.note] += 1
        static = self.objects
        if triggers:
            wanted = {t.target for t in triggers if t.id == T.TELEPORT}
            anchors = {}
            if wanted:
                for raw in raw_objects:
                    groups = _groups(raw)
                    for g in groups:
                        if g in wanted and g not in anchors:
                            anchors[g] = (_num(raw, 2), _num(raw, 3), groups)
            self.triggers = T.Setup(triggers, self.objects, anchors)
            static = self.triggers.static
        for obj in static:
            for s in range(int(obj.minx // SECTION_SIZE), int(obj.maxx // SECTION_SIZE) + 1):
                self.sections.setdefault(s, []).append(obj)
        self.last_object_x = max_x
        self.end_x = max_x + LEVEL_END_PADDING
        # gd-sim kills above y = 1476.3.  2.2 levels can be taller ("dynamic level
        # height"), so the limit is raised above the highest object.  EST.
        self.max_y = max(MAX_Y, max_y + 300.0)

    # ------------------------------------------------------------------ building
    def _build(self, raw, x, y):
        try:
            oid = int(float(raw[1]))
        except (TypeError, ValueError):
            return None
        d = O.OBJECTS.get(oid)
        if d is None:
            self.report.skipped[oid] += 1
            return None
        if d.kind == O.NOOP:
            self.report.ignored[d.note] += 1
            return None
        if _num(raw, 121) != 0:
            self.report.ignored['objects flagged "No Touch" (decoration)'] += 1
            return None

        rot = _num(raw, 6) % 360.0
        if 128 in raw or 129 in raw:
            base = _num(raw, 32, 1.0)
            sx, sy = abs(_num(raw, 128, base)), abs(_num(raw, 129, base))
        else:
            sx = sy = abs(_num(raw, 32, 1.0))
        sx, sy = sx or 1.0, sy or 1.0

        o = GameObject()
        o.id, o.kind, o.arg, o.note = oid, d.kind, d.arg, d.note
        o.x, o.y, o.rot = x, y, rot
        o.flip_x, o.flip_y = _num(raw, 4) != 0, _num(raw, 5) != 0
        o.groups = _groups(raw)
        o.multi = _num(raw, 99) != 0
        o.passable = _num(raw, 134) != 0
        o.corners, o.radius, o.orient, o.upside = None, 0.0, 0, False
        w, h = d.w * sx, d.h * sy
        quarter = abs(rot / 90.0 - round(rot / 90.0)) < 1e-6
        turns = int(round(rot / 90.0)) % 4

        if d.kind == O.SAW:
            o.radius = d.arg * max(sx, sy)
            w = h = o.radius * 2.0
        elif d.kind in (O.SLOPE, O.SLOPE_HAZARD):
            if not quarter:
                self.report.ignored["slope at a non-90-degree rotation"] += 1
                return None
            # gd-sim Slope.cpp: flips shift the orientation by 1 (x), 3 (y) or 2 (both)
            shift = 2 if (o.flip_x and o.flip_y) else 1 if o.flip_x else 3 if o.flip_y else 0
            o.orient = (turns + shift) % 4
        elif d.kind == O.PORTAL_TELEPORT:
            o.arg = _num(raw, 54)              # where the exit portal is: this far above (gddocs key 54)
        elif d.kind in (O.PAD, O.ORB):
            # a pad points "down" when turned past 90 degrees or flipped vertically (gd-sim Pad.cpp)
            ang = (rot + (180.0 if o.flip_y else 0.0)) % 360.0
            o.upside = 90.0 < ang < 270.0

        if quarter:
            if turns % 2:
                w, h = h, w
        elif d.kind != O.SAW:
            o.corners = obb_corners(x, y, w, h, rot)
            if d.kind in (O.SOLID, O.BREAKABLE):
                self.report.assumed["approximate collision: solid at a non-90-degree rotation"] += 1
        o.w, o.h = w, h
        if o.corners:
            xs = [c[0] for c in o.corners]
            ys = [c[1] for c in o.corners]
            o.minx, o.maxx, o.miny, o.maxy = min(xs), max(xs), min(ys), max(ys)
        else:
            o.minx, o.maxx = x - w / 2.0, x + w / 2.0
            o.miny, o.maxy = y - h / 2.0, y + h / 2.0
        return o

    # ------------------------------------------------------------------ queries
    def next_stamp(self):
        self._stamp += 1
        return self._stamp

    def query(self, minx, maxx, miny, maxy):
        """Objects that never move whose bounding box touches the given rect, in level-string
        order.  Simulation.query adds the ones that triggers move."""
        self._stamp += 1
        stamp = self._stamp
        out = []
        sections = self.sections
        for s in range(int(minx // SECTION_SIZE), int(maxx // SECTION_SIZE) + 1):
            for o in sections.get(s, ()):
                if o.stamp != stamp:
                    o.stamp = stamp
                    if not (o.maxx < minx or maxx < o.minx or o.maxy < miny or maxy < o.miny):
                        out.append(o)
        if len(out) > 1:
            out.sort(key=_uid)
        return out


def _uid(o):
    return o.uid


def _channel(entry):
    """"1_r_2_g_3_b_6_id..." -> (id, (r, g, b))"""
    parts = entry.split("_")
    d = dict(zip(parts[0::2], parts[1::2]))
    try:
        rgb = tuple(max(0, min(255, int(float(d[k])))) for k in ("1", "2", "3"))
        return int(float(d.get("6", 0))), rgb
    except (KeyError, ValueError):
        return None, None


def _colours(h):
    out = {}
    if h.get("kS38"):
        for entry in str(h["kS38"]).split("|"):
            channel, rgb = _channel(entry)
            if channel:
                out[channel] = rgb
        return out
    for key, channel in (("kS29", 1000), ("kS30", 1001)):
        if h.get(key):
            rgb = _channel(str(h[key]))[1]
            if rgb:
                out[channel] = rgb
    for keys, channel in ((("kS1", "kS2", "kS3"), 1000), (("kS4", "kS5", "kS6"), 1001)):
        if channel not in out and all(k in h for k in keys):
            out[channel] = tuple(max(0, min(255, int(_num(h, k)))) for k in keys)
    return out


def _groups(raw):
    return tuple(int(g) for g in str(raw.get(57, "")).split(".") if g.isdigit())


# ---------------------------------------------------------------------- authoring
class LevelBuilder:
    """Tiny helper for writing level strings in code (tests, tools/make_test_level.py)."""

    def __init__(self, mode=0, speed=1, mini=False, flipped=False, extra_header=None):
        speed_code = {1: 0, 0: 1, 2: 2, 3: 3, 4: 4}[speed]
        hdr = {"kA13": 0, "kA15": 0, "kA16": 0, "kA6": 0, "kA7": 0, "kA17": 0, "kA18": 0,
               "kS39": 0, "kA2": mode, "kA3": int(mini), "kA8": 0, "kA4": speed_code,
               "kA9": 0, "kA10": 0, "kA11": int(flipped)}
        hdr.update(extra_header or {})
        self.header = hdr
        self.items = []

    def add(self, oid, x, y, rot=0, flip_x=False, flip_y=False, scale=1.0, **extra):
        parts = [1, oid, 2, _fmt(x), 3, _fmt(y)]
        if flip_x:
            parts += [4, 1]
        if flip_y:
            parts += [5, 1]
        if rot:
            parts += [6, _fmt(rot)]
        if scale != 1.0:
            parts += [32, _fmt(scale)]
        for k, v in extra.items():
            parts += [int(k.lstrip("k")), v]
        self.items.append(",".join(str(p) for p in parts))
        return self

    def block(self, col, row, oid=1):
        """A 30x30 object on the editor grid: column/row 0 is the first cell above ground."""
        return self.add(oid, 15 + 30 * col, 15 + 30 * row)

    def blocks(self, col0, col1, row, oid=1):
        for c in range(col0, col1 + 1):
            self.block(c, row, oid)
        return self

    def string(self):
        hdr = ",".join("%s,%s" % kv for kv in self.header.items())
        return hdr + ";" + ";".join(self.items) + ";"

    def level(self, name="built"):
        return Level(self.string(), name)


def _fmt(v):
    return ("%.4f" % v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
