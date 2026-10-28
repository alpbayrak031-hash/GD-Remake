"""Every physics constant used by the engine, each tagged with where it comes from.

Source tags
-----------
OSGD  StarryDawn72/open-source-gd-project (MIT) - hand-verified reconstructions of
      GD 2.2081 functions (PlayerObject::update, updateJump, ringJump, ...).
GDP   camila314/gdp (branch 2.2) - decompiled 2.2 physics functions
      (updateTimeMod, collidedWithObjectInternal, checkSnapJumpToObject, ...).
SIM   camila314/pathfinder -> gd-sim - a physics simulator validated against the
      game; used for hitbox tables and for behaviour the two decomps do not cover.
DOCS  Wyliemaster/gddocs - level format documentation.
MEM   Recalled from older (2.1) decompilations; not re-verified against 2.2.
EST   Estimate / inference. Treat as "probably close", not as fact.

Units
-----
Positions are GD units (30 per block, ground top at y = 0, as stored in level
strings).  Vertical velocity is kept in the game's native unit (the value of
PlayerObject::m_yVelocity).  One native unit moves the player 0.9 * 0.25 = 0.225
units per 240 Hz step, i.e. 54 units/second - multiply by VEL_TO_UNITS_PER_SEC
for a human-readable number.
"""
import array

_F = array.array("f", [0.0])


def f32(x: float, _box=_F) -> float:
    """Round a Python float to the nearest IEEE-754 single, like a C `float` (storing it in
    a C float array does exactly that conversion, a little faster than struct)."""
    _box[0] = x
    return _box[0]


# --------------------------------------------------------------------------- time
TPS = 240                # GDP GJBaseGameLayer::update: steps = max(1, delta * 240 / min(timewarp, 1))
DT = 0.25                # GDP: each step calls player->update(delta * 60 / steps) -> exactly 0.25
DT_SCALED = f32(DT * 0.9)  # OSGD PlayerObject::update: updateJump(dt * 0.9), deltaY = yVel * dt * 0.9
                           # (EST: stored as a C float; a double would differ by 3e-8)
VEL_TO_UNITS_PER_SEC = 54.0  # 0.9 * 0.25 * 240; SIM states "velocity is stored as 1/54 of distance per second"

# Player positions live in cocos2d CCPoint (two C floats) and OSGD's update() does
# setPosition(getPosition() + ccp(dx, dy)), so positions are accumulated in single
# precision.  Set False to accumulate in doubles instead.
FLOAT32_POSITIONS = True

# --------------------------------------------------------------------------- speeds
# GDP PlayerObject::updateTimeMod.  Columns: m_playerSpeed, m_yStart (cube jump
# velocity), m_gravity (cube/robot only), m_speedMultiplier.  All four are C floats.
#                 speed  yStart      gravity    multiplier
SPEED_TABLE = (
    (f32(0.7), f32(10.620032), f32(0.940199), f32(5.980002)),   # 0.5x
    (f32(0.9), f32(11.180032), f32(0.958199), f32(5.770002)),   # 1x
    (f32(1.1), f32(11.420032), f32(0.957199), f32(5.870002)),   # 2x
    (f32(1.3), f32(11.230032), f32(0.961199), f32(6.000002)),   # 3x
    (f32(1.6), f32(11.230032), f32(0.961199), f32(6.000002)),   # 4x
)
SPEED_NAMES = ("0.5x", "1x", "2x", "3x", "4x")
# Resulting horizontal speeds, units/second (speed * multiplier * 60):
#   251.16  311.58  387.42  468.00  576.00
# These match SIM's player_speeds table to 7 significant digits.

# Gravity used by ball, spider and every flying mode regardless of speed portal.
GRAVITY_FLYING = 0.958199   # OSGD updateJump (GDP prints the same literal as 0.9582)

