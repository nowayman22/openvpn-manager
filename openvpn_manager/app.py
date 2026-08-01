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
        self.set_default_size(460, 640)
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

        self._spark = Sparkline()
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

        self._toast.set_child(box)
        toolbar.set_content(self._toast)
        self.set_content(toolbar)

        import_action = Gio.SimpleAction.new("import", None)
        import_action.connect("activate", self._on_import)
        self.add_action(import_action)

        self._reload_profiles()
        GLib.timeout_add(1000, self._tick)

    @staticmethod
    def _speed_header(text):
        label = Gtk.Label(label=text, halign=Gtk.Align.CENTER)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        return label

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

    def _prompt_credentials(self, profile):
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
                    profile, user_entry.get_text(), pass_entry.get_text())

        dialog.connect("response", on_response)
        dialog.present()

    def _save_creds_and_connect(self, profile, username, password):
        if not username or not password:
            self._report(False, "Credentials required",
                         "username and password must not be empty")
            return
        self._set_status(f"Saving credentials for {profile}…")
        self._spawn(vpn.setcreds_argv(profile, vpn.helper_path()),
                    success_msg=f"Credentials saved for {profile}",
                    fail_prefix=f"Saving credentials for {profile} failed",
                    stdin_text=f"{username}\n{password}\n",
                    on_done=lambda: self._connect(profile))

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
