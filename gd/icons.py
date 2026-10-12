"""Vector icons for the eight game modes and the difficulty faces.

Every icon is a short list of shapes in a local frame: x to the right, y up, the
30-unit player hitbox being the square from -1 to 1.  The same data is drawn three
ways: as the animated player (IconSprite), as a small static picture on portals,
in the menus and in the editor (draw_icon), and each mode keeps ONE colour
everywhere - the portal that turns you into a ship is the colour of the ship.

The silhouettes are deliberately different so a mode is recognisable at a glance
even when small: square (cube), rocket with a rider (ship), spoked wheel (ball),
dome on a saucer (ufo), arrowhead (wave), head on two legs (robot), flat body on
four legs (spider), ball with three thrusters (swing).
"""
import math

CUBE, SHIP, BALL, UFO, WAVE, ROBOT, SPIDER, SWING = range(8)
MODE_NAMES = ("Cube", "Ship", "Ball", "UFO", "Wave", "Robot", "Spider", "Swing")
# One colour per mode, chosen to stay apart from each other and from the red of hazards.
MODE_COLORS = ("#6BE35A", "#FF5FD2", "#FF8C1A", "#FFE23F", "#35D6FF", "#F2F4F8", "#A678FF", "#2FE0A8")
DARK = "#141926"
EDGE = "#06080D"
ROLE = {"d": DARK, "w": "#FFFFFF", "a": "#FFB02E", "g": "#BFE9FF", "e": EDGE}


def _circle(cx, cy, r, n=20, ry=None, a0=0.0, a1=360.0):
    ry = r if ry is None else ry
    span = math.radians(a1 - a0)
    steps = n if a1 - a0 >= 360.0 else n // 2
    last = steps if a1 - a0 < 360.0 else steps - 1
    return [(cx + r * math.cos(math.radians(a0) + span * i / steps),
             cy + ry * math.sin(math.radians(a0) + span * i / steps)) for i in range(last + 1)]


def _box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _sq(cx, cy, half):
    return _box(cx - half, cy - half, cx + half, cy + half)


# (kind, points, fill role, outlined?, line width)   kind: "poly" or "line"
def P(points, fill, outline=True):
    return ("poly", points, fill, outline, 0.0)


def L(points, fill="p", width=0.2):
    return ("line", points, fill, False, width)


