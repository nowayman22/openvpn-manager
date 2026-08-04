"""Horizontal tree diagram drawn with Cairo. Style B: flat cards + gradients."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk  # noqa: E402
import cairo

from .topology import compute_layout

DARK_BG = (0.05, 0.06, 0.10)
CARD_FILL_TOP = (0.18, 0.20, 0.25)
CARD_FILL_BOT = (0.10, 0.11, 0.15)
CARD_BORDER = (0.25, 0.28, 0.35)
PC_BORDER = (0.49, 0.68, 0.64)      # accent
ROUTER_BORDER = (0.85, 0.65, 0.34)  # warning
TUNNEL_AUTO_BORDER = (0.66, 0.71, 0.40)  # success
TUNNEL_MANUAL_BORDER = (0.50, 0.55, 0.65)
EDGE_COLOR = (0.49, 0.68, 0.64)

#: Tunnel status -> (fill rgb, radius). Statuses without a mapping paint a
#: faint outline instead.
_TUNNEL_DOT = {
    "connected": (0.45, 0.75, 0.35, 5),
    "ok": (0.45, 0.75, 0.35, 5),
    "connecting": (0.95, 0.65, 0.10, 5),
    "testing": (0.95, 0.65, 0.10, 5),
    "failed": (0.85, 0.35, 0.32, 5),
    "fail": (0.85, 0.35, 0.32, 5),
}


def tunnel_dot(status):
    """Return (r, g, b, radius) for a tunnel status, or None for outline."""
    return _TUNNEL_DOT.get(status)

NODE_W = 160
NODE_H = 56
LEVEL_PAD = 80
NODE_PAD = 12
CORNER_RADIUS = 10


class TopologyDiagram(Gtk.DrawingArea):
    """Cairo-drawn horizontal tree diagram."""

    def __init__(self, on_context_menu=None):
        super().__init__()
        self._on_context_menu = on_context_menu
        self._topo = None
        self._boxes = []
        self._tunnel_status = {}
        self._hover_id = None
        self._total_w = 200
        self._total_h = 200
        self.set_draw_func(self._draw)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        click = Gtk.GestureClick.new()
        click.set_button(0)  # 0 = any button; default 1 ignores right-click
        click.connect("pressed", self._on_click)
        self.add_controller(click)

    def set_topology(self, topo):
        self._topo = topo
        live = {d.id for d in topo.devices}
        self._tunnel_status = {k: v for k, v in self._tunnel_status.items()
                               if k in live}
        self._boxes = compute_layout(topo, col_width=NODE_W,
                                     node_pad=NODE_PAD, level_pad=LEVEL_PAD)
        max_x = max((b.x + NODE_W for b in self._boxes), default=200)
        max_y = max((b.y + NODE_H for b in self._boxes), default=200)
        self._total_w = max_x + 40
        self._total_h = max_y + 40
        self.set_content_width(int(self._total_w))
        self.set_content_height(int(self._total_h))
        self.queue_draw()

    def set_tunnel_status(self, device_id, status):
        """Record a tunnel connectivity test result and redraw."""
        self._tunnel_status[device_id] = status
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        cr.set_source_rgb(*DARK_BG)
        cr.paint()

        if not self._topo:
            return

        # edges first (behind nodes)
        by_id = {b.device_id: b for b in self._boxes}
        for edge in self._topo.edges:
            src = by_id.get(edge.source_id)
            dst = by_id.get(edge.target_id)
            if not src or not dst:
                continue
            cr.set_source_rgb(*EDGE_COLOR)
            cr.set_line_width(2.0)
            if edge.style == "dashed":
                cr.set_dash([6, 4])
            else:
                cr.set_dash([])
            # horizontal line from right edge of src to left edge of dst
            x1 = src.x + NODE_W
            y1 = src.y + NODE_H / 2
            x2 = dst.x
            y2 = dst.y + NODE_H / 2
            mid_x = x1 + (x2 - x1) / 2
            cr.move_to(x1, y1)
            cr.line_to(mid_x, y1)
            cr.line_to(mid_x, y2)
            cr.line_to(x2, y2)
            cr.stroke()
        cr.set_dash([])

        # nodes
        for b in self._boxes:
            dev = next((d for d in self._topo.devices if d.id == b.device_id), None)
            if dev is None:
                continue
            is_hover = self._hover_id == b.device_id
            self._draw_node(cr, dev, b, is_hover)

    def _draw_node(self, cr, dev, box, hover):
        x, y, w, h = box.x, box.y, box.w, box.h

        # shadow
        cr.save()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.rectangle(x + 3, y + 3, w, h)
        cr.fill()
        cr.restore()

        # gradient fill
        pat = cairo.LinearGradient(x, y, x + w, y + h)
        pat.add_color_stop_rgba(0, *CARD_FILL_TOP, 1)
        pat.add_color_stop_rgba(1, *CARD_FILL_BOT, 1)
        cr.set_source(pat)
        self._rounded_rect(cr, x, y, w, h)
        cr.fill()

        # border
        if dev.kind == "pc":
            border = PC_BORDER
        elif dev.kind == "router":
            border = ROUTER_BORDER
        elif dev.kind == "tunnel" and dev.manual:
            border = TUNNEL_MANUAL_BORDER
        elif dev.kind == "tunnel":
            border = TUNNEL_AUTO_BORDER
        else:
            border = CARD_BORDER
        cr.set_source_rgb(*border)
        cr.set_line_width(2.0 if hover else 1.5)
        self._rounded_rect(cr, x, y, w, h)
        cr.stroke()

        # text
        cr.set_source_rgb(0.90, 0.90, 0.85)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(12)
        label = dev.label if len(dev.label) <= 20 else dev.label[:19] + "…"
        ext = cr.text_extents(label)
        cr.move_to(x + (w - ext.width) / 2, y + 22)
        cr.show_text(label)

        cr.set_source_rgb(0.53, 0.53, 0.53)
        cr.set_font_size(10)
        sub = " · ".join(filter(None, [dev.ip, dev.detail]))
        if sub:
            sub = sub if len(sub) <= 28 else sub[:27] + "…"
            ext = cr.text_extents(sub)
            cr.move_to(x + (w - ext.width) / 2, y + 40)
            cr.show_text(sub)

        # green dot for active auto-tunnel
        if dev.kind == "tunnel" and not dev.manual:
            cr.set_source_rgb(0.60, 0.75, 0.35)
            cr.arc(x + w - 14, y + 14, 5, 0, 2 * 3.14159)
            cr.fill()

        # connectivity test / connection status dot (bottom-left)
        if dev.kind == "tunnel":
            dot = tunnel_dot(self._tunnel_status.get(dev.id))
            sx, sy = x + 14, y + h - 14
            if dot is not None:
                cr.set_source_rgb(*dot[:3])
                cr.arc(sx, sy, dot[3], 0, 2 * 3.14159)
                cr.fill()
            else:
                cr.set_source_rgba(0.45, 0.48, 0.55, 0.5)
                cr.arc(sx, sy, 4, 0, 2 * 3.14159)
                cr.set_line_width(1.0)
                cr.stroke()

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r=CORNER_RADIUS):
        cr.new_path()
        cr.arc(x + r, y + r, r, 3.14159, 3 * 3.14159 / 2)
        cr.arc(x + w - r, y + r, r, 3 * 3.14159 / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 3.14159 / 2)
        cr.arc(x + r, y + h - r, r, 3.14159 / 2, 3.14159)
        cr.close_path()

    def _on_motion(self, _controller, x, _y):
        hit = None
        for b in self._boxes:
            if b.x <= x <= b.x + b.w:
                hit = b.device_id
                break
        if hit != self._hover_id:
            self._hover_id = hit
            self.queue_draw()

    def _on_click(self, gesture, _n_press, x, y):
        hit = None
        for b in self._boxes:
            if b.x <= x <= b.x + b.w and b.y <= y <= b.y + b.h:
                hit = b.device_id
                break
        if gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
            if hit and self._on_context_menu and self._topo:
                dev = next((d for d in self._topo.devices
                            if d.id == hit), None)
                if dev is not None:
                    self._on_context_menu(dev, x, y)
            return
        self._hover_id = hit
        self.queue_draw()
