"""Trails, fire and particles - everything that follows the player but is not the player.

Purely visual: nothing here is read by the simulation.

What is shown, and when, follows Geometry Dash:

  regular trail   a ribbon that fades out over 0.3 s.  Ship, UFO, wave and swing have it
                  the whole time.  Cube, ball, robot and spider get it only when
                  something launches them - an orb, a pad, a gravity change, a spider
                  teleport - and lose it the moment they land.  It is off during a dash.
                  (PlayerObject::activateStreak and its callers: ringJump, propellPlayer,
                  flipGravity - not for the ball -, the toggle*Mode functions; and
                  startDashing -> deactivateStreak.)
  wave trail      the solid zig-zag band the wave leaves behind it, with a pale core.
                  It keeps every corner until the corner scrolls off screen, and fades
                  in 0.2 s when the wave ends; in dual mode the second icon leaves one
                  too, in its lighter colour.  (HardStreak::updateStroke: the band is
                  6 x size x pulse wide, the white core 2 x size x pulse at 65 %;
                  deactivateStreak -> fadeOutStreak2(0.2).)
  fire            ship: exhaust while the button is held; robot: under the feet while a
                  held jump is being boosted; swing: one flame at the back and one on
                  the side gravity pulls away from (updateSwingFire); dash orb: a cone
                  behind the player, flickering, with sparks thrown back off it.
  orb ring        hitting an orb sends a ring out from it that widens and fades
                  (GD's circle wave on ringJump; its size and length here are EST).
  particles       squares kicked up while a ground mode slides, a burst on landing,
                  exhaust from ship and UFO, and a 0.12 s burst under the UFO on each
                  click (playBurstEffect).
  spider          a beam between the two ends of a teleport.

Honest limits: tkinter has no textures and no transparency, so a fade is drawn by
mixing the colour towards the background, the ribbon is flat-coloured, and GD's
particle sprites are plain squares here.  Values marked MEM are remembered from GD
(PlayerObject::setupStreak is not in the decompilations); EST are judged by eye.
"""
import math
import random
from collections import deque

from . import icons
from .constants import MINI_SCALE, ROBOT_BOOST_MAX, TPS
from .player import BALL, CUBE, FLYING, ROBOT, SHIP, SPIDER, SWING, UFO, WAVE

TRAIL_TICKS = int(0.3 * TPS)      # MEM: the default streak fades in 0.3 s
TRAIL_WIDTH = 10.0                # MEM: default streak stroke, units (x0.8 for the wave: toggleDartMode)
TRAIL_OPACITY = 0.6               # EST: the streak texture is translucent
SEGMENTS = 12
WAVE_OUTER, WAVE_INNER = 6.0, 2.0  # HardStreak::updateStroke
WAVE_PULSE = 1.6                  # EST: m_pulseSize follows the music (0.4 .. 2.5); there is none here
WAVE_FADE_TICKS = int(0.2 * TPS)  # deactivateStreak -> fadeOutStreak2(0.2)
UFO_BURST_TICKS = int(0.12 * TPS)  # playBurstEffect
BEAM_TICKS = int(0.25 * TPS)      # EST
MAX_PARTICLES = 56
RING_TICKS = int(0.32 * TPS)      # EST: how long an orb's ring takes to widen and fade
RING_FROM, RING_TO = 14.0, 46.0   # EST: its radius, units
FLAME = "#FFB02E"
FLAME_CORE = "#FFF3B0"


def blend(a, b, t):
    """Colour `t` of the way from hex colour a to hex colour b."""
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(
        int(round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * t)) for i in (1, 3, 5))