ICONS = {
    CUBE: [
        P(_sq(0, 0, 1.0), "p"),
        P(_sq(0, 0, 0.7), "d", False),
        P(_box(-0.44, 0.06, -0.12, 0.38), "w", False),
        P(_box(0.12, 0.06, 0.44, 0.38), "w", False),
        P(_box(-0.44, -0.44, 0.44, -0.2), "w", False),
    ],
    SHIP: [
        P([(-1.15, -0.62), (-1.85, -0.36), (-1.15, -0.1)], "a", False),
        P([(-1.2, -0.02), (-1.5, 0.56), (-0.7, -0.02)], "p"),
        P([(-1.2, -0.02), (-1.2, -0.62), (-0.82, -0.9), (0.72, -0.9), (1.5, -0.38), (1.02, -0.02)], "p"),
        P([(-0.95, -0.5), (0.98, -0.5), (1.16, -0.34), (-0.95, -0.34)], "d", False),
        P(_sq(-0.05, 0.36, 0.4), "p"),
        P(_sq(-0.05, 0.36, 0.24), "d", False),
    ],
    BALL: [
        P(_circle(0, 0, 1.0), "p"),
        P(_circle(0, 0, 0.72), "d", False),
        P(_box(-0.72, -0.15, 0.72, 0.15), "p", False),
        P(_box(-0.15, -0.72, 0.15, 0.72), "p", False),
        P(_circle(0, 0, 0.3, 14), "w", False),
    ],
    UFO: [
        P(_circle(0, -0.1, 0.74, 20, ry=1.0, a0=0.0, a1=180.0), "g"),
        P(_sq(0, 0.3, 0.3), "p"),
        P(_sq(0, 0.3, 0.17), "d", False),
        P([(-0.5, -0.6), (0.5, -0.6), (0.32, -0.86), (-0.32, -0.86)], "d"),
        P(_circle(0, -0.3, 1.42, 24, ry=0.42), "p"),
        P(_circle(-0.78, -0.3, 0.13, 10), "w", False),
        P(_circle(0, -0.36, 0.13, 10), "w", False),
        P(_circle(0.78, -0.3, 0.13, 10), "w", False),
    ],
    WAVE: [        # a small dart: about 70 % of the other icons, as in GD
        P([(-0.72, -0.62), (0.94, 0.0), (-0.72, 0.62), (-0.3, 0.0)], "p"),
        P([(-0.36, -0.29), (0.45, 0.0), (-0.36, 0.29), (-0.16, 0.0)], "d", False),
    ],
    ROBOT: [
        L([(-0.22, -0.12), (-0.62, -0.55), (-0.36, -0.98), (0.0, -0.98)], "p", 0.24),
        L([(0.22, -0.12), (0.6, -0.5), (0.34, -0.98), (0.72, -0.98)], "p", 0.24),
        P([(-0.46, -0.22), (0.42, -0.22), (0.32, 0.1), (-0.36, 0.1)], "d"),
        L([(0.05, 0.98), (0.05, 1.3)], "p", 0.12),
        P(_circle(0.05, 1.36, 0.13, 10), "a", False),
        P(_box(-0.45, 0.04, 0.55, 1.0), "p"),
        P(_box(-0.12, 0.42, 0.47, 0.72), "d", False),
        P(_box(0.14, 0.49, 0.38, 0.65), "w", False),
    ],
    SPIDER: [
        L([(-0.35, 0.02), (-0.98, 0.58), (-1.32, -0.98)], "p", 0.17),
        L([(-0.22, -0.08), (-0.6, 0.26), (-0.78, -0.98)], "p", 0.17),
        L([(0.22, -0.08), (0.6, 0.26), (0.78, -0.98)], "p", 0.17),
        L([(0.35, 0.02), (0.98, 0.58), (1.32, -0.98)], "p", 0.17),
        P([(-0.56, -0.34), (0.56, -0.34), (0.78, 0.04), (0.46, 0.48), (-0.46, 0.48), (-0.78, 0.04)], "p"),
        P(_box(-0.5, -0.06, 0.5, 0.26), "d", False),
        P(_box(-0.32, 0.0, -0.1, 0.2), "w", False),
        P(_box(0.1, 0.0, 0.32, 0.2), "w", False),
    ],
    SWING: [       # a ball with thrusters above, below and behind; the flames are drawn by gd/effects.py
        P([(-0.6, -0.24), (-1.05, -0.4), (-1.05, 0.4), (-0.6, 0.24)], "p"),
        P([(-0.3, 0.56), (0.3, 0.56), (0.2, 0.92), (-0.2, 0.92)], "d"),
        P([(-0.3, -0.56), (0.3, -0.56), (0.2, -0.92), (-0.2, -0.92)], "d"),
        P(_circle(0, 0, 0.7), "p"),
        P(_circle(0, 0, 0.42, 16), "d", False),
        P(_circle(0, 0, 0.17, 10), "w", False),
    ],
}


def _colour(role, primary):
    return primary if role == "p" else ROLE[role]


def _transform(points, cx, cy, half, rotation, flipped):
    a = -math.radians(rotation)
    c, s = math.cos(a), math.sin(a)
    out = []
    for x, y in points:
        if flipped:
            y = -y
        out.append(cx + (x * c - y * s) * half)
        out.append(cy - (x * s + y * c) * half)
    return out


def draw_icon(cv, mode, cx, cy, half, rotation=0.0, flipped=False, colour=None, tags=()):
    """Draw a mode icon on a canvas, centred on (cx, cy) in canvas pixels; the hitbox
    square is `half` pixels in each direction.  Returns the created item ids."""
    primary = colour or MODE_COLORS[mode]
    items = []
    for kind, points, role, outlined, width in ICONS[mode]:
        flat = _transform(points, cx, cy, half, rotation, flipped)
        if kind == "poly":
            items.append(cv.create_polygon(*flat, fill=_colour(role, primary),
                                           outline=EDGE if outlined else "", width=1, tags=tags))
        else:
            items.append(cv.create_line(*flat, fill=_colour(role, primary), width=max(1.0, width * half),
                                        capstyle="round", joinstyle="round", tags=tags))
    return items


