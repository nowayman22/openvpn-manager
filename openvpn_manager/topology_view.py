"""Network topology view as a libadwaita tree of groups."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

_ICONS = {
    "router": "network-wired-symbolic",
    "device": "network-transmit-receive-symbolic",
    "tunnel": "network-vpn-symbolic",
}


class TopologyView(Gtk.Box):
    """Renders a Topology model: this machine, the LAN, and VPN tunnels."""

    def __init__(self, on_scan=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._on_scan = on_scan
        self._lan_children = []
        self._vpn_children = []

        self._host_label = Gtk.Label(halign=Gtk.Align.START, wrap=True)
        self._host_label.add_css_class("title-3")
        self.append(self._host_label)

        self._lan_group = Adw.PreferencesGroup()
        self._lan_group.set_title("Network")
        self.append(self._lan_group)

        self._vpn_group = Adw.PreferencesGroup()
        self._vpn_group.set_title("VPN Tunnels")
        self.append(self._vpn_group)

        if self._on_scan is not None:
            scan_btn = Gtk.Button(label="Scan network")
            scan_btn.connect("clicked", lambda *_: self._on_scan())
            self.append(scan_btn)

    def set_topology(self, topo):
        """Render a Topology model, replacing the previous contents."""
        self._host_label.set_text(self._host_text(topo))
        self._lan_group.set_title(
            f"Network · {topo.lan.subnet}" if topo.lan else "Network")
        self._render_lan(topo)
        self._render_vpn(topo)

    @staticmethod
    def _host_text(topo):
        ips = ", ".join(topo.local_ips)
        return f"{topo.hostname} · {ips}" if ips else topo.hostname

    def _lan_rows(self, topo):
        if topo.lan is None:
            return [self._hint("No LAN detected")]
        rows = [self._device_row(topo.lan.gateway, "router")]
        rows.extend(self._device_row(dev, "device") for dev in topo.lan.devices)
        return rows

    def _vpn_rows(self, topo):
        if not topo.tunnels:
            return [self._hint("No VPN tunnel connected")]
        return [self._device_row(t, "tunnel") for t in topo.tunnels]

    @staticmethod
    def _device_row(dev, kind):
        row = Adw.ActionRow(title=dev.label)
        row.set_subtitle(" · ".join(filter(None, [dev.ip, dev.mac, dev.detail])))
        icon = Gtk.Image(icon_name=_ICONS.get(
            kind, "network-transmit-receive-symbolic"))
        icon.add_css_class("dim-label")
        row.add_prefix(icon)
        if kind == "tunnel":
            dot = Gtk.Label(label="●")
            dot.add_css_class("success")
            row.add_suffix(dot)
        return row

    @staticmethod
    def _hint(text):
        label = Gtk.Label(label=text, halign=Gtk.Align.START)
        label.add_css_class("dim-label")
        return label

    def _render_lan(self, topo):
        for child in self._lan_children:
            self._lan_group.remove(child)
        self._lan_children = self._lan_rows(topo)
        for child in self._lan_children:
            self._lan_group.add(child)

    def _render_vpn(self, topo):
        for child in self._vpn_children:
            self._vpn_group.remove(child)
        self._vpn_children = self._vpn_rows(topo)
        for child in self._vpn_children:
            self._vpn_group.add(child)
