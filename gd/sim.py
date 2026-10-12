"""Fixed-step simulation: input -> player update -> collisions, 240 times a second.

Step order (camila314/gdp GJBaseGameLayer::update, per sub-step):
  1. queued button presses/releases are applied            (processCommands)
  2. player.update(0.25)  - velocity, then position        (PlayerObject::update)
  3. collisions           - level bounds, then portals / pads / orbs / slopes,
                            then solids, then hazards      (checkCollisions)
  4. player.update_rotation(0.25) - the icon's angle       (PlayerObject::updateRotation)

Which of the player's three boxes each object is tested against:
  hazards, orbs, pads, portals   the outer box.  If the object sits at an angle that is
                                 not a multiple of 90 degrees, its own rotated box must
                                 ALSO overlap the player's rotated box (OSGD
                                 collisionCheckObjects: m_shouldUseOuterOb -> overlaps1Way
                                 both ways).  The hazard loop itself is not in any
                                 decompilation; it is assumed to use the same test.
  solid blocks                   the outer box to land on or bump a face, the inner box
                                 to decide whether running into the block is fatal.

GJBaseGameLayer::checkCollisions itself is not available in any decompilation I
could find.  The pass order (everything else -> solids -> hazards) is from OSGD's
collisionCheckObjects, which defers solids and hazards into separate lists; the
handling of the level's floor/ceiling follows gd-sim.
"""
import hashlib
import math
import struct

from . import constants as C
from . import objects as O
from .constants import f32
from .geometry import (circle_hits_player_fixed, circle_hits_rect_legacy,
                       obb_overlaps_obb, obb_overlaps_rect, rects_touch)
from .player import BALL, CUBE, ROBOT, SPIDER, WAVE, FLYING, Mode, Player

_BOUNDS = {Mode.SHIP: C.BOUNDS_SHIP, Mode.UFO: C.BOUNDS_SHIP, Mode.WAVE: C.BOUNDS_SHIP,
           Mode.SWING: C.BOUNDS_SWING, Mode.BALL: C.BOUNDS_BALL, Mode.SPIDER: C.BOUNDS_SPIDER}
_FLIP_GRACE_TICKS = C.FLIP_GRACE_SECONDS * C.TPS
_SPIDER_GRACE_TICKS = C.SPIDER_GRACE_SECONDS * C.TPS
_SOLIDS = (O.SOLID, O.BREAKABLE)
_HAZARDS = (O.HAZARD, O.SAW)
_SLOPES = (O.SLOPE, O.SLOPE_HAZARD)


def _pos(v):
    return f32(v) if C.FLOAT32_POSITIONS else v


