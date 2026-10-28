"""Look and feel shared by the menu, the game HUD and the editor.

Everything is drawn on a tkinter Canvas instead of using native buttons: native
widgets cannot be coloured on macOS, and a drawn interface looks the same on every
platform.  `Button` gives a drawn rectangle hover and click behaviour through
canvas tags; screens simply redraw themselves when their state changes.
"""
import math
import sys

BG = "#0D111C"
PANEL = "#182033"
PANEL_HI = "#222C47"
LINE = "#2C3756"
TEXT = "#EEF1F7"
MUTED = "#8B94AB"
GREEN = "#59E36B"
CYAN = "#35D6FF"
YELLOW = "#FFD23F"
RED = "#FF5A4D"
FONT = "Helvetica Neue" if sys.platform == "darwin" else "Helvetica"
MONO = "Menlo" if sys.platform == "darwin" else "Courier"

STYLES = {
    #            fill       hover      text
    "primary": ("#3FBF5F", "#59E36B", "#06220D"),
    "normal": (PANEL_HI, "#2E3A5E", TEXT),
    "quiet": (PANEL, PANEL_HI, MUTED),
    "danger": ("#7A1F2B", "#B02A3B", "#FFE3E3"),
    "accent": ("#1F6FB8", "#2B8BE0", "#FFFFFF"),
}


def font(size, bold=False):
    return (FONT, size, "bold") if bold else (FONT, size)


_fonts = {}


def fit(text, spec, width):
    """`text`, shortened with "..." if it would be wider than `width` pixels in font `spec`."""
    from tkinter import font as tkfont
    measure = _fonts.get(spec)
    if measure is None:
        measure = _fonts[spec] = tkfont.Font(font=spec).measure
    if measure(text) <= width:
        return text
    while len(text) > 1 and measure(text + "...") > width:
        text = text[:-1]
    return text.rstrip() + "..."


def rounded_points(x0, y0, x1, y1, r):
    r = max(0.0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(7):
            a = math.radians(a0 + 15 * i)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return pts


def rounded(cv, x0, y0, x1, y1, r=10, **kw):
    """A rounded rectangle (real arc points, not a smoothed polygon)."""
    return cv.create_polygon(*rounded_points(x0, y0, x1, y1, r), **kw)


def mix(a, b, t):
    """Colour `t` of the way from hex colour a to hex colour b."""
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(
        int(round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * t)) for i in (1, 3, 5))


SKY_TOP, SKY_BOTTOM = "#0A0E19", "#16203A"


def backdrop(cv, w, h, ground=True):
    """The menu's background: a sky that lightens towards a glowing ground line, with a
    few large faint squares drifting in it, the way GD's menus look."""
    bands = 12
    for i in range(bands):
        y0, y1 = h * i / bands, h * (i + 1) / bands + 1
        cv.create_rectangle(0, y0, w, y1, fill=mix(SKY_TOP, SKY_BOTTOM, i / (bands - 1.0)), outline="",
                            tags="backdrop")
    for x, y, size, t in ((0.08, 0.30, 150, 0.05), (0.84, 0.18, 210, 0.04), (0.70, 0.62, 120, 0.05),
                          (0.22, 0.70, 90, 0.04), (0.50, 0.10, 70, 0.035), (0.95, 0.75, 160, 0.03)):
        cx, cy = w * x, h * y
        fill = mix(mix(SKY_TOP, SKY_BOTTOM, y), "#FFFFFF", t)
        cv.create_rectangle(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2, fill=fill,
                            outline=mix(fill, "#FFFFFF", t * 1.6), width=2, tags="backdrop")
    if ground:
        top = h - 46
        cv.create_rectangle(0, top, w, h, fill="#0B1222", outline="", tags="backdrop")
        for i in range(0, int(w) + 90, 90):                       # ground tiles
            cv.create_line(i, top + 1, i - 30, h, fill="#111B33", width=2, tags="backdrop")
        cv.create_line(0, top, w, top, fill=mix("#35D6FF", SKY_BOTTOM, 0.55), width=5, tags="backdrop")
        cv.create_line(0, top, w, top, fill="#BFEFFF", width=2, tags="backdrop")


