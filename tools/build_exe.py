#!/usr/bin/env python3
r"""Build "GD Remake.exe" for Windows - a self-contained app.

    python tools\build_exe.py              ->  dist\GD Remake\GD Remake.exe
    python tools\build_exe.py FOLDER       ->  FOLDER\GD Remake\GD Remake.exe
    python tools\build_exe.py --one-file   ->  dist\GD Remake.exe (one file, slower to start)

The app carries everything it runs on: the .exe, the game's Python source and built-in
levels, and a private copy of the Python runtime it was built with (the interpreter, the
standard library, and Tcl/Tk for the window).  Nothing has to be installed on the PC that
runs it - copy the folder anywhere and double-click "GD Remake.exe".  The default build
is a folder because it starts straight away; --one-file is a single .exe that unpacks
itself into a temporary folder on every start, which takes a few seconds.

To BUILD you need PyInstaller (`pip install pyinstaller`), which writes the .exe and
collects the runtime; Pillow (`pip install pillow`) draws the icon, and without it the app
keeps Python's own.  No compiler is needed.

The window has no console behind it, so the game's print() output goes nowhere unless
GD_REMAKE_LOG names a file for it (tools/win_app_hook.py, which runs inside the app).

After building, the script starts the app with a throwaway folder for its files and with
PYTHON*/TCL*/TK* taken out of its environment, has it open its window, draw every menu
screen and pass a level between a host and a guest, and fails if that does not work or if
anything it loaded came from outside the app (main.py --where).

The game never writes inside the app: levels you make and your progress live in
Documents/GD Remake (see gd/paths.py).
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "GD Remake"
VERSION = "1.7"
DIST = os.path.join(ROOT, "dist")
HOOK = os.path.join(ROOT, "tools", "win_app_hook.py")

# Modules the game reaches for by name while it runs, which PyInstaller cannot find by
# reading the source (main.py --where imports this same list).
HIDDEN = ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.simpledialog", "tkinter.font",
          "http.client", "urllib.parse", "ssl", "hashlib", "zipfile", "socket", "ipaddress", "secrets",
          "gzip", "zlib", "json", "csv", "threading", "subprocess", "array", "bisect")
# Left out: the test suites, the documentation tools, and everything installed beside the
# game that it never imports.  Nothing here is reachable from main.py.
EXCLUDE = ("tests", "tools", "unittest", "doctest", "pydoc", "pdb", "idlelib", "lib2to3", "turtledemo",
           "setuptools", "pip", "PIL", "numpy", "pygame", "sqlite3", "distutils")

# The icon is the picture the macOS app's icon is drawn from (the SVG in tools/build_app.py):
# a dark rounded tile, the ground with its white line, a spike, and the cube, leaning left.
SKY = ((0x1B, 0x25, 0x42), (0x0B, 0x0F, 0x1A))     # the sky, top and bottom
TILE = (100, 100, 924, 924, 186)                   # the rounded square everything is drawn inside

VERSION_RESOURCE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=(%(major)s, %(minor)s, 0, 0), prodvers=(%(major)s, %(minor)s, 0, 0),
                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
          StringStruct('FileDescription', 'GD Remake - a Geometry Dash physics recreation'),
          StringStruct('FileVersion', '%(version)s'),
          StringStruct('InternalName', 'GD Remake'),
          StringStruct('OriginalFilename', 'GD Remake.exe'),
          StringStruct('ProductName', 'GD Remake'),
          StringStruct('ProductVersion', '%(version)s')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return "(it wrote no report)"


def folder_size(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(base, name)
            if not os.path.islink(full):
                total += os.path.getsize(full)
    return total


# ---------------------------------------------------------------------------- the program
def stage_levels(work):
    """The built-in level pack, gathered where PyInstaller can pick it up: index.json, the
    levels it lists with their replays, and the test level.  Levels the player made are not
    part of the program and are never copied."""
    sys.path.insert(0, ROOT)
    from gd.menu_tk import load_index
    folder = os.path.join(work, "levels")
    os.makedirs(folder)
    wanted = ["index.json", "test_level.gmd", "test_level.gdr2", "test_level.replay.json"]
    for entry in load_index():
        wanted += [entry["file"]] + ([entry["replay"]] if entry.get("replay") else [])
    for name in wanted:
        source = os.path.join(ROOT, "levels", name)
        if os.path.isfile(source):
            shutil.copy2(source, folder)
    return folder, len(os.listdir(folder))


def make_icon(work):
    """AppIcon.ico, drawn at twice life size and shrunk so the edges come out smooth.  It
    goes into the .exe (Explorer and the taskbar) and beside the program, where gd/ui.py
    finds it for the window itself.  Returns "" when Pillow is not installed."""
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        return ""
    k = 2                                                  # draw this many times life size
    n = 1024 * k

    def s(*values):                                        # the SVG's coordinates, scaled
        return tuple(round(v * k) for v in values)

    x0, y0, x1, y1, radius = TILE
    column = Image.new("RGB", (1, n))                      # the sky: a gradient down the tile
    column.putdata([tuple(round(top + (bottom - top) * min(1.0, max(0.0, (y / k - y0) / (y1 - y0))))
                          for top, bottom in zip(*SKY)) for y in range(n)])
    sky = column.resize((n, n))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(s(x0, y0, x1, y1), radius=radius * k, fill=255)

    scene = Image.new("RGBA", (n, n), (0, 0, 0, 0))        # the ground, its line, and a spike
    draw = ImageDraw.Draw(scene)
    draw.rectangle(s(100, 742, 924, 924), fill="#070A12")
    draw.rectangle(s(100, 736, 924, 746), fill="#DDE3F0")
    draw.polygon([s(664, 736), s(744, 586), s(824, 736)], fill="#6E1B24", outline="#FF5A5A", width=10 * k)

    cube = Image.new("RGBA", (n, n), (0, 0, 0, 0))         # the icon, on a layer of its own so it can lean
    face = ImageDraw.Draw(cube)
    face.rounded_rectangle(s(250, 350, 590, 690), radius=26 * k, fill="#6BE35A")
    face.rounded_rectangle(s(302, 402, 538, 638), radius=10 * k, fill="#0D111C")
    face.rectangle(s(338, 444, 394, 500), fill="#FFFFFF")  # eyes
    face.rectangle(s(446, 444, 502, 500), fill="#FFFFFF")
    face.rectangle(s(338, 548, 502, 588), fill="#FFFFFF")  # mouth
    cube = cube.rotate(14, resample=Image.BICUBIC, center=s(420, 520))     # the SVG's rotate(-14 420 520)
    scene.alpha_composite(cube)
    scene.putalpha(ImageChops.multiply(scene.getchannel("A"), mask))       # nothing outside the tile

    icon = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    icon.paste(sky, (0, 0), mask)
    icon.alpha_composite(scene)
    path = os.path.join(work, "AppIcon.ico")
    icon.resize((1024, 1024), Image.LANCZOS).save(
        path, format="ICO", sizes=[(side, side) for side in (16, 24, 32, 48, 64, 128, 256)])
    return path


# ---------------------------------------------------------------------------- the build
def pyinstaller(work, levels, icon, one_file):
    """Hand it all to PyInstaller: the entry script, the level pack and the icon as data,
    the runtime hook, and the version Explorer shows in the file's properties.  Returns the
    warnings worth repeating."""
    resource = os.path.join(work, "version.txt")
    with open(resource, "w", encoding="utf-8") as fh:
        fh.write(VERSION_RESOURCE % {"major": VERSION.split(".")[0], "minor": VERSION.split(".")[1],
                                     "version": VERSION})
    data = [(levels, "levels"), (os.path.join(ROOT, "README.md"), ".")]
    if icon:
        data.append((icon, "."))                           # beside the program, for the window icon
    cmd = [sys.executable, "-m", "PyInstaller", os.path.join(ROOT, "main.py"),
           "--name", NAME, "--windowed", "--noupx", "--noconfirm", "--clean",
           "--onefile" if one_file else "--onedir",
           "--distpath", DIST, "--workpath", os.path.join(work, "build"), "--specpath", work,
           "--runtime-hook", HOOK, "--version-file", resource]
    if icon:
        cmd += ["--icon", icon]
    for source, target in data:
        cmd += ["--add-data", "%s%s%s" % (source, os.pathsep, target)]
    for name in HIDDEN:
        cmd += ["--hidden-import", name]
    for name in EXCLUDE:
        cmd += ["--exclude-module", name]
    done = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if done.returncode != 0:
        sys.exit("PyInstaller failed:\n%s" % "\n".join(done.stdout.splitlines()[-40:]))
    return [line.strip() for line in done.stdout.splitlines() if "Hidden import" in line and "not found" in line]


def self_check(exe, work):
    """Start the built app where it has nothing of this Python's environment and a throwaway
    folder for its files, and ask the game where everything came from.  The app has no
    console, so GD_REMAKE_LOG catches the report (tools/win_app_hook.py)."""
    log = os.path.join(work, "where.txt")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "TCL", "TK", "GD_REMAKE"))}
    env["GD_REMAKE_HOME"] = os.path.join(work, "home")
    env["GD_REMAKE_LOG"] = log
    try:
        done = subprocess.run([exe, "--where"], env=env, cwd=work, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        sys.exit("the built app never finished its self-check:\n%s" % read(log))
    report = read(log)
    if (done.returncode != 0 or "outside the app: none" not in report or "window " not in report
            or "sharing        a level went" not in report):
        sys.exit("the built app failed its self-check (exit %d):\n%s%s"
                 % (done.returncode, report, done.stdout or ""))
    return report


def running(exe):
    """The processes holding open the very app we are about to replace, if any.  Another
    copy of the game somewhere else - on the Desktop, say - is none of our business, so
    each process is matched by the file it is running, not just by its name."""
    if not os.path.exists(exe):
        return []
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s.exe" % NAME, "/FO", "CSV", "/NH"],
                             stdout=subprocess.PIPE, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    quoted = '"%s.exe"' % NAME
    pids = [line.split(",")[1].strip('"') for line in out.splitlines() if line.startswith(quoted)]
    want = os.path.normcase(os.path.abspath(exe))
    return [pid for pid in pids if os.path.normcase(image_of(int(pid))) == want]


def image_of(pid):
    """The file a process is running, asked of Windows directly ("" when it will not say)."""
    import ctypes
    QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32.dll")
    handle = kernel32.OpenProcess(QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------- putting it together
def build(one_file=False):
    if not sys.platform.startswith("win"):
        sys.exit("This builds a Windows app; run it on Windows.  (tools/build_app.py builds the macOS one.)")
    try:
        import PyInstaller                                 # noqa: F401 - imported to say so clearly
    except ImportError:
        sys.exit("PyInstaller is not installed: pip install pyinstaller")
    target = os.path.join(DIST, NAME + ".exe") if one_file else os.path.join(DIST, NAME)
    exe = target if one_file else os.path.join(target, NAME + ".exe")
    open_now = running(exe)
    if open_now:                                           # replacing its files would crash it
        sys.exit("%s is open (process %s). Quit it first, then build again." % (NAME, ", ".join(open_now)))
    if os.path.isdir(target):                              # only ever our own previous build
        shutil.rmtree(target)
    elif os.path.exists(target):
        os.remove(target)

    with tempfile.TemporaryDirectory(prefix="gdexe") as work:
        levels, files = stage_levels(work)
        icon = make_icon(work)
        warnings = pyinstaller(work, levels, icon, one_file)
        check = self_check(exe, work)

    size = folder_size(target) if os.path.isdir(target) else os.path.getsize(target)
    print("built %s  (%.0f MB)" % (exe, size / 1e6))
    print("  layout: %s   icon: %s   built-in level files: %d"
          % ("one file" if one_file else "folder", "yes" if icon else "no (install Pillow)", files))
    print("  self-contained: carries Python %s and Tcl/Tk copied from %s"
          % (sys.version.split()[0], sys.base_prefix))
    for line in check.splitlines():
        if line.startswith(("window", "sharing", "outside")):
            print("  self-check: %s" % line.strip())
    for line in warnings:
        print("  %s" % line)
    return exe


if __name__ == "__main__":
    folders = [a for a in sys.argv[1:] if a != "--one-file"]
    if folders:                                            # python tools\build_exe.py [output folder]
        DIST = os.path.abspath(folders[0])
    build(one_file="--one-file" in sys.argv[1:])