class Simulation:
    def __init__(self, level):
        self.level = level
        slopes = [o for o in level.objects if o.kind in _SLOPES]
        # OSGD collisionCheckObjects tests slopes against a rect twice their size
        self._margin = max([max(o.w, o.h) / 2.0 for o in slopes] + [2.0])
        self.reset()

    # ------------------------------------------------------------------ lifecycle
    def reset(self):
        lv = self.level
        self.tick = 0
        self.button = False
        self.finished = False
        self.destroyed = set()            # uids of broken breakable blocks
        self.log = []                     # (tick, text) - what the player touched
        self._launch = None
        p = self.player = Player(self)
        p.fix_gravity_bug = lv.fix_gravity_bug
        p.mode = Mode(lv.start_mode)
        p.mini = lv.start_mini
        p.upside_down = lv.start_flipped
        p.update_time_mod(lv.start_speed)
        p.refresh_size()
        if p.mode in (ROBOT, SPIDER):
            p.robot_boost_charge = C.ROBOT_BOOST_MAX
        # gd-sim: the player always starts at (0, 15); floor 0, ceiling = the mode's bounds
        p.x = p.last_x = p.rot_x = 0.0
        p.y = p.last_y = p.rot_y = 15.0
        self.floor = 0.0
        self.ceiling = _BOUNDS.get(p.mode)

    def snapshot(self):
        return (self.tick, self.button, self.finished, set(self.destroyed), self.floor,
                self.ceiling, self.player.copy(), len(self.log))

    def restore(self, snap):
        (self.tick, self.button, self.finished, destroyed, self.floor, self.ceiling,
         player, log_len) = snap
        self.destroyed = set(destroyed)
        self.player = player.copy()
        del self.log[log_len:]

    @property
    def dead(self):
        return self.player.dead

    @property
    def time(self):
        return self.tick / C.TPS

    # ------------------------------------------------------------------ stepping
    def step(self, pressed):
        """Advance exactly one 1/240 s step with the jump button up or down."""
        p = self.player
        if p.dead or self.finished:
            return
        pressed = bool(pressed)
        if pressed != self.button:
            self.button = pressed
            if pressed:
                p.push_button()
            else:
                p.release_button()
        p.update(C.DT)
        self._collide(p)
        if not p.dead:
            p.update_rotation(C.DT)
        self.tick += 1
        if not p.dead and p.x >= self.level.end_x:
            self.finished = True

    def run(self, inputs, max_ticks=None, trace=None):
        """Play an InputSequence until death, the end of the level or max_ticks."""
        if max_ticks is None:
            max_ticks = int(self.level.end_x / (C.SPEED_TABLE[0][0] * C.SPEED_TABLE[0][3] * C.DT)) + C.TPS
        states = inputs.iter_states() if inputs is not None else None
        while self.tick < max_ticks and not self.player.dead and not self.finished:
            self.step(next(states) if states else False)
            if trace is not None:
                trace.append(self.state())
        return self

    def state(self):
        p = self.player
        return (self.tick, p.x, p.y, p.y_vel, int(p.mode), p.speed, p.upside_down, p.mini,
                p.can_jump, p.dead)

    def digest(self, trace):
        """Stable hash of a trace - two runs are identical iff the digests match."""
        h = hashlib.sha256()
        for row in trace:
            h.update(struct.pack("<iddd4i2?", row[0], row[1], row[2], row[3], row[4], row[5],
                                 int(row[6]), int(row[7]), row[8], row[9]))
        return h.hexdigest()

    def _note(self, text):
        self.log.append((self.tick, text))

    # ------------------------------------------------------------------ collisions
    def _collide(self, p):
        p.was_on_slope = p.is_on_slope          # PlayerObject::preCollision
        p.is_on_slope = False

        self._bounds(p)
        if p.dead:
            return

        m = self._margin
        hw, hh = p.half_w, p.half_h
        solids, hazards = [], []
        self._launch = None
        for o in self.level.query(p.x - hw - m, p.x + hw + m, p.y - hh - m, p.y + hh + m):
            kind = o.kind
            if kind in _SOLIDS:
                if o.uid not in self.destroyed:
                    solids.append(o)
            elif kind in _HAZARDS:
                hazards.append(o)
            elif o.uid not in p.used:
                hw, hh = p.half_w, p.half_h
                minx, maxx, miny, maxy = p.x - hw, p.x + hw, p.y - hh, p.y + hh
                if kind in _SLOPES:
                    if rects_touch(minx, maxx, miny, maxy, o.x - o.w, o.x + o.w, o.y - o.h, o.y + o.h):
                        self._slope(p, o)
                elif rects_touch(minx, maxx, miny, maxy, o.minx, o.maxx, o.miny, o.maxy):
                    if o.corners is not None and not obb_overlaps_obb(o.corners, p.oriented_box()):
                        continue
                    self._touch(p, o)
                if p.dead:
                    return

        if self._launch is not None and not p.is_on_slope:
            # topped out on an uphill slope and no further slope caught the player:
            # it is thrown off (gd-sim Slope::calc, speed from gdp collidedWithSlopeInternal)
            p.y = _pos(self._launch)
            p.set_y_velocity(p.modified_slope_velocity())
            p.is_jumping = True
            p.can_jump = False
            p.on_ground = False
            p.slope = None
            self._note("launched off slope")

        # gd-sim resolves blocks in reverse collection order (later sections first)
        solids.sort(key=lambda o: (-(o.x // C.SECTION_SIZE), -o.uid))
        for o in solids:
            self._solid(p, o, o.minx, o.maxx, o.miny, o.maxy)
            if p.dead:
                return

        hw, hh = p.half_w, p.half_h
        minx, maxx, miny, maxy = p.x - hw, p.x + hw, p.y - hh, p.y + hh
        rotated = None                          # the player's rotated box, built only if needed
        for o in hazards:
            if o.kind == O.SAW:
                if self.level.fix_radius_collision:
                    hit = circle_hits_player_fixed(o.x, o.y, o.radius, p.x, p.y, hw * 2.0)
                else:
                    hit = circle_hits_rect_legacy(o.x, o.y, o.radius, minx, maxx, miny, maxy)
            else:
                hit = rects_touch(minx, maxx, miny, maxy, o.minx, o.maxx, o.miny, o.maxy)
                if hit and o.corners is not None:
                    if rotated is None:
                        rotated = p.oriented_box()
                    hit = obb_overlaps_obb(o.corners, rotated)
            if hit:
                p.die("hazard (object id %d at x=%.0f)" % (o.id, o.x))
                return

        if p.was_on_slope and not p.is_on_slope:
            self._left_slope(p)

    # -- level floor / ceiling -------------------------------------------------
    def _bounds(self, p):
        """Ground and mode ceiling.  Behaviour from gd-sim (Player::postCollision and
        the per-vehicle clamp functions)."""
        up = p.upside_down
        # gd-sim's wave keeps its centre a full hitbox away from the ground
        ext = p.half_h * 2.0 if p.mode == WAVE else p.half_h
        floor, ceiling = self.floor, self.ceiling

        if p.y - ext <= floor:
            if p.mode == WAVE:
                p.y = _pos(floor + ext)
            elif not up:
                if p.y_vel <= 0.0:
                    p.y = _pos(floor + ext)
                    p.hit_ground()
            elif p.y - ext < floor:
                if ceiling is None:
                    # EST (gd-sim): a flipped cube/robot that reaches the ground dies
                    p.die("hit the ground upside down")
                    return
                p.y = _pos(floor + ext)
                if p.y_vel < 0.0:
                    p.y_vel = 0.0
        if ceiling is not None and p.y + ext >= ceiling:
            if p.mode == WAVE:
                p.y = _pos(ceiling - ext)
            elif up:
                if p.y_vel >= 0.0:
                    p.y = _pos(ceiling - ext)
                    p.hit_ground()
            elif p.y + ext > ceiling:
                p.y = _pos(ceiling - ext)
                if p.y_vel > 0.0:
                    p.y_vel = 0.0
        if p.y > self.level.max_y:
            p.die("left the top of the level")

    # -- portals, pads, orbs -----------------------------------------------------
    def _touch(self, p, o):
        kind = o.kind
        if kind == O.ORB:
            if p.ring_jump(o):
                self._note("%s orb" % o.arg)
        elif kind == O.PAD:
            if o.arg == "blue":
                if p.gravity_bump(o):
                    p.used.add(o.uid)
                    self._note("blue pad")
            else:
                p.used.add(o.uid)
                p.bump(o)
                self._note("%s pad" % o.arg)
        elif kind == O.PORTAL_GRAVITY:
            p.used.add(o.uid)
            target = {"normal": False, "flipped": True}.get(o.arg, not p.upside_down)
            if p.flip_gravity(target):
                self._note("gravity %s" % ("flipped" if target else "normal"))
        elif kind == O.PORTAL_MODE:
            p.used.add(o.uid)
            mode = Mode(o.arg)
            if p.set_mode(mode):
                self._note("mode -> %s" % mode.name.lower())
            # gd-sim VehiclePortal.cpp: the play area is re-centred on every mode portal
            bounds = _BOUNDS.get(mode)
            if bounds is None:
                self.floor, self.ceiling = 0.0, None
            else:
                self.floor = max(0.0, 30.0 * math.ceil((o.y - (bounds / 2.0 + 30.0)) / 30.0))
                self.ceiling = self.floor + bounds
        elif kind == O.PORTAL_SIZE:
            p.used.add(o.uid)
            if p.mini != o.arg:
                p.set_mini(o.arg)
                self._note("size -> %s" % ("mini" if o.arg else "normal"))
        elif kind == O.PORTAL_SPEED:
            p.used.add(o.uid)
            if p.speed != o.arg:
                p.update_time_mod(o.arg)
                self._note("speed -> %s" % C.SPEED_NAMES[o.arg])

    # -- solids --------------------------------------------------------------------
    def _solid(self, p, o, ominx, omaxx, ominy, omaxy):
        """camila314/gdp PlayerObject::collidedWithObjectInternal, classic mode, static
        objects.  Three outcomes: land on the surface facing the player's feet, bump
        the head (flying modes and ball only), or die when the small inner hitbox
        (0.3 x size) reaches the block."""
        hw, hh = p.half_w, p.half_h
        pminx, pmaxx, pminy, pmaxy = p.x - hw, p.x + hw, p.y - hh, p.y + hh
        if not rects_touch(pminx, pmaxx, pminy, pmaxy, ominx, omaxx, ominy, omaxy):
            return
        if o.corners is not None:
            # EST: blocks at odd angles are only tested for the lethal overlap
            if obb_overlaps_rect(o.corners, p.x - hw * 0.3, p.x + hw * 0.3, p.y - hh * 0.3, p.y + hh * 0.3):
                self._crush(p, o)
            return

        s = p.slope
        if s is not None and o.kind in _SOLIDS and (p.is_on_slope or p.was_on_slope):
            # gdp: while on a slope, blocks touching it (rect scaled 1.2 x 1.1) that do not
            # rise above its surface are skipped, so the block at the top of a ramp does
            # not catch the player before the ramp can launch it.
            if rects_touch(ominx, omaxx, ominy, omaxy, s.x - s.w * 0.6, s.x + s.w * 0.6,
                           s.y - s.h * 0.55, s.y + s.h * 0.55):
                ex = min(max(o.x, s.minx), s.maxx)
                r = s.h / s.w
                surf = s.miny + (ex - s.minx) * r if s.slope_rises else s.maxy - (ex - s.minx) * r
                if (ominy >= surf - 2.0) if s.slope_is_ceiling else (omaxy <= surf + 2.0):
                    return

        up = p.upside_down
        fly = p.mode in FLYING
        thr = C.SNAP_THRESHOLD_FLY if fly else C.SNAP_THRESHOLD
        dy = p.y - p.last_y
        yv = p.y_vel
        g = -1.0 if up else 1.0
        lethal = dy * g > 0.0
        head_candidate = False

        if p.mode == WAVE:
            lethal = True
        else:
            bumps_head = (fly or p.mode == BALL) and not o.passable
            # --- the block's TOP face against the player's lower edge
            if up and not bumps_head:
                lethal = True
            else:
                low = pminy + thr
                if low < omaxy and low - dy < omaxy:
                    lethal = True                      # the top is out of reach: maybe a wall
                elif yv > 0.0:
                    lethal = False                     # moving up through the top zone: ignored
                else:
                    p.y = _pos(omaxy + hh)
                    if not up:
                        self._land(p, o)
                    elif p.y_vel < 0.0:
                        p.y_vel = 0.0
                    return
            # --- the block's BOTTOM face against the player's upper edge
            if lethal and (up or bumps_head):
                high = pmaxy - thr
                if not (ominy < high and ominy < high - dy):
                    if yv >= 0.0:
                        p.y = _pos(ominy - hh)
                        if up:
                            self._land(p, o)
                        elif p.y_vel > 0.0:
                            p.y_vel = 0.0
                        return
            if up:
                head_candidate = (pmaxy - thr >= omaxy) or (pmaxy - thr - dy >= omaxy)
            else:
                head_candidate = (pminy + thr <= ominy) or (pminy + thr - dy <= ominy)

        if not lethal or o.passable:
            return
        k = C.INNER_HITBOX_SCALE
        iminx, imaxx, iminy, imaxy = p.x - hw * k, p.x + hw * k, p.y - hh * k, p.y + hh * k
        if not rects_touch(iminx, imaxx, iminy, imaxy, ominx, omaxx, ominy, omaxy):
            return
        # The gravity-flip grace (see constants.FLIP_GRACE_PUSH).  It is not available to
        # the rating search: like a buffered jump, it is a quirk a level should not depend on.
        if (C.FLIP_GRACE_PUSH and not p.strict_taps and not p.no_flip_push and head_candidate
                and self.tick - p.last_flip_tick < _FLIP_GRACE_TICKS):
            p.y = _pos(omaxy + hh) if up else _pos(ominy - hh)      # just flipped: pushed out, not killed
            p.hit_ground()
            p.on_ground = False
            self._note("pushed out of a block (gravity flip grace)")
            return
        if p.mode == SPIDER and omaxx < imaxx and self.tick - p.last_spider_tick < _SPIDER_GRACE_TICKS:
            return
        self._crush(p, o)

    def _crush(self, p, o):
        if o.kind == O.BREAKABLE:
            self.destroyed.add(o.uid)
            self._note("broke block")
        else:
            p.die("ran into a block (object id %d at x=%.0f)" % (o.id, o.x))

    def _land(self, p, o):
        if p.mode == CUBE and o.kind in _SOLIDS:
            p.check_snap_jump_to_object(o)
        p.hit_ground()

    # -- slopes --------------------------------------------------------------------
    def _slope(self, p, o):
        """APPROXIMATE.  The geometry (slopeYPos, the 1/cos radius, the clamps, the
        launch speed) is from camila314/gdp collidedWithSlopeInternal + OSGD
        preSlopeCollision; the attach / stick / release rules are simplified and
        follow gd-sim where the decompilation was too tangled to port faithfully."""
        hw, hh = p.half_w, p.half_h
        pminx, pmaxx, pminy, pmaxy = p.x - hw, p.x + hw, p.y - hh, p.y + hh
        attached = p.was_on_slope and p.slope is o

        # 1. the slope's upright side and its flat side behave like 1-unit thick blocks
        #    (OSGD preSlopeCollision).  Skipped for the slope the player is riding.
        if not attached:
            hit_side = False
            if o.orient in (1, 2) and p.x < o.minx:
                if rects_touch(pminx, pmaxx, pminy, pmaxy, o.minx, o.minx + 1.0, o.miny, o.maxy):
                    self._solid(p, o, o.minx, o.minx + 1.0, o.miny, o.maxy)
                    hit_side = True
            if o.slope_is_ceiling:
                if p.y >= o.maxy and rects_touch(pminx, pmaxx, pminy, pmaxy, o.minx, o.maxx, o.maxy - 1.0, o.maxy):
                    self._solid(p, o, o.minx, o.maxx, o.maxy - 1.0, o.maxy)
                    hit_side = True
            elif p.y <= o.miny + 1.0 and rects_touch(pminx, pmaxx, pminy, pmaxy, o.minx, o.maxx, o.miny, o.miny + 1.0):
                self._solid(p, o, o.minx, o.maxx, o.miny, o.miny + 1.0)
                hit_side = True
            if hit_side or p.dead:
                return
            pminy, pmaxy = p.y - hh, p.y + hh
        if not rects_touch(pminx, pmaxx, pminy, pmaxy, o.minx, o.maxx, o.miny, o.maxy):
            return

        # 2. where the centre must be to rest on the diagonal (gdp slopeYPos + clamps)
        up = p.upside_down
        g = -1.0 if up else 1.0
        ratio = o.h / o.w
        angle = math.atan(ratio)
        if o.slope_rises:
            surface = o.miny + (p.x - o.minx) * ratio
        else:
            surface = o.maxy - (p.x - o.minx) * ratio
        hazard = o.kind == O.SLOPE_HAZARD
        radius = hh / math.cos(angle)
        if o.slope_is_ceiling:
            side = -1.0                                   # free space is below the diagonal
            if hazard:
                surface -= C.SLOPE_HAZARD_OFFSET
            limit = o.miny - hh
            target = max(surface - radius, limit)
            target = min(target, o.maxy)
        else:
            side = 1.0
            if hazard:
                surface += C.SLOPE_HAZARD_OFFSET
            limit = o.maxy + hh
            target = min(surface + radius, limit)
            target = max(target, o.miny)
        inside = side * p.y < side * target
        walkable = side == g
        uphill = (o.orient == 0) if not up else (o.orient == 3)

        if not walkable:
            # the diagonal is on the head side
            if not inside:
                return
            if hazard or p.mode == WAVE:
                p.die("slope (object id %d at x=%.0f)" % (o.id, o.x))
            elif p.mode in FLYING or p.mode == BALL:
                p.y = _pos(target)
                if side * p.y_vel < 0.0:
                    p.y_vel = 0.0
            elif side * (target - p.y) <= 2.0:
                p.y = _pos(target)
                p.set_y_velocity(side * max(side * p.y_vel, 2.0))
                p.can_jump = False
                p.on_ground = False
            else:
                p.die("hit a slope head first (object id %d at x=%.0f)" % (o.id, o.x))
            return

        collided = inside
        if not collided and attached and not uphill and g * p.y_vel <= 0.0 and p.x <= o.maxx:
            collided = True                               # keep following a slope downhill
        if not collided:
            return
        if hazard or p.mode == WAVE:
            p.die("slope (object id %d at x=%.0f)" % (o.id, o.x))
            return

        slope_y_vel = o.h * p.x_vel / o.w
        launch = min(C.SLOPE_VEL_FACTOR / angle, C.SLOPE_VEL_CAP) * slope_y_vel
        if p.mode in FLYING or p.mode == BALL:
            launch *= C.SLOPE_FLY_BALL_MOD
        p.slope_velocity = launch * g * (1.0 if uphill else -1.0)

        if attached and uphill and target == limit:
            self._launch = target         # top reached; decided after all slopes are checked
            return

        before = p.y_vel
        p.y = _pos(target)
        p.hit_ground()
        if g * before > 5.0:
            p.set_y_velocity(before)                      # a pad/orb boost survives the contact
            p.can_jump = False
        if not p.was_on_slope:
            p.slope_start_tick = self.tick
        p.slope = o
        p.is_on_slope = True

    def _left_slope(self, p):
        o = p.slope
        if o is None:
            return
        up = p.upside_down
        uphill = (o.orient == 0) if not up else (o.orient == 3)
        if not uphill and not p.is_jumping and p.x > o.maxx:
            # ran off the low end of a downhill slope: keep the slope's direction (gd-sim "falls")
            g = -1.0 if up else 1.0
            p.set_y_velocity(-g * p.x_vel * (o.h / o.w))
            p.can_jump = False
        p.slope = None

    # -- spider --------------------------------------------------------------------
    def spider_teleport(self, p):
        """ESTIMATE.  spiderTestJumpInternal is not decompiled anywhere I found.  This
        flips gravity and moves the spider straight to the first thing above (or
        below) it in its column: a solid or the mode's ceiling/floor.  If a hazard
        comes first the spider is placed on it and dies."""
        up = p.upside_down
        direction = -1.0 if up else 1.0               # towards the surface it will land on
        hw, hh = p.half_w, p.half_h
        edge = p.y + direction * hh
        far = self.level.max_y if direction > 0 else -30.0
        best, best_obj = None, None
        for o in self.level.query(p.x - hw + 1.0, p.x + hw - 1.0, min(edge, far), max(edge, far)):
            if o.kind in _SOLIDS:
                if o.uid in self.destroyed:
                    continue
            elif o.kind not in _HAZARDS:
                continue
            face = o.miny if direction > 0 else o.maxy
            dist = (face - edge) * direction
            if dist >= -1e-3 and (best is None or dist < best):
                best, best_obj = dist, o
        bound = self.ceiling if direction > 0 else self.floor
        bound_dist = None if bound is None else (bound - edge) * direction

        p.flip_gravity(not up)
        p.last_spider_tick = self.tick
        p.is_jumping = False
        if best is not None and (bound_dist is None or best <= bound_dist):
            p.y = _pos(edge + direction * best - direction * hh)
            if best_obj.kind in _HAZARDS:
                p.die("teleported into a hazard (object id %d)" % best_obj.id)
                return
            p.y_vel = 0.0
        elif bound_dist is not None:
            p.y = _pos(bound - direction * hh)
            p.y_vel = 0.0
        self._note("spider teleport")
