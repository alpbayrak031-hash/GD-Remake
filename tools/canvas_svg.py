"""Developer tool: dump a tkinter Canvas to an SVG file.

tkinter cannot save a picture of itself without extra libraries, so this walks the
canvas items and writes equivalent SVG.  It exists to look at screens while
working on them (e.g. `sips -s format png shot.svg --out shot.png`), and covers
what this project draws: rectangles, polygons, ovals, lines and text.
"""
import html


def _font(cv, item):
    spec = cv.itemcget(item, "font")
    parts = cv.tk.splitlist(spec)
    family = parts[0] if parts else "Helvetica"
    size = 12
    weight = "normal"
    for p in parts[1:]:
        if p.lstrip("-").isdigit():
            size = abs(int(p))
        elif p == "bold":
            weight = "bold"
    return family, size, weight


_ANCHOR = {"nw": ("start", 0.8), "n": ("middle", 0.8), "ne": ("end", 0.8),
           "w": ("start", 0.32), "center": ("middle", 0.32), "e": ("end", 0.32),
           "sw": ("start", -0.2), "s": ("middle", -0.2), "se": ("end", -0.2)}


def canvas_to_svg(cv, path, background=None):
    cv.update_idletasks()
    w, h = cv.winfo_width(), cv.winfo_height()
    x0, y0 = cv.canvasx(0), cv.canvasy(0)
    bg = background or cv.cget("bg")
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="%g %g %d %d">' % (w, h, x0, y0, w, h),
           '<rect x="%g" y="%g" width="%d" height="%d" fill="%s"/>' % (x0, y0, w, h, bg)]

    def paint(item, stroke_key="outline"):
        fill = cv.itemcget(item, "fill") or "none"
        stroke = cv.itemcget(item, stroke_key) if stroke_key else ""
        width = cv.itemcget(item, "width")
        attrs = 'fill="%s"' % fill
        if stroke:
            attrs += ' stroke="%s" stroke-width="%s"' % (stroke, width)
            dash = cv.itemcget(item, "dash")
            if dash:
                attrs += ' stroke-dasharray="%s"' % dash.replace(" ", ",")
        return attrs

    for item in cv.find_all():
        if cv.itemcget(item, "state") == "hidden":
            continue
        kind = cv.type(item)
        c = cv.coords(item)
        if kind == "rectangle":
            x, y = min(c[0], c[2]), min(c[1], c[3])
            out.append('<rect x="%g" y="%g" width="%g" height="%g" %s/>' % (x, y, abs(c[2] - c[0]), abs(c[3] - c[1]), paint(item)))
        elif kind == "oval":
            out.append('<ellipse cx="%g" cy="%g" rx="%g" ry="%g" %s/>' % (
                (c[0] + c[2]) / 2, (c[1] + c[3]) / 2, abs(c[2] - c[0]) / 2, abs(c[3] - c[1]) / 2, paint(item)))
        elif kind == "polygon":
            pts = " ".join("%g,%g" % (c[i], c[i + 1]) for i in range(0, len(c), 2))
            out.append('<polygon points="%s" %s/>' % (pts, paint(item)))
        elif kind == "line":
            pts = " ".join("%g,%g" % (c[i], c[i + 1]) for i in range(0, len(c), 2))
            colour = cv.itemcget(item, "fill") or "#000"
            extra = ' stroke-linecap="round" stroke-linejoin="round"' if cv.itemcget(item, "capstyle") == "round" else ""
            dash = cv.itemcget(item, "dash")
            if dash:
                extra += ' stroke-dasharray="%s"' % dash.replace(" ", ",")
            out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%s"%s/>' % (
                pts, colour, cv.itemcget(item, "width"), extra))
        elif kind == "text":
            family, size, weight = _font(cv, item)
            anchor, shift = _ANCHOR.get(cv.itemcget(item, "anchor"), ("middle", 0.32))
            lines = cv.itemcget(item, "text").split("\n")
            justify = cv.itemcget(item, "justify")
            first = c[1] + size * shift
            if cv.itemcget(item, "anchor") in ("w", "center", "e"):
                first -= (len(lines) - 1) * size * 1.25 / 2
            elif cv.itemcget(item, "anchor") in ("sw", "s", "se"):
                first -= (len(lines) - 1) * size * 1.25
            for i, line in enumerate(lines):
                out.append('<text x="%g" y="%g" fill="%s" font-family="%s, Helvetica, Arial, sans-serif" font-size="%d" '
                           'font-weight="%s" text-anchor="%s" xml:space="preserve">%s</text>' % (
                               c[0], first + i * size * 1.25, cv.itemcget(item, "fill") or "#000", family, size, weight,
                               anchor if justify != "center" or anchor != "start" else anchor, html.escape(line)))
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return path
