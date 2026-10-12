#!/usr/bin/env python3
"""Build "GD Remake.app" for macOS.

    python3 tools/build_app.py            ->  dist/GD Remake.app

The app is a small native launcher plus a copy of the game's Python source and the
built-in levels.  The launcher runs the game inside its own process (it embeds the
Python it was built with), so the Dock shows one icon - the game's - and not a
second "Python" one.  It is not a self-contained app: it needs that same Python
(a python.org framework build with tkinter) to be installed on the Mac that runs it.

Needs only what macOS and Xcode's command line tools provide: clang, sips, iconutil
and codesign.  Without clang (or with a Python that is not a framework build) it
falls back to a shell-script launcher, which works but shows Python's Dock icon.

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
VERSION = "1.0"
DIST = os.path.join(ROOT, "dist")

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
/* GD Remake launcher: runs Contents/Resources/app/main.py with the embedded Python. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX], script[PATH_MAX + 64];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, real)) {
        fprintf(stderr, "GD Remake: cannot find myself\n");
        return 1;
    }
    char *slash = strrchr(real, '/');               /* .../Contents/MacOS/<name> */
    if (!slash) return 1;
    *slash = '\0';
    snprintf(script, sizeof(script), "%s/../Resources/app/main.py", real);

    setenv("GD_REMAKE_APP", "1", 1);                /* main.py reports crashes in a dialog */

    char **args = calloc((size_t)argc + 2, sizeof(char *));
    int n = 0;
    args[n++] = argv[0];
    args[n++] = script;
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "-psn_", 5) != 0)      /* old Finders pass a process serial number */
            args[n++] = argv[i];

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.write_bytecode = 0;                      /* never write into the app bundle */
    PyStatus status = PyConfig_SetBytesArgv(&config, n, args);
    if (!PyStatus_Exception(status))
        status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        fprintf(stderr, "GD Remake: Python did not start: %s\n", status.err_msg ? status.err_msg : "?");
        return 1;
    }
    return Py_RunMain();
}
"""

LAUNCHER_SH = """#!/bin/sh
# Fallback launcher (no clang or no framework Python at build time): shows Python's Dock icon.
HERE="$(cd "$(dirname "$0")" && pwd)"
export GD_REMAKE_APP=1
export PYTHONDONTWRITEBYTECODE=1
exec "{python}" "$HERE/../Resources/app/main.py" "$@"
"""


def run(*cmd):
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout


def have(tool):
    return shutil.which(tool) is not None


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
    if not (have("sips") and have("iconutil")):
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


def make_launcher(path, work):
    """Native launcher embedding this Python; a shell script if that is impossible.
    Returns a word describing what was built."""
    library = os.path.join(sys.base_prefix, "Python")          # the framework's shared library
    if have("clang") and sysconfig.get_config_var("PYTHONFRAMEWORK") and os.path.isfile(library):
        source = os.path.join(work, "launcher.c")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write(LAUNCHER_C)
        try:
            run("clang", "-O2", "-o", path, source, "-I", sysconfig.get_path("include"), library)
            return "native"
        except subprocess.CalledProcessError as exc:
            print("  clang failed, using a shell launcher instead:\n%s" % exc.stdout)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(LAUNCHER_SH.format(python=sys.executable))
    os.chmod(path, 0o755)
    return "shell script"


def build():
    if sys.platform != "darwin":
        sys.exit("This builds a macOS app; run it on a Mac.")
    bundle = os.path.join(DIST, NAME + ".app")
    if os.path.exists(bundle):
        shutil.rmtree(bundle)                                   # only ever our own previous build
    contents = os.path.join(bundle, "Contents")
    resources = os.path.join(contents, "Resources")
    os.makedirs(os.path.join(contents, "MacOS"))
    os.makedirs(resources)

    files = copy_program(os.path.join(resources, "app"))
    with tempfile.TemporaryDirectory(prefix="gdapp") as work:
        icon = make_icon(resources, work)
        launcher = make_launcher(os.path.join(contents, "MacOS", NAME), work)

    info = {
        "CFBundleName": NAME,
        "CFBundleDisplayName": NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.games",
        "NSHighResolutionCapable": True,
        "NSDocumentsFolderUsageDescription":
            "GD Remake keeps the levels you build and your progress in Documents/GD Remake.",
    }
    if icon:
        info["CFBundleIconFile"] = "AppIcon"
    with open(os.path.join(contents, "Info.plist"), "wb") as fh:
        plistlib.dump(info, fh)

    signed = False
    if have("codesign"):                                        # ad hoc: enough to run on this Mac
        try:
            run("codesign", "--force", "--deep", "--sign", "-", bundle)
            signed = True
        except subprocess.CalledProcessError as exc:
            print("  not signed: %s" % exc.stdout)

    print("built %s" % bundle)
    print("  launcher: %s   icon: %s   signed: %s   built-in level files: %d"
          % (launcher, "yes" if icon else "no", "ad hoc" if signed else "no", files))
    print("  uses Python %s at %s" % (sys.version.split()[0], sys.base_prefix))
    return bundle


if __name__ == "__main__":
    build()
