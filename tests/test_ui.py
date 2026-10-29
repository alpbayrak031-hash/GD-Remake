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


class StartUp(unittest.TestCase):
    """The one test that opens the real window (for a moment): the app must come up, show
    every menu screen and close again.  It uses the throwaway player folder set at the
    top of this file."""

    def test_the_app_starts_and_every_menu_screen_draws(self):
        import tkinter as tk
        from gd import menu_tk
        try:
            app = menu_tk.App()
        except tk.TclError as exc:
            self.skipTest("no display: %s" % exc)
        errors = []
        app.root.report_callback_exception = lambda *args: errors.append(args[1])
        try:
            self.assertEqual(app.home_error, "")
            for show in (app.redraw, app.show_levels, app.show_mine, app.show_stats, app.home):
                show()
                app.root.update()
            self.assertGreater(len(app.cv.find_all()), 5, "the menu drew something")
            self.check_online_save(app)
            self.check_fps_toggle(app)
        finally:
            app.quit()
        self.assertEqual(errors, [])

    def check_online_save(self, app):
        """Save on the Online Levels screen puts a copy in My Levels, named after the level,
        and does not make a second copy when pressed again.  (The download is faked.)"""
        from gd import paths
        from gd.level import LevelBuilder
        from gd.levelio import load_level_file
        cached = os.path.join(paths.SAVEDATA, "online", "4242.gmd")
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        write_gmd(cached, "Saved Online", LevelBuilder().block(5, 0).string())
        app.show_online()
        screen = app.screen
        screen.fetched[4242] = cached
        screen.level_id.set("4242")
        screen.save()
        target = os.path.join(paths.USER_LEVELS, "Saved Online.gmd")
        self.assertTrue(os.path.isfile(target), os.listdir(paths.USER_LEVELS))
        self.assertEqual(load_level_file(target).name, "Saved Online")
        self.assertIn("Saved to My Levels", screen.done)
        screen.save()
        self.assertIn("Already in My Levels", screen.done)
        self.assertEqual([f for f in os.listdir(paths.USER_LEVELS) if f.startswith("Saved Online")],
                         ["Saved Online.gmd"])
        screen.level_id.set("not an id")
        screen.save()
        self.assertIn("ID", screen.error)
        app.home()

    def check_fps_toggle(self, app):
        """F shows and hides the counter in a level, the pause menu has it too, and the
        choice is remembered for the next level."""
        import types
        import time as clock
        from gd import paths, settings
        from gd.menu_tk import pack_levels
        self.assertFalse(settings.get("show_fps"))
        self.assertTrue(app.play(pack_levels()[0]["path"], save=False))
        game = app.game
        press = lambda key: game._key_press(types.SimpleNamespace(keysym=key))
        end = clock.time() + 0.6
        while clock.time() < end:                         # let a few frames go by
            app.root.update()
            clock.sleep(0.01)
        self.assertEqual(game.cv.itemcget(game.it_fps, "text"), "")
        press("f")
        game._draw(clock.perf_counter())
        shown = game.cv.itemcget(game.it_fps, "text")
        self.assertRegex(shown, r"^(\d+|--) FPS    1% low (\d+|--)    1% high (\d+|--)$")
        self.assertNotEqual(shown.split()[0], "--", "frames were counted")
        self.assertTrue(settings.get("show_fps"), "remembered")
        self.assertTrue(os.path.isfile(os.path.join(paths.SAVEDATA, "settings.json")))
        press("Escape")                                   # the pause menu lists it
        labels = [game.cv.itemcget(i, "text") for i in game.cv.find_withtag("panel")
                  if game.cv.type(i) == "text"]
        self.assertIn("FPS counter: on", labels)
        press("f")
        self.assertIn("FPS counter: off", [game.cv.itemcget(i, "text") for i in game.cv.find_withtag("panel")
                                           if game.cv.type(i) == "text"])
        game.exit()
        self.assertFalse(settings.get("show_fps"))