# --------------------------------------------------------------------------- per-mode
MINI_SCALE = 0.6            # OSGD m_playerScale / SIM size portal factor
MINI_JUMP_MOD = 0.8         # OSGD updateJump / ringJump / propellPlayer: scale_mod for mini
MINI_FLY_MOD = 0.85         # OSGD updateJump: flying modes divide accel and limits by 0.85 when mini

CUBE_TERMINAL = 15.0        # OSGD updateJump: clamp to -15 (normal) / +15 (flipped); cube, ball, robot, spider
GRAV_SCALE_BALL = 0.6       # OSGD updateJump: ground_mode_gravity_scale for ball and spider
GRAV_SCALE_ROBOT = 0.9      # OSGD updateJump
BALL_SWITCH_KEEP = f32(0.6)  # OSGD/GDP: after the ball flips gravity, m_yVelocity *= 0.6f
ROBOT_JUMP_MOD = 0.5        # OSGD updateJump: robot jump velocity = yStart * 0.5
ROBOT_BOOST_MAX = 1.5       # OSGD updateJump: boost while m_robotBoostCharge < 1.5
ROBOT_BOOST_RATE = 0.1      # OSGD updateJump: charge += dt * 0.1  (=> 67 steps = 0.279 s of boost)

FLY_MAX_UP = 8.0            # OSGD updateJump: upper clamp  8.0 / scale_mod
FLY_MAX_DOWN = 6.4          # OSGD updateJump: lower clamp -8.0 * 0.8 / scale_mod (swing uses 8.0 both ways)
SHIP_THRUST = -1.0          # OSGD updateJump ship_mod while holding
SHIP_FALL = 0.8             # OSGD updateJump ship_mod, not holding, already "falling"
SHIP_FALL_RISING = 1.2      # OSGD updateJump ship_mod, not holding, still rising
SHIP_BOOST_HOLD_FALLING = 0.5   # OSGD updateJump boost_mod when holding and falling
SHIP_BOOST = 0.4            # OSGD updateJump boost_mod otherwise
UFO_JUMP = 7.0              # OSGD updateJump: bird jump velocity (normal size)
UFO_JUMP_MINI = 8.0         # OSGD updateJump: mini uses 8.0 * 0.85 = 6.8
UFO_GRAV_FALL = 0.8         # OSGD updateJump bird_mod when falling ...
UFO_GRAV_RISE = 1.2         # ... and when rising; both are then multiplied by 0.5
SWING_GRAV = 0.4            # OSGD updateJump swing gravity factor (normal size)
SWING_GRAV_MINI = 0.6       # OSGD updateJump (mini)
SWING_FLIP_KEEP = 0.8       # OSGD updateJump: velocity kept when the swing flips gravity
GRAVITY_FLIP_KEEP = 0.5     # OSGD PlayerObject::flipGravity: m_yVelocity *= 0.5
MODE_SWITCH_KEEP = 0.5      # OSGD toggleFlyMode/BirdMode/DartMode/SwingMode: m_yVelocity *= 0.5
                            # (applied when such a mode is switched on AND when it is switched off)

# --------------------------------------------------------------------------- hitboxes
PLAYER_SIZE = 30.0          # default m_width / m_height
WAVE_SIZE = 10.0            # OSGD toggleDartMode: m_width = m_height = 10
SPIDER_SIZE = 27.0          # OSGD toggleSpiderMode: m_width = m_height = 27
INNER_HITBOX_SCALE = 0.3    # GDP collidedWithObjectInternal: getObjectRect(0.3, 0.3) decides death vs solids
                            # (SIM hard-codes the result for a normal cube: 9 x 9)

# The player has three boxes, the same three GD's own "Show Hitboxes" draws:
#   outer    PLAYER_SIZE square, never rotates.  Hazards, orbs, pads, portals, and
#            landing on / bumping into the faces of blocks.
#   inner    INNER_HITBOX_SCALE x the outer one, never rotates.  Decides death against
#            solid blocks (running into a wall).
#   rotated  the outer box turned by the icon's rotation.  Only consulted for objects
#            placed at an angle that is not a multiple of 90 degrees (see gd/sim.py).