def title(cv, x, y, text, size, colour=TEXT, shadow="#05070D"):
    """Big text with a hard drop shadow and a dark outline, so it stands off the backdrop."""
    f = font(size, True)
    d = max(2, size // 14)
    cv.create_text(x + d, y + d * 1.5, text=text, fill=shadow, font=f)
    for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        cv.create_text(x + ox, y + oy, text=text, fill=shadow, font=f)
    return cv.create_text(x, y, text=text, fill=colour, font=f)


def bar(cv, x, y, w, h, fraction, colour, track="#0A0D15", tags=()):
    """Progress bar.  Returns the item ids of (track, fill)."""
    fraction = max(0.0, min(1.0, fraction))
    a = rounded(cv, x, y, x + w, y + h, h / 2.0, fill=track, outline=LINE, tags=tags)
    b = None
    if fraction > 0:
        b = rounded(cv, x + 2, y + 2, x + 2 + max(h - 4, (w - 4) * fraction), y + h - 2, (h - 4) / 2.0,
                    fill=colour, outline="", tags=tags)
    return a, b


class Button:
    """A drawn button.  `command` runs on click; `key` is only a label for the
    shortcut - the screen that owns the button handles the key itself."""
    _count = 0

    def __init__(self, cv, x, y, w, h, text, command, style="normal", size=14, key=None, enabled=True,
                 tags=(), icon=None, radius=10):
        Button._count += 1
        self.cv = cv
        self.tag = "btn%d" % Button._count
        self.command = command
        self.enabled = enabled
        fill, self.hover, fg = STYLES[style]
        self.fill = fill
        if not enabled:
            fill, fg = "#141A29", "#4A5268"
            self.fill = fill
        all_tags = (self.tag,) + tuple(tags)
        rounded(cv, x, y + 3, x + w, y + h + 3, radius, fill=mix(fill, "#000000", 0.45), outline="", tags=all_tags)
        self.rect = rounded(cv, x, y, x + w, y + h, radius, fill=fill, outline=mix(fill, "#FFFFFF", 0.12),
                            tags=all_tags)
        label_x = x + w / 2.0
        if icon is not None:
            icon(cv, x + h / 2.0 + 4, y + h / 2.0, all_tags)
            label_x = x + h / 2.0 + (w - h / 2.0) / 2.0 + 4
        cy = y + h / 2.0 - (6 if key and h >= 46 else 0)
        self.label = cv.create_text(label_x, cy, text=text, fill=fg, font=font(size, True), tags=all_tags)
        if key:
            if h >= 46:
                cv.create_text(label_x, y + h - 13, text=key, fill=fg, font=font(9), tags=all_tags)
            else:
                cv.create_text(x + w - 8, y + h / 2.0, text=key, fill=fg, anchor="e", font=font(9), tags=all_tags)
        if enabled:
            cv.tag_bind(self.tag, "<Enter>", lambda e: (cv.itemconfigure(self.rect, fill=self.hover),
                                                        cv.configure(cursor="hand2")))
            cv.tag_bind(self.tag, "<Leave>", lambda e: (cv.itemconfigure(self.rect, fill=self.fill),
                                                        cv.configure(cursor="")))
            cv.tag_bind(self.tag, "<ButtonRelease-1>", self._click)

    def _click(self, _event):
        self.cv.configure(cursor="")
        self.command()
        return "break"


def format_time(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return "%d h %02d min" % (seconds // 3600, seconds % 3600 // 60)
    if seconds >= 60:
        return "%d min %02d s" % (seconds // 60, seconds % 60)
    return "%d s" % seconds