class GameViewDrawing(unittest.TestCase):
    """The game view draws only the part of the level near the camera, and moves what
    triggers move."""

    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest("no display: %s" % exc)
        self.addCleanup(self.root.destroy)

    def view(self, level):
        from gd.render_tk import GameView
        from gd.sim import Simulation
        v = GameView(self.root, Simulation(level))
        v.frame.pack(fill="both", expand=True)
        self.root.update_idletasks()
        return v

    def test_only_chunks_near_the_camera_are_on_the_canvas(self):
        from gd import render_tk
        b = LevelBuilder()
        for col in range(0, 2000, 4):                   # a block every 4 columns for 60,000 units
            b.block(col, 3)
        v = self.view(b.level())
        v._draw(0.0)
        near = len(v.cv.find_withtag("obj0"))
        self.assertEqual(near, 2, "the first block: its face and its inner line")
        self.assertEqual(v.cv.find_withtag("obj%d" % 499), (), "the last one is far away")
        self.assertLess(len(v.drawn), 6)
        v.sim.player.x = 30000.0
        v._draw(0.0)
        self.assertEqual(v.cv.find_withtag("obj0"), (), "dropped once the camera left it")
        self.assertTrue(v.cv.find_withtag("obj%d" % 250))
        # level objects stay under the player and the HUD, however late they were drawn
        order = v.cv.find_all()
        self.assertLess(order.index(v.cv.find_withtag("obj250")[0]), order.index(v.it_layer))
        self.assertLess(order.index(v.it_layer), order.index(v.it_bar))
        self.assertEqual(render_tk.FPS_CAP, 240)

    def test_moved_groups_move_on_screen_and_switched_off_ones_vanish(self):
        from gd import triggers as T
        b = LevelBuilder()
        b.add(1, 315, 105, k57="4")
        b.add(1, 345, 105, k57="5")
        b.add(T.MOVE, 0, 300, k51=4, k29=60)            # group 4 jumps up two blocks at once
        b.add(T.TOGGLE, 0, 330, k51=5, k56=0)           # group 5 is switched off
        b.block(100, 11)
        v = self.view(b.level())
        v._draw(0.0)
        (moving,) = [i for i in v.cv.find_withtag("k0") if "art" not in v.cv.gettags(i)]
        y0 = v.cv.coords(moving)[1]
        v.sim.step(False)
        v._draw(0.0)
        self.assertAlmostEqual(v.cv.coords(moving)[1], y0 - 60 * v.zoom, places=3)
        self.assertEqual(v.cv.find_withtag("k1"), (), "the switched-off block is gone")
        v.sim.reset()
        v._draw(0.0)
        self.assertAlmostEqual(v.cv.coords(moving)[1], y0, places=3)
        self.assertTrue(v.cv.find_withtag("k1"), "and back after a restart")

    def test_the_ceiling_slides_in_like_gd_and_covers_the_level_beyond_it(self):
        b = LevelBuilder()
        b.add(13, 45, 45)                               # a ship portal at the start
        b.block(4, 14)                                  # a block above the ceiling
        v = self.view(b.level())
        v.frame_dt = 1 / 60.0
        band = v.it_bounds["ceiling"][0]
        v._draw(0.0)
        self.assertEqual(v.cv.itemcget(band, "state"), "hidden", "a cube has no ceiling")
        while v.sim.ceiling is None:
            v.sim.step(False)
        v._draw(0.0)
        first = v.bound_at["ceiling"]
        self.assertGreater(first, v.sim.ceiling, "it starts above the screen ...")
        v._draw(0.0)
        self.assertTrue(v.sim.ceiling < v.bound_at["ceiling"] < first, "... comes down ...")
        for _ in range(120):
            v._draw(0.0)
        self.assertEqual(v.bound_at["ceiling"], v.sim.ceiling, "... and settles on the real ceiling")
        self.assertEqual(v.cv.itemcget(band, "state"), "normal")
        self.assertEqual(v.cv.itemcget(band, "fill"), v.ground, "in the ground's colour")
        order = v.cv.find_all()
        self.assertGreater(order.index(band), order.index(v.cv.find_withtag("obj1")[0]), "drawn over the level")
        v.sim.reset()
        v._draw(0.0)
        self.assertEqual(v.cv.itemcget(band, "state"), "hidden", "a restart takes it away at once")


