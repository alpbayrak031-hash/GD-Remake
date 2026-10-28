"""Object ID -> physics definition.

Hitbox sizes for solids, hazards, saws, slopes, pads, orbs and the 1.x portals are
taken from camila314/pathfinder's gd-sim (src/Objects/Object.cpp), which lists the
in-game hitbox of each object in units, before the object's own scale is applied.
Anything marked ASSUMED below is an ID I know to be that object but whose hitbox
is not in that table; it borrows the size of its nearest sibling.

IDs that are not listed here have no physics mapping and are skipped by the
importer (and counted in its report).  You can add more without touching code:
see `load_overrides`.
"""
import json
from collections import namedtuple

ObjDef = namedtuple("ObjDef", "kind w h arg note")

SOLID, BREAKABLE, HAZARD, SAW = "solid", "breakable", "hazard", "saw"
SLOPE, SLOPE_HAZARD = "slope", "slope_hazard"
PAD, ORB = "pad", "orb"
PORTAL_MODE, PORTAL_GRAVITY, PORTAL_SIZE, PORTAL_SPEED = (
    "portal_mode", "portal_gravity", "portal_size", "portal_speed")
PORTAL_DUAL = "portal_dual"
PORTAL_TELEPORT = "portal_teleport"
NOOP = "noop"

OBJECTS = {}


def _ids(*spec):
    """Expand ints and (lo, hi) inclusive ranges."""
    out = []
    for s in spec:
        if isinstance(s, tuple):
            out.extend(range(s[0], s[1] + 1))
        else:
            out.append(s)
    return out


def _add(kind, w, h, ids, arg=None, note=""):
    for i in ids:
        OBJECTS[i] = ObjDef(kind, float(w), float(h), arg, note)


# ------------------------------------------------------------------ solids (gd-sim)
_add(SOLID, 30, 30, _ids((1, 4), (6, 7), 63, (69, 72), (74, 78), (81, 83), (90, 96),
                         (116, 119), (121, 122), 146, (160, 163), (165, 169), 173, 175,
                         (207, 210), (212, 213), (247, 250), (252, 258), (260, 261),
                         (263, 265), (267, 272), (274, 275), 467, (469, 471),
                         (1203, 1204), (1209, 1210), (1221, 1222), 1226))
_add(SOLID, 15, 15, _ids(64, 195, 206, 220, 661, (1155, 1157), 1208, 1910))
_add(SOLID, 30, 14, _ids(40, 147, 215, (369, 370), (1903, 1905)))
_add(SOLID, 30, 21, _ids((170, 172), 174, 192))
_add(SOLID, 30, 1.5, _ids(468, 475, 1260))
_add(SOLID, 30, 16, _ids(62, 65, 66, 68))
_add(SOLID, 30, 3, _ids(1202, 1262))
_add(SOLID, 30, 6, _ids(1220, 1264))
_add(SOLID, 15, 8, _ids(196, 219, 1911))
_add(SOLID, 8, 15, _ids(204))
_add(SOLID, 30, 15, _ids(662, 663, 664))
_add(SOLID, 30, 10, _ids(1561))
_add(SOLID, 15, 10, _ids(1567))
_add(SOLID, 12, 12, _ids(1566))
_add(SOLID, 17, 17, _ids(1565))
_add(SOLID, 30, 7, _ids(1227))
_add(SOLID, 22, 22, _ids(328))
_add(SOLID, 22, 21, _ids(197))
_add(SOLID, 21, 21, _ids(194))
_add(SOLID, 14, 21, _ids(176))
_add(SOLID, 30, 2, _ids(1562))
_add(SOLID, 25, 3, _ids(1343))
_add(SOLID, 27, 2, _ids(1340))
_add(SOLID, 37, 23, _ids(34))
_add(BREAKABLE, 30, 30, _ids(143))

