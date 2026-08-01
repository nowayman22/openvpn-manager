"""Rolling throughput graph as a GTK DrawingArea."""

import collections

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

MAX_SAMPLES = 60


class Sparkline(Gtk.DrawingArea):
    def __init__(self, down_color=None, up_color=None):
        super().__init__()
        self.set_content_height(120)
        self.set_hexpand(True)
        self._down = collections.deque(maxlen=MAX_SAMPLES)
        self._up = collections.deque(maxlen=MAX_SAMPLES)
        self._down_color = down_color or (0.20, 0.60, 0.86)
        self._up_color = up_color or (0.90, 0.49, 0.13)
        self.set_draw_func(self._draw)

    def push(self, up_bps: float, down_bps: float) -> None:
        self._up.append(max(0.0, up_bps))
        self._down.append(max(0.0, down_bps))
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        peak = max([1.0, *self._up, *self._down])
        self._draw_series(cr, self._down, width, height, peak, self._down_color)
        self._draw_series(cr, self._up, width, height, peak, self._up_color)

    def _draw_series(self, cr, series, width, height, peak, rgb):
        if len(series) < 2:
            return
        step = width / (MAX_SAMPLES - 1)
        cr.set_line_width(2.0)
        cr.set_source_rgb(*rgb)
        for i, value in enumerate(series):
            x = i * step
            y = height - (value / peak) * (height - 4) - 2
            if i == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()
