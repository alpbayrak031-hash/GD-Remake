"""Player state and per-mode physics.

This is a port of the classic-mode (non-platformer) paths of these 2.2 functions,
as reconstructed in StarryDawn72/open-source-gd-project (MIT):
  PlayerObject::update, updateJump, setYVelocity, flipGravity, ringJump,
  propellPlayer, bumpPlayer, startDashing, limitDashRotation,
  checkSnapJumpToObject, playerIsFallingBugged, updateJumpVariables,
  toggleFlyMode / BirdMode / DartMode / SwingMode / RobotMode / SpiderMode.
Member names follow that project (m_canJump -> can_jump, m_isJumpUnused ->
jump_unused, ...).  Places where I had to infer behaviour are marked "INFERRED".
"""
import math
from enum import IntEnum

from . import constants as C
from .constants import f32
from .geometry import obb_corners, slerp2d


class Mode(IntEnum):
    CUBE = 0
    SHIP = 1
    BALL = 2
    UFO = 3
    WAVE = 4
    ROBOT = 5
    SPIDER = 6
    SWING = 7


CUBE, SHIP, BALL, UFO, WAVE, ROBOT, SPIDER, SWING = Mode
FLYING = frozenset((SHIP, UFO, WAVE, SWING))
_HALVING = FLYING                     # modes whose toggle function halves the velocity
_KEY = {SHIP: "ship", UFO: "ufo", BALL: "ball", ROBOT: "robot", SPIDER: "spider"}


def round3(v):
    """PlayerObject::setYVelocity: keep three decimals, C round() (half away from zero)."""
    whole = int(v)                    # truncates toward zero like the C cast
    if whole != v:
        frac = (v - whole) * 1000.0
        frac = math.floor(frac + 0.5) if frac >= 0 else -math.floor(-frac + 0.5)
        v = whole + frac / 1000.0
    return v


def _pick(table, mode, mini):
    key = _KEY.get(mode, "default")
    if mini and key + "_mini" in table:
        return table[key + "_mini"]
    return table.get(key, table["default"])


