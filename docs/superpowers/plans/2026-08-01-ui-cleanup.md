# UI Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the OpenVPN Manager window from a flat widget stack into a card-based dashboard (connection card, usage card, empty state) per the approved spec.

**Architecture:** Single-file refactor of `openvpn_manager/app.py`. Replace the flat `Gtk.Box` of labels with an `Adw.Clamp` + two `Adw.PreferencesGroup` cards plus an `Adw.StatusPage` empty state. All widget-writing methods (`_reload_profiles`, `_tick`, `_update_usage`) are rewired to the new widget refs. Backend modules and the pytest suite are untouched.

**Tech Stack:** Python 3, GTK 4, libadwaita 1.9.2 (system `python3` has `gi`; venv has pytest), PyGObject.

## Global Constraints

- Only `openvpn_manager/app.py` may change. `vpn.py`, `stats.py`, `sparkline.py`, `format.py`, and `tests/` must stay byte-identical.
- `Adw.Card` / `Adw.CardGroup` do NOT exist in this system's libadwaita 1.9.2 typelib (verified). Use `Adw.PreferencesGroup` for cards.
- Use existing helpers: `human_speed`, `human_bytes`, `human_duration` (from `.format`), `Sparkline`, `Sampler`, `detect_iface` (imports already present at top of `app.py`).
- Tests run with `.venv/bin/python -m pytest` (currently 34 passing). App runs with system `python3 -m openvpn_manager`.
- Verified API facts: `Adw.ComboRow.set_model(Gtk.StringList)`, `.get_selected()`, `.set_selected(int)`; `Adw.Clamp.set_maximum_size(int)`; `Adw.StatusPage.set_child(widget)`; `Adw.PreferencesGroup.add(widget)`; `Adw.ActionRow(title=...)` + `.set_subtitle(str)`; `Gtk.Grid.attach(widget, col, row, w, h)`; icon `network-vpn-symbolic` exists in Adwaita.
- Widgets are exercised manually (project convention; no GTK unit tests).

---
### Task 1: Rebuild the window layout in app.py

**Files:**
- Modify: `openvpn_manager/app.py`

**Interfaces:**
- Consumes: existing imports (`Adw, Gio, GLib, Gtk`, `vpn`, `human_bytes, human_duration, human_speed`, `Sparkline`, `Sampler, detect_iface`), existing methods `_connect`, `_prompt_credentials`, `_save_creds_and_connect`, `_spawn`, `_report`, `_uptime`, `_iface_ip`.
- Produces: new widget attributes consumed by `_tick`/`_update_usage`/`_reload_profiles`:
  - `self._status_pill` (unchanged, `Gtk.Label`)
  - `self._profile_combo` (`Adw.ComboRow`)
  - `self._action_btn` (`Gtk.Button`)
  - `self._up_label`, `self._down_label` (`Gtk.Label`)
  - `self._spark` (unchanged, `Sparkline`)
  - `self._session_row`, `self._uptime_row`, `self._ip_row`, `self._remote_row`, `self._proto_row` (`Adw.ActionRow`)
  - `self._status_line` (unchanged, `Gtk.Label`)
  - `self._cards_box`, `self._empty_page`
  - helpers: `_set_status_pill(text, connected)`, `_set_action_button(label, style, sensitive)`, `_speed_header(text)` (static), `_set_info_rows(iface, profile)`, `_open_import()`

The whole change is one atomic edit set in `app.py`. Do the steps in order; run the verifications in Step 8 and Step 9 before committing.

- [ ] **Step 1: Rewrite `Window.__init__`**

Replace the body of `Window.__init__` (currently lines 22-80: from `self.set_default_size(420, 560)` through `GLib.timeout_add(1000, self._tick)`) with:

```python
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
```

Note: `super().__init__(application=app, title="OpenVPN Manager")` must stay as the first line of `__init__` (it is currently line 23); keep it.

- [ ] **Step 2: Replace the `_labeled` helper with `_speed_header`**

Delete the `_labeled` method (currently lines 82-87) and replace it with:

