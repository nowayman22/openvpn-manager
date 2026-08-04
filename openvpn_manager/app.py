"""Main GTK4/libadwaita application window."""

import getpass
import os
import socket
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import palette
from . import topology
from . import vpn
from .format import human_bytes, human_duration, human_speed
from .palette import load_palette, palette_to_css, sparkline_colors
from .sparkline import Sparkline
from .stats import Sampler, detect_iface
from .topology import (Device, build_tree_topology, delete_manual_tunnel,
                       descendant_ids, load_manual_tunnels,
                       save_manual_tunnels, update_manual_tunnel)
from .topology_view import TopologyView
from . import tunnels as tunnel_backend

APP_ID = "dev.nikits.OpenVpnManager"


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="OpenVPN Manager")
        self.set_default_size(460, 640)
        self._sampler = None
        self._iface = None
        self._topology_sweeping = False
        self._topology_last_sweep = None
        self._cidr = None
        self._provider = Gtk.CssProvider()
        self._reload_pending = False
        self._reload_retries = 5
        self._palette = load_palette()
        self._spark_colors = (
            sparkline_colors(self._palette) if self._palette else (None, None))
        if self._palette is not None:
            self._apply_theme_css(self._palette)
        self._tunnels = tunnel_backend.TunnelManager()
        self.connect("close-request", self._on_close_request)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self._status_pill = Gtk.Label(label="Disconnected")
        self._status_pill.add_css_class("dim-label")
        header.pack_end(self._status_pill)

        menu = Gio.Menu()
        menu.append("Import .ovpn", "win.import")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_start(menu_btn)
        toolbar.add_top_bar(header)

        self._toast = Adw.ToastOverlay()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)

        self._warning = Adw.Banner()
        self._warning.set_revealed(False)
        box.append(self._warning)

        self._empty_page = Adw.StatusPage()
        self._empty_page.set_title("No profiles imported")
        self._empty_page.set_description(
            "Import an .ovpn file to start using your VPN.")
        self._empty_page.set_icon_name("network-vpn-symbolic")
        empty_btn = Gtk.Button(label="Browse for .ovpn…")
        empty_btn.add_css_class("suggested-action")
        empty_btn.connect("clicked", self._on_import_clicked)
        self._empty_page.set_child(empty_btn)
        box.append(self._empty_page)

        self._cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        conn_group = Adw.PreferencesGroup()
        conn_group.set_title("Connection")
        self._profile_combo = Adw.ComboRow()
        self._profile_combo.set_title("Profile")
        self._profile_combo.set_subtitle("Select an OpenVPN profile")
        conn_group.add(self._profile_combo)
        self._cards_box.append(conn_group)

        self._action_btn = Gtk.Button(label="Connect")
        self._action_btn.set_hexpand(True)
        self._action_btn.add_css_class("suggested-action")
        self._action_btn.connect("clicked", self._on_action)
        self._cards_box.append(self._action_btn)

        usage_group = Adw.PreferencesGroup()
        usage_group.set_title("Usage")

        speed_grid = Gtk.Grid()
        speed_grid.set_column_homogeneous(True)
        speed_grid.set_column_spacing(12)
        up_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        up_box.append(self._speed_header("UP"))
        self._up_label = Gtk.Label(label="0 B/s", halign=Gtk.Align.CENTER)
        self._up_label.add_css_class("title-1")
        up_box.append(self._up_label)
        down_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        down_box.append(self._speed_header("DOWN"))
        self._down_label = Gtk.Label(label="0 B/s", halign=Gtk.Align.CENTER)
        self._down_label.add_css_class("title-1")
        down_box.append(self._down_label)
        speed_grid.attach(up_box, 0, 0, 1, 1)
        speed_grid.attach(down_box, 1, 0, 1, 1)
        usage_group.add(speed_grid)

        self._spark = Sparkline(
            down_color=self._spark_colors[0], up_color=self._spark_colors[1])
        usage_group.add(self._spark)

        self._session_row = Adw.ActionRow(title="Session")
        self._session_row.set_subtitle("↑ 0 B  ↓ 0 B")
        usage_group.add(self._session_row)

        self._uptime_row = Adw.ActionRow(title="Uptime")
        self._uptime_row.set_subtitle("00:00:00")
        usage_group.add(self._uptime_row)

        self._ip_row = Adw.ActionRow(title="VPN IP")
        self._ip_row.set_subtitle("—")
        usage_group.add(self._ip_row)

        self._remote_row = Adw.ActionRow(title="Remote")
        self._remote_row.set_subtitle("—")
        usage_group.add(self._remote_row)

        self._proto_row = Adw.ActionRow(title="Protocol")
        self._proto_row.set_subtitle("—")
        usage_group.add(self._proto_row)

        self._cards_box.append(usage_group)

        self._status_line = Gtk.Label(label="", halign=Gtk.Align.START, wrap=True)
        self._status_line.add_css_class("dim-label")
        self._cards_box.append(self._status_line)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(460)
        clamp.set_child(self._cards_box)
        box.append(clamp)

        self._stack = Adw.ViewStack()
        self._stack.add_titled(box, "status", "Status")

        self._topo_view = TopologyView(on_scan=self._on_scan_requested,
                                       on_add_tunnel=self._on_add_tunnel,
                                       on_context_menu=self._on_node_context)
        topo_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            margin_top=12, margin_bottom=12,
                            margin_start=12, margin_end=12)
        topo_page.append(self._topo_view)
        self._stack.add_titled(topo_page, "topology", "Topology")

        self._toast.set_child(self._stack)
        toolbar.set_content(self._toast)
        self.set_content(toolbar)

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        header.set_title_widget(switcher)

        import_action = Gio.SimpleAction.new("import", None)
        import_action.connect("activate", self._on_import)
        self.add_action(import_action)

        self._reload_profiles()
        GLib.timeout_add(1000, self._tick)
        self._start_theme_monitor()

    @staticmethod
    def _speed_header(text):
        label = Gtk.Label(label=text, halign=Gtk.Align.CENTER)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        return label

    def _apply_theme_css(self, pal):
        try:
            self._provider.load_from_string(palette_to_css(pal))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self._provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except GLib.Error:
            pass  # keep the last good colors if the CSS fails to load

    def _start_theme_monitor(self):
        try:
            monitor = Gio.File.new_for_path(str(palette.THEME_DIR))
            self._monitor = monitor.monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        self._monitor.connect("changed", self._on_theme_changed)

    def _on_theme_changed(self, *_args):
        if self._reload_pending:
            return
        self._reload_pending = True
        GLib.timeout_add(200, self._reload_theme)

    def _reload_theme(self):
        self._reload_pending = False
        pal = load_palette()
        if pal is None:
            if self._reload_retries > 0:
                self._reload_retries -= 1
                return True  # file may be mid-swap; retry briefly
            return False  # give up, keep the last good colors
        self._reload_retries = 5
        self._apply_theme_css(pal)
        self._spark.set_colors(*sparkline_colors(pal))
        return False

    def _reload_profiles(self):
        readable = vpn.client_dir_readable()
        if not readable:
            user = getpass.getuser()
            self._warning.set_title(
                f"Cannot read {vpn.CLIENT_DIR}. Run ./install.sh, or: "
                f"sudo setfacl -m u:{user}:rx {vpn.CLIENT_DIR}")
            self._warning.set_revealed(True)
        else:
            self._warning.set_revealed(False)
        self._profiles = vpn.discover_profiles()
        self._profile_combo.set_model(Gtk.StringList.new(self._profiles))
        if self._profiles:
            self._profile_combo.set_selected(0)
            self._profile_combo.set_sensitive(True)
            self._action_btn.set_sensitive(True)
            self._cards_box.set_visible(True)
            self._empty_page.set_visible(False)
        else:
            self._profile_combo.set_sensitive(False)
            self._action_btn.set_sensitive(False)
            self._cards_box.set_visible(False)
            self._empty_page.set_visible(True)

    def _set_status(self, text: str, error: bool = False):
        self._status_line.set_text(text)
        self._status_line.remove_css_class("error")
        self._status_line.remove_css_class("success")
        if text:
            self._status_line.add_css_class("error" if error else "success")

    def _selected_profile(self):
        if not self._profiles:
            return None
        idx = self._profile_combo.get_selected()
        if idx < 0 or idx >= len(self._profiles):
            return None
        return self._profiles[idx]

    def _on_action(self, _btn):
        profile = self._selected_profile()
        if not profile:
            return
        state = vpn.is_active(profile)
        if state == "active":
            self._set_status(f"Disconnecting {profile}…")
            self._spawn(vpn.disconnect_argv(profile),
                        success_msg=f"Disconnected {profile}",
                        fail_prefix=f"Failed to disconnect {profile}")
        elif state == "activating":
            return  # button is disabled while connecting; stay defensive
        elif vpn.needs_credentials(f"{vpn.CLIENT_DIR}/{profile}.conf"):
            self._prompt_credentials(profile)
        else:
            self._connect(profile)

    def _connect(self, profile):
        self._set_status(f"Connecting {profile}…")
        self._spawn(vpn.connect_argv(profile),
                    success_msg=f"Connected {profile}",
                    fail_prefix=f"Failed to connect {profile}")

    def _prompt_credentials(self, profile, on_done=None):
        dialog = Adw.MessageDialog.new(
            self, "Credentials required",
            f"Profile '{profile}' uses username/password authentication.")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        user_entry = Gtk.Entry(placeholder_text="Username")
        pass_entry = Gtk.PasswordEntry(show_peek_icon=True)
        body.append(user_entry)
        body.append(pass_entry)
        dialog.set_extra_child(body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Save & Connect")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "connect":
                self._save_creds_and_connect(
                    profile, user_entry.get_text(), pass_entry.get_text(),
                    on_done=on_done)

        dialog.connect("response", on_response)
        dialog.present()

    def _save_creds_and_connect(self, profile, username, password,
                                on_done=None):
        if not username or not password:
            self._report(False, "Credentials required",
                         "username and password must not be empty")
            return
        self._set_status(f"Saving credentials for {profile}…")
        self._spawn(vpn.setcreds_argv(profile, vpn.helper_path()),
                    success_msg=f"Credentials saved for {profile}",
                    fail_prefix=f"Saving credentials for {profile} failed",
                    stdin_text=f"{username}\n{password}\n",
                    on_done=on_done or (lambda: self._connect(profile)))

    def _on_import(self, _action, _param):
        self._open_import()

    def _on_import_clicked(self, _btn):
        self._open_import()

    def _open_import(self):
        dialog = Gtk.FileDialog(title="Import .ovpn profile")
        dialog.open(self, None, self._on_import_chosen)

    def _on_import_chosen(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # user cancelled the file picker
        path = gfile.get_path()
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self._set_status(f"Importing {name}… (approve the password prompt)")
        self._spawn(vpn.import_argv(path),
                    success_msg=f"Imported {name}",
                    fail_prefix=f"Import of {name} failed",
                    on_done=self._reload_profiles)

    def _spawn(self, argv, success_msg=None, fail_prefix="Command failed",
               on_done=None, stdin_text=None):
        flags = Gio.SubprocessFlags.STDERR_PIPE
        if stdin_text is not None:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        try:
            proc = Gio.Subprocess.new(argv, flags)
        except GLib.Error as exc:
            self._report(False, fail_prefix, str(exc))
            return

        def done(p, res):
            try:
                _ok, _out, err = p.communicate_utf8_finish(res)
            except GLib.Error as exc:
                self._report(False, fail_prefix, str(exc))
                return
            if p.get_exit_status() != 0:
                self._report(False, fail_prefix, (err or "").strip())
            else:
                self._report(True, success_msg or "Done", "")
                if on_done:
                    on_done()

        proc.communicate_utf8_async(stdin_text, None, done)

    def _report(self, ok: bool, message: str, detail: str):
        if ok:
            self._set_status(message)
            self._toast.add_toast(Adw.Toast.new(message))
        else:
            full = f"{message}: {detail}" if detail else message
            self._set_status(full, error=True)
            self._toast.add_toast(Adw.Toast.new(full))

    def _set_status_pill(self, text, connected):
        self._status_pill.set_text(text)
        self._status_pill.remove_css_class("success")
        self._status_pill.remove_css_class("dim-label")
        self._status_pill.add_css_class("success" if connected else "dim-label")

    def _set_action_button(self, label, style, sensitive):
        self._action_btn.set_label(label)
        self._action_btn.set_sensitive(sensitive)
        self._action_btn.remove_css_class("suggested-action")
        self._action_btn.remove_css_class("destructive-action")
        if style:
            self._action_btn.add_css_class(style)

    def _tick(self):
        try:
            self._paint_tunnel_connections()
            profile = self._selected_profile()
            if not profile:
                return True
            state = vpn.is_active(profile)
            if state == "active":
                self._set_status_pill("Connected", True)
                self._set_action_button("Disconnect", "destructive-action", True)
                self._update_usage(profile)
            elif state == "activating":
                self._set_status_pill("Connecting", False)
                self._set_action_button("Connecting…", None, False)
            else:
                self._set_status_pill("Disconnected", False)
                self._set_action_button("Connect", "suggested-action", True)
                self._sampler = None
                self._iface = None
            self._maybe_refresh_topology()
        except Exception:
            pass  # keep the timer alive; skip this tick on transient errors
        return True

    def _paint_tunnel_connections(self):
        diagram = self._topo_view._diagram
        topo = diagram._topo
        if topo is None:
            return
        for dev in topo.devices:
            if dev.kind == "tunnel" and dev.manual:
                stage, _msg = self._tunnels.status(dev.id)
                diagram.set_tunnel_status(dev.id, stage)

    def _on_close_request(self, *_args):
        self._tunnels.clear()
        return False  # allow the window to close

    def _update_usage(self, profile):
        iface = detect_iface()
        if iface and iface != self._iface:
            self._iface = iface
            self._sampler = Sampler(iface)
        if not self._sampler:
            return
        try:
            s = self._sampler.sample()
        except OSError:
            return
        self._up_label.set_text(human_speed(s["up_bps"]))
        self._down_label.set_text(human_speed(s["down_bps"]))
        self._spark.push(s["up_bps"], s["down_bps"])
        self._session_row.set_subtitle(
            f"↑ {human_bytes(s['total_tx'])}  ↓ {human_bytes(s['total_rx'])}")
        self._uptime_row.set_subtitle(self._uptime(profile))
        self._set_info_rows(iface, profile)

    def _set_info_rows(self, iface, profile):
        ip = self._iface_ip(iface)
        remote, proto = vpn.parse_remote(f"{vpn.CLIENT_DIR}/{profile}.conf")
        self._ip_row.set_subtitle(ip or "—")
        self._remote_row.set_subtitle(remote or "—")
        self._proto_row.set_subtitle((proto or "—").upper())

    def _uptime(self, profile):
        mono = vpn.unit_property(profile, "ActiveEnterTimestampMonotonic")
        try:
            started_us = int(mono)
        except ValueError:
            return "00:00:00"
        now_us = GLib.get_monotonic_time()
        return human_duration(max(0, (now_us - started_us) // 1_000_000))

    def _iface_ip(self, iface):
        try:
            cp = subprocess.run(["ip", "-o", "-4", "addr", "show", iface],
                                capture_output=True, text=True)
            parts = cp.stdout.split()
            if "inet" in parts:
                return parts[parts.index("inet") + 1].split("/")[0]
        except (OSError, ValueError, IndexError):
            return None
        return None

    def _maybe_refresh_topology(self):
        if self._stack.get_visible_child_name() != "topology":
            return
        self._refresh_topology()
        now = time.monotonic()
        if (not self._topology_sweeping and
                (self._topology_last_sweep is None or
                 now - self._topology_last_sweep >= 60)):
            self._on_scan_requested()

    def _on_scan_requested(self):
        if self._topology_sweeping or self._cidr is None:
            return
        self._topology_sweeping = True
        self._spawn_quiet(topology.sweep_argv(self._cidr),
                          on_done=self._on_sweep_done)

    def _on_sweep_done(self):
        self._topology_sweeping = False
        self._topology_last_sweep = time.monotonic()
        self._refresh_topology()

    def _spawn_quiet(self, argv, on_done=None):
        try:
            proc = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_SILENCE |
                Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            if on_done:
                on_done()
            return

        def done(p, res):
            try:
                p.wait_finish(res)
            except GLib.Error:
                pass
            if on_done:
                on_done()

        proc.wait_async(None, done)

    def _refresh_topology(self):
        route_text = self._read_proc("/proc/net/route")
        gateway, cidr = topology.gateway_and_subnet(route_text)
        self._cidr = cidr
        addr_text = self._run_ip("ip", "-o", "-4", "addr", "show")
        own = topology.local_ips(addr_text)
        arp_text = self._read_proc("/proc/net/arp")
        ndisc_text = self._run_ip("ip", "-6", "neigh", "show")
        devices = topology.neighbors(arp_text, ndisc_text, own_ips=own)
        lan = topology.build_segment(gateway, cidr, devices)
        auto_tunnels = topology.connected_tunnels(
            self._profiles, topology.tun_ips(addr_text))
        manual = load_manual_tunnels()
        auto_tunnels = tunnel_backend.dedupe_auto_tunnels(auto_tunnels, manual)
        hostname = socket.gethostname() or "This machine"
        topo = build_tree_topology(hostname, own, lan, auto_tunnels, manual)
        self._topo_view.set_topology(topo)

    @staticmethod
    def _read_proc(path):
        try:
            with open(path) as fh:
                return fh.read()
        except OSError:
            return ""

    @staticmethod
    def _run_ip(*argv):
        try:
            cp = subprocess.run(argv, capture_output=True, text=True)
            return cp.stdout or ""
        except OSError:
            return ""

    def _on_add_tunnel(self, parent_id=None):
        dialog = self._tunnel_dialog(parent_id=parent_id)
        if dialog is not None:
            dialog.present()

    def _tunnel_dialog(self, parent_id=None, source_profile=None, tunnel=None):
        devices = self._topo_view._diagram._topo.devices if (
            self._topo_view._diagram._topo) else []
        if not devices:
            return None

        dialog = Adw.MessageDialog.new(
            self, "Edit Tunnel" if tunnel else "Add Tunnel",
            "Add a manual tunnel node to the topology tree.")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # Adw.ComboRow is a PreferencesRow and must live in a
        # PreferencesGroup (a GtkListBox) or its popover cannot open.
        group = Adw.PreferencesGroup()

        # Source picker: Free-form or an imported .ovpn profile.
        source_combo = Adw.ComboRow(title="Source")
        source_combo.set_model(Gtk.StringList.new(
            ["Free-form", *self._profiles]))
        source_combo.set_selected(0)
        group.add(source_combo)

        # Parent picker. In edit mode, exclude self and descendants.
        if tunnel is not None:
            excluded = descendant_ids(load_manual_tunnels(),
                                      tunnel.id) | {tunnel.id}
            parent_devices = [d for d in devices if d.id not in excluded]
        else:
            parent_devices = list(devices)
        parent_labels = [f"{d.label} ({d.ip or d.id})"
                         for d in parent_devices]
        parent_combo = Adw.ComboRow(title="Connect from")
        parent_combo.set_model(Gtk.StringList.new(parent_labels))
        if parent_id is not None:
            for i, d in enumerate(parent_devices):
                if d.id == parent_id:
                    parent_combo.set_selected(i)
                    break
        else:
            parent_combo.set_selected(0)
        group.add(parent_combo)

        label_entry = Gtk.Entry(placeholder_text="Label (e.g. nested-ssh)")
        if tunnel is not None:
            label_entry.set_text(tunnel.label)
        body.append(label_entry)

        remote_entry = Gtk.Entry(placeholder_text="Remote (hostname or IP)")
        if tunnel is not None:
            remote_entry.set_text(tunnel.detail or "")
        body.append(remote_entry)

        proto_names = ["SSH", "WireGuard", "OpenVPN", "Other"]
        proto_combo = Adw.ComboRow(title="Protocol")
        proto_combo.set_model(Gtk.StringList.new(proto_names))
        if tunnel is not None:
            idx = (proto_names.index(tunnel.protocol)
                   if tunnel.protocol in proto_names else 0)
            proto_combo.set_selected(idx)
        else:
            proto_combo.set_selected(0)
        group.add(proto_combo)

        body.append(group)

        # SSH-only fields: username and optional fixed SOCKS port.
        ssh_fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        user_entry = Gtk.Entry(placeholder_text="Username (SSH)")
        default_user = tunnel.user if (tunnel is not None and tunnel.user) \
            else getpass.getuser()
        user_entry.set_text(default_user)
        ssh_fields.append(user_entry)
        port_entry = Gtk.Entry(placeholder_text="Local SOCKS port (optional)")
        if tunnel is not None and tunnel.port is not None:
            port_entry.set_text(str(tunnel.port))
        ssh_fields.append(port_entry)
        body.append(ssh_fields)

        def refresh_ssh_fields():
            proto_names_ = proto_names
            ssh_fields.set_visible(
                proto_combo.get_selected() >= 0
                and proto_names_[proto_combo.get_selected()] == "SSH")

        proto_combo.connect("notify::selected",
                            lambda *_: refresh_ssh_fields())
        refresh_ssh_fields()

        def on_source_changed(*_args):
            idx = source_combo.get_selected()
            if idx <= 0 or idx > len(self._profiles):
                return
            profile = self._profiles[idx - 1]
            remote, _proto = vpn.parse_remote(
                f"{vpn.CLIENT_DIR}/{profile}.conf")
            if not label_entry.get_text().strip():
                label_entry.set_text(profile)
            if remote and not remote_entry.get_text().strip():
                remote_entry.set_text(remote)
            proto_combo.set_selected(proto_names.index("OpenVPN"))

        source_combo.connect("notify::selected", on_source_changed)
        if tunnel is not None and tunnel.profile in self._profiles:
            source_combo.set_selected(1 + self._profiles.index(tunnel.profile))
        elif source_profile is not None and source_profile in self._profiles:
            source_combo.set_selected(1 + self._profiles.index(source_profile))
            on_source_changed()

        dialog.set_extra_child(body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Save" if tunnel else "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response != "add":
                return
            label = label_entry.get_text().strip()
            remote = remote_entry.get_text().strip()
            if not label:
                _d.set_body("Label is required.")
                return
            if not remote:
                _d.set_body("Remote is required.")
                return
            parent_idx = parent_combo.get_selected()
            parent_id_sel = (parent_devices[parent_idx].id
                             if 0 <= parent_idx < len(parent_devices)
                             else "pc")
            proto_model = proto_combo.get_model()
            proto = proto_model.get_string(proto_combo.get_selected()) if (
                proto_combo.get_selected() >= 0) else "SSH"
            profile = None
            src_idx = source_combo.get_selected()
            if proto == "OpenVPN" and 0 < src_idx <= len(self._profiles):
                profile = self._profiles[src_idx - 1]
            user = None
            port = None
            if proto == "SSH":
                user = user_entry.get_text().strip() or None
                port_text = port_entry.get_text().strip()
                if port_text:
                    try:
                        port = int(port_text)
                    except ValueError:
                        port = None  # invalid -> auto-assign at connect time
            existing = load_manual_tunnels()
            if tunnel is not None:
                existing = update_manual_tunnel(
                    existing, tunnel.id, label=label, parent_id=parent_id_sel,
                    remote=remote, protocol=proto, profile=profile,
                    user=user, port=port)
                save_manual_tunnels(existing)
                self._refresh_topology()
                self._toast.add_toast(
                    Adw.Toast.new(f"Tunnel '{label}' updated"))
            else:
                import uuid
                new_tun = Device(
                    id=f"manual:{uuid.uuid4().hex[:8]}",
                    kind="tunnel", label=label, parent_id=parent_id_sel,
                    manual=True, protocol=proto, detail=remote,
                    profile=profile, user=user, port=port)
                existing.append(new_tun)
                save_manual_tunnels(existing)
                self._refresh_topology()
                self._toast.add_toast(
                    Adw.Toast.new(f"Tunnel '{label}' added"))

        dialog.connect("response", on_response)
        return dialog

    def _on_node_context(self, device, x, y):
        popover = Gtk.Popover()
        popover.set_parent(self._topo_view._diagram)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        if device.kind == "tunnel":
            if tunnel_backend.is_connectable(device, self._profiles):
                stage, _msg = self._tunnels.status(device.id)
                label = ("Disconnect" if stage in ("connected", "connecting")
                         else "Connect…")
                conn_btn = Gtk.Button(label=label)
                conn_btn.add_css_class("flat")
                conn_btn.set_halign(Gtk.Align.START)
                if stage in ("connected", "connecting"):
                    conn_btn.connect("clicked", lambda *_: (
                        popover.popdown(), self._on_disconnect_tunnel(device)))
                else:
                    conn_btn.connect("clicked", lambda *_: (
                        popover.popdown(), self._on_connect_tunnel(device)))
                box.append(conn_btn)
            elif device.manual:
                disabled = Gtk.Button(label="Connect…")
                disabled.add_css_class("flat")
                disabled.set_halign(Gtk.Align.START)
                disabled.set_sensitive(False)
                disabled.set_tooltip_text(
                    "Connect is not supported for this tunnel type.")
                box.append(disabled)
            test_btn = Gtk.Button(label="Test connectivity…")
            test_btn.add_css_class("flat")
            test_btn.set_halign(Gtk.Align.START)
            test_btn.set_sensitive(bool(device.detail))
            test_btn.connect("clicked", lambda *_: (
                popover.popdown(), self._on_test_tunnel(device)))
            box.append(test_btn)

        if device.manual:
            edit_btn = Gtk.Button(label="Edit…")
            edit_btn.add_css_class("flat")
            edit_btn.set_halign(Gtk.Align.START)
            edit_btn.connect("clicked", lambda *_: (
                popover.popdown(), self._on_edit_tunnel(device)))
            box.append(edit_btn)
            delete_btn = Gtk.Button(label="Delete…")
            delete_btn.add_css_class("flat")
            delete_btn.set_halign(Gtk.Align.START)
            delete_btn.connect("clicked", lambda *_: (
                popover.popdown(), self._on_delete_tunnel(device)))
            box.append(delete_btn)

        add_child_btn = Gtk.Button(label="Add child tunnel…")
        add_child_btn.add_css_class("flat")
        add_child_btn.set_halign(Gtk.Align.START)
        add_child_btn.connect("clicked", lambda *_: (
            popover.popdown(), self._on_add_tunnel(parent_id=device.id)))
        box.append(add_child_btn)

        popover.set_child(box)
        popover.set_pointing_to(Gdk.Rectangle(int(x), int(y), 1, 1))
        self._context_popover = popover
        popover.popup()

    def _on_edit_tunnel(self, device):
        dialog = self._tunnel_dialog(tunnel=device)
        if dialog is not None:
            dialog.present()

    def _on_delete_tunnel(self, device):
        children = [d for d in (self._topo_view._diagram._topo.devices if (
            self._topo_view._diagram._topo) else [])
            if d.parent_id == device.id]
        note = " Its children will be re-parented to its parent." if children else ""
        dialog = Adw.MessageDialog.new(
            self, "Delete tunnel",
            f"Delete '{device.label}'?{note}")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("delete")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response != "delete":
                return
            existing = load_manual_tunnels()
            updated = delete_manual_tunnel(existing, device.id)
            save_manual_tunnels(updated)
            self._refresh_topology()
            self._toast.add_toast(
                Adw.Toast.new(f"Tunnel '{device.label}' deleted"))

        dialog.connect("response", on_response)
        dialog.present()

    def _on_test_tunnel(self, device):
        self._topo_view._diagram.set_tunnel_status(device.id, "testing")
        self._toast.add_toast(
            Adw.Toast.new(f"Testing tunnel '{device.label}'…"))

        def work():
            status, message = topology.test_tunnel(
                device.detail, device.protocol)
            GLib.idle_add(self._finish_tunnel_test, device, status, message)

        threading.Thread(target=work, daemon=True).start()

    def _on_connect_tunnel(self, device):
        if device.protocol == "OpenVPN" and device.profile:
            conf = f"{vpn.CLIENT_DIR}/{device.profile}.conf"
            if vpn.needs_credentials(conf):
                self._prompt_credentials(
                    device.profile,
                    on_done=lambda: self._on_connect_tunnel(device))
                return
        topo = self._topo_view._diagram._topo
        if topo is None:
            return
        results = self._tunnels.connect(
            device, topo=topo, profiles=self._profiles)
        self._finish_tunnel_connect(results)

    def _finish_tunnel_connect(self, results):
        self._refresh_topology()
        for device_id, ok, msg in results:
            if msg == "skipped":
                continue
            toast = Adw.Toast.new(
                f"{self._tunnel_label(device_id)}: {msg}")
            if not ok:
                toast.set_timeout(4)
            self._toast.add_toast(toast)

    def _on_disconnect_tunnel(self, device):
        topo = self._topo_view._diagram._topo
        if topo is None:
            return
        self._tunnels.disconnect(device.id, topo=topo)
        self._refresh_topology()
        self._toast.add_toast(
            Adw.Toast.new(f"Disconnected '{device.label}'"))

    def _tunnel_label(self, device_id):
        topo = self._topo_view._diagram._topo
        if topo:
            for d in topo.devices:
                if d.id == device_id:
                    return d.label
        return device_id

    def _finish_tunnel_test(self, device, status, message):
        self._topo_view._diagram.set_tunnel_status(device.id, status)
        if status == "ok":
            self._toast.add_toast(Adw.Toast.new(
                f"Tunnel '{device.label}' reachable — {message}"))
        else:
            self._toast.add_toast(Adw.Toast.new(
                f"Tunnel '{device.label}' no response — {message}"))


class OpenVpnManagerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.get_active_window()
        if not win:
            win = Window(self)
        win.present()


def main() -> int:
    return OpenVpnManagerApp().run(None)
