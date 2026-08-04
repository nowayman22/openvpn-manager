"""Network topology view: tree diagram + action buttons."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .topology_diagram import TopologyDiagram


class TopologyView(Gtk.Box):
    """Renders a Topology as a horizontal tree diagram."""

    def __init__(self, on_scan=None, on_add_tunnel=None, on_context_menu=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._on_scan = on_scan
        self._on_add_tunnel = on_add_tunnel
        self._on_context_menu = on_context_menu

        self._host_label = Gtk.Label(halign=Gtk.Align.START, wrap=True)
        self._host_label.add_css_class("title-3")
        self.append(self._host_label)

        scroller = Gtk.ScrolledWindow()
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        self._diagram = TopologyDiagram(on_context_menu=on_context_menu)
        scroller.set_child(self._diagram)
        self.append(scroller)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if self._on_scan is not None:
            scan_btn = Gtk.Button(label="Scan network")
            scan_btn.connect("clicked", lambda *_: self._on_scan())
            btn_box.append(scan_btn)
        if self._on_add_tunnel is not None:
            add_btn = Gtk.Button(label="Add Tunnel")
            add_btn.add_css_class("suggested-action")
            add_btn.connect("clicked", lambda *_: self._on_add_tunnel())
            btn_box.append(add_btn)
        self.append(btn_box)

    def set_topology(self, topo):
        """Render a Topology model into the diagram."""
        self._host_label.set_text(self._host_text(topo))
        self._diagram.set_topology(topo)

    @staticmethod
    def _host_text(topo):
        ips = ", ".join(topo.local_ips)
        return f"{topo.hostname} · {ips}" if ips else topo.hostname