class FpsCounter(unittest.TestCase):
    def feed(self, durations, start=100.0):
        from gd.fps import FrameStats
        stats, now = FrameStats(), start
        for d in durations:
            now += d
            stats.add(now, d)
        return stats, now

    def test_a_steady_frame_rate(self):
        stats, now = self.feed([1 / 60.0] * 600)
        s = stats.summary(now)
        self.assertAlmostEqual(s["fps"], 60.0, places=6)
        self.assertAlmostEqual(s["low"], 60.0, places=6)
        self.assertAlmostEqual(s["high"], 60.0, places=6)
        self.assertEqual(stats.text(now), "60 FPS    1% low 60    1% high 60")

    def test_one_stall_drags_the_one_percent_low_down_but_not_the_average(self):
        # 899 frames at 100 FPS and one 0.5 s stall (9.5 s in all): the slowest 1% are 9 frames
        stats, now = self.feed([0.01] * 450 + [0.5] + [0.01] * 449)
        s = stats.summary(now)
        self.assertEqual(s["frames"], 900)
        self.assertAlmostEqual(s["low"], 9 / (0.5 + 8 * 0.01), places=6)      # 15.5 FPS
        self.assertAlmostEqual(s["high"], 100.0, places=6)
        self.assertAlmostEqual(s["fps"], 100.0, places=6)                    # the stall was 4.5 s ago
        self.assertIn("100 FPS    1% low 16    1% high 100", stats.text(now))

    def test_the_fastest_frames_make_the_one_percent_high(self):
        stats, now = self.feed([0.02] * 198 + [0.005, 0.004])                 # 200 frames: 1% is 2
        self.assertAlmostEqual(stats.summary(now)["high"], 2 / 0.009, places=6)

    def test_only_the_last_ten_seconds_count(self):
        stats, now = self.feed([0.5] * 40 + [0.01] * 1500)                     # slow, then 15 s at 100 FPS
        s = stats.summary(now)
        self.assertAlmostEqual(s["frames"], 1000, delta=1)                     # (the frame on the edge may count)
        self.assertAlmostEqual(s["low"], 100.0, places=6)

    def test_too_few_frames_for_one_percent_figures(self):
        stats, now = self.feed([0.01] * 10)
        s = stats.summary(now)
        self.assertEqual((s["low"], s["high"]), (None, None))
        self.assertEqual(stats.text(now), "100 FPS    1% low --    1% high --")
        from gd.fps import FrameStats
        self.assertEqual(FrameStats().text(5.0), "-- FPS    1% low --    1% high --")
        stats.add(now, 0.0)                                                    # nonsense is ignored
        stats.add(now, -1.0)
        self.assertEqual(stats.summary(now)["frames"], 10)
        stats.reset()
        self.assertEqual(stats.summary(now)["frames"], 0)


class Settings(Temp):
    def test_remembered_and_safe(self):
        from gd import settings
        old = settings._path
        settings._path = lambda: os.path.join(self.dir, "save", "settings.json")
        self.addCleanup(setattr, settings, "_path", old)
        self.assertFalse(settings.get("show_fps"), "off until switched on")
        settings.put("show_fps", True)
        self.assertTrue(settings.get("show_fps"))
        settings.put("show_fps", False)
        self.assertFalse(settings.get("show_fps"))
        for junk in ("{ not json", "[]", json.dumps({"show_fps": "yes", "unknown": 1})):
            with open(settings._path(), "w") as fh:
                fh.write(junk)
            self.assertEqual(settings.load(), settings.DEFAULTS)