# ------------------------------------------------------------------ hazards (gd-sim)
_add(HAZARD, 2.40039063, 3.20001221, _ids(720, 991, 1731, 1733))
_add(HAZARD, 9, 7.2, _ids(61, 446, 1719, 1728))
_add(HAZARD, 9, 6, _ids(365, 667, 1716, 1730))
_add(HAZARD, 2.6, 4.8, _ids(392, 458, 459))
_add(HAZARD, 6, 12, _ids(8, 144, 177, 216))          # the full-size spike
_add(HAZARD, 4, 7.6, _ids(103, 145, 218))            # medium spike
_add(HAZARD, 6, 5.6, _ids(39, 205, 217))             # half spike
_add(HAZARD, 4.5, 5.2, _ids(768, 1727))
_add(HAZARD, 5.2, 7.2, _ids(447, 1729))
_add(HAZARD, 14.1, 20, _ids(135, 1711))
_add(HAZARD, 6, 4.4, _ids(422, 1726))
_add(HAZARD, 6, 6.8, _ids(244, 1721))
_add(HAZARD, 6, 7.2, _ids(243, 1720))
_add(HAZARD, 9, 5.2, _ids(421, 1725))
_add(HAZARD, 9, 10.8, _ids(9, 1715))
_add(HAZARD, 9, 12, _ids(989, 1732))
_add(HAZARD, 11.4, 16.4, _ids(1714))
_add(HAZARD, 13.5, 22.4, _ids(1712))
_add(HAZARD, 9, 4, _ids(368, 1722))
_add(HAZARD, 11.7, 20, _ids(1713))
_add(HAZARD, 6, 6.4, _ids(178))
_add(HAZARD, 25, 6, _ids(919))
_add(HAZARD, 4, 8, _ids(179))

# Blocks gd-sim does not list: hitboxes from Open-GD/OpenGD's table (Source/LongData.cpp,
# _pHitboxes), which is taken from the game; all centred on the object.
_add(SOLID, 43, 22, (329,), None, "hitbox from OpenGD (LongData.cpp)")
_add(SOLID, 15, 1.5, (1154,), None, "hitbox from OpenGD (LongData.cpp)")
_add(SOLID, 15, 2, (1563,), None, "hitbox from OpenGD (LongData.cpp)")
_add(SOLID, 12, 12, (1564,), None, "hitbox from OpenGD (LongData.cpp)")
_add(SOLID, 62, 32, (1568,), None, "hitbox from OpenGD (LongData.cpp)")
_add(SOLID, 32, 32, (1569,), None, "hitbox from OpenGD (LongData.cpp)")

# Saws: gd-sim stores the collision RADIUS in both size fields.
for _radius, _saw_ids in (
        (32.3, (88, 186, 740, 1705)), (21.6, (89, 1706)), (12, (98,)), (15.660001, (183,)),
        (20.4, (184,)), (2.8500001, (185,)), (21.960001, (187, 741)), (12.6000004, (188, 742)),
        (28.9, (397, 1708)), (17.44, (398, 1709)), (12.900001, (399, 1710)), (32, (675, 1734)),
        (17.5100002, (676, 1735)), (12.479999, (677, 1736)), (30.4, (678,)), (18.54, (679,)),
        (10.8, (680,)), (24, (918,)), (4, (1582, 1583)), (25, (1619,)), (15, (1620,)),
        (12, (1707,)), (6, (1701, 1702, 1703))):
    _add(SAW, _radius * 2, _radius * 2, _saw_ids, arg=_radius)

# ------------------------------------------------------------------ slopes (gd-sim)
_SLOPES_45 = (289, 294, 299, 305, 309, 315, 321, 326, 331, 337, 343, 349, 353, 371, 483,
              492, 651, 665, 673, 709, 711, 726, 728, 886, 1338, 1341, 1344, 1723, 1743,
              1745, 1747, 1749, 1906)
_SLOPES_26 = (291, 295, 301, 307, 311, 317, 323, 327, 333, 339, 345, 351, 355, 367, 372,
              484, 493, 652, 666, 674, 710, 712, 727, 729, 887, 1339, 1342, 1345, 1724,
              1744, 1746, 1748, 1750, 1907)
_add(SLOPE, 30, 30, _SLOPES_45)
_add(SLOPE, 60, 30, _SLOPES_26)
_add(SLOPE_HAZARD, 30, 30, (363, 1717))
_add(SLOPE_HAZARD, 60, 30, (364, 366, 1718))

# ------------------------------------------------------------------ pads
_add(PAD, 25, 4, (35,), "yellow")
_add(PAD, 25, 5, (140,), "pink")
_add(PAD, 25, 6, (67,), "blue")
_add(PAD, 25, 6, (1332,), "red", "ASSUMED hitbox (same as blue pad)")
_add(PAD, 25, 6, (3005,), "spider", "ASSUMED hitbox (same as blue pad)")

