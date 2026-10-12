"""The level editor's document model (no GUI code, so it can be tested headless).

A document keeps EVERY object of the level as its raw key/value dictionary, including
objects this engine cannot simulate, so opening a real level and saving it again does
not throw decoration or triggers away.
"""
import math

from . import objects as O
from .level import Level
from .levelio import RawLevel, load_level_file, write_gmd

GRID = 30.0

# What the editor offers, grouped as in the side panel: (object id, label).
PALETTE = [
    ("Blocks", [(1, "Block"), (40, "Slab"), (143, "Breakable")]),
    ("Hazards", [(8, "Spike"), (39, "Small spike"), (98, "Saw small"), (89, "Saw medium"), (88, "Saw big")]),
    ("Slopes", [(289, "Slope 45"), (291, "Slope 26")]),
    ("Pads", [(35, "Yellow pad"), (140, "Pink pad"), (1332, "Red pad"), (67, "Blue pad"), (3005, "Spider pad")]),
    ("Orbs", [(36, "Yellow orb"), (141, "Pink orb"), (1333, "Red orb"), (84, "Blue orb"), (1022, "Green orb"),
              (1330, "Black orb"), (1704, "Dash orb"), (1751, "Gravity dash"), (3004, "Spider orb")]),
    ("Mode portals", [(12, "Cube"), (13, "Ship"), (47, "Ball"), (111, "UFO"), (660, "Wave"), (745, "Robot"),
                      (1331, "Spider"), (1933, "Swing")]),
    ("Other portals", [(11, "Gravity up"), (10, "Gravity down"), (2926, "Gravity toggle"), (101, "Mini"),
                       (99, "Normal size"), (200, "Speed 0.5x"), (201, "Speed 1x"), (202, "Speed 2x"),
                       (203, "Speed 3x"), (1334, "Speed 4x")]),
]
LABELS = {oid: label for _, items in PALETTE for oid, label in items}

# Where an object sits inside its grid cell: offset from the cell centre, for rotation 0.
# Pads and small spikes rest on the cell floor, slabs hang at the top, the long slope
# covers two cells.  The offset turns with the object, so a pad rotated 180 degrees
# sticks to the ceiling of its cell.
CELL_OFFSET = {40: (0.0, 8.0), 39: (0.0, -8.0), 35: (0.0, -13.0), 140: (0.0, -12.5), 1332: (0.0, -12.0),
               67: (0.0, -12.0), 3005: (0.0, -12.0), 291: (15.0, 0.0)}

_SPEED_CODE = {1: 0, 0: 1, 2: 2, 3: 3, 4: 4}        # speed index -> kA4 value
_SPEED_INDEX = {v: k for k, v in _SPEED_CODE.items()}
_DEFAULT_HEADER = {"kA13": "0", "kA15": "0", "kA16": "0", "kA6": "0", "kA7": "0", "kA17": "0", "kA18": "0",
                   "kS39": "0", "kA2": "0", "kA3": "0", "kA8": "0", "kA4": "0", "kA9": "0", "kA10": "0",
                   "kA11": "0"}
_factory = Level("")                                  # only used for its object builder


def _fmt(v):
    if isinstance(v, float):
        return ("%.4f" % v).rstrip("0").rstrip(".")
    return str(v)


def _num(raw, key, default=0.0):
    try:
        return float(raw.get(key, default))
    except (TypeError, ValueError):
        return default


class EdObject:
    """One object in the document.  `phys` is its physics definition, or None for
    objects the engine does not simulate (they are kept, drawn as markers)."""
    __slots__ = ("uid", "raw", "phys")
    _next = 1

    def __init__(self, raw):
        self.uid = EdObject._next
        EdObject._next += 1
        self.raw = raw
        rep = _factory.report
        rep.skipped.clear(), rep.ignored.clear(), rep.assumed.clear()
        self.phys = _factory._build(raw, _num(raw, 2), _num(raw, 3))
        if self.phys is not None:
            self.phys.uid, self.phys.stamp = self.uid, -1

    @property
    def id(self):
        return int(_num(self.raw, 1))

    @property
    def x(self):
        return _num(self.raw, 2)

    @property
    def y(self):
        return _num(self.raw, 3)

    @property
    def rot(self):
        return _num(self.raw, 6) % 360.0

    def bounds(self):
        """(minx, maxx, miny, maxy) used for picking; never smaller than 20 x 20."""
        p = self.phys
        if p is None:
            return self.x - 15.0, self.x + 15.0, self.y - 15.0, self.y + 15.0
        cx, cy = (p.minx + p.maxx) / 2.0, (p.miny + p.maxy) / 2.0
        hw, hh = max(10.0, (p.maxx - p.minx) / 2.0), max(10.0, (p.maxy - p.miny) / 2.0)
        return cx - hw, cx + hw, cy - hh, cy + hh


def cell_position(oid, col, row, rot=0.0, grid=GRID):
    """Centre of object `oid` when placed in grid cell (col, row)."""
    ox, oy = CELL_OFFSET.get(oid, (0.0, 0.0))
    a = -math.radians(rot)
    c, s = math.cos(a), math.sin(a)
    x = (col + 0.5) * grid + ox * c - oy * s
    y = (row + 0.5) * grid + ox * s + oy * c
    return round(x, 3), round(y, 3)


def snap_position(oid, wx, wy, rot=0.0, half=False):
    """Where object `oid` lands when dropped at world point (wx, wy).

    Full grid: the centre of the 30-unit cell under the cursor.  Half grid: the nearest
    multiple of 15, which is how GD's editor lets you place things between cells."""
    if half:
        cx, cy = round(wx / 15.0) * 15.0, round(wy / 15.0) * 15.0
    else:
        cx, cy = (math.floor(wx / GRID) + 0.5) * GRID, (math.floor(wy / GRID) + 0.5) * GRID
    ox, oy = CELL_OFFSET.get(oid, (0.0, 0.0))
    a = -math.radians(rot)
    c, s = math.cos(a), math.sin(a)
    return round(cx + ox * c - oy * s, 3), round(cy + ox * s + oy * c, 3), cx, cy


