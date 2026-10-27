#!/usr/bin/env python3
"""Play or simulate a Geometry Dash level.

  python3 main.py                              # level select menu
  python3 main.py mylevel.gmd                  # any level file (.gmd .gmd2 .lvl .dat .txt .json)
  python3 editor.py                            # level editor
  python3 main.py mylevel.gmd --replay run.gdr2
  python3 main.py CCLocalLevels.dat --list
  python3 main.py mylevel.gmd --headless --replay run.gdr2 --trace trace.csv
  python3 tools/build_app.py                   # make "GD Remake.app" (macOS)
"""
import argparse
import csv
import os
import sys

from gd import constants as C
from gd import objects
from gd.level import Level
from gd.levelio import LevelFormatError, list_levels, load_level_file
from gd.macros import InputSequence, MacroFormatError, load_macro
from gd.sim import Simulation

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEVEL = os.path.join(HERE, "levels", "test_level.gmd")
DEFAULT_REPLAY = os.path.join(HERE, "levels", "test_level.gdr2")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", nargs="?", help="level file (default: open the level select menu)")
    ap.add_argument("--select", help="which level to load from a multi-level file (index or name)")
    ap.add_argument("--list", action="store_true", help="list the levels inside the file and exit")
    ap.add_argument("--info", action="store_true", help="print the import report and exit")
    ap.add_argument("--replay", help="macro to play back (.gdr2 .gdr .json .txt)")
    ap.add_argument("--replay-offset", type=int, default=0, help="shift the macro by N ticks")
    ap.add_argument("--record", help="save your own inputs here on exit (.json or .gdr2)")
    ap.add_argument("--headless", action="store_true", help="no window: simulate and print the result")
    ap.add_argument("--trace", help="headless: write one CSV row per tick")
    ap.add_argument("--max-ticks", type=int, help="headless: stop after this many ticks")
    ap.add_argument("--objects", help="JSON file with extra object definitions")
    ap.add_argument("--hitboxes", action="store_true", help="start with hitboxes visible")
    ap.add_argument("--debug", action="store_true", help="start with the per-tick debug overlay visible")
    ap.add_argument("--practice", action="store_true", help="start in practice mode (checkpoints)")
    ap.add_argument("--no-save", action="store_true", help="do not record progress for this run")
    ap.add_argument("--zoom", type=float, default=1.5)
    ap.add_argument("--double-positions", action="store_true",
                    help="accumulate positions in doubles instead of C floats")
    ap.add_argument("--where", action="store_true",
                    help="print where the program, its Python and your files are, and exit")
    args = ap.parse_args(argv)
    if args.where:
        return where()

    if args.double_positions:
        C.FLOAT32_POSITIONS = False
    if args.objects:
        print("loaded %d object override(s)" % objects.load_overrides(args.objects))

    path = args.level
    replay_path = args.replay
    if path is None and not (args.headless or args.info or args.list):
        try:
            from gd.menu_tk import App as Menu
        except ImportError as exc:
            print("error: tkinter is not available (%s). Give a level and use --headless." % exc, file=sys.stderr)
            return 2
        Menu().run()
        return 0
    if path is None:
        path = DEFAULT_LEVEL
        if not os.path.exists(path):
            ap.error("no level given and %s is missing - run tools/make_test_level.py" % path)
        if replay_path is None and args.headless and os.path.exists(DEFAULT_REPLAY):
            replay_path = DEFAULT_REPLAY

    try:
        if args.list:
            for i, lv in enumerate(list_levels(path)):
                print("%3d  %-32s %7d chars  %s" % (i, lv.name, len(lv.level_string), lv.source))
            return 0
        raw = load_level_file(path, args.select)
    except (LevelFormatError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    level = Level(raw)
    print("loaded %r from %s" % (level.name, raw.source))
    print(level.report.summary())
    if args.info:
        return 0

    inputs = None
    if replay_path:
        try:
            inputs = load_macro(replay_path, args.replay_offset)
        except (MacroFormatError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        m = inputs.meta
        print("replay: %d input events, format %s%s" % (
            len(inputs), m.get("format", "?"),
            ", %d events for player 2 / other buttons dropped" % m["dropped_events"] if m.get("dropped_events") else ""))
        if m.get("warning"):
            print("  WARNING: " + m["warning"])

    sim = Simulation(level)
    if args.headless:
        trace = [] if args.trace else None
        sim.run(inputs or InputSequence(), args.max_ticks, trace)
        p = sim.player
        outcome = "COMPLETED" if sim.finished else ("DIED: " + p.death_cause if p.dead else "stopped")
        print("%s at tick %d (%.3f s), x=%.3f y=%.3f, %.1f%% of the level"
              % (outcome, sim.tick, sim.time, p.x, p.y, 100.0 * min(1.0, p.x / level.end_x)))
        for tick, text in sim.log[-12:]:
            print("  tick %6d  %s" % (tick, text))
        if trace is not None:
            with open(args.trace, "w", newline="") as fh:
                wr = csv.writer(fh)
                wr.writerow(["tick", "x", "y", "y_vel_native", "y_vel_units_per_s", "mode", "speed",
                             "flipped", "mini", "can_jump", "dead"])
                for r in trace:
                    wr.writerow([r[0], "%.6f" % r[1], "%.6f" % r[2], "%.3f" % r[3],
                                 "%.3f" % (r[3] * C.VEL_TO_UNITS_PER_SEC), r[4],
                                 C.SPEED_NAMES[r[5]], int(r[6]), int(r[7]), int(r[8]), int(r[9])])
            print("wrote %d rows to %s" % (len(trace), args.trace))
        return 0 if sim.finished else 1

    try:
        from gd.menu_tk import App
    except ImportError as exc:
        print("error: tkinter is not available (%s). Use --headless." % exc, file=sys.stderr)
        return 2
    app = App()
    started = app.play(path, practice=args.practice, replay=replay_path, standalone=True, save=not args.no_save,
                       select=args.select, replay_offset=args.replay_offset, record_path=args.record,
                       zoom=args.zoom, show_hitboxes=args.hitboxes, debug=args.debug)
    if not started:
        return 2
    app.run()
    return 0


def where():
    """Where everything comes from.  Inside the app nothing may come from outside it (the
    build script runs this to check); from source it simply shows your setup."""
    import importlib
    from gd import paths
    print("program        %s" % paths.ROOT)
    print("your files     %s" % paths.HOME)
    print("python         %s  (%s)" % (sys.version.split()[0], sys.prefix))
    print("standard lib   %s" % os.path.dirname(os.__file__))
    tcl = "?"
    try:
        import tkinter
        import _tkinter
        interp = tkinter.Tcl()
        tcl = interp.eval("info library")
        print("tkinter        %s" % os.path.dirname(tkinter.__file__))
        print("_tkinter       %s" % _tkinter.__file__)
        print("tcl library    %s  (Tcl %s)" % (tcl, interp.eval("info patchlevel")))
    except Exception as exc:
        print("tkinter        NOT AVAILABLE: %s" % exc)
        return 1
    # everything the game imports while running, plus what the importers and the bot need
    for name in ("gd.menu_tk", "gd.editor_tk", "gd.timing", "gd.share", "gd.online", "gd.fps", "gd.settings",
                 "http.client", "urllib.parse", "gd.macros", "gd.levelio", "hashlib", "zipfile",
                 "socket", "ipaddress", "secrets", "tkinter.simpledialog",
                 "gzip", "zlib", "json", "csv", "threading", "subprocess", "tkinter.filedialog",
                 "tkinter.messagebox", "tkinter.font"):
        importlib.import_module(name)
    import hashlib
    hashlib.sha1(b"x").hexdigest(), hashlib.md5(b"x").hexdigest(), hashlib.sha256(b"x").hexdigest()
    if not os.environ.get("GD_REMAKE_APP"):
        return 0
    # the build script's check: the window itself must come up, draw its menus and close
    from gd.menu_tk import App
    window = App()
    for show in (window.redraw, window.show_levels, window.show_mine, window.show_stats):
        show()
        window.root.update()
    window.quit()
    print("window         opened, drew every menu screen and closed")
    # ... and sharing must work with the app's own networking modules (this computer only)
    import time
    from gd import share
    from gd.level import LevelBuilder
    room = share.Room("check", port=0, discovery_port=0).start()
    guest = share.Guest("guest").join("127.0.0.1", room.port, room.code)
    room.share("Check", LevelBuilder().block(3, 0).string())
    deadline = time.time() + 10
    while time.time() < deadline and not guest.shelf:
        time.sleep(0.02)
    if guest.shelf:
        guest.request(guest.shelf[0]["id"])
    while time.time() < deadline and not guest.inbox:
        time.sleep(0.02)
    arrived = bool(guest.inbox) and guest.inbox[0][2] == LevelBuilder().block(3, 0).string()
    guest.leave()
    room.leave()
    print("sharing        %s" % ("a level went from a host to a guest on this computer" if arrived else "FAILED"))
    if not arrived:
        return 1
    app = os.path.dirname(os.path.dirname(os.path.dirname(paths.ROOT)))      # .../GD Remake.app
    outside = sorted({getattr(m, "__file__", None) or "" for m in list(sys.modules.values())} - {""})
    outside = [f for f in outside if not paths.inside(f, app)]
    outside += [p for p in (sys.prefix, os.path.dirname(os.__file__), tcl) if not paths.inside(p, app)]
    print("app            %s" % app)
    print("outside the app: %s" % (", ".join(outside) if outside else "none"))
    return 1 if outside else 0


def run_as_app():
    """Started by the macOS app's launcher.  A double-clicked app has no terminal, so a
    crash is written to savedata/last_error.txt in the player's folder and shown."""
    try:
        return main()
    except Exception:
        import traceback
        text = traceback.format_exc()
        where = ""
        try:
            from gd import paths
            os.makedirs(paths.SAVEDATA, exist_ok=True)
            where = os.path.join(paths.SAVEDATA, "last_error.txt")
            with open(where, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            where = ""
        try:
            from tkinter import messagebox
            messagebox.showerror("GD Remake stopped", "Something went wrong and the game has to close.\n\n%s%s"
                                 % (text.strip().splitlines()[-1], "\n\nDetails: " + where if where else ""))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(run_as_app() if os.environ.get("GD_REMAKE_APP") else main())
