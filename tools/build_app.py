#!/usr/bin/env python3
"""Build "GD Remake.app" for macOS - a self-contained app.

    python3 tools/build_app.py            ->  dist/GD Remake.app

The app carries everything it runs on: a small native launcher, the game's Python
source and built-in levels, and a private copy of the Python runtime it was built
with (the interpreter, the standard library, and Tcl/Tk for the window).  Nothing
has to be installed on the Mac that runs it.  The launcher runs the game inside
its own process, so the Dock shows one icon - the game's.

To BUILD you need a python.org "framework" Python (that is where the runtime is
copied from) and Xcode's command line tools (clang, install_name_tool, codesign);
sips and iconutil, which come with macOS, make the icon.  Without those the script
falls back to a shell-script launcher that uses the Python on the building Mac.

After building, the script starts the app's own Python in a sandbox that is denied
all access to the Python it was copied from, and fails if anything still reaches
outside the app.

The game never writes inside the app: levels you make and your progress live in
Documents/GD Remake (see gd/paths.py).
"""
import os
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "GD Remake"
BUNDLE_ID = "org.gdremake.GDRemake"
VERSION = "1.1"
MIN_MACOS = "11.0"                 # the oldest macOS python.org's own binaries run on (Apple Silicon)
DIST = os.path.join(ROOT, "dist")
PYVER = "%d.%d" % sys.version_info[:2]

# Standard-library parts a game does not need: test suites, IDLE, installers, docs.
STDLIB_SKIP = ("test", "idlelib", "turtledemo", "ensurepip", "lib2to3", "pydoc_data", "venv",
               "site-packages", "__pycache__", "config-%s-darwin" % PYVER, "__phello__")
# Extension modules left out: the interpreter's own test helpers, and the two that need
# OpenSSL (the game opens no network connections; hashlib falls back to its built-in code).
DYNLOAD_SKIP = ("_test", "_xx", "xx", "_ctypes_test", "_ssl.", "_hashlib.")

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1B2542"/>
      <stop offset="1" stop-color="#0B0F1A"/>
    </linearGradient>
    <clipPath id="tile"><rect x="100" y="100" width="824" height="824" rx="186"/></clipPath>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="186" fill="url(#sky)"/>
  <g clip-path="url(#tile)">
    <rect x="100" y="742" width="824" height="182" fill="#070A12"/>
    <rect x="100" y="736" width="824" height="10" fill="#DDE3F0"/>
    <polygon points="664,736 744,586 824,736" fill="#6E1B24" stroke="#FF5A5A" stroke-width="10" stroke-linejoin="round"/>
    <g transform="rotate(-14 420 520)">
      <rect x="250" y="350" width="340" height="340" rx="26" fill="#6BE35A"/>
      <rect x="302" y="402" width="236" height="236" rx="10" fill="#0D111C"/>
      <rect x="338" y="444" width="56" height="56" fill="#FFFFFF"/>
      <rect x="446" y="444" width="56" height="56" fill="#FFFFFF"/>
      <rect x="338" y="548" width="164" height="40" fill="#FFFFFF"/>
    </g>
  </g>