```python
    @staticmethod
    def _speed_header(text):
        label = Gtk.Label(label=text, halign=Gtk.Align.CENTER)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        return label
```

- [ ] **Step 3: Rewrite `_reload_profiles`**

Replace the current `_reload_profiles` (lines 89-103) with:

```python
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
```

- [ ] **Step 4: Rewrite `_tick` and add `_set_status_pill` + `_set_action_button`**

Replace the current `_tick` (lines 231-248) with:

```python
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
```

- [ ] **Step 5: Rewrite `_update_usage` and add `_set_info_rows`; delete `_info`**

Replace the current `_update_usage` (lines 250-267) with:

```python
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
```

Then delete the now-unused `_info` method (lines 278-288).

- [ ] **Step 6: Add an `activating` guard to `_on_action`**

Replace the current `_on_action` (lines 120-133) with:

```python
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
```

- [ ] **Step 7: Add `_open_import` and the button-click handler**

Replace the current `_on_import` (lines 177-179) with:

```python
    def _on_import(self, _action, _param):
        self._open_import()

    def _on_import_clicked(self, _btn):
        self._open_import()

    def _open_import(self):
        dialog = Gtk.FileDialog(title="Import .ovpn profile")
        dialog.open(self, None, self._on_import_chosen)
```

- [ ] **Step 8: Verify module imports and tests**

Run:

```
python3 -c "import openvpn_manager.app"
.venv/bin/python -m pytest -q
```

Expected: import prints nothing and exits 0; pytest reports `34 passed`.

- [ ] **Step 9: Manual verification of every UI state**

Run the app in a display session:

```
python3 -m openvpn_manager
```

Verify each state and fix anything off before committing:

1. **No profiles** (temporarily point away from real profiles if needed, e.g. check with `CLIENT_DIR` unreadable): StatusPage "No profiles imported" shows with a suggested "Browse for .ovpn…" button; cards are hidden; clicking the button opens the file picker; canceling returns to the empty state.
2. **Disconnected (with profiles)**: Connection card (ComboRow "Profile" + full-width suggested "Connect" button) and Usage card both visible; usage values read `0 B/s` / `UP`/`DOWN`, session `↑ 0 B  ↓ 0 B`, uptime `00:00:00`, info rows show `—`; status pill "Disconnected" dimmed.
3. **Connecting** (click Connect): button disabled and labeled "Connecting…", pill "Connecting".
4. **Connected** (wait for systemd unit to activate): pill "Connected" in green, button "Disconnect" (destructive styling), speed/sparkline/session/uptime update live, info rows show real IP/remote/proto.
5. **Credential profile** (one with bare `auth-user-pass`): Connect opens the credentials dialog; Save & Connect works; Cancel leaves it disconnected.
6. **Import** (menu and empty-state button): success imports a profile and refreshes the list.

- [ ] **Step 10: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "feat: rebuild UI as card dashboard with empty state

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage:** Layout (Clamp + 2 PreferencesGroup cards + ComboRow + full-width button + speed grid + sparkline + ActionRows + StatusPage) — Task 1 Step 1. Empty state replaces cards — Step 1/Step 3. Usage card always visible, zeroed when idle — Steps 1/4/5. Info as separate rows — Steps 1/5. Header pill success styling — Step 4. Activating button fix — Steps 4/6. Window 460x640 — Step 1. All present.
- **Placeholder scan:** No TBD/TODO; every step has full code.
- **Type consistency:** Widget refs in Steps 4/5 (`_up_label`, `_session_row`, `_ip_row`, etc.) match the attributes created in Step 1. `_speed_header` called in Step 1, defined in Step 2. `_on_import_clicked` connected in Step 1, defined in Step 7. `_open_import` defined in Step 7, called in both handlers. `_set_info_rows` defined in Step 5, called in Step 5's `_update_usage`. Consistent.
- **Backend untouched:** Steps modify only `app.py`. `_iface_ip`, `_uptime`, `_spawn`, `_report`, `_connect`, `_prompt_credentials`, `_save_creds_and_connect`, `_selected_profile`, `_on_import_chosen` are unchanged and still referenced correctly.