# --------------------------------------------------------------------------- rotation
# The icon's rotation used to be decoration; it now feeds the rotated box.
ROTATE_TIME = 26.0 / 60.0       # OSGD runNormalRotation: the cube turns 180 degrees in this many
ROTATE_TIME_MINI = 20.0 / 60.0  # seconds while in the air (415.38 deg/s; mini 540 deg/s) - at every speed
ROTATE_EASE = 0.175             # OSGD updateRotation(dt, target): Slerp factor = dt * playerSpeed * 0.175 ...
ROTATE_EASE_GROUND = 3.0        # ... x 3 for a cube/robot/spider standing on flat ground
ROTATE_EASE_SHIP = 0.5          # ... x 0.5 for the ship
ROTATE_FALL_START = -0.25       # OSGD updateJump / updateRotation: playerIsFalling(-0.25)
BALL_SPIN = 120.0 / 0.2         # OSGD runBallRotation: 600 deg/s rolling at 1x (x 1/0.8 when mini)
BALL_SPIN_AIR = -340.0 / 0.8    # OSGD runBallRotation2: -425 deg/s after a gravity switch or an orb
BALL_SPIN_MINI = 0.8
# OSGD runBallRotation: both spins are divided by 311.5801 / (the speed's units per second)
BALL_SPIN_TIME = (311.5801 / 251.16, 1.0, 311.5801 / 387.4201, 311.5801 / 468.0002, 311.5801 / 576.0002)
# OSGD updateShipRotation: Slerp factor per mode (the tilt follows the direction of travel)
TILT_SHIP = 0.15                # ship and swing
TILT_UFO = 0.07                 # the ufo only leans by -0.4 x the angle, capped at 0.1 rad
TILT_UFO_SHARE = -0.4
TILT_UFO_MAX = 0.1
TILT_WAVE = 0.25
TILT_WAVE_MINI = 0.4
TILT_MIN_MOVE = 1.2             # OSGD: no update when the squared distance moved is below dt * 1.2

# --------------------------------------------------------------------------- solids
SNAP_THRESHOLD = 10.0       # GDP collidedWithObjectInternal: cube / ball / robot / spider
SNAP_THRESHOLD_FLY = 6.0    # GDP collidedWithObjectInternal: ship / ufo / wave / swing
FLIP_GRACE_SECONDS = 0.1    # GDP: head collisions within 0.1 s of a gravity flip snap instead of killing
# The game's code for that, at the end of collidedWithObjectInternal (camila314/gdp, 2.2):
#     if (canSnap && m_lastFlipTime != 0.0 && m_totalTime - m_lastFlipTime < 0.1) {
#         if (m_isUpsideDown) setPositionY(floatC + playerRect.size.height * 0.5);   // floatC = the block's top
#         else                setPositionY(objRect.getMinY() - playerRect.size.height * 0.5);
#         hitGround(object, true); ...
# It runs where the player would otherwise die (its 0.3 box is inside a block).  canSnap
# means the block is on the player's HEAD side with the feet side at least 10 units
# clear.  The visible effect: a ball that switches gravity 50-100 ms before a one-block
# step is lifted onto the step instead of dying on its side ("the ball goes up a
# block").  gd-sim has no such rule.  It is kept because it is in the game's code; set
# this to False to have such collisions simply kill.
FLIP_GRACE_PUSH = True
SPIDER_GRACE_SECONDS = 0.04  # GDP: same idea after a spider teleport

# Cube "stair snapping" (OSGD checkSnapJumpToObject).  Per speed:
# (step-up dx, step-up dx when mini, step-down dx, double-step-up dx, tolerance)
SNAP_TABLE = {
    0: (90.0, 90.0, 120.0, 60.0, 1.0),
    1: (120.0, 90.0, 150.0, 90.0, 1.0),
    2: (150.0, 90.0, 195.0, 120.0, 2.0),
    3: (180.0, 90.0, 225.0, 135.0, 2.0),
    4: (120.0, 120.0, 150.0, 90.0, 1.0),   # 4x has no branch in the game: it falls through to the defaults
}