# ------------------------------------------------------------------ orbs
_add(ORB, 36, 36, (36,), "yellow")
_add(ORB, 36, 36, (84,), "blue")
_add(ORB, 36, 36, (141,), "pink")
_add(ORB, 36, 36, (1022,), "green")
_add(ORB, 36, 36, (1330,), "black")
_add(ORB, 36, 36, (1333,), "red")
_add(ORB, 36, 36, (1704,), "dash", "ASSUMED hitbox (same as other orbs)")
_add(ORB, 36, 36, (1751,), "gravity_dash", "ASSUMED hitbox (same as other orbs)")
_add(ORB, 36, 36, (3004,), "spider", "ASSUMED hitbox (same as other orbs)")

# ------------------------------------------------------------------ portals
# arg for mode portals is the Mode integer (see player.Mode)
_add(PORTAL_MODE, 34, 86, (12,), 0)      # cube
_add(PORTAL_MODE, 34, 86, (13,), 1)      # ship
_add(PORTAL_MODE, 34, 86, (47,), 2)      # ball
_add(PORTAL_MODE, 34, 86, (111,), 3)     # ufo
_add(PORTAL_MODE, 34, 86, (660,), 4)     # wave
_add(PORTAL_MODE, 34, 86, (745,), 5, "ASSUMED hitbox (same as 1.x mode portals)")    # robot
_add(PORTAL_MODE, 34, 86, (1331,), 6, "ASSUMED hitbox (same as 1.x mode portals)")   # spider
_add(PORTAL_MODE, 34, 86, (1933,), 7, "ASSUMED hitbox (same as 1.x mode portals)")   # swing
_add(PORTAL_GRAVITY, 25, 75, (10,), "normal")
_add(PORTAL_GRAVITY, 25, 75, (11,), "flipped")
_add(PORTAL_GRAVITY, 25, 75, (2926,), "toggle", "ASSUMED hitbox (same as 10/11)")
_add(PORTAL_SIZE, 31, 90, (99,), False)
_add(PORTAL_SIZE, 31, 90, (101,), True)
_add(PORTAL_SPEED, 35, 44, (200,), 0)
_add(PORTAL_SPEED, 33, 56, (201,), 1)
_add(PORTAL_SPEED, 51, 56, (202,), 2)
_add(PORTAL_SPEED, 65, 56, (203,), 3)
_add(PORTAL_SPEED, 69, 56, (1334,), 4)
_add(PORTAL_DUAL, 34, 86, (286,), True, "ASSUMED hitbox (same as mode portals); dual mode is an ESTIMATE")
_add(PORTAL_DUAL, 34, 86, (287,), False, "ASSUMED hitbox (same as mode portals); dual mode is an ESTIMATE")
_add(PORTAL_TELEPORT, 25, 75, (747,), None,
     "ASSUMED hitbox (same as gravity portals); where it puts the player is an ESTIMATE")
_add(NOOP, 0, 0, (45, 46), None, "mirror portal: visual only")

# Known gameplay-relevant IDs that are deliberately NOT simulated.  Listing them
# only makes the importer's report readable.
UNSUPPORTED_NAMES = {
    31: "start position",
    32: "enable trail", 33: "disable trail", 142: "secret coin",
    749: "teleport portal exit",
    914: "text", 1006: "pulse trigger",
    1007: "alpha trigger",
    1329: "user coin", 1346: "rotate trigger", 1347: "follow trigger",
    1520: "shake trigger", 1585: "animate trigger", 1594: "toggle orb",
    1595: "touch trigger", 1611: "count trigger", 1612: "hide player trigger",
    1613: "show player trigger", 1616: "stop trigger", 1755: "D block", 1811: "instant count trigger",
    1812: "on-death trigger", 1813: "J block", 1814: "follow player Y trigger",
    1815: "collision trigger", 1816: "collision block", 1817: "pickup trigger",
    1818: "BG effect on", 1819: "BG effect off", 1829: "S block", 1859: "H block",
}


def load_overrides(path):
    """Merge extra definitions from a JSON file.

    Format: {"1234": {"kind": "solid", "w": 30, "h": 30}, "88": {"kind": "saw", "w": 64, "h": 64, "arg": 32}}
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for key, d in data.items():
        OBJECTS[int(key)] = ObjDef(d["kind"], float(d.get("w", 30)), float(d.get("h", 30)),
                                   d.get("arg"), d.get("note", "user override"))
    return len(data)
