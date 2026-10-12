"""Small collision helpers: axis-aligned rects, oriented boxes (SAT) and circles."""
import math


def rects_touch(a_minx, a_maxx, a_miny, a_maxy, b_minx, b_maxx, b_miny, b_maxy):
    """cocos2d CCRect::intersectsRect - edges that merely touch count as a hit."""
    return not (a_maxx < b_minx or b_maxx < a_minx or a_maxy < b_miny or b_maxy < a_miny)


def obb_corners(cx, cy, w, h, rotation_deg):
    """Corners of a w x h box centred on (cx, cy), rotated clockwise like GD's key 6."""
    a = -math.radians(rotation_deg)
    c, s = math.cos(a), math.sin(a)
    hw, hh = w / 2.0, h / 2.0
    return [(cx + px * c - py * s, cy + px * s + py * c)
            for px, py in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]


def _project(corners, ax, ay):
    dots = [x * ax + y * ay for x, y in corners]
    return min(dots), max(dots)


def obb_overlaps_rect(corners, minx, maxx, miny, maxy):
    """Separating-axis test between an oriented box and an axis-aligned rect.

    The game's OBB2D::overlaps1Way pair is the same test (two axes per box).
    """
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    if max(xs) < minx or min(xs) > maxx or max(ys) < miny or min(ys) > maxy:
        return False
    rect = ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy))
    for i in (0, 1):
        ax = corners[i + 1][0] - corners[i][0]
        ay = corners[i + 1][1] - corners[i][1]
        a0, a1 = _project(corners, ax, ay)
        b0, b1 = _project(rect, ax, ay)
        if a1 < b0 or b1 < a0:
            return False
    return True


def _one_way(a, b):
    """OBB2D::overlaps1Way: neither edge direction of box `a` separates the two boxes.
    Boxes that merely touch count as overlapping, as in the game."""
    for i in (0, 1):
        ax = a[i + 1][0] - a[i][0]
        ay = a[i + 1][1] - a[i][1]
        a0, a1 = _project(a, ax, ay)
        b0, b1 = _project(b, ax, ay)
        if a1 < b0 or b1 < a0:
            return False
    return True


def obb_overlaps_obb(a, b):
    """Two oriented boxes, each a list of 4 corners in order.  This is exactly how the
    game tests a rotated object against the player's rotated box (OSGD
    collisionCheckObjects): objectOB->overlaps1Way(playerOB) && playerOB->overlaps1Way(objectOB)."""
    return _one_way(a, b) and _one_way(b, a)


def slerp2d(from_angle, to_angle, t):
    """OSGD FreeFunctions Slerp2D: interpolate between two angles (radians) along the
    shorter arc.  The result is always within one turn of zero."""
    ha, hb = from_angle * 0.5, to_angle * 0.5
    ca, sa, cb, sb = math.cos(ha), math.sin(ha), math.cos(hb), math.sin(hb)
    cos_omega = sb * sa + cb * ca
    if cos_omega < 0.0:
        cos_omega, sb, cb = -cos_omega, -sb, -cb
    c0, c1 = 1.0 - t, t
    if 1.0 - cos_omega > 0.0001:
        omega = math.acos(min(1.0, cos_omega))
        sin_omega = math.sin(omega)
        c0 = math.sin((1.0 - t) * omega) / sin_omega
        c1 = math.sin(t * omega) / sin_omega
    half = math.atan2(sa * c0 + c1 * sb, ca * c0 + c1 * cb)
    return half + half


def circle_hits_rect_legacy(cx, cy, radius, minx, maxx, miny, maxy):
    """GJBaseGameLayer::objectIntersectsCircle (OSGD): the pre-2.2 saw test.

    True when the rect contains the circle centre or one of the rect's CORNERS is
    strictly inside the circle.  Note that this is not a real circle/rect test: an
    edge can pass through the circle without a hit.  That quirk is the game's.
    """
    if minx <= cx <= maxx and miny <= cy <= maxy:
        return True
    r2 = radius * radius
    for px, py in ((maxx, maxy), (maxx, miny), (minx, miny), (minx, maxy)):
        if (px - cx) ** 2 + (py - cy) ** 2 < r2:
            return True
    return False


def circle_hits_player_fixed(cx, cy, radius, px, py, player_width):
    """GJBaseGameLayer::playerIntersectsCircle (OSGD): used when the level has
    "Fix Radius Collision" (kA39) enabled - the player is a circle too."""
    return math.hypot(px - cx, py - cy) <= radius + player_width / 2.0