class WaveTrail:
    """The wave's solid zig-zag band behind one icon (dual mode has two of them)."""

    def __init__(self, cv, hidden):
        self.it_band = cv.create_polygon(0, 0, 0, 0, 0, 0, outline="", **hidden)
        self.it_core = cv.create_polygon(0, 0, 0, 0, 0, 0, outline="", **hidden)
        self.reset()

    def reset(self):
        self.points = []                                 # every corner so far
        self.active = False
        self.dir = 0
        self.fade = None                                 # ticks left of the fade-out
        self.size = 1.0
        self.head = (0.0, 0.0)
        self.colour = icons.MODE_COLORS[4]

    def tick(self, q, colour):
        """q: the icon (None when there is no second one)."""
        if q is not None and q.mode == WAVE and not q.dead:
            dy = q.y - q.last_y
            direction = 0 if abs(dy) < 1e-6 else (1 if dy > 0 else -1)
            if not self.active:
                self.points = [(q.last_x, q.last_y)]
                self.active, self.fade = True, None
            elif direction != self.dir:
                self.points.append((q.last_x, q.last_y))
            self.dir = direction
            self.head = (q.x, q.y)
            self.size = MINI_SCALE if q.mini else 1.0
            self.colour = colour
        elif self.active:
            self.active = False
            self.fade = WAVE_FADE_TICKS
        if self.fade is not None:
            self.fade -= 1
            if self.fade <= 0:
                self.fade, self.points = None, []

    def draw(self, fx, camera_left):
        cv, z, bg = fx.cv, fx.z, fx.bg
        pts = self.points
        if not (pts and (self.active or self.fade is not None)):
            fx._hide(self.it_band)
            fx._hide(self.it_core)
            return
        while len(pts) > 2 and pts[1][0] < camera_left - 60:
            pts.pop(0)                                   # corners that scrolled away (clearBehindXPos)
        chain = pts + [self.head]
        fade = 1.0 if self.fade is None else self.fade / float(WAVE_FADE_TICKS)
        for item, width, colour in ((self.it_band, WAVE_OUTER, self.colour),
                                    (self.it_core, WAVE_INNER, blend(self.colour, "#FFFFFF", 0.65))):
            half = width * self.size * WAVE_PULSE / 2.0
            top, bottom = [], []
            for (x0, y0), (x1, y1) in zip(chain, chain[1:]):
                if x1 - x0 < 1e-6:
                    continue
                # a band of constant thickness, cut vertically at each corner so the
                # pieces meet in clean points
                hv = half * math.hypot(x1 - x0, y1 - y0) / (x1 - x0)
                top += [x0 * z, -(y0 + hv) * z, x1 * z, -(y1 + hv) * z]
                bottom += [(x0, y0 - hv), (x1, y1 - hv)]
            if len(top) >= 4:
                flat = list(top)
                for x, y in reversed(bottom):
                    flat += [x * z, -y * z]
                cv.coords(item, *flat)
                fx._show(item, fill=blend(bg, colour, fade))
            else:
                fx._hide(item)