# --------------------------------------------------------------------------- orbs
# OSGD PlayerObject::ringJump.  Velocity = yStart * factor * (0.8 if mini) * flipMod,
# then ball/spider multiply by 0.7 and swing by 0.6.
ORB_PINK = {"ship": 0.37, "ufo": 0.42, "ball": 0.77, "default": 0.72}
ORB_RED = {"ship": 1.0, "ship_mini": 1.4, "ufo": 1.02, "ufo_mini": 1.36,
           "ball": 1.34, "robot": 1.28, "spider": 1.34, "default": 1.38}
ORB_BLUE = 0.8              # then flipGravity halves it -> 0.4 * yStart towards the new floor
ORB_GREEN_SHIP = 0.7
ORB_ROBOT = 0.9             # robot hitting a yellow orb
ORB_BALL_SPIDER_KEEP = 0.7
ORB_SWING_KEEP = 0.6
ORB_BLACK = 15.0            # cube/ball/robot: -15 * flipMod
ORB_BLACK_SPIDER_MOD = 1.1
ORB_BLACK_FLY = 14.0        # ship/wave/swing: -14; ufo: -14 * 0.8
ORB_BLACK_UFO_MOD = 0.8
DASH_MAX_ANGLE = 70.0       # OSGD limitDashRotation

# --------------------------------------------------------------------------- pads
PAD_BASE = 16.0             # OSGD propellPlayer: yVel = bumpMod * 16 * flipMod * (0.8 if mini)
PAD_BALL_KEEP = 0.6         # OSGD propellPlayer: ball / spider / swing multiply by 0.6 afterwards
# bumpMod per pad and mode.  GJBaseGameLayer::getBumpMod is not in either decomp, so
# these are DERIVED from SIM's measured pad velocity tables (value / 54 / 16, with
# the 0.8 mini and 0.6 ball factors removed).  The cube values are exactly the
# well-known 1.0 / 0.65 / 1.25.
PAD_MOD = {
    "yellow": {"default": 1.0},
    "pink": {"default": 0.65, "ship": 0.35, "ufo": 0.4, "ball": 0.7},
    "red": {"default": 1.25, "ship": 0.63, "ship_mini": 0.95, "ufo": 0.6, "ufo_mini": 0.98},
}
PAD_BLUE = 0.8              # SIM: blue pad = -0.4 * 16 after the gravity flip halves it
# EST: robot, spider and swing use the "default" column; SIM does not cover them.

# --------------------------------------------------------------------------- slopes
SLOPE_VEL_FACTOR = 1.12     # GDP collidedWithSlopeInternal: min(1.12 / angle, 1.54) * slopeYVelocity
SLOPE_VEL_CAP = 1.54
SLOPE_FLY_BALL_MOD = 0.75   # GDP: flying modes and ball
SLOPE_JUMP_SHARE = 0.25     # OSGD updateJump: jump adds getModifiedSlopeYVel() * 0.25 ...
SLOPE_JUMP_CAP = 1.4        # ... capped at 1.4 * the normal jump velocity
SLOPE_HAZARD_OFFSET = 4.0   # GDP GameObject::slopeYPos

# --------------------------------------------------------------------------- level bounds
BOUNDS_SHIP = 300.0         # SIM Vehicle bounds (ship, ufo, wave)
BOUNDS_BALL = 240.0         # SIM Vehicle bounds (ball)
BOUNDS_SPIDER = 240.0       # MEM GJBaseGameLayer::getGroundHeightForMode - unverified for 2.2
BOUNDS_SWING = 300.0        # MEM, same caveat
MAX_Y = 1476.3              # SIM Player::postCollision: dead if pos.y > 1476.3
LEVEL_END_PADDING = 340.0   # MEM: level length = last object x + 340.  EST for 2.2.
SECTION_SIZE = 100          # SIM Level::sectionSize ("sections are used just like real GD")
