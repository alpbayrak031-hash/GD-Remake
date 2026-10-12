"""Tests for saved progress, the icon definitions and the menu's level catalogue.

(The windows themselves are exercised by hand; these cover the logic behind them.)
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# the player's folder (normally Documents/GD Remake) must never be touched by a test
if "GD_REMAKE_HOME" not in os.environ:
    os.environ["GD_REMAKE_HOME"] = tempfile.mkdtemp(prefix="gdhome")
    atexit.register(shutil.rmtree, os.environ["GD_REMAKE_HOME"], True)

from gd import icons                                       # noqa: E402
from gd.level import LevelBuilder                          # noqa: E402
from gd.levelio import write_gmd                           # noqa: E402
from gd import paths                                       # noqa: E402
from gd.progress import DEFAULTS, Progress, level_key      # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gdui")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.save = os.path.join(self.dir, "save", "progress.json")


class ProgressFile(Temp):
    def test_defaults_for_an_unplayed_level(self):
        p = Progress(self.save)
        self.assertEqual(p.get("x"), DEFAULTS)
        self.assertFalse(p.completed("x"))
        self.assertFalse(os.path.exists(self.save), "nothing is written until something happens")

    def test_best_only_ever_goes_up_and_practice_is_separate(self):
        p = Progress(self.save)
        self.assertTrue(p.reached("a", 41.26))
        self.assertFalse(p.reached("a", 30))
        self.assertTrue(p.reached("a", 77, practice=True))
        self.assertEqual((p.get("a")["best"], p.get("a")["practice"]), (41.3, 77.0))
        self.assertTrue(p.reached("a", 250))                 # clamped
        self.assertEqual(p.get("a")["best"], 100.0)

    def test_completion_and_best_time(self):
        p = Progress(self.save)
        p.attempt("a")
        p.attempt("a")
        p.complete("a", 40.5)
        p.complete("a", 44.0)
        p.complete("a", 39.25)
        rec = p.get("a")
        self.assertEqual((rec["attempts"], rec["completions"], rec["best"], rec["best_time"]), (2, 3, 100.0, 39.25))
        p.complete("b", 10.0, practice=True)                 # practice never counts as beating a level
        self.assertEqual((p.get("b")["completions"], p.get("b")["practice"], p.get("b")["best"]), (0, 100.0, 0.0))
        self.assertTrue(p.completed("a"))
        self.assertFalse(p.completed("b"))

    def test_saved_and_loaded_back(self):
        p = Progress(self.save)
        p.attempt("levels/a.gmd")
        p.reached("levels/a.gmd", 63)
        p.add_play("levels/a.gmd", 12.34, 17)
        p.save()
        self.assertFalse(os.path.exists(self.save + ".tmp"))
        again = Progress(self.save)
        rec = again.get("levels/a.gmd")
        self.assertEqual((rec["attempts"], rec["best"], rec["jumps"], rec["seconds"]), (1, 63.0, 17, 12.3))
        self.assertIsNotNone(rec["last_played"])
        with open(self.save) as fh:
            self.assertEqual(json.load(fh)["version"], 1)

    def test_totals_and_reset(self):
        p = Progress(self.save)
        for key, pct in (("a", 100), ("b", 40), ("c", 0)):
            p.attempt(key)
            p.add_play(key, 10, 5)
            if pct == 100:
                p.complete(key, 30)
            else:
                p.reached(key, pct)
        t = p.totals(["a", "b", "c", "never played"])
        self.assertEqual((t["levels"], t["completed"], t["attempts"], t["jumps"], t["seconds"]), (4, 1, 3, 15, 30.0))
        p.reset("a")
        self.assertEqual(p.get("a"), DEFAULTS)
        self.assertEqual(p.get("b")["best"], 40.0)
        p.reset()
        self.assertEqual(Progress(self.save).levels, {})

    def test_a_damaged_save_is_set_aside_not_trusted(self):
        os.makedirs(os.path.dirname(self.save))
        with open(self.save, "w") as fh:
            fh.write("{ this is not json")
        p = Progress(self.save)
        self.assertEqual(p.levels, {})
        self.assertTrue(os.path.exists(self.save + ".broken"))
        p.attempt("a")
        p.save()
        self.assertEqual(Progress(self.save).get("a")["attempts"], 1)

    def test_level_key(self):
        inside = os.path.join(ROOT, "levels", "01_first_steps.gmd")
        self.assertEqual(level_key(inside), "levels/01_first_steps.gmd")
        outside = os.path.join(self.dir, "x.gmd")
        self.assertEqual(level_key(outside), os.path.abspath(outside).replace(os.sep, "/"))
        # the player's own levels are known by name, wherever their folder is
        self.assertEqual(level_key(os.path.join(paths.USER_LEVELS, "My level.gmd")), "mine/My level.gmd")
        self.assertEqual(level_key(os.path.join(paths.USER_LEVELS, "trash", "old.gmd")), "mine/trash/old.gmd")

    def test_where_things_live(self):
        self.assertTrue(os.path.isfile(os.path.join(paths.PACK_LEVELS, "index.json")), "the pack ships with the program")
        self.assertEqual(paths.HOME, os.environ["GD_REMAKE_HOME"])
        self.assertEqual((paths.USER_LEVELS, paths.SAVEDATA, paths.BACKUPS),
                         (os.path.join(paths.HOME, "levels"), os.path.join(paths.HOME, "savedata"),
                          os.path.join(paths.HOME, "savedata", "backups")))
        self.assertFalse(paths.inside(paths.USER_LEVELS, paths.ROOT), "nothing is written into the program")
        self.assertTrue(paths.inside(os.path.join(paths.ROOT, "levels", "x.gmd"), paths.PACK_LEVELS))
        self.assertFalse(paths.inside(paths.ROOT + "2", paths.ROOT))
        self.assertEqual(paths.ensure_home(), "")
        self.assertTrue(os.path.isdir(paths.USER_LEVELS) and os.path.isdir(paths.SAVEDATA))


class Icons(unittest.TestCase):
    def bounds(self, mode):
        xs = [x for _k, pts, _r, _o, _w in icons.ICONS[mode] for x, _ in pts]
        ys = [y for _k, pts, _r, _o, _w in icons.ICONS[mode] for _, y in pts]
        return min(xs), max(xs), min(ys), max(ys)

    def test_every_mode_has_an_icon_a_name_and_its_own_colour(self):
        self.assertEqual(sorted(icons.ICONS), list(range(8)))
        self.assertEqual(len(icons.MODE_NAMES), 8)
        self.assertEqual(len(set(icons.MODE_COLORS)), 8)
        for mode in range(8):
            self.assertGreaterEqual(len(icons.ICONS[mode]), 2)
            self.assertTrue(any(role == "p" for _k, _p, role, _o, _w in icons.ICONS[mode]),
                            "%s must show its mode colour" % icons.MODE_NAMES[mode])

    def test_icons_stay_near_the_hitbox(self):
        for mode in range(8):
            x0, x1, y0, y1 = self.bounds(mode)
            self.assertGreaterEqual(x0, -2.0)
            self.assertLessEqual(x1, 2.0)
            self.assertGreaterEqual(y0, -1.5)
            self.assertLessEqual(y1, 1.6)

    @staticmethod
    def area(points):
        return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))) / 2.0

    def test_silhouettes_differ(self):
        # shapes, not just colours, must tell the modes apart: compare each icon's overall
        # box together with the area of its biggest solid part (a square and a circle share
        # a box but not an area)
        shapes = {}
        for mode in range(8):
            x0, x1, y0, y1 = self.bounds(mode)
            biggest = max(self.area(pts) for kind, pts, _r, _o, _w in icons.ICONS[mode] if kind == "poly")
            shapes[mode] = (round(x1 - x0, 1), round(y1 - y0, 1), round(biggest, 1))
        self.assertEqual(len(set(shapes.values())), 8, shapes)
        for a in range(8):
            for b in range(a + 1, 8):
                difference = sum(abs(p - q) for p, q in zip(shapes[a], shapes[b]))
                self.assertGreater(difference, 0.4, "%s and %s are too alike" % (icons.MODE_NAMES[a], icons.MODE_NAMES[b]))

    def test_no_mode_is_hazard_red(self):
        def rgb(c):
            return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        hazard = rgb("#FF5A5A")
        for mode, colour in enumerate(icons.MODE_COLORS):
            distance = sum((a - b) ** 2 for a, b in zip(rgb(colour), hazard)) ** 0.5
            self.assertGreater(distance, 80, icons.MODE_NAMES[mode])

    def test_every_difficulty_has_a_colour(self):
        for name in ("easy", "medium", "hard", "very hard", "custom"):
            self.assertIn(name, icons.DIFFICULTY_COLORS)


class Catalogue(Temp):
    def setUp(self):
        Temp.setUp(self)
        from gd import menu_tk
        self.menu = menu_tk
        self.levels = os.path.join(self.dir, "levels")          # the player's folder
        self.pack = os.path.join(self.dir, "pack")              # the built-in levels
        os.makedirs(self.levels)
        os.makedirs(self.pack)
        old = menu_tk.LEVELS, menu_tk.PACK
        menu_tk.LEVELS, menu_tk.PACK = self.levels, self.pack
        menu_tk._info_cache.clear()
        self.addCleanup(lambda: setattr(menu_tk, "LEVELS", old[0]) or setattr(menu_tk, "PACK", old[1]))
        self.addCleanup(menu_tk._info_cache.clear)

    def make(self, name, title, build=None, folder=None):
        b = LevelBuilder()
        b.block(40, 0)
        if build:
            build(b)
        path = os.path.join(folder or self.levels, name)
        write_gmd(path, title, b.string())
        return path

    def test_the_pack_and_my_levels_are_separate_folders(self):
        self.make("01_pack.gmd", "Pack", folder=self.pack)
        self.make("test_level.gmd", "Physics Test", folder=self.pack)
        mine = self.make("mine.gmd", "Mine")
        os.makedirs(os.path.join(self.levels, "trash"))
        self.make("gone.gmd", "Deleted", folder=os.path.join(self.levels, "trash"))
        for folder, name, data in ((self.pack, "01_pack.gdr2", "GDR"), (self.levels, "notes.txt", "hi")):
            with open(os.path.join(folder, name), "w") as fh:
                fh.write(data)
        with open(os.path.join(self.pack, "index.json"), "w") as fh:
            json.dump({"levels": [{"file": "01_pack.gmd", "replay": "01_pack.gdr2", "name": "Pack",
                                   "difficulty": "easy", "seconds": 10}]}, fh)
        self.assertEqual(self.menu.my_levels(), [mine])
        pack = self.menu.pack_levels()
        self.assertEqual([e["name"] for e in pack], ["Pack", "Physics Test"])
        self.assertTrue(pack[0]["replay"].endswith("01_pack.gdr2"))

    def test_no_levels_folder_yet_is_not_an_error(self):
        self.menu.LEVELS = os.path.join(self.dir, "not there")
        self.assertEqual(self.menu.my_levels(), [])

    def test_level_info(self):
        path = self.make("New_level.gmd", "New level", lambda b: (b.add(13, 300, 45), b.add(660, 600, 45), b.add(13, 900, 45)))
        info = self.menu.level_info(path)
        self.assertEqual(info["name"], "New level")            # default title: fall back to the file name
        self.assertEqual(info["modes"], [0, 1, 4])             # in order of appearance, no repeats
        self.assertEqual(info["objects"], 4)
        named = self.make("x.gmd", "Sky High")
        self.assertEqual(self.menu.level_info(named)["name"], "Sky High")
        bad = os.path.join(self.levels, "bad.gmd")
        with open(bad, "w") as fh:
            fh.write("not a level")
        self.assertIsNone(self.menu.level_info(bad))

    def test_unique_path_never_overwrites(self):
        first = self.menu.unique_path(self.levels, "My: Level?")
        self.assertEqual(os.path.basename(first), "My_ Level_.gmd")
        with open(first, "w"):
            pass
        second = self.menu.unique_path(self.levels, "My: Level?")
        self.assertNotEqual(first, second)
        self.assertEqual(os.path.basename(second), "My_ Level_ 2.gmd")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class FakeCanvas:
    """Just enough of a tkinter Canvas for Effects to run without a window."""

    def __init__(self):
        self.items = {}

    def _new(self, **kw):
        self.items[len(self.items) + 1] = dict(kw)
        return len(self.items)

    def create_polygon(self, *coords, **kw):
        return self._new(**kw)

    create_line = create_rectangle = create_polygon

    def itemconfigure(self, item, **kw):
        self.items[item].update(kw)

    def coords(self, item, *coords):
        self.items[item]["coords"] = coords

    def shown(self, ids):
        return [i for i in ids if self.items[i].get("state") == "normal"]


class Trails(unittest.TestCase):
    """The rules for when each trail shows, which are GD's (see gd/effects.py)."""

    def run_level(self, build=None, mode=0, presses=(), ticks=400, flipped=False):
        from gd.effects import Effects
        from gd.sim import Simulation
        b = LevelBuilder(mode=mode, flipped=flipped)
        b.block(300, 0)
        if build:
            build(b)
        sim = Simulation(b.level())
        cv = FakeCanvas()
        fx = Effects(cv, 1.5, "#101010")
        seen = []
        for t in range(ticks):
            pressed = any(a <= t < z for a, z in presses)
            clicked = pressed and not sim.button
            sim.step(pressed)
            fx.tick(sim, clicked)
            seen.append((sim.tick, fx.streak_on, sim.player.can_jump, int(sim.player.mode)))
            if sim.dead:
                break
        fx.draw(1 / 60.0, 0.0, sim.player.x - 300)
        return sim, fx, cv, seen

    def test_blend(self):
        from gd.effects import blend
        self.assertEqual(blend("#000000", "#ffffff", 0.0), "#000000")
        self.assertEqual(blend("#000000", "#ffffff", 1.0), "#ffffff")
        self.assertEqual(blend("#102030", "#304050", 0.5), "#203040")
        self.assertEqual(blend("#000000", "#ffffff", 7.0), "#ffffff")

    def test_plain_cube_jump_leaves_no_trail(self):
        _sim, _fx, _cv, seen = self.run_level(presses=[(20, 24)], ticks=200)
        self.assertFalse(any(on for _t, on, _g, _m in seen))

    def test_pad_starts_the_cube_trail_and_landing_ends_it(self):
        _sim, _fx, _cv, seen = self.run_level(lambda b: b.add(35, 150, 2), ticks=420)
        on = [t for t, streak, _g, _m in seen if streak]
        self.assertTrue(on, "a pad must start the trail")
        landed = next(t for t, _s, grounded, _m in seen if grounded and t > on[0])
        self.assertEqual(max(on), landed - 1, "the trail stops on the step the cube lands")

    def test_orb_starts_the_trail(self):
        def build(b):
            b.add(36, 345, 75)                      # a yellow orb on the arc of the first jump
        _sim, _fx, _cv, seen = self.run_level(build, presses=[(190, 196), (236, 242)], ticks=330)
        self.assertTrue(any(on for _t, on, _g, _m in seen))

    def test_flying_modes_always_trail(self):
        for mode in (1, 3, 7):
            _sim, fx, cv, seen = self.run_level(mode=mode, ticks=120)
            self.assertTrue(all(on for _t, on, _g, _m in seen), mode)
            self.assertTrue(cv.shown(fx.it_seg), "the ribbon is drawn for mode %d" % mode)

    def test_ball_gets_no_trail_from_a_gravity_portal_but_the_cube_does(self):
        portal = lambda b: b.add(11, 200, 45)       # noqa: E731
        _sim, _fx, _cv, ball = self.run_level(portal, mode=2, ticks=200)
        _sim, _fx, _cv, cube = self.run_level(portal, mode=0, ticks=200)
        self.assertFalse(any(on for _t, on, _g, _m in ball))
        self.assertTrue(any(on for _t, on, _g, _m in cube))

    def test_wave_leaves_a_solid_trail_with_a_point_at_every_turn(self):
        # hold, release, hold: two changes of direction after the start
        _sim, fx, cv, _seen = self.run_level(mode=4, presses=[(5, 60), (100, 140)], ticks=160)
        self.assertTrue(fx.wave_active)
        # start, settling on the floor, up, down, sliding on the floor, up again
        self.assertEqual(len(fx.wave_points), 6)
        xs = [x for x, _y in fx.wave_points]
        self.assertEqual(xs, sorted(xs))
        self.assertTrue(cv.shown([fx.it_wave, fx.it_core]))
        self.assertFalse(cv.shown(fx.it_seg), "the wave shows its solid trail, not the fading ribbon")

    def test_wave_trail_fades_out_after_the_wave_ends(self):
        from gd.effects import WAVE_FADE_TICKS
        build = lambda b: b.add(12, 400, 45)        # noqa: E731  (a cube portal on the floor)
        sim, fx, _cv, _seen = self.run_level(build, mode=4, ticks=330)
        self.assertEqual(int(sim.player.mode), 0)
        self.assertFalse(fx.wave_active)
        _sim, fx2, _cv, _seen = self.run_level(build, mode=4, ticks=330 + WAVE_FADE_TICKS + 5)
        self.assertEqual(fx2.wave_points, [])

    def test_spider_teleport_draws_a_beam(self):
        _sim, fx, cv, _seen = self.run_level(mode=6, presses=[(30, 34)], ticks=40)
        self.assertIsNotNone(fx.beam)
        self.assertAlmostEqual(abs(fx.beam[3] - fx.beam[1]), 240 - 27, delta=1.0)   # floor to ceiling
        self.assertTrue(cv.shown([fx.it_beam]))

    def test_fire(self):
        _sim, fx, _cv, _seen = self.run_level(mode=1, presses=[(0, 50)], ticks=50)       # ship, holding
        self.assertEqual(len(fx.flames), 1)
        _sim, fx, _cv, _seen = self.run_level(mode=1, ticks=50)                          # ship, not holding
        self.assertEqual(fx.flames, [])
        _sim, fx, _cv, _seen = self.run_level(mode=7, ticks=50)                          # swing: back + one side
        self.assertEqual(len(fx.flames), 2)
        _sim, fx, _cv, _seen = self.run_level(mode=5, presses=[(20, 60)], ticks=45)      # robot, boosting
        self.assertEqual(len(fx.flames), 1)
        _sim, fx, _cv, _seen = self.run_level(mode=5, presses=[(20, 24)], ticks=45)      # robot, tapped
        self.assertEqual(fx.flames, [])

    def test_reset_clears_everything(self):
        _sim, fx, _cv, _seen = self.run_level(mode=4, presses=[(5, 60)], ticks=80)
        fx.reset()
        self.assertEqual((len(fx.history), fx.wave_points, fx.particles, fx.beam), (0, [], [], None))