</svg>
"""

LAUNCHER_C = r"""
/* GD Remake launcher: runs Contents/Resources/app/main.py with the Python inside the app
   (Contents/Frameworks/Python.framework) when there is one, else with the Python this
   launcher was linked against. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define PYVER "@PYVER@"

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX], contents[PATH_MAX];
    char script[PATH_MAX + 64], home[PATH_MAX + 96], lib[PATH_MAX + 128];
    uint32_t size = sizeof(exe);
    struct stat st;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, real)) {
        fprintf(stderr, "GD Remake: cannot find myself\n");
        return 1;
    }
    char *slash = strrchr(real, '/');               /* .../Contents/MacOS/<name> */
    if (!slash) return 1;
    *slash = '\0';
    slash = strrchr(real, '/');                     /* .../Contents/MacOS */
    if (!slash) return 1;
    *slash = '\0';
    snprintf(contents, sizeof(contents), "%s", real);
    snprintf(script, sizeof(script), "%s/Resources/app/main.py", contents);
    snprintf(home, sizeof(home), "%s/Frameworks/Python.framework/Versions/" PYVER, contents);
    int bundled = stat(home, &st) == 0;

    setenv("GD_REMAKE_APP", "1", 1);                /* main.py reports crashes in a dialog */
    if (bundled) {                                  /* Tcl/Tk must use the copies in the app too */
        snprintf(lib, sizeof(lib), "%s/lib/tcl8.6", home);
        setenv("TCL_LIBRARY", lib, 1);
        snprintf(lib, sizeof(lib), "%s/lib/tk8.6", home);
        setenv("TK_LIBRARY", lib, 1);
    }

    char **args = calloc((size_t)argc + 2, sizeof(char *));
    int n = 0;
    args[n++] = argv[0];
    args[n++] = script;
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "-psn_", 5) != 0)      /* old Finders pass a process serial number */
            args[n++] = argv[i];

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.write_bytecode = 0;                      /* never write into the app bundle */
    config.use_environment = 0;                     /* ignore PYTHONPATH, PYTHONHOME, ... */
    config.user_site_directory = 0;                 /* and packages installed in the user's home */
    status = PyConfig_SetBytesArgv(&config, n, args);
    if (!PyStatus_Exception(status) && bundled)
        status = PyConfig_SetBytesString(&config, &config.home, home);
    if (!PyStatus_Exception(status))
        status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        fprintf(stderr, "GD Remake: Python did not start: %s\n", status.err_msg ? status.err_msg : "?");
        return 1;
    }
    return Py_RunMain();
}
""".replace("@PYVER@", PYVER)

LAUNCHER_SH = """#!/bin/sh
# Fallback launcher (no clang or no framework Python at build time): not self-contained,
# and it shows Python's Dock icon.
HERE="$(cd "$(dirname "$0")" && pwd)"
export GD_REMAKE_APP=1
export PYTHONDONTWRITEBYTECODE=1
exec "{python}" "$HERE/../Resources/app/main.py" "$@"
"""


def run(*cmd):
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout


def have(*tools):
    return all(shutil.which(tool) is not None for tool in tools)


def is_macho(path):
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError:
        return False
    return magic in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xbe\xba\xfe\xca",
                     b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce")


def machos(folder):
    out = []
    for base, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(base, name)
            if not os.path.islink(path) and is_macho(path):
                out.append(path)
    return sorted(out)


def links(path):
    """The libraries a Mach-O file loads (for every processor type in it)."""
    out = set()
    for line in run("otool", "-L", path).splitlines():
        if line.startswith("\t"):
            out.add(line.strip().split(" (compatibility")[0])
    return out


# ---------------------------------------------------------------------------- the program
def copy_program(app_dir):
    """The game itself: main.py, editor.py, the gd package and the built-in levels.
    Levels the player made are not part of the program and are never copied."""
    os.makedirs(app_dir)
    for name in ("main.py", "editor.py", "README.md"):
        shutil.copy2(os.path.join(ROOT, name), app_dir)
    shutil.copytree(os.path.join(ROOT, "gd"), os.path.join(app_dir, "gd"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sys.path.insert(0, ROOT)
    from gd.menu_tk import load_index
    levels = os.path.join(app_dir, "levels")
    os.makedirs(levels)
    wanted = ["index.json", "test_level.gmd", "test_level.gdr2", "test_level.replay.json"]
    for entry in load_index():
        wanted += [entry["file"]] + ([entry["replay"]] if entry.get("replay") else [])
    for name in wanted:
        source = os.path.join(ROOT, "levels", name)
        if os.path.isfile(source):
            shutil.copy2(source, levels)
    return len(os.listdir(levels))


def make_icon(resources, work):
    """AppIcon.icns from the SVG above (sips rasterises, iconutil packs).  Optional."""
    if not have("sips", "iconutil"):
        return False
    svg, big = os.path.join(work, "icon.svg"), os.path.join(work, "icon.png")
    with open(svg, "w", encoding="utf-8") as fh:
        fh.write(ICON_SVG)
    iconset = os.path.join(work, "AppIcon.iconset")
    os.makedirs(iconset)
    try:
        run("sips", "-s", "format", "png", svg, "--out", big)
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = "icon_%dx%d%s.png" % (points, points, "@2x" if scale == 2 else "")
                run("sips", "-z", str(points * scale), str(points * scale), big, "--out", os.path.join(iconset, name))
        run("iconutil", "-c", "icns", iconset, "-o", os.path.join(resources, "AppIcon.icns"))
    except (subprocess.CalledProcessError, OSError) as exc:
        print("  no icon: %s" % (getattr(exc, "stdout", "") or exc))
        return False
    return True


# ---------------------------------------------------------------------------- the runtime
def copy_runtime(frameworks):
    """A private Python.framework inside the app: interpreter, standard library, Tcl/Tk.
    Returns (the version folder, the path the system copy had) - or None when the Python
    running this script is not a framework build that can be copied."""
    source = sys.base_prefix                                    # .../Python.framework/Versions/3.x
    if not (sysconfig.get_config_var("PYTHONFRAMEWORK") and os.path.isfile(os.path.join(source, "Python"))
            and os.path.isfile(os.path.join(source, "lib", "libtk8.6.dylib"))
            and have("install_name_tool", "otool", "codesign")):
        return None
    framework = os.path.join(frameworks, "Python.framework")
    home = os.path.join(framework, "Versions", PYVER)
    lib = os.path.join(home, "lib")
    os.makedirs(lib)
    shutil.copy2(os.path.join(source, "Python"), home)
    os.makedirs(os.path.join(home, "Resources"))
    shutil.copy2(os.path.join(source, "Resources", "Info.plist"), os.path.join(home, "Resources"))
    os.symlink(PYVER, os.path.join(framework, "Versions", "Current"))
    os.symlink("Versions/Current/Python", os.path.join(framework, "Python"))
    os.symlink("Versions/Current/Resources", os.path.join(framework, "Resources"))

    # the standard library, without what a game never imports
    stdlib = "python" + PYVER

    def skip(folder, names):
        out = [n for n in names if n in STDLIB_SKIP or n.endswith((".pyc", ".a"))]
        if os.path.basename(folder) == "lib-dynload":
            out += [n for n in names if n.startswith(DYNLOAD_SKIP)]
        return out

    shutil.copytree(os.path.join(source, "lib", stdlib), os.path.join(lib, stdlib), ignore=skip, symlinks=True)
    os.makedirs(os.path.join(lib, stdlib, "site-packages"))    # empty: nothing third-party is used

    # Tcl/Tk: the two libraries and their script folders (msgcat in tcl8 is needed by Tk's dialogs)
    for name in ("libtcl8.6.dylib", "libtk8.6.dylib"):
        shutil.copy2(os.path.join(source, "lib", name), lib)
    shutil.copytree(os.path.join(source, "lib", "tcl8.6"), os.path.join(lib, "tcl8.6"),
                    ignore=shutil.ignore_patterns("*.c"))
    shutil.copytree(os.path.join(source, "lib", "tk8.6"), os.path.join(lib, "tk8.6"),
                    ignore=shutil.ignore_patterns("demos"))
    terms = os.path.join(source, "lib", "tk8.6", "demos", "license.terms")
    if os.path.isfile(terms):                                   # Tcl/Tk's licence travels with it
        shutil.copy2(terms, os.path.join(lib, "tk8.6", "license.terms"))
    shutil.copytree(os.path.join(source, "lib", "tcl8"), os.path.join(lib, "tcl8"),
                    ignore=shutil.ignore_patterns("tcltest*", "tdbc"))
    return home, source


def relink(bundle, home, source, launcher):
    """Point every library reference at the copies inside the app.  Fails loudly if a
    file would still load something from the Python it was copied from."""
    inside = "@executable_path/../Frameworks/Python.framework/Versions/%s" % PYVER
    run("install_name_tool", "-id", inside + "/Python", os.path.join(home, "Python"))
    for name in ("libtcl8.6.dylib", "libtk8.6.dylib"):
        run("install_name_tool", "-id", "%s/lib/%s" % (inside, name), os.path.join(home, "lib", name))
    for path in [launcher] + machos(home):
        for old in links(path):
            if old.startswith(source + "/"):
                run("install_name_tool", "-change", old, inside + old[len(source):], path)
    stray = {}
    for path in [launcher] + machos(home):
        bad = [d for d in links(path) if not d.startswith(("/usr/lib/", "/System/", "@executable_path/"))]
        missing = [d for d in links(path) if d.startswith("@executable_path/../Frameworks/")
                   and not os.path.isfile(os.path.join(bundle, "Contents", d[len("@executable_path/../"):]))]
        if bad or missing:
            stray[os.path.relpath(path, bundle)] = bad + missing
    if stray:
        sys.exit("not self-contained - these still load something from outside the app:\n%s"
                 % "\n".join("  %s -> %s" % kv for kv in sorted(stray.items())))


def make_launcher(path, work, library):
    """Native launcher; a shell script if it cannot be compiled.  Returns a description."""
    if have("clang") and sysconfig.get_config_var("PYTHONFRAMEWORK") and os.path.isfile(library):
        source = os.path.join(work, "launcher.c")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write(LAUNCHER_C)
        # one slice per processor type the Python has (python.org builds have both
        # Apple Silicon and Intel), so the app runs on either
        try:
            archs = run("lipo", "-archs", library).split() if have("lipo") else []
        except subprocess.CalledProcessError:
            archs = []
        base = ["clang", "-O2", "-mmacosx-version-min=" + MIN_MACOS, "-o", path, source,
                "-I", sysconfig.get_path("include"), library]
        problem = ""
        for wanted in (archs, []):                              # all of them, else just this Mac's
            try:
                run(*(base + [flag for arch in wanted for flag in ("-arch", arch)]))
                return "native (%s)" % (" + ".join(wanted) if wanted else "this Mac's processor only")
            except subprocess.CalledProcessError as exc:
                problem = exc.stdout
        print("  clang failed, using a shell launcher instead:\n%s" % problem)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(LAUNCHER_SH.format(python=sys.executable))
    os.chmod(path, 0o755)
    return "shell script"


def sign(bundle, home):
    """Ad hoc signatures, inside out: every library, the framework, then the app.
    (Changing a library's load paths invalidates the signature it came with.)"""
    if not have("codesign"):
        return False
    try:
        if home:
            for path in machos(home):
                run("codesign", "--force", "--sign", "-", path)
            run("codesign", "--force", "--sign", "-", os.path.dirname(os.path.dirname(home)))   # the framework
        run("codesign", "--force", "--sign", "-", bundle)
        run("codesign", "--verify", "--deep", "--strict", bundle)
    except subprocess.CalledProcessError as exc:
        print("  signing problem: %s" % exc.stdout)
        return False
    return True


def self_check(launcher, source, work):
    """Start the app's Python where it cannot read the Python it was copied from, and ask
    the game where everything came from (main.py --where)."""
    profile = '(version 1)(allow default)(deny file-read* (subpath "%s"))' % os.path.dirname(os.path.dirname(source))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "TCL", "TK"))}
    env["GD_REMAKE_HOME"] = os.path.join(work, "home")
    cmd = [launcher, "--where"]
    sandboxed = have("sandbox-exec")
    if sandboxed:
        cmd = ["sandbox-exec", "-p", profile] + cmd
    result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
    if result.returncode != 0 or "outside the app: none" not in result.stdout:
        sys.exit("the built app failed its self-check:\n%s" % result.stdout)
    return "passed%s" % (" with the installed Python blocked" if sandboxed else " (no sandbox-exec: not isolated)")