class LevelDocument:
    def __init__(self, name="New level"):
        self.name = name
        self.path = None
        self.header = dict(_DEFAULT_HEADER)
        self.objects = []
        self.dirty = False
        self._undo, self._redo = [], []

    # ------------------------------------------------------------------ files
    @classmethod
    def from_string(cls, level_string, name="Unnamed"):
        from .levelio import split_level_string
        doc = cls(name)
        header, raws = split_level_string(level_string)
        if header:
            doc.header = dict(header)
        doc.objects = [EdObject(raw) for raw in raws]
        return doc

    @classmethod
    def load(cls, path, select=None):
        raw = load_level_file(path, select)
        doc = cls.from_string(raw.level_string, raw.name)
        # only plain .gmd files are written back in place; anything else is "Save as"
        doc.path = path if path.lower().endswith(".gmd") and select is None else None
        return doc

    def to_string(self):
        head = ",".join("%s,%s" % (k, v) for k, v in self.header.items())
        parts = []
        for o in self.objects:
            keys = [1, 2, 3] + sorted(k for k in o.raw if k not in (1, 2, 3))
            parts.append(",".join("%d,%s" % (k, o.raw[k]) for k in keys if k in o.raw))
        return head + ";" + ";".join(parts) + (";" if parts else "")

    def to_level(self):
        return Level(RawLevel(self.name, self.to_string(), "editor"))

    def save(self, path=None):
        path = path or self.path
        if not path:
            raise ValueError("no file name")
        write_gmd(path, self.name, self.to_string())
        self.path = path
        self.dirty = False

    # ------------------------------------------------------------------ start settings
    def get_start(self):
        h = self.header
        return {"mode": int(_num(h, "kA2")), "speed": _SPEED_INDEX.get(int(_num(h, "kA4")), 1),
                "mini": _num(h, "kA3") != 0, "flipped": _num(h, "kA11") != 0}

    def set_start(self, mode=None, speed=None, mini=None, flipped=None):
        self._checkpoint()
        if mode is not None:
            self.header["kA2"] = str(int(mode))
        if speed is not None:
            self.header["kA4"] = str(_SPEED_CODE[int(speed)])
        if mini is not None:
            self.header["kA3"] = str(int(bool(mini)))
        if flipped is not None:
            self.header["kA11"] = str(int(bool(flipped)))

    # ------------------------------------------------------------------ editing
    def _checkpoint(self):
        self._undo.append((list(self.objects), dict(self.header)))
        del self._undo[:-200]
        self._redo.clear()
        self.dirty = True

    def begin_stroke(self):
        """Call once before a mouse drag so the whole drag is one undo step."""
        self._checkpoint()

    def place(self, oid, x, y, rot=0.0, flip_x=False, flip_y=False, checkpoint=True):
        """Add an object; returns it, or None if an identical one is already there."""
        rot = rot % 360.0
        for o in self.objects:
            if o.id == oid and abs(o.x - x) < 0.01 and abs(o.y - y) < 0.01 and abs(o.rot - rot) < 0.01:
                return None
        raw = {1: str(int(oid)), 2: _fmt(float(x)), 3: _fmt(float(y))}
        if flip_x:
            raw[4] = "1"
        if flip_y:
            raw[5] = "1"
        if rot:
            raw[6] = _fmt(float(rot))
        if checkpoint:
            self._checkpoint()
        obj = EdObject(raw)
        self.objects.append(obj)
        self.dirty = True
        return obj

    def place_in_cell(self, oid, col, row, rot=0.0, flip_x=False, flip_y=False, grid=GRID, checkpoint=True):
        x, y = cell_position(oid, col, row, rot, grid)
        return self.place(oid, x, y, rot, flip_x, flip_y, checkpoint)

    def pick(self, x, y):
        """Topmost object under a point (smallest one wins when several overlap)."""
        best, best_area = None, None
        for o in self.objects:
            minx, maxx, miny, maxy = o.bounds()
            if minx <= x <= maxx and miny <= y <= maxy:
                area = (maxx - minx) * (maxy - miny)
                if best is None or area <= best_area:
                    best, best_area = o, area
        return best

    def erase(self, obj, checkpoint=True):
        if obj in self.objects:
            if checkpoint:
                self._checkpoint()
            self.objects.remove(obj)
            self.dirty = True
            return True
        return False

    def clear(self):
        self._checkpoint()
        self.objects = []

    def undo(self):
        if not self._undo:
            return False
        self._redo.append((list(self.objects), dict(self.header)))
        self.objects, self.header = self._undo.pop()
        self.dirty = True
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append((list(self.objects), dict(self.header)))
        self.objects, self.header = self._redo.pop()
        self.dirty = True
        return True

    # ------------------------------------------------------------------ info
    def extent(self):
        xs = [o.x for o in self.objects] or [0.0]
        ys = [o.y for o in self.objects] or [0.0]
        return min(xs), max(xs), min(ys), max(ys)

    def counts(self):
        simulated = sum(1 for o in self.objects if o.phys is not None)
        return simulated, len(self.objects) - simulated


def describe(oid):
    if oid in LABELS:
        return LABELS[oid]
    d = O.OBJECTS.get(oid)
    return "%s %d" % (d.kind, oid) if d else O.UNSUPPORTED_NAMES.get(oid, "object %d" % oid)