class EditorSelection(unittest.TestCase):
    """The document side of the editor's Select tool: every change is one undo step and
    never alters an object in place (the undo history keeps the old ones)."""

    def doc(self):
        from gd.editor_model import LevelDocument
        d = LevelDocument()
        self.a = d.place(1, 45, 15)
        self.b = d.place(8, 75, 15)
        self.c = d.place(36, 300, 105)
        return d

    def where(self, d):
        return sorted((o.id, o.x, o.y, o.rot) for o in d.objects)

    def test_box_selection(self):
        d = self.doc()
        self.assertEqual({o.id for o in d.inside(0, 100, 0, 40)}, {1, 8})
        self.assertEqual({o.id for o in d.inside(280, 320, 90, 120)}, {36})
        self.assertEqual(d.inside(500, 600, 0, 40), [])

    def test_move_and_undo(self):
        d = self.doc()
        before = self.where(d)
        moved = d.move([self.a, self.b], 30, 30)
        self.assertEqual(sorted((o.id, o.x, o.y) for o in moved), [(1, 75.0, 45.0), (8, 105.0, 45.0)])
        self.assertEqual((self.a.x, self.a.y), (45.0, 15.0), "the original object is untouched")
        self.assertEqual(len(d.objects), 3)
        d.undo()
        self.assertEqual(self.where(d), before)
        d.redo()
        self.assertIn((1, 75.0, 45.0, 0.0), self.where(d))

    def test_turn_keeps_a_group_together(self):
        d = self.doc()
        turned = d.turn([self.a, self.b])
        self.assertEqual(sorted((o.id, o.x, o.y, o.rot) for o in turned), [(1, 60.0, 30.0, 90.0), (8, 60.0, 0.0, 90.0)])
        d.turn(turned)
        d.undo()
        d.undo()
        self.assertEqual(self.where(d), sorted([(1, 45.0, 15.0, 0.0), (8, 75.0, 15.0, 0.0), (36, 300.0, 105.0, 0.0)]))

    def test_four_turns_come_back(self):
        d = self.doc()
        objs = [self.a, self.b]
        for _ in range(4):
            objs = d.turn(objs)
        self.assertEqual(sorted((o.id, o.x, o.y, o.rot) for o in objs), [(1, 45.0, 15.0, 0.0), (8, 75.0, 15.0, 0.0)])
        self.assertTrue(all(6 not in o.raw for o in objs), "a full turn leaves no rotation key behind")

    def test_copy_paste_and_remove(self):
        d = self.doc()
        pasted = d.paste([dict(self.a.raw), dict(self.b.raw)], 90, 0)
        self.assertEqual(sorted((o.id, o.x) for o in pasted), [(1, 135.0), (8, 165.0)])
        self.assertEqual(len(d.objects), 5)
        self.assertTrue(d.remove(pasted))
        self.assertEqual(len(d.objects), 3)
        self.assertFalse(d.remove(pasted), "already gone")
        d.undo()
        self.assertEqual(len(d.objects), 5)
        d.undo()
        self.assertEqual(len(d.objects), 3)
        self.assertEqual(d.paste([], 30, 0), [])

    def test_the_fast_paths_agree_with_a_full_rescan(self):
        """The document keeps an index, counts and the level's extent up to date as it goes
        (that is what keeps the editor quick on big levels); after any mix of edits they
        must say exactly what scanning every object says."""
        import random
        from gd.editor_model import LevelDocument
        d, rng = LevelDocument(), random.Random(3)

        def check():
            objs = d.objects
            xs, ys = [o.x for o in objs] or [0.0], [o.y for o in objs] or [0.0]
            sim = sum(1 for o in objs if o.phys is not None)
            self.assertEqual(d.counts(), (sim, len(objs) - sim))
            self.assertEqual(d.extent(), (min(xs), max(xs), min(ys), max(ys)))
            self.assertEqual({o.uid for o in d.near(500, 900)}, {o.uid for o in objs if 500 <= o.x <= 900})
            self.assertEqual(d.alive(objs[:5]), objs[:5])

        check()
        for _ in range(600):
            r = rng.random()
            if r < 0.55 or not d.objects:
                d.place(rng.choice((1, 8, 36, 914, 12)), rng.randrange(0, 3000, 15), rng.randrange(0, 600, 15))
            elif r < 0.75:
                d.erase(rng.choice(d.objects))
            elif r < 0.85:
                d.move(rng.sample(d.objects, min(3, len(d.objects))), rng.choice((-30, 30)), rng.choice((-15, 0, 15)))
            elif r < 0.9:
                d.remove(rng.sample(d.objects, min(2, len(d.objects))))
            elif r < 0.95:
                d.undo()
            else:
                d.paste([dict(o.raw) for o in d.objects[:2]], 60, 0)
            check()

    def test_what_changed_is_listed_for_the_editor(self):
        from gd.editor_model import LevelDocument
        d = LevelDocument()
        self.assertIsNone(d.take_changes(), "a fresh document: redraw everything")
        a = d.place(1, 45, 15)
        b = d.place(8, 75, 15)
        self.assertEqual(d.take_changes(), [("add", a), ("add", b)])
        d.erase(a)
        moved = d.move([b], 30, 0)
        self.assertEqual(d.take_changes(), [("remove", a), ("remove", b), ("add", moved[0])])
        d.undo()
        self.assertIsNone(d.take_changes(), "undo replaces the whole list")
        self.assertEqual(d.take_changes(), [])

    def test_new_palette_objects_are_all_simulated(self):
        from gd import objects as O
        from gd.editor_model import PALETTE, EdObject
        for group, items in PALETTE:
            if group in ("Triggers", "Decoration"):         # no physics by design (tested below)
                continue
            for oid, label in items:
                self.assertIn(oid, O.OBJECTS, label)
                self.assertIsNotNone(EdObject({1: str(oid), 2: "15", 3: "15"}).phys, label)