# ---------------------------------------------------------------------------- putting it together
def folder_size(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(base, name)
            if not os.path.islink(full):
                total += os.path.getsize(full)
    return total


def build():
    if sys.platform != "darwin":
        sys.exit("This builds a macOS app; run it on a Mac.")
    bundle = os.path.join(DIST, NAME + ".app")
    if os.path.exists(bundle):
        shutil.rmtree(bundle)                                   # only ever our own previous build
    contents = os.path.join(bundle, "Contents")
    resources = os.path.join(contents, "Resources")
    launcher_path = os.path.join(contents, "MacOS", NAME)
    os.makedirs(os.path.dirname(launcher_path))
    os.makedirs(resources)

    files = copy_program(os.path.join(resources, "app"))
    runtime = copy_runtime(os.path.join(contents, "Frameworks"))
    with tempfile.TemporaryDirectory(prefix="gdapp") as work:
        icon = make_icon(resources, work)
        launcher = make_launcher(launcher_path, work, os.path.join(sys.base_prefix, "Python"))
        if runtime and launcher.startswith("native"):
            relink(bundle, runtime[0], runtime[1], launcher_path)
        elif runtime:                                           # a shell launcher cannot use it
            shutil.rmtree(os.path.join(contents, "Frameworks"))
            runtime = None

        info = {
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": NAME,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleInfoDictionaryVersion": "6.0",
            "LSMinimumSystemVersion": MIN_MACOS,
            "LSApplicationCategoryType": "public.app-category.games",
            "NSHighResolutionCapable": True,
            "NSDocumentsFolderUsageDescription":
                "GD Remake keeps the levels you build and your progress in Documents/GD Remake.",
        }
        if icon:
            info["CFBundleIconFile"] = "AppIcon"
        with open(os.path.join(contents, "Info.plist"), "wb") as fh:
            plistlib.dump(info, fh)

        signed = sign(bundle, runtime[0] if runtime else None)
        check = self_check(launcher_path, runtime[1], work) if runtime else "skipped"

    print("built %s  (%.0f MB)" % (bundle, folder_size(bundle) / 1e6))
    print("  launcher: %s   icon: %s   signed: %s   built-in level files: %d"
          % (launcher, "yes" if icon else "no", "ad hoc" if signed else "no", files))
    if runtime:
        print("  self-contained: carries Python %s and Tcl/Tk copied from %s" % (sys.version.split()[0], runtime[1]))
        print("  self-check: %s" % check)
    else:
        print("  NOT self-contained: it uses the Python at %s" % sys.base_prefix)
    return bundle


if __name__ == "__main__":
    build()
