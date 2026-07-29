"""Main GTK4/libadwaita application window."""

import getpass
import os
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import vpn
from .format import human_bytes, human_duration, human_speed
from .sparkline import Sparkline
from .stats import Sampler, detect_iface

APP_ID = "dev.nikits.OpenVpnManager"


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="OpenVPN Manager")
        self.set_default_size(420, 560)
        self._sampler = None
        self._iface = None

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

        self._profile_dropdown = Gtk.DropDown.new_from_strings([])
        box.append(self._labeled("Profile", self._profile_dropdown))

        self._action_btn = Gtk.Button(label="Connect")
        self._action_btn.add_css_class("suggested-action")
        self._action_btn.connect("clicked", self._on_action)
        box.append(self._action_btn)

        self._speed_label = Gtk.Label(label="↑ 0 B/s    ↓ 0 B/s")
        box.append(self._speed_label)
        self._spark = Sparkline()
        box.append(self._spark)
        self._data_label = Gtk.Label(label="Session: ↑ 0 B  ↓ 0 B", halign=Gtk.Align.START)
        box.append(self._data_label)
        self._uptime_label = Gtk.Label(label="Uptime: 00:00:00", halign=Gtk.Align.START)
        box.append(self._uptime_label)
        self._info_label = Gtk.Label(label="", halign=Gtk.Align.START, wrap=True)
        box.append(self._info_label)

        self._status_line = Gtk.Label(label="", halign=Gtk.Align.START, wrap=True)
        self._status_line.add_css_class("dim-label")
        box.append(self._status_line)

        self._toast.set_child(box)
        toolbar.set_content(self._toast)
        self.set_content(toolbar)

        import_action = Gio.SimpleAction.new("import", None)
        import_action.connect("activate", self._on_import)
        self.add_action(import_action)

        self._reload_profiles()
        GLib.timeout_add(1000, self._tick)

    def _labeled(self, text, widget):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label=text))
        widget.set_hexpand(True)
        row.append(widget)
        return row

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
        model = Gtk.StringList.new(self._profiles or ["(no profiles)"])
        self._profile_dropdown.set_model(model)
        self._profile_dropdown.set_sensitive(bool(self._profiles))
        self._action_btn.set_sensitive(bool(self._profiles))

    def _set_status(self, text: str, error: bool = False):
        self._status_line.set_text(text)
        self._status_line.remove_css_class("error")
        self._status_line.remove_css_class("success")
        if text:
            self._status_line.add_css_class("error" if error else "success")

    def _selected_profile(self):
        if not self._profiles:
            return None
        idx = self._profile_dropdown.get_selected()
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
        else:
            self._set_status(f"Connecting {profile}…")
            self._spawn(vpn.connect_argv(profile),
                        success_msg=f"Connected {profile}",
                        fail_prefix=f"Failed to connect {profile}")

    def _on_import(self, _action, _param):
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

    def _spawn(self, argv, success_msg=None, fail_prefix="Command failed", on_done=None):
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
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

        proc.communicate_utf8_async(None, None, done)

    def _report(self, ok: bool, message: str, detail: str):
        if ok:
            self._set_status(message)
            self._toast.add_toast(Adw.Toast.new(message))
        else:
            full = f"{message}: {detail}" if detail else message
            self._set_status(full, error=True)
            self._toast.add_toast(Adw.Toast.new(full))

    def _tick(self):
        profile = self._selected_profile()
        if not profile:
            return True
        state = vpn.is_active(profile)
        if state == "active":
            self._status_pill.set_text("Connected")
            self._action_btn.set_label("Disconnect")
            self._update_usage(profile)
        elif state == "activating":
            self._status_pill.set_text("Connecting")
            self._action_btn.set_label("Cancel")
        else:
            self._status_pill.set_text("Disconnected")
            self._action_btn.set_label("Connect")
            self._sampler = None
            self._iface = None
        return True

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
        self._speed_label.set_text(
            f"↑ {human_speed(s['up_bps'])}    ↓ {human_speed(s['down_bps'])}")
        self._spark.push(s["up_bps"], s["down_bps"])
        self._data_label.set_text(
            f"Session: ↑ {human_bytes(s['total_tx'])}  ↓ {human_bytes(s['total_rx'])}")
        self._uptime_label.set_text("Uptime: " + self._uptime(profile))
        self._info_label.set_text(self._info(profile, iface))

    def _uptime(self, profile):
        mono = vpn.unit_property(profile, "ActiveEnterTimestampMonotonic")
        try:
            started_us = int(mono)
        except ValueError:
            return "00:00:00"
        now_us = GLib.get_monotonic_time()
        return human_duration(max(0, (now_us - started_us) // 1_000_000))

    def _info(self, profile, iface):
        remote, proto = vpn.parse_remote(f"{vpn.CLIENT_DIR}/{profile}.conf")
        ip = self._iface_ip(iface)
        bits = []
        if ip:
            bits.append(f"IP {ip}")
        if remote:
            bits.append(f"remote {remote}")
        if proto:
            bits.append(f"proto {proto}")
        return "   ".join(bits)

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