class IconSprite:
    """The animated player: the icon's canvas items, moved every frame."""

    def __init__(self, cv, mode, tags=("player",)):
        self.cv = cv
        self.mode = mode
        self.tags = tags
        self.items = draw_icon(cv, mode, 0, 0, 1.0, tags=tags)
        self._half = None
        self._colour = MODE_COLORS[mode]

    def place(self, cx, cy, half, rotation=0.0, flipped=False):
        cv = self.cv
        for item, (kind, points, _role, _outlined, width) in zip(self.items, ICONS[self.mode]):
            cv.coords(item, *_transform(points, cx, cy, half, rotation, flipped))
            if kind == "line" and half != self._half:
                cv.itemconfigure(item, width=max(1.0, width * half))
        self._half = half

    def set_colour(self, colour):
        if colour == self._colour:
            return
        self._colour = colour
        for item, (kind, _points, role, _outlined, _width) in zip(self.items, ICONS[self.mode]):
            if role == "p":
                self.cv.itemconfigure(item, fill=colour)

    def destroy(self):
        for item in self.items:
            self.cv.delete(item)
        self.items = []


# ------------------------------------------------------------------ difficulty faces
DIFFICULTY_COLORS = {"easy": "#3FBF5F", "medium": "#F2B01E", "hard": "#F0642D", "very hard": "#C2185B",
                     "custom": "#5D8DFF", "test": "#5D8DFF"}


def draw_face(cv, difficulty, cx, cy, r, tags=()):
    """A round face whose expression says how hard the level is (like GD's)."""
    colour = DIFFICULTY_COLORS.get(difficulty, "#5D8DFF")
    items = []

    def poly(points, fill, outline=""):
        flat = []
        for x, y in points:
            flat += [cx + x * r, cy - y * r]
        items.append(cv.create_polygon(*flat, fill=fill, outline=outline, tags=tags))

    if difficulty == "very hard":                       # horns
        poly([(-0.85, 0.5), (-1.05, 1.25), (-0.4, 0.88)], colour, EDGE)
        poly([(0.85, 0.5), (1.05, 1.25), (0.4, 0.88)], colour, EDGE)
    poly(_circle(0, 0, 1.0, 28), colour, EDGE)
    if difficulty in ("custom", "test"):                # a wrench-free, neutral "user" mark
        poly(_circle(0, 0.25, 0.32, 14), "#FFFFFF")
        poly(_circle(0, -0.62, 0.6, 16, ry=0.45, a0=0.0, a1=180.0), "#FFFFFF")
        return items
    brow = {"easy": 0.0, "medium": 0.0, "hard": 0.16, "very hard": 0.26}.get(difficulty, 0.0)
    for side in (-1, 1):
        poly(_circle(side * 0.38, 0.18, 0.2, 12), "#FFFFFF")
        poly(_circle(side * 0.38, 0.16, 0.09, 8), EDGE)
        if brow:                                         # angry eyebrows slanting inwards
            poly([(side * 0.66, 0.52 + brow), (side * 0.12, 0.36), (side * 0.14, 0.26),
                  (side * 0.68, 0.42 + brow)], EDGE)
    if difficulty == "easy":                             # smile
        poly(_circle(0, -0.3, 0.46, 16, ry=0.32, a0=180.0, a1=360.0), EDGE)
    elif difficulty == "medium":                         # flat mouth
        poly(_box(-0.4, -0.5, 0.4, -0.36), EDGE)
    elif difficulty == "hard":                           # frown
        poly(_circle(0, -0.62, 0.42, 16, ry=0.26, a0=0.0, a1=180.0), EDGE)
    else:                                                # bared teeth
        poly(_box(-0.48, -0.66, 0.48, -0.32), EDGE)
        for i in range(4):
            x0 = -0.42 + i * 0.22
            poly(_box(x0, -0.6, x0 + 0.16, -0.38), "#FFFFFF")
    return items
