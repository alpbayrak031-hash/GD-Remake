"""The numbers behind the in-game FPS counter (no window code, so it can be tested).

FPS       frames drawn in the last second, divided by the time they took.
1% low    the average frame rate of the slowest 1% of the frames in the last 10
          seconds - a single long stall shows up here even when the average is fine.
1% high   the same for the fastest 1%.

A "frame" is one pass of the game view's draw loop.  The loop asks to be called again
5 ms after each frame, so the counter tops out a little below 200 whatever the screen's
refresh rate is; the physics is unaffected either way (it always runs at 240 steps a
second, see gd/render_tk.py).
"""
import math
from collections import deque

WINDOW = 10.0          # seconds of frames the 1% figures look at
RECENT = 1.0           # seconds the plain FPS figure looks at
MIN_FRAMES = 30        # fewer frames than this and the 1% figures are not shown yet


class FrameStats:
    def __init__(self, window=WINDOW):
        self.window = window
        self.frames = deque()            # (when the frame ended, how long it took), oldest first

    def reset(self):
        self.frames.clear()

    def add(self, now, duration):
        if duration <= 0.0:
            return
        self.frames.append((now, duration))
        cutoff = now - self.window
        while self.frames and self.frames[0][0] < cutoff:
            self.frames.popleft()

    def summary(self, now):
        """-> {"fps", "low", "high", "frames"}; a figure is None when there is not enough to go on."""
        recent = [d for t, d in self.frames if t >= now - RECENT]
        fps = len(recent) / sum(recent) if recent else None
        times = sorted(d for t, d in self.frames if t >= now - self.window)
        low = high = None
        if len(times) >= MIN_FRAMES:
            share = max(1, math.ceil(len(times) * 0.01))           # how many frames make up "1%"
            low = share / sum(times[-share:])
            high = share / sum(times[:share])
        return {"fps": fps, "low": low, "high": high, "frames": len(times)}

    def text(self, now):
        s = self.summary(now)

        def show(value):
            return "--" if value is None else "%d" % round(value)
        return "%s FPS    1%% low %s    1%% high %s" % (show(s["fps"]), show(s["low"]), show(s["high"]))
