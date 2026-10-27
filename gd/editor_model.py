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
    ("Blocks", [(1, "Block"), (40, "Slab"), (62, "Half block"), (64, "Small block"), (1202, "Platform"),
                (468, "Thin line"), (143, "Breakable")]),
    ("Hazards", [(8, "Spike"), (103, "Medium spike"), (39, "Small spike"), (98, "Saw small"), (89, "Saw medium"),
                 (88, "Saw big")]),
    ("Slopes", [(289, "Slope 45"), (291, "Slope 26"), (363, "Spike slope 45"), (364, "Spike slope 26")]),
    ("Pads", [(35, "Yellow pad"), (140, "Pink pad"), (1332, "Red pad"), (67, "Blue pad"), (3005, "Spider pad")]),
    ("Orbs", [(36, "Yellow orb"), (141, "Pink orb"), (1333, "Red orb"), (84, "Blue orb"), (1022, "Green orb"),
              (1330, "Black orb"), (1704, "Dash orb"), (1751, "Gravity dash"), (3004, "Spider orb")]),
    ("Mode portals", [(12, "Cube"), (13, "Ship"), (47, "Ball"), (111, "UFO"), (660, "Wave"), (745, "Robot"),
                      (1331, "Spider"), (1933, "Swing")]),
    ("Other portals", [(11, "Gravity up"), (10, "Gravity down"), (2926, "Gravity toggle"), (101, "Mini"),
                       (99, "Normal size"), (286, "Dual on"), (287, "Dual off"), (200, "Speed 0.5x"),
                       (201, "Speed 1x"), (202, "Speed 2x"), (203, "Speed 3x"), (1334, "Speed 4x")]),
]
LABELS = {oid: label for _, items in PALETTE for oid, label in items}

# Where an object sits inside its grid cell: offset from the cell centre, for rotation 0.
# Pads and small spikes rest on the cell floor, slabs hang at the top, the long slope
# covers two cells.  The offset turns with the object, so a pad rotated 180 degrees
# sticks to the ceiling of its cell.
CELL_OFFSET = {40: (0.0, 8.0), 39: (0.0, -8.0), 35: (0.0, -13.0), 140: (0.0, -12.5), 1332: (0.0, -12.0),
               67: (0.0, -12.0), 3005: (0.0, -12.0), 291: (15.0, 0.0), 364: (15.0, 0.0), 62: (0.0, 7.0),
               1202: (0.0, 13.5), 468: (0.0, 14.25), 103: (0.0, -5.0)}

PICK_REACH = 120.0               # how far from its centre an object can still be clicked (big saws, scaled objects)
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


SECTION = 120.0                 # width of the strips the document indexes its objects by


def _section(x):
    return int(math.floor(x / SECTION))