class Autosave(Temp):
    """The editor's saving logic, without opening a window."""

    def setUp(self):
        super().setUp()
        from gd import editor_tk
        self.mod = editor_tk
        self.levels, self.backups = os.path.join(self.dir, "levels"), os.path.join(self.dir, "backups")
        old = editor_tk.LEVELS, editor_tk.BACKUPS
        editor_tk.LEVELS, editor_tk.BACKUPS = self.levels, self.backups
        self.addCleanup(lambda: setattr(editor_tk, "LEVELS", old[0]) or setattr(editor_tk, "BACKUPS", old[1]))

    def editor(self, path=None):
        from gd.editor_model import LevelDocument
        ed = self.mod.Editor.__new__(self.mod.Editor)
        ed.doc = LevelDocument.load(path) if path else LevelDocument()
        ed.save_error, ed._retry_at, ed._created, ed._backed_up = "", 0.0, None, set()
        return ed

    def test_an_untouched_new_level_writes_nothing(self):
        ed = self.editor()
        self.assertTrue(ed.autosave())
        self.assertFalse(os.path.exists(self.levels))

    def test_a_new_level_gets_its_own_file_on_the_first_change(self):
        ed = self.editor()
        ed.doc.place(1, 45, 15)
        self.assertTrue(ed.autosave())
        self.assertEqual(os.listdir(self.levels), ["New level.gmd"])
        self.assertFalse(ed.doc.dirty)
        other = self.editor()                                   # a second new level never overwrites the first
        other.doc.place(8, 75, 15)
        other.autosave()
        self.assertEqual(sorted(os.listdir(self.levels)), ["New level 2.gmd", "New level.gmd"])
        self.assertFalse(os.path.exists(self.backups), "files made this session need no backup")

    def test_an_existing_file_is_backed_up_once_then_overwritten(self):
        os.makedirs(self.levels)
        path = os.path.join(self.levels, "mine.gmd")
        write_gmd(path, "Mine", LevelBuilder().block(3, 0).string())
        original = read(path)
        ed = self.editor(path)
        ed.doc.place(8, 195, 15)
        self.assertTrue(ed.autosave())
        first = read(path)
        self.assertNotEqual(first, original)
        backup = os.path.join(self.backups, "mine.gmd")
        self.assertEqual(read(backup), original)
        ed.doc.place(8, 225, 15)
        ed.autosave()
        self.assertNotEqual(read(path), first)
        self.assertEqual(read(backup), original, "the backup stays the version from before the session")
        self.assertEqual([n for n in os.listdir(self.levels) if n.endswith(".tmp")], [])

    def test_a_failed_save_is_reported_and_keeps_the_changes(self):
        ed = self.editor()
        ed.doc.place(1, 45, 15)
        ed.doc.path = ed._created = os.path.join(self.dir, "missing folder", "x.gmd")
        self.assertFalse(ed.autosave())
        self.assertTrue(ed.save_error)
        self.assertTrue(ed.doc.dirty, "still marked as unsaved, so it is tried again")
        ed.doc.path = ed._created = os.path.join(self.dir, "x.gmd")
        self.assertTrue(ed.autosave())
        self.assertEqual(ed.save_error, "")