class EditorTriggersAndDecoration(unittest.TestCase):
    """The editor's Triggers and Decoration tabs and its properties window (the logic)."""

    def test_every_new_palette_object_is_something(self):
        from gd.editor_model import PALETTE, EdObject, is_trigger
        tabs = dict(PALETTE)
        for oid, label in tabs["Triggers"]:
            self.assertTrue(is_trigger(oid), label)
        for oid, label in tabs["Decoration"]:
            o = EdObject({1: str(oid), 2: "15", 3: "15"})
            self.assertIsNone(o.phys, label)
            self.assertIsNotNone(o.deco, label)
        self.assertIn((747, "Teleport"), tabs["Other portals"])

    def test_a_trigger_set_up_in_the_window_works_in_the_level(self):
        from gd.editor_model import LevelDocument, fields_for, form_values, parse_form
        from gd.sim import Simulation
        doc = LevelDocument()
        for row in range(12):                           # a wall in group 3 ...
            wall = doc.place(1, 615, 15 + 30 * row)
            doc.set_values([wall], parse_form(fields_for([wall]), {57: "3"}))
        doc.place(1, 2415, 345)                         # (the level goes on past it)
        trigger = doc.place(1049, 300, 200)             # ... which a toggle trigger switches off
        fields = fields_for([trigger])
        self.assertEqual([f[0] for f in fields], [57, 51, 56, 11, 62, 87])
        (trigger,) = doc.set_values([trigger], parse_form(fields, {51: "3", 56: "0"}))
        self.assertEqual(form_values([trigger], fields)[51], "3")
        sim = Simulation(doc.to_level())
        while not (sim.dead or sim.finished) and sim.tick < 1200:
            sim.step(False)
        self.assertFalse(sim.dead, sim.player.death_cause)

    def test_bad_input_is_explained_and_units_are_converted(self):
        from gd.editor_model import fields_for, parse_form, EdObject
        move = [EdObject({1: "901", 2: "0", 3: "0"})]
        fields = fields_for(move)
        out = parse_form(fields, {51: "2", 28: "1.5", 30: "Bounce out", 58: "1"})
        self.assertEqual((out[51], out[28], out[30], out[58], out[29]), ("2", "45", "9", "1", None))
        for bad in ({51: "two"}, {51: "-1"}, {57: "1, x"}):
            with self.assertRaises(ValueError):
                parse_form(fields, bad)
        mixed = [EdObject({1: "1", 2: "0", 3: "0"}), EdObject({1: "901", 2: "0", 3: "0"})]
        self.assertEqual([f[0] for f in fields_for(mixed)], [57], "different kinds: only their groups")


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

    def test_files_are_named_after_their_levels(self):
        from gd.timing import Timings
        first = self.make("New level.gmd", "Sky High")              # named in the editor after its first save
        self.make("New_level.gmd", "Wave: Challenge?")              # the name needs cleaning to be a file name
        self.make("Mine.gmd", "Mine")
        self.make("Mine 2.gmd", "Mine")                             # a second level with the same name
        self.make("draft.gmd", "New level")                         # never named: the file name is its name
        with open(os.path.join(self.levels, "junk.gmd"), "w") as fh:
            fh.write("not a level")
        trash = os.path.join(self.levels, "trash")
        os.makedirs(trash)
        self.make("old.gmd", "Bug Recreation", folder=trash)
        progress = Progress(self.save)
        progress.reached(level_key(first), 55)
        progress.reached(level_key(os.path.join(self.levels, "old.gmd")), 30)
        timings = Timings(os.path.join(self.dir, "save", "timing.json"))
        timings.store(first, timings.digest(first), {"state": "free"})

        moved = self.menu.tidy_level_files(progress, timings)
        self.assertEqual(sorted(os.path.basename(new) for _old, new in moved),
                         ["Bug Recreation.gmd", "Sky High.gmd", "Wave_ Challenge_.gmd"])
        self.assertEqual(sorted(os.listdir(self.levels)), ["Mine 2.gmd", "Mine.gmd", "Sky High.gmd",
                                                           "Wave_ Challenge_.gmd", "draft.gmd", "junk.gmd", "trash"])
        self.assertEqual(os.listdir(trash), ["Bug Recreation.gmd"])
        renamed = os.path.join(self.levels, "Sky High.gmd")
        self.assertEqual(progress.get(level_key(renamed))["best"], 55.0, "progress follows the file")
        self.assertNotIn(level_key(first), progress.levels)
        self.assertEqual(Progress(self.save).get(level_key(renamed))["best"], 55.0, "... and is saved")
        self.assertEqual(progress.get(level_key(os.path.join(self.levels, "Bug Recreation.gmd")))["best"], 30.0)
        self.assertEqual(timings.lookup(renamed), {"state": "free"}, "the measured timing follows too")
        self.assertEqual(self.menu.level_info(renamed)["name"], "Sky High")
        self.assertEqual(self.menu.tidy_level_files(progress, timings), [], "a tidy folder is left alone")

        # a name that is taken is never overwritten
        self.make("zzz.gmd", "Sky High")
        moved = self.menu.tidy_level_files(progress, timings)
        self.assertEqual([os.path.basename(new) for _old, new in moved], ["Sky High 2.gmd"])
        self.assertTrue(os.path.isfile(renamed))
        self.assertEqual(self.menu.tidy_level_files(), [])

    def test_no_levels_folder_yet_is_not_an_error(self):
        self.menu.LEVELS = os.path.join(self.dir, "not there")
        self.assertEqual(self.menu.my_levels(), [])
        self.assertEqual(self.menu.tidy_level_files(), [])

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