class LevelDocument:
    """A level being edited.  Built for levels with tens of thousands of objects: objects
    are indexed by the strip of the level they are in, so finding what is under the mouse
    or on screen never looks at the whole level, and `changes` tells the editor exactly
    what was added or removed since it last asked, so it redraws only that."""

    def __init__(self, name="New level"):
        self.name = name
        self.path = None
        self.header = dict(_DEFAULT_HEADER)
        self._objects = []
        self._index = None              # section -> objects in it (built when first needed)
        self._uids = None               # the uids of all objects (built when first needed)
        self._stats = None              # (counts, extent), until the next change
        self.revision = 0
        self.changes = None             # [("add" | "remove", object)] since take_changes(); None = everything
        self.dirty = False
        self._undo, self._redo = [], []

    # ------------------------------------------------------------------ the object list
    @property
    def objects(self):
        return self._objects

    @objects.setter
    def objects(self, objs):
        """Replacing the whole list (undo, redo, loading, group edits) starts the indexes afresh."""
        self._objects = objs
        self._changed(None)

    def _changed(self, entry):
        self.revision += 1
        if entry is None:
            self._stats = None
            self._index = self._uids = self.changes = None
            return
        kind, obj = entry
        if self._stats is not None:              # keep the counts and the extent without a rescan
            (sim, other), (x0, x1, y0, y1) = self._stats
            step = 1 if kind == "add" else -1
            if obj.phys is not None:
                sim += step
            else:
                other += step
            if kind == "add":
                if len(self._objects) == 1:     # the first object: the empty level's (0, 0) does not count
                    x0 = x1 = obj.x
                    y0 = y1 = obj.y
                self._stats = ((sim, other), (min(x0, obj.x), max(x1, obj.x), min(y0, obj.y), max(y1, obj.y)))
            elif obj.x in (x0, x1) or obj.y in (y0, y1):
                self._stats = None               # it was at the edge: measure again when asked
            else:
                self._stats = ((sim, other), (x0, x1, y0, y1))
        if self._index is not None:
            strip = self._index.setdefault(_section(obj.x), [])
            if kind == "add":
                strip.append(obj)
            elif obj in strip:
                strip.remove(obj)
        if self._uids is not None:
            if kind == "add":
                self._uids.add(obj.uid)
            else:
                self._uids.discard(obj.uid)
        if self.changes is not None:
            self.changes.append(entry)

    def take_changes(self):
        """What changed since the last call: a list of ("add" | "remove", object), or None
        when too much changed to list (redraw everything)."""
        out, self.changes = self.changes, []
        return out

    def near(self, x0, x1):
        """Objects whose x lies between x0 and x1 (from the index, not a scan)."""
        if self._index is None:
            index = {}
            for o in self._objects:
                index.setdefault(_section(o.x), []).append(o)
            self._index = index
        out = []
        for key in range(_section(x0), _section(x1) + 1):
            for o in self._index.get(key, ()):
                if x0 <= o.x <= x1:
                    out.append(o)
        return out

    def alive(self, objs):
        """Those of `objs` that are still in the level."""
        if self._uids is None:
            self._uids = {o.uid for o in self._objects}
        return [o for o in objs if o.uid in self._uids]

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
        for o in self.near(x - 0.01, x + 0.01):
            if o.id == oid and abs(o.y - y) < 0.01 and abs(o.rot - rot) < 0.01:
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
        self._objects.append(obj)
        self._changed(("add", obj))
        self.dirty = True
        return obj

    def place_in_cell(self, oid, col, row, rot=0.0, flip_x=False, flip_y=False, grid=GRID, checkpoint=True):
        x, y = cell_position(oid, col, row, rot, grid)
        return self.place(oid, x, y, rot, flip_x, flip_y, checkpoint)

    def pick(self, x, y):
        """Topmost object under a point (smallest one wins when several overlap)."""
        best, best_area = None, None
        for o in self.near(x - PICK_REACH, x + PICK_REACH):
            minx, maxx, miny, maxy = o.bounds()
            if minx <= x <= maxx and miny <= y <= maxy:
                area = (maxx - minx) * (maxy - miny)
                if best is None or area <= best_area:
                    best, best_area = o, area
        return best

    def erase(self, obj, checkpoint=True):
        if self.alive([obj]):
            if checkpoint:
                self._checkpoint()
            self._objects.remove(obj)
            self._changed(("remove", obj))
            self.dirty = True
            return True
        return False

    def clear(self):
        self._checkpoint()
        self.objects = []

    # ------------------------------------------------------------------ selections
    # Objects are never changed in place - an edited object is replaced by a new one - so
    # the undo history, which keeps lists of objects, stays valid.
    def inside(self, minx, maxx, miny, maxy):
        """Objects whose picking box overlaps the rectangle (a drag-selection)."""
        out = []
        for o in self.near(minx - PICK_REACH, maxx + PICK_REACH):
            a, b, c, d = o.bounds()
            if a <= maxx and b >= minx and c <= maxy and d >= miny:
                out.append(o)
        return out

    @staticmethod
    def _copy(obj, dx=0.0, dy=0.0, turn=0.0, about=None):
        raw = dict(obj.raw)
        x, y = obj.x, obj.y
        if turn:                               # a quarter turn clockwise about `about`
            ax, ay = about
            x, y = ax + (y - ay), ay - (x - ax)
            raw[6] = _fmt((obj.rot + turn) % 360.0)
            if raw[6] == "0":
                del raw[6]
        raw[2], raw[3] = _fmt(round(x + dx, 3)), _fmt(round(y + dy, 3))
        return EdObject(raw)

    def move(self, objs, dx, dy, checkpoint=True):
        """Shift objects by (dx, dy) units.  Returns the moved objects (new ones)."""
        objs = self.alive(objs)
        if not objs or (dx == 0 and dy == 0):
            return objs
        if checkpoint:
            self._checkpoint()
        moved = {o.uid: self._copy(o, dx, dy) for o in objs}
        self._replace(objs, moved)
        return list(moved.values())

    def _replace(self, old, new):
        """Swap objects for their edited copies, keeping the indexes up to date."""
        self._objects = [new.get(o.uid, o) for o in self._objects]
        for o in old:
            self._changed(("remove", o))
            self._changed(("add", new[o.uid]))
        self.dirty = True

    def turn(self, objs, checkpoint=True):
        """Rotate objects a quarter turn clockwise, as a group, about the centre of the grid
        cells they cover - so a turned group stays on the grid.  Returns the new objects."""
        objs = self.alive(objs)
        if not objs:
            return objs
        if checkpoint:
            self._checkpoint()
        xs, ys = [o.x for o in objs], [o.y for o in objs]
        about = (round((min(xs) + max(xs)) / 2.0 / 15.0) * 15.0, round((min(ys) + max(ys)) / 2.0 / 15.0) * 15.0)
        turned = {o.uid: self._copy(o, turn=90.0, about=about) for o in objs}
        self._replace(objs, turned)
        return list(turned.values())

    def remove(self, objs, checkpoint=True):
        gone = {o.uid for o in self.alive(objs)}
        if not gone:
            return False
        if checkpoint:
            self._checkpoint()
        removed = [o for o in self._objects if o.uid in gone]
        self._objects = [o for o in self._objects if o.uid not in gone]
        for o in removed:
            self._changed(("remove", o))
        self.dirty = True
        return True

    def paste(self, raws, dx, dy, checkpoint=True):
        """Add copies of objects (given as their raw key/value dicts), shifted.  Returns them."""
        if not raws:
            return []
        if checkpoint:
            self._checkpoint()
        added = [self._copy(EdObject(dict(raw)), dx, dy) for raw in raws]
        self._objects.extend(added)
        for o in added:
            self._changed(("add", o))
        self.dirty = True
        return added

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
    def _statistics(self):
        if self._stats is None:
            objs = self._objects
            xs = [o.x for o in objs] or [0.0]
            ys = [o.y for o in objs] or [0.0]
            simulated = sum(1 for o in objs if o.phys is not None)
            self._stats = ((simulated, len(objs) - simulated), (min(xs), max(xs), min(ys), max(ys)))
        return self._stats

    def extent(self):
        return self._statistics()[1]

    def counts(self):
        return self._statistics()[0]


def describe(oid):
    if oid in LABELS:
        return LABELS[oid]
    d = O.OBJECTS.get(oid)
    return "%s %d" % (d.kind, oid) if d else O.UNSUPPORTED_NAMES.get(oid, "object %d" % oid)