def _snap360(a):
    """Normalise an angle to (-180, 180]."""
    a = math.fmod(a, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    return a


class Player:
    def __init__(self, world=None):
        self.world = world            # Simulation (for spider teleports and the tick counter)
        self.x = 0.0
        self.y = 15.0
        self.last_x = 0.0
        self.last_y = 15.0
        self.y_vel = 0.0
        self.mode = CUBE
        self.mini = False
        self.upside_down = False
        self.fix_gravity_bug = False
        # input
        self.holding_jump = False
        self.was_holding_jump = False
        self.jump_unused = False              # a click that nothing has consumed yet
        self.holding_release_delayed = False
        self.can_ring_jump = False
        self.touched_ring = False
        # ground / air state
        self.can_jump = False
        self.on_ground = False
        self.is_jumping = False               # rising from a jump, pad or orb ("boosted")
        self.velocity_uncapped = False        # red pad/orb, black orb: flying clamps are off
        self.robot_boost_charge = 0.0
        self.robot_boost_invalidated = False
        # dash orbs
        self.is_dashing = False
        self.dash_y = 0.0
        self.dash_angle = 0.0
        # slopes
        self.is_on_slope = False
        self.was_on_slope = False
        self.slope = None
        self.slope_velocity = 0.0
        self.slope_start_tick = 0
        # cube stair snapping
        self.snap_obj = None
        self.snap_distance = 0.0
        # timers (in ticks)
        self.last_flip_tick = -10 ** 9
        self.last_spider_tick = -10 ** 9
        # misc
        # rotation: degrees, clockwise.  Part of the simulation - it turns the rotated hitbox.
        self.rotation = 0.0
        self.rotation_speed = 0.0             # degrees per second; 0 when nothing is spinning it
        self.is_rotating = False              # the cube is turning in the air
        self.ball_air_spin = False            # the ball is spinning from a gravity switch or an orb
        self.rot_x, self.rot_y = 0.0, 15.0    # where the player was at the last rotation update
        self.dead = False
        self.death_cause = ""
        self.used = frozenset()               # uids of objects already activated (shared by copies: never
                                              # changed in place, only replaced - snapshots stay cheap)
        self.on_pads = frozenset()            # multi-activate pads touched at the last collision check
        # Analysis aid, off in normal play: a jump that starts because the button was
        # ALREADY down when the player landed (a "buffered" jump) counts as a failure,
        # and the gravity-flip grace does not save the player (gd/sim.py _solid).
        # See gd/analysis.py for why the difficulty rating needs this.
        self.strict_taps = False
        self.no_flip_push = False             # the bot's middle setting: held jumps yes, that grace no
        self.half_w = self.half_h = 15.0
        self.update_time_mod(1)

    # ------------------------------------------------------------------ basics
    def copy(self):
        other = Player.__new__(Player)
        other.__dict__.update(self.__dict__)
        return other

    def flip_mod(self):
        return -1.0 if self.upside_down else 1.0

    def is_flying(self):
        return self.mode in FLYING

    def _tick(self):
        return self.world.tick if self.world is not None else 0

    def refresh_size(self):
        base = C.WAVE_SIZE if self.mode == WAVE else C.SPIDER_SIZE if self.mode == SPIDER else C.PLAYER_SIZE
        size = base * (C.MINI_SCALE if self.mini else 1.0)
        self.half_w = self.half_h = size / 2.0

    def update_time_mod(self, index):
        """GDP PlayerObject::updateTimeMod - what a speed portal changes."""
        self.speed = index
        self.player_speed, self.y_start, self.gravity, self.speed_mult = C.SPEED_TABLE[index]
        # getCurrentXVelocity(): a float * float product, so it is rounded to single precision
        self.x_vel = f32(self.player_speed * self.speed_mult)
        if self.mode == BALL:
            self.run_rotate_action()          # the ball's spin depends on the speed

    # ------------------------------------------------------------------ the three hitboxes
    # Each returns world coordinates.  See gd/constants.py ("hitboxes") for what uses which.
    def outer_rect(self):
        """Red in the hitbox view.  (minx, maxx, miny, maxy); never rotates."""
        hw, hh = self.half_w, self.half_h
        return self.x - hw, self.x + hw, self.y - hh, self.y + hh

    def inner_rect(self):
        """Blue in the hitbox view: the small box that decides death against solid blocks."""
        hw, hh = self.half_w * C.INNER_HITBOX_SCALE, self.half_h * C.INNER_HITBOX_SCALE
        return self.x - hw, self.x + hw, self.y - hh, self.y + hh

    def oriented_box(self):
        """Dark red in the hitbox view: the outer box turned with the icon, as 4 corners.
        INFERRED: GameObject::updateOrientedBox is in neither decompilation; the size is
        taken to be the outer rect's and the angle is PlayerObject::getObjectRotation(),
        which OSGD shows is simply getRotation()."""
        return obb_corners(self.x, self.y, self.half_w * 2.0, self.half_h * 2.0, self.rotation)

    def set_y_velocity(self, v):
        self.y_vel = round3(v)

    def add_y_velocity(self, v):
        self.y_vel = round3(self.y_vel + v)

    def is_falling(self, y_velocity):
        """OSGD playerIsFalling(yVelocity)."""
        return -y_velocity < self.y_vel if self.upside_down else self.y_vel < y_velocity

    def is_falling_bugged(self):
        """OSGD playerIsFallingBugged.  "Falling" means slower than +2*gravity, and
        with flipped gravity the legacy code compares against the same POSITIVE
        threshold - that asymmetry is the well known upside-down physics quirk.
        Levels with "Fix Gravity Bug" (kA32) and the swing use the mirrored test."""
        threshold = self.gravity + self.gravity
        if self.mode == SWING or self.fix_gravity_bug:
            return self.y_vel > -threshold if self.upside_down else self.y_vel < threshold
        return self.y_vel > threshold if self.upside_down else self.y_vel < threshold

    def die(self, cause):
        if not self.dead:
            self.dead = True
            self.death_cause = cause

    # ------------------------------------------------------------------ input
    def push_button(self):
        self.holding_jump = True
        self.jump_unused = True
        # INFERRED: PlayerObject::pushButton is not in either decomp.  gd-sim applies
        # one step of gravity to a jump started by a fresh click but not to one started
        # by a held button, which is exactly what an immediate updateJump(0) on press
        # produces (older decompilations of pushButton contain that call).
        self.update_jump(0.0)

    def release_button(self):
        self.holding_jump = False
        if self.is_dashing:
            self.stop_dashing()
        if self.mode == ROBOT:
            # INFERRED (releaseButton is not decompiled): letting go ends the robot's
            # boost for this jump; pressing again in mid-air must not resume it.
            self.robot_boost_invalidated = True

    # ------------------------------------------------------------------ state changes
    def flip_gravity(self, flip):
        """OSGD PlayerObject::flipGravity."""
        if self.upside_down == flip:
            return False
        self.upside_down = flip
        self.last_flip_tick = self._tick()
        self.y_vel *= C.GRAVITY_FLIP_KEEP          # plain multiply, not rounded
        self.can_jump = False
        if self.mode == BALL:
            self.stop_rotation()
            self.run_ball_rotation2()
        return True

    def set_mode(self, mode):
        """switchedToMode + the toggleXMode pair.  Every toggle of ship, ufo, wave or
        swing (on OR off) halves the velocity, so ship -> ufo quarters it."""
        mode = Mode(mode)
        old = self.mode
        if old == mode:
            return False
        for m in (old, mode):
            if m in _HALVING:
                self.y_vel *= C.MODE_SWITCH_KEEP
                self.on_ground = False
                self.can_jump = False
        if mode in (ROBOT, SPIDER):
            self.robot_boost_charge = C.ROBOT_BOOST_MAX
        self.mode = mode
        # every toggleXMode calls stopRotation(); all but the ball's also setRotation(0).
        # (Leaving the ball upside down really sets 180 - the same box, so 0 is used.)
        self.stop_rotation()
        if mode != BALL:
            self.rotation = 0.0
        self.refresh_size()
        return True

    def set_mini(self, mini):
        self.mini = bool(mini)
        self.refresh_size()

    def hit_ground(self):
        """Landing: velocity is zeroed and the player may jump again."""
        self.y_vel = 0.0
        self.can_jump = True
        self.on_ground = True
        self.is_jumping = False

    # ------------------------------------------------------------------ per-step update
    def update(self, dt=C.DT):
        """OSGD PlayerObject::update (classic branch)."""
        if self.y_vel > 1000.0:
            self.y_vel = 1000.0
        elif self.y_vel < -1000.0:
            self.y_vel = -1000.0
        self.last_x, self.last_y = self.x, self.y

        scaled = C.DT_SCALED if dt == C.DT else f32(dt * 0.9)
        self.update_jump(scaled)
        if self.is_dashing:
            self.set_y_velocity(0.0)

        dx = self.x_vel * dt
        dy = self.y_vel * scaled
        if self.mode == WAVE and not self.is_dashing:
            dy = abs(dx) * self.flip_mod() * (1.0 if self.holding_jump else -1.0)
            if self.mini:
                dy += dy
        elif self.is_dashing:
            dy = dx * self.dash_y

        if C.FLOAT32_POSITIONS:
            self.x = f32(self.x + f32(dx))
            self.y = f32(self.y + f32(dy))
        else:
            self.x += dx
            self.y += dy

        # updateJumpVariables
        self.holding_release_delayed = self.holding_jump
        self.can_ring_jump = self.jump_unused
        self.touched_ring = False

    def update_jump(self, dt):
        """OSGD PlayerObject::updateJump (classic branch; gravity triggers not modelled)."""
        mode = self.mode
        up = self.upside_down
        flip = -1.0 if up else 1.0
        holding = self.holding_jump
        mini = self.mini
        is_holding_jump = holding
        if mode == ROBOT and not self.jump_unused:
            is_holding_jump = False
        flying = mode in FLYING
        gravity = C.GRAVITY_FLYING if (flying or mode == BALL or mode == SPIDER) else self.gravity
        scale_mod = C.MINI_JUMP_MOD if mini else 1.0

        if flying:
            if mini:
                scale_mod = C.MINI_FLY_MOD
            max_y = C.FLY_MAX_UP / scale_mod
            min_y = -C.FLY_MAX_DOWN / scale_mod
            v = self.y_vel
            if not up:
                recap = (0.0 <= v < max_y) or (min_y < v <= 0.0)
            else:
                recap = (-max_y < v <= 0.0) or (0.0 <= v < -min_y)
            if recap:
                self.velocity_uncapped = False

            if mode == SHIP:
                if holding and not self.velocity_uncapped:
                    ship_mod = C.SHIP_THRUST
                else:
                    if self.velocity_uncapped:
                        if up:
                            ship_mod = C.SHIP_THRUST if self.y_vel > 0.0 else C.SHIP_FALL
                        else:
                            ship_mod = C.SHIP_THRUST if self.y_vel < 0.0 else C.SHIP_FALL
                    else:
                        ship_mod = C.SHIP_FALL
                    if not holding and not self.is_falling_bugged():
                        ship_mod = C.SHIP_FALL_RISING
                boost_mod = C.SHIP_BOOST_HOLD_FALLING if (holding and self.is_falling_bugged()) else C.SHIP_BOOST
                self.add_y_velocity(-((gravity * dt * flip * ship_mod * boost_mod) / scale_mod))
                if holding:
                    self.on_ground = False

            elif mode == UFO:
                if self.jump_unused and holding:
                    self.jump_unused = False
                    jump = (C.UFO_JUMP_MINI if mini else C.UFO_JUMP) * flip * scale_mod
                    if (up and jump < self.y_vel) or (not up and jump > self.y_vel):
                        self.set_y_velocity(jump)
                        if (self.was_on_slope or self.is_on_slope) and self.slope_velocity > 0.0:
                            maximum = self.y_vel * C.SLOPE_JUMP_CAP
                            self.add_y_velocity(self.slope_velocity * 0.5)
                            self.set_y_velocity(min(self.y_vel, maximum))
                bird_mod = C.UFO_GRAV_FALL if self.is_falling_bugged() else C.UFO_GRAV_RISE
                self.add_y_velocity(-((gravity * dt * flip * bird_mod * 0.5) / scale_mod))
                if holding:
                    self.on_ground = False

            elif mode == WAVE:
                # the real movement is computed in update(); this value only matters
                # when leaving the wave (it is what gets halved by the mode switch)
                self.set_y_velocity(self.x_vel * flip * (1.0 if holding else -1.0))

            else:  # SWING
                if self.jump_unused and holding:
                    self.jump_unused = False
                    before = self.y_vel
                    self.flip_gravity(not up)
                    self.set_y_velocity(before * C.SWING_FLIP_KEEP)
                    up = self.upside_down
                    flip = -flip
                swing_mod = C.SWING_GRAV_MINI if mini else C.SWING_GRAV
                self.add_y_velocity(-(swing_mod * (gravity * dt * flip)))
                if holding:
                    self.on_ground = False
                scale_mod = 1.0

            mode_scale = 1.0 if mode == SWING else C.FLY_MAX_DOWN / C.FLY_MAX_UP   # 0.8
            if not self.velocity_uncapped and mode != WAVE:
                if not up:
                    self.set_y_velocity(max(self.y_vel, mode_scale * -8.0 / scale_mod))
                    self.set_y_velocity(min(self.y_vel, 8.0 / scale_mod))
                else:
                    self.set_y_velocity(max(self.y_vel, -8.0 / scale_mod))
                    self.set_y_velocity(min(self.y_vel, mode_scale * 8.0 / scale_mod))
            if self.is_falling_bugged():
                self.is_jumping = False

        else:  # cube, ball, robot, spider
            if mode == BALL or mode == SPIDER:
                grav_scale = C.GRAV_SCALE_BALL
            elif mode == ROBOT:
                grav_scale = C.GRAV_SCALE_ROBOT
            else:
                grav_scale = 1.0

            if is_holding_jump and self.can_jump and self.strict_taps and dt != 0.0 and not self.is_dashing:
                self.die("buffered jump (not allowed while rating)")
            elif is_holding_jump and self.can_jump:
                if mode == SPIDER and not self.is_dashing:
                    self.spider_jump()
                elif not self.is_dashing:
                    self.is_jumping = True
                    self.on_ground = False
                    self.can_jump = False
                    self.jump_unused = False
                    self.robot_boost_invalidated = False
                    self.robot_boost_charge = 0.0
                    jump = self.y_start
                    if mode == ROBOT:
                        jump *= C.ROBOT_JUMP_MOD
                    self.set_y_velocity((jump * flip) * scale_mod)
                    if (self.was_on_slope or self.is_on_slope) and self.slope_velocity * flip > 0.0:
                        threshold = self.y_vel * C.SLOPE_JUMP_CAP
                        slope_vel = self.slope_velocity if mode == BALL else self.modified_slope_velocity()
                        self.add_y_velocity(slope_vel * C.SLOPE_JUMP_SHARE)
                        if not up:
                            self.set_y_velocity(min(self.y_vel, threshold))
                        else:
                            self.set_y_velocity(max(self.y_vel, threshold))
                    if mode == BALL:
                        self.flip_gravity(not up)
                        self.y_vel *= C.BALL_SWITCH_KEEP      # plain multiply, not rounded
                        self.holding_jump = False
                    elif mode == CUBE:
                        self.run_rotate_action()
            elif self.is_jumping:
                if mode == ROBOT and holding and not self.robot_boost_invalidated:
                    if self.robot_boost_charge < C.ROBOT_BOOST_MAX:
                        self.robot_boost_charge += dt * C.ROBOT_BOOST_RATE
                        self.add_y_velocity(gravity * dt * flip * grav_scale)
                self.add_y_velocity(-(grav_scale * gravity * dt * flip))
                if self.is_falling_bugged():
                    self.is_jumping = False
                    self.on_ground = False
            else:
                if self.is_falling_bugged():
                    self.can_jump = False
                self.add_y_velocity(-(grav_scale * gravity * dt * flip))
                if not up:
                    self.set_y_velocity(max(self.y_vel, -C.CUBE_TERMINAL))
                else:
                    self.set_y_velocity(min(self.y_vel, C.CUBE_TERMINAL))
                if (mode == CUBE and not self.is_rotating and not self.is_on_slope
                        and self.is_falling(C.ROTATE_FALL_START)):
                    self.run_rotate_action()               # walked off an edge: start turning
                if self.is_falling_bugged():
                    if (self.y_vel > 4.0) if up else (self.y_vel < -4.0):
                        self.on_ground = False

        self.was_holding_jump = self.holding_jump

    # ------------------------------------------------------------------ slopes
    def modified_slope_velocity(self):
        """OSGD getModifiedSlopeYVel: launches are weaker in the first 0.1 s on a slope."""
        on_slope = (self._tick() - self.slope_start_tick) / C.TPS
        boost = 1.0
        if on_slope < 0.1:
            boost = max(0.4, on_slope * 10.0)
        return self.slope_velocity * boost

    # ------------------------------------------------------------------ orbs
    def ring_jump(self, obj):
        """OSGD PlayerObject::ringJump.  Returns True if the orb fired."""
        kind = obj.arg
        if self.dead or (obj.uid in self.used):
            return False
        if not self.can_ring_jump or self.is_dashing:
            return False
        if not self.holding_release_delayed:
            return False
        if self.touched_ring:
            return False
        if not obj.multi:
            self.used = self.used | {obj.uid}
        self.touched_ring = True
        mode, mini = self.mode, self.mini

        if kind == "spider":
            if obj.upside != self.upside_down:
                self.flip_gravity(not self.upside_down)
            self.spider_jump()
        elif kind == "dash":
            self.start_dashing(obj)
        elif kind == "gravity_dash":
            self.flip_gravity(not self.upside_down)
            self.start_dashing(obj)
        elif kind == "black":
            self.jump_unused = False
            if mode in FLYING:
                v = self.flip_mod() * -C.ORB_BLACK_FLY
                if mode == UFO:
                    v *= C.ORB_BLACK_UFO_MOD
            else:
                v = self.flip_mod() * -C.ORB_BLACK
                if mode == SPIDER:
                    v *= C.ORB_BLACK_SPIDER_MOD
            self.set_y_velocity(v)
            self._spin_from_orb()
            self.velocity_uncapped = True
            if mode == BALL or mode == SWING:
                self.holding_jump = False
        else:
            self.jump_unused = False
            self.is_jumping = True
            self.on_ground = False
            self.can_jump = False
            scale_mod = C.MINI_JUMP_MOD if mini else 1.0
            vel = self.y_start
            if kind == "blue":
                vel *= C.ORB_BLUE
            elif kind == "green":
                if mode == SHIP:
                    vel *= C.ORB_GREEN_SHIP
            elif kind == "pink":
                vel *= _pick(C.ORB_PINK, mode, False)
            elif kind == "red":
                vel *= _pick(C.ORB_RED, mode, mini)
            elif mode == ROBOT:
                vel *= C.ORB_ROBOT
            if kind == "green":
                self.flip_gravity(not self.upside_down)
            self.set_y_velocity(self.flip_mod() * vel * scale_mod)
            if mode == BALL or mode == SPIDER:
                self.y_vel *= C.ORB_BALL_SPIDER_KEEP
                self.holding_jump = False
            elif mode == SWING:
                self.y_vel *= C.ORB_SWING_KEEP
                self.holding_jump = False
            if kind == "blue":
                self.flip_gravity(not self.upside_down)
            self._spin_from_orb()
            if kind == "red":
                self.velocity_uncapped = True
        return True

    def _spin_from_orb(self):
        """OSGD ringJump: runBallRotation2() for the ball, runRotateAction() otherwise."""
        if self.mode == BALL:
            self.run_ball_rotation2()
        else:
            self.run_rotate_action()

    # ------------------------------------------------------------------ pads
    def propel(self, bump_mod):
        """OSGD PlayerObject::propellPlayer."""
        self.is_jumping = True
        self.on_ground = False
        self.can_jump = False
        self.is_on_slope = False
        self.was_on_slope = False
        scale_mod = C.MINI_JUMP_MOD if self.mini else 1.0
        self.set_y_velocity((bump_mod * C.PAD_BASE * self.flip_mod()) * scale_mod)
        if self.mode in (BALL, SPIDER, SWING):
            self.y_vel *= C.PAD_BALL_KEEP
        self.run_rotate_action()

    def bump(self, obj):
        """OSGD PlayerObject::bumpPlayer for yellow / pink / red / spider pads."""
        kind = obj.arg
        # The game sets this flag when "fix robot jump" applies, so holding jump on a
        # pad does not start a robot boost on top of the pad velocity.
        self.robot_boost_invalidated = True
        if kind == "spider":
            if obj.upside != self.upside_down:
                self.flip_gravity(not self.upside_down)
            self.spider_jump()
            return
        self.propel(_pick(C.PAD_MOD[kind], self.mode, self.mini))
        self.velocity_uncapped = (kind == "red")

    def gravity_bump(self, obj):
        """Blue pad.  gravBumpPlayer is not decompiled; behaviour and the 0.8 factor are
        from gd-sim: a pad on the floor only works with normal gravity, a pad on the
        ceiling only with flipped gravity."""
        if obj.upside != self.upside_down:
            return False
        self.robot_boost_invalidated = True
        self.propel(C.PAD_BLUE)
        self.flip_gravity(not self.upside_down)
        self.velocity_uncapped = False
        return True

    # ------------------------------------------------------------------ dash orbs
    def start_dashing(self, obj):
        """OSGD startDashing + limitDashRotation (classic mode)."""
        if self.is_dashing:
            return
        self.is_dashing = True
        ring_rot = obj.rot + (180.0 if obj.flip_x else 0.0)
        angle = _snap360(math.fmod(-ring_rot, 360.0))
        if abs(angle) > 90.0:                         # pointing backwards: mirror it forwards
            angle = _snap360((-180.0 if angle <= 0.0 else 180.0) - angle)
        angle = max(-C.DASH_MAX_ANGLE, min(C.DASH_MAX_ANGLE, angle))
        rad = math.radians(angle)
        self.dash_y = math.sin(rad) / abs(math.cos(rad))
        self.dash_angle = angle

    def stop_dashing(self):
        self.is_dashing = False
        if self.mode == BALL:
            self.run_ball_rotation()

    # ------------------------------------------------------------------ spider
    def spider_jump(self):
        if self.world is not None:
            self.world.spider_teleport(self)
        else:
            self.flip_gravity(not self.upside_down)
        self.holding_jump = False

    # ------------------------------------------------------------------ cube stair snap
    def check_snap_jump_to_object(self, obj):
        """OSGD checkSnapJumpToObject: nudges x so staircase jumps stay in rhythm."""
        prev = self.snap_obj
        if prev is not None and prev.uid != obj.uid:
            up_dx, up_dx_mini, down_dx, double_dx, tol = C.SNAP_TABLE[self.speed]
            if self.mini:
                up_dx = up_dx_mini
            step = self.flip_mod() * 30.0
            ddx, ddy = obj.x - prev.x, obj.y - prev.y
            if ((abs(ddx - up_dx) <= tol and abs(ddy - step) <= tol)
                    or (abs(ddx - down_dx) <= tol and abs(ddy + step) <= tol)
                    or (abs(ddx - double_dx) <= tol and abs(ddy - 2.0 * step) <= tol)):
                final_x = obj.x + self.snap_distance
                if abs(final_x - self.x) > tol:
                    final_x = self.x - tol if final_x <= self.x else self.x + tol
                self.x = f32(final_x) if C.FLOAT32_POSITIONS else final_x
        self.snap_obj = obj
        self.snap_distance = self.x - obj.x

    # ------------------------------------------------------------------ rotation
    # The icon's angle.  It is what turns the rotated hitbox, so it is simulated, not
    # just drawn.  Ported from OSGD: runRotateAction, runNormalRotation, runBallRotation,
    # runBallRotation2, stopRotation, convertToClosestRotation, updateRotation (both
    # overloads), updateShipRotation and updateSlopeRotation.
    def stop_rotation(self):
        self.is_rotating = False
        self.ball_air_spin = False
        self.rotation_speed = 0.0

    def run_rotate_action(self):
        if self.is_dashing:
            return
        self.stop_rotation()
        if self.mode == BALL:
            self.run_ball_rotation()
        else:
            self.run_normal_rotation()

    def run_normal_rotation(self):
        """The cube's spin in the air: half a turn per ROTATE_TIME, whatever the speed."""
        if self.mode != CUBE or self.is_dashing:
            return                                # ship, ufo, wave, swing, robot and spider never spin
        self.is_rotating = True
        duration = C.ROTATE_TIME_MINI if self.mini else C.ROTATE_TIME
        self.rotation_speed = 180.0 * self.flip_mod() / duration

    def _ball_spin_scale(self):
        return (C.BALL_SPIN_MINI if self.mini else 1.0) * C.BALL_SPIN_TIME[self.speed]

    def run_ball_rotation(self):
        if self.is_dashing:
            return
        self.ball_air_spin = False
        self.rotation_speed = self.flip_mod() * C.BALL_SPIN / self._ball_spin_scale()
        self.is_rotating = True

    def run_ball_rotation2(self):
        if self.is_dashing:
            return
        self.ball_air_spin = True
        self.rotation_speed = self.flip_mod() * C.BALL_SPIN_AIR / self._ball_spin_scale()

    def closest_rotation(self, rotation):
        """OSGD convertToClosestRotation: `rotation` plus whichever multiple of 90 degrees
        lands nearest the current angle, so a cube settles on the side it is closest to."""
        if self.mode in FLYING or self.mode in (ROBOT, SPIDER) or self.is_dashing:
            return rotation
        closest = rotation
        current = math.fmod(int(self.rotation), 360)          # C: (int)getRotation() % 360
        if rotation < current:
            while True:
                rotation += 90.0
                if rotation >= current:
                    break
                closest = rotation
        else:
            while True:
                rotation -= 90.0
                if rotation <= current:
                    break
                closest = rotation
        if abs(current - rotation) >= abs(current - closest):
            return closest
        return rotation

    def _ease_rotation(self, dt, target):
        """OSGD updateRotation(dt, rotation): turn part of the way towards `target`."""
        speed = self.player_speed * C.ROTATE_EASE
        mode = self.mode
        if mode == SHIP:
            speed *= C.ROTATE_EASE_SHIP
        elif (mode not in FLYING and mode != BALL and self.on_ground
              and not self.is_on_slope and not self.was_on_slope):
            speed *= C.ROTATE_EASE_GROUND
        t = min(dt, dt * speed)
        self.rotation = math.degrees(slerp2d(math.radians(self.rotation), math.radians(target), t))

    def _tilt(self, dt):
        """OSGD updateShipRotation: flying modes lean into their direction of travel."""
        if self.is_on_slope:
            return
        dx = self.x - self.rot_x
        dy = -(self.y - self.rot_y)
        if dx * dx + dy * dy < dt * C.TILT_MIN_MOVE:
            return
        angle = math.atan2(dy, dx)
        mode = self.mode
        if mode == UFO:
            angle *= C.TILT_UFO_SHARE
            if self.upside_down:
                angle = min(angle, C.TILT_UFO_MAX)
            else:
                angle = max(angle, -C.TILT_UFO_MAX)
            speed = C.TILT_UFO
        elif mode == WAVE:
            speed = C.TILT_WAVE_MINI if self.mini else C.TILT_WAVE
        else:
            speed = C.TILT_SHIP
        self.rotation = math.degrees(slerp2d(math.radians(self.rotation), angle, min(dt, speed * dt)))

    def _slope_angle(self):
        """EST: the sign convention of m_slopeRotation is not visible in the decompilations."""
        s = self.slope
        angle = math.degrees(math.atan2(s.h, s.w))
        return -angle if s.slope_rises else angle

    def update_rotation(self, dt=C.DT):
        """OSGD updateRotation(dt).  The game calls it once per step AFTER the collision
        pass (GDP GJBaseGameLayer::update), so collisions see the angle of the step before."""
        if not self.is_dashing:
            mode = self.mode
            if mode != BALL:
                if self.is_on_slope and self.slope is not None:
                    self._ease_rotation(dt, self.closest_rotation(self._slope_angle()))
                elif mode in FLYING:
                    self._tilt(dt)
                elif (self.on_ground and not self.is_falling(C.ROTATE_FALL_START)
                      and not self.was_on_slope):
                    self.stop_rotation()
                    self._ease_rotation(dt, self.closest_rotation(0.0))
                elif mode in (ROBOT, SPIDER) and not self.was_on_slope:
                    self._ease_rotation(dt * 0.5, 0.0)
            elif self.on_ground and (self.ball_air_spin or self.rotation_speed == 0.0):
                self.run_ball_rotation()          # EST: hitGround is not decompiled; rolling resumes on landing
            if self.rotation_speed != 0.0:
                self.rotation += dt / 60.0 * self.rotation_speed
        self.rot_x, self.rot_y = self.x, self.y
