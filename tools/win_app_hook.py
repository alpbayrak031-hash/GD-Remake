"""Runs inside the built Windows app, before main.py (PyInstaller calls it a runtime hook).

A windowed Windows program has no console behind it, so `sys.stdout` and `sys.stderr`
are not usable and the game's own print() calls would fail.  They are pointed at a sink
that throws the text away - or, when GD_REMAKE_LOG names a file, at that file, which is
how tools/build_exe.py reads the app's report when it checks the build.

GD_REMAKE_APP tells main.py it has no terminal to report a crash on, so it writes one to
savedata/last_error.txt and shows it in a dialog.  The macOS app's launcher sets the same
variable; here there is no launcher, so the app sets it for itself.
"""
import os
import sys


class _Sink:
    """Enough of a text stream for print(): everything written to it is dropped."""
    encoding, errors = "utf-8", "replace"

    def write(self, text):
        return len(text)

    def writelines(self, lines):
        pass

    def flush(self):
        pass

    def close(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("the app has no console")


def _streams():
    log = os.environ.get("GD_REMAKE_LOG")
    if log:
        try:                                         # line buffered: the report is readable at once
            return open(log, "a", encoding="utf-8", errors="replace", buffering=1)
        except OSError:
            pass
    return None


_log = _streams()
for _name in ("stdout", "stderr"):
    if _log is not None or getattr(sys, _name, None) is None:
        setattr(sys, _name, _log or _Sink())
os.environ.setdefault("GD_REMAKE_APP", "1")