class Effects:
    def __init__(self, cv, zoom, background, orb_colours=None):
        self.cv, self.z, self.bg = cv, zoom, background
        self.orb_colours = orb_colours or {}
        self.rng = random.Random(7)
        hidden = dict(state="hidden", tags="fx")
        self.waves = (WaveTrail(cv, hidden), WaveTrail(cv, hidden))     # the first icon's, the second's
        self.it_seg = [cv.create_line(0, 0, 0, 0, capstyle="butt", **hidden) for _ in range(SEGMENTS)]
        self.it_beam = cv.create_line(0, 0, 0, 0, capstyle="round", **hidden)
        self.it_flame = [(cv.create_polygon(0, 0, 0, 0, 0, 0, outline="", fill=FLAME, **hidden),
                          cv.create_polygon(0, 0, 0, 0, 0, 0, outline="", fill=FLAME_CORE, **hidden)) for _ in range(3)]
        self.it_part = [cv.create_rectangle(0, 0, 0, 0, outline="", **hidden) for _ in range(MAX_PARTICLES)]
        self.it_ring = [cv.create_oval(0, 0, 0, 0, fill="", **hidden) for _ in range(3)]
        self.shown = {}                                  # item -> its options as last set (None: hidden)
        self.reset()

    def reset(self):
        """Forget everything (a new attempt, or a jump back to a checkpoint)."""
        self.history = deque(maxlen=TRAIL_TICKS + 1)     # (x, y, streak on?, width, colour)
        self.streak_on = False
        for wave in self.waves:
            wave.reset()
        self.particles = []                              # [x, y, vx, vy, life, max life, size, colour, gravity]
        self.flames = []                                 # (x, y, dx, dy, length, half width) per frame
        self.swing_in = 0.0
        self.beam = None                                 # [x0, y0, x1, y1, ticks left, colour]
        self.rings = []                                  # orb rings: [x, y, colour, ticks left]
        self.ufo_burst = 0
        self.log_seen = 0
        self.was_grounded = True
        self.tick_count = 0
        self.swing_flip = None
        self.prev_pos = None

    # ------------------------------------------------------------------ per simulation step
    def tick(self, sim, pressed_now):
        p = sim.player
        self.tick_count += 1
        mode = p.mode
        colour = icons.MODE_COLORS[int(mode)]
        scale = MINI_SCALE if p.mini else 1.0
        g = -1.0 if p.upside_down else 1.0
        half = 15.0 * scale
        a = -math.radians(p.rotation)
        ca, sa = math.cos(a), math.sin(a)

        def local(lx, ly):
            """A point of the icon (x right, y up, hitbox = -1..1) in world units."""
            ly = ly * g
            return p.x + (lx * ca - ly * sa) * half, p.y + (lx * sa + ly * ca) * half

        if len(sim.log) < self.log_seen:
            self.log_seen = len(sim.log)
        events = [text for _tick, text in sim.log[self.log_seen:]]
        self.log_seen = len(sim.log)

        # --- regular trail on/off
        if p.dead:
            self.streak_on = False
        elif mode in FLYING:
            self.streak_on = True
        else:
            launched = any(("orb" in e or "pad" in e or "teleport" in e or "launched" in e
                            or (e.startswith("gravity") and mode != BALL)) for e in events)
            if launched:
                self.streak_on = True
            elif p.can_jump:
                self.streak_on = False
        if p.is_dashing and mode != WAVE:
            self.streak_on = False
        # GD draws the wave's ribbon additively over its solid trail; without transparency it
        # would only hide that trail, so the wave shows the solid trail alone
        ribbon = self.streak_on and mode != WAVE
        if mode == SHIP:
            ax, ay = local(-1.0, -0.3)
        elif mode == UFO:
            ax, ay = local(0.0, -0.5)
        else:
            ax, ay = p.x, p.y
        width = TRAIL_WIDTH * scale * (0.8 if mode == WAVE else 1.0)
        self.history.append((ax, ay, ribbon, width, colour))

        # --- wave trails: remember every corner (the second icon's in its lighter colour,
        # as gd/render_tk.py draws that icon)
        q = sim.player2
        self.waves[0].tick(p, colour)
        self.waves[1].tick(q, blend(icons.MODE_COLORS[int(q.mode)], "#FFFFFF", 0.35) if q is not None else colour)

        # --- spider teleport beam
        if any("teleport" in e for e in events):
            # from where the spider was a step ago (a teleport can happen before the step's
            # own "last position" is taken, so that cannot be used)
            x0, y0 = self.prev_pos or (p.last_x, p.last_y)
            self.beam = [x0, y0, p.x, p.y, BEAM_TICKS, colour]
        self.prev_pos = (p.x, p.y)
        if self.beam:
            self.beam[4] -= 1
            if self.beam[4] <= 0:
                self.beam = None

        # --- a ring from each orb hit this step
        for e in events:
            if e.endswith(" orb"):
                o = self._orb_near(sim)
                x, y = (o.x, o.y) if o is not None else (p.x, p.y)
                self.rings = self.rings[-(len(self.it_ring) - 1):] + [[x, y, self.orb_colours.get(e[:-4], "#FFFFFF"),
                                                                       RING_TICKS]]
        for ring in self.rings:
            ring[3] -= 1
        self.rings = [ring for ring in self.rings if ring[3] > 0]

        # --- particles
        rnd = self.rng.uniform
        grounded = p.can_jump and not p.dead
        if mode in (CUBE, BALL, ROBOT, SPIDER):
            feet = p.y - g * p.half_h
            if grounded and not self.was_grounded:                       # landing
                for _ in range(8):
                    self._emit(p.x + rnd(-10, 6) * scale, feet, rnd(-150, 120), g * rnd(30, 130), 0.32, rnd(2.5, 4.0), colour, g)
            elif grounded and self.tick_count % 4 == 0:                  # sliding
                self._emit(p.x - p.half_w * 0.8, feet, rnd(-120, -30), g * rnd(20, 110), 0.3, rnd(2.0, 3.5), colour, g)
        elif mode == SHIP and not p.dead:
            rx, ry = local(-1.25, -0.36)
            if self.tick_count % 4 == 0:
                self._emit(rx, ry, rnd(-110, -50), rnd(-25, 25), 0.3, rnd(2.0, 3.2), colour, 0.0)
            if p.holding_jump and self.tick_count % 2 == 0:
                self._emit(rx, ry, rnd(-240, -140), -g * rnd(10, 70), 0.26, rnd(2.5, 4.5), FLAME, 0.0)
            if p.on_ground and not p.holding_jump and self.tick_count % 3 == 0:   # dragging along the floor
                self._emit(p.x - 8, p.y - g * p.half_h, rnd(-150, -40), g * rnd(20, 90), 0.28, rnd(2.0, 3.2), colour, g)
        elif mode == UFO and not p.dead:
            ux, uy = local(0.0, -0.8)
            if self.tick_count % 7 == 0:
                self._emit(ux + rnd(-8, 8) * scale, uy, rnd(-40, 10), -g * rnd(40, 100), 0.3, rnd(2.0, 3.2), colour, 0.0)
            if pressed_now:
                self.ufo_burst = UFO_BURST_TICKS
            if self.ufo_burst > 0:
                self.ufo_burst -= 1
                self._emit(ux + rnd(-12, 12) * scale, uy, rnd(-90, 90), -g * rnd(120, 240), 0.28, rnd(2.5, 4.0), colour, 0.0)
        self.was_grounded = grounded or mode in FLYING

        # --- fire: where each flame starts, which way it points, how long it is
        flames = []
        if not p.dead:
            if mode == SHIP and p.holding_jump:
                fx, fy = local(-1.2, -0.36)
                tx, ty = local(-2.2, -0.36)
                flames.append((fx, fy, tx - fx, ty - fy, 15.0 * scale, 4.5 * scale))
            elif mode == ROBOT and p.holding_jump and p.is_jumping and not p.robot_boost_invalidated \
                    and 0.27 < p.robot_boost_charge < ROBOT_BOOST_MAX:
                fx, fy = local(0.0, -1.0)
                flames.append((fx, fy, 0.0, -g, 17.0 * scale, 5.0 * scale))
            elif mode == SWING:
                if self.swing_flip is not None and self.swing_flip != p.upside_down:
                    self.swing_in = 0.0                 # gravity just changed: the flame swaps sides
                self.swing_in = min(1.0, self.swing_in + 0.05)
                bx, by = local(-1.05, 0.0)
                tx, ty = local(-2.0, 0.0)
                flames.append((bx, by, tx - bx, ty - by, 10.0 * scale, 3.5 * scale))
                fx, fy = local(0.0, 0.92)              # the side gravity pulls away from
                tx, ty = local(0.0, 2.0)
                flames.append((fx, fy, tx - fx, ty - fy, 16.0 * scale * self.swing_in, 4.5 * scale))
            if p.is_dashing:
                n = math.hypot(1.0, p.dash_y)
                flames.append((p.x - 8.0 / n, p.y - 8.0 * p.dash_y / n, -1.0, -p.dash_y, 30.0 * scale, 9.0 * scale))
                if self.tick_count % 2 == 0:                # sparks thrown back off the flame
                    back = self.rng.uniform(260, 420)
                    side = self.rng.uniform(-60, 60)
                    self._emit(p.x - 14.0 / n * scale, p.y - 14.0 * p.dash_y / n * scale,
                               -back / n - side * p.dash_y / n, -back * p.dash_y / n + side / n, 0.22,
                               self.rng.uniform(2.0, 3.6), FLAME_CORE if self.tick_count % 4 else FLAME, 0.0)
        if mode != SWING:
            self.swing_in = 0.0
        self.flames = flames
        self.swing_flip = p.upside_down

    @staticmethod
    def _orb_near(sim):
        """The orb an icon is touching (the one that just fired), or None."""
        best, far = None, 40.0 * 40.0
        for q in (sim.player, sim.player2):
            if q is None:
                continue
            for o in sim.query(q.x - 30.0, q.x + 30.0, q.y - 30.0, q.y + 30.0):
                d = (o.x - q.x) ** 2 + (o.y - q.y) ** 2
                if o.kind == "orb" and d < far:
                    best, far = o, d
        return best

    def _emit(self, x, y, vx, vy, life, size, colour, gravity):
        if len(self.particles) < MAX_PARTICLES:
            self.particles.append([x, y, vx, vy, life, life, size, colour, gravity])

    # ------------------------------------------------------------------ per drawn frame
    def _hide(self, item):
        if self.shown.get(item, None) is not None or item not in self.shown:
            self.cv.itemconfigure(item, state="hidden")
            self.shown[item] = None

    def _show(self, item, **options):
        """Show an item with these options - asking Tk only when something changed."""
        key = tuple(sorted(options.items()))
        if self.shown.get(item) != key:
            self.cv.itemconfigure(item, state="normal", **options)
            self.shown[item] = key

    def draw(self, dt, now, camera_left):
        cv, z, bg = self.cv, self.z, self.bg

        # --- regular trail: SEGMENTS pieces, each a little more faded than the one before
        hist = self.history
        n = len(hist)
        step = TRAIL_TICKS // SEGMENTS
        for k, item in enumerate(self.it_seg):
            i0, i1 = n - 1 - k * step, n - 1 - (k + 1) * step
            if i1 < 0:
                self._hide(item)
                continue
            x0, y0, on0, w0, col = hist[i0]
            x1, y1, on1, _w, _c = hist[i1]
            if not (on0 and on1) or abs(x0 - x1) > 80:
                self._hide(item)
                continue
            strength = TRAIL_OPACITY * (1.0 - (k + 0.5) / SEGMENTS)
            cv.coords(item, x0 * z, -y0 * z, x1 * z, -y1 * z)
            self._show(item, fill=blend(bg, col, strength), width=max(1.0, w0 * z))

        # --- wave trails
        for wave in self.waves:
            wave.draw(self, camera_left)

        # --- spider beam
        if self.beam:
            x0, y0, x1, y1, left, colour = self.beam
            strength = left / float(BEAM_TICKS)
            cv.coords(self.it_beam, x0 * z, -y0 * z, x1 * z, -y1 * z)
            self._show(self.it_beam, fill=blend(bg, colour, strength),
                             width=max(1.0, 9.0 * strength * z))
        else:
            self._hide(self.it_beam)

        # --- fire
        for i, (outer, inner) in enumerate(self.it_flame):
            if i >= len(self.flames) or self.flames[i][4] <= 0.5:
                self._hide(outer)
                self._hide(inner)
                continue
            x, y, dx, dy, length, half = self.flames[i]
            d = math.hypot(dx, dy) or 1.0
            dx, dy = dx / d, dy / d
            length *= 0.82 + 0.18 * math.sin(now * 46.0 + i * 2.1)       # flicker
            for item, k in ((outer, 1.0), (inner, 0.55)):
                px, py = -dy * half * k, dx * half * k
                tx, ty = x + dx * length * k, y + dy * length * k
                cv.coords(item, (x + px) * z, -(y + py) * z, tx * z, -ty * z, (x - px) * z, -(y - py) * z)
                self._show(item)

        # --- orb rings
        for i, item in enumerate(self.it_ring):
            if i >= len(self.rings):
                self._hide(item)
                continue
            x, y, colour, left = self.rings[i]
            t = 1.0 - left / float(RING_TICKS)                 # 0 -> 1 as it widens
            ease = 1.0 - (1.0 - t) ** 3
            r = RING_FROM + (RING_TO - RING_FROM) * ease
            cv.coords(item, (x - r) * z, -(y + r) * z, (x + r) * z, -(y - r) * z)
            self._show(item, outline=blend(bg, colour, 1.0 - t), width=max(1.0, round(4.0 * (1.0 - t) * z, 1)))

        # --- particles
        alive = []
        for part in self.particles:
            part[4] -= dt
            if part[4] <= 0:
                continue
            part[3] -= part[8] * 520.0 * dt                              # they fall back down
            part[0] += part[2] * dt
            part[1] += part[3] * dt
            alive.append(part)
        self.particles = alive
        for i, item in enumerate(self.it_part):
            if i >= len(alive):
                self._hide(item)
                continue
            x, y, _vx, _vy, life, max_life, size, colour, _g = alive[i]
            t = life / max_life
            s = size * (0.4 + 0.6 * t) / 2.0
            cv.coords(item, (x - s) * z, -(y + s) * z, (x + s) * z, -(y - s) * z)
            self._show(item, fill=blend(bg, colour, 0.25 + 0.75 * t))
