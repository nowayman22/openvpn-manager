# OpenVPN Manager — Design

Date: 2026-07-29
Status: Approved

## Summary

A native GTK4 / libadwaita desktop application for Hyprland/Wayland that manages
OpenVPN client profiles and visualizes live usage. It lists profiles found under
`/etc/openvpn/client/`, connects and disconnects them via the systemd
`openvpn-client@` template units, imports `.ovpn` files, and displays live
throughput, cumulative session data, uptime, and connection info. It launches
from the desktop app launcher like any other application.

Target platform: Arch Linux, Hyprland/Wayland. Python 3 with `python-gobject`,
GTK 4, and libadwaita (all already installed).

## Goals

- Full profile management: connect, disconnect, switch profile, import `.ovpn`.
- Live usage indicator: up/down throughput graph, cumulative session bytes,
  connection uptime, and connection info (VPN IP, remote host, cipher, protocol).
- Opens like a normal app via a `.desktop` entry.
- Silent (password-less) connect/disconnect after a one-time polkit rule install.

## Non-Goals

- Managing an OpenVPN *server* (only `openvpn-client@` units).
- Editing profile contents in-app (import only; editing is out of scope).
- NetworkManager integration (NetworkManager is not installed on this system).
- Multi-user / remote management.

## Architecture

Python package `openvpn_manager/`:

- `app.py` — `Adw.Application` subclass and the main `Adw.ApplicationWindow`.
  Owns the 1-second refresh timer via `GLib.timeout_add(1000, ...)`. Builds the
  UI from the widgets below and wires button actions to the backend.
- `vpn.py` — backend logic, no GTK imports:
  - `discover_profiles(dir)` — returns profile names from `*.conf` files in the
    client dir.
  - `state(name)` — active/inactive/activating via
    `systemctl is-active openvpn-client@<name>`.
  - `connect(name)` / `disconnect(name)` — `systemctl start|stop
    openvpn-client@<name>` (async).
  - `import_profile(src_path)` — copy an `.ovpn`/`.conf` into the client dir as
    `<name>.conf` via `pkexec`.
  - `unit_property(name, prop)` — read a systemd unit property (e.g.
    `ActiveEnterTimestamp`) via `systemctl show`.
  - All process execution goes through a single injectable `run(argv)` function
    so the module is unit-testable with a fake runner.
- `stats.py` — throughput sampler, no GTK imports:
  - `detect_iface()` — find the active `tun*`/`tap*` interface in
    `/sys/class/net/`.
  - `Sampler` — reads `rx_bytes`/`tx_bytes`, keeps a baseline captured when the
    interface first appears, and on each `sample()` returns instantaneous
    up/down speed (delta / elapsed) plus cumulative session totals (current -
    baseline).
- `sparkline.py` — `Gtk.DrawingArea` subclass drawing a rolling throughput graph
  with Cairo. Holds a fixed-length deque of recent samples; `push(up, down)`
  appends and queues a redraw. Auto-scales the y-axis to the window max.
- `format.py` — pure helpers: `human_bytes(n)`, `human_speed(bytes_per_s)`,
  `human_duration(seconds)` → `HH:MM:SS`. Fully unit-tested.

### UI layout

`Adw.ApplicationWindow` with an `Adw.HeaderBar` and a vertical content box:

- Header: app title, a connection status pill (Connected / Connecting /
  Disconnected), and an overflow menu (Import `.ovpn`, About).
- Profile row: `Adw.ComboRow` (or dropdown) to pick the active profile.
- Action row: a primary Connect/Disconnect toggle button; an Import button.
- Usage section (visible when connected):
  - Throughput cards: current ↑ and ↓ speed (`human_speed`).
  - The `Sparkline` graph beneath the cards.
  - Session data card: cumulative ↑/↓ bytes (`human_bytes`).
  - Uptime card (`human_duration`).
  - Connection info card: VPN IP, remote host, protocol, cipher.

## Data Flow

1. On startup, `discover_profiles()` populates the profile dropdown; the first
   active profile (if any) is auto-selected, else the first profile.
2. `GLib.timeout_add(1000)` fires every second:
   - Query `state()` of the selected profile → update the status pill and
     Connect/Disconnect button label.
   - If active: `Sampler.sample()` → update speed cards, push to sparkline,
     update session-data and uptime cards; refresh connection info.
   - If not active: show zero throughput, hide/blank the usage section.
3. Connect/Disconnect and Import run as async subprocesses
   (`Gio.Subprocess`) so the UI thread never blocks.

## Privilege Model

- **Read paths need no root:** `systemctl is-active`/`show`, reading
  `/sys/class/net/<iface>/statistics/*`, and `ip -j addr show` all work as the
  normal user.
- **Connect/disconnect:** `systemctl start|stop openvpn-client@*` requires
  authorization. A one-time polkit rule (installed by `install.sh` to
  `/etc/polkit-1/rules.d/50-openvpn-manager.rules`) grants the invoking user
  password-less `org.freedesktop.systemd1.manage-units` for units whose name
  matches `openvpn-client@`. Without this rule the app still works but the
  desktop polkit agent prompts for a password each time.
- **Import:** copying a file into root-owned `/etc/openvpn/client/` is done with
  `pkexec cp`, which triggers the desktop auth dialog. Import is infrequent, so a
  per-action prompt is acceptable and no extra polkit rule is needed for it.

## Connection Info Sourcing

- **VPN IP:** `ip -j addr show <iface>` (JSON), first inet address.
- **Remote host / protocol:** parsed from the profile's `remote` and `proto`
  lines.
- **Uptime:** `ActiveEnterTimestamp` from `systemctl show openvpn-client@<name>`.
- **Cipher:** best-effort from the unit journal (negotiated `data-ciphers`);
  falls back to the profile's `data-ciphers`/`cipher` line when the journal is
  not readable.

## Error and Empty States

- No profiles found → empty state with an "Import a .ovpn file" call to action.
- Selected profile is `activating` but no `tun` interface yet → status pill shows
  "Connecting", usage section shows zeros.
- `systemctl`/`pkexec` failure or user cancel → `Adw.Toast` with the captured
  stderr/message; UI returns to prior state.
- Interface disappears mid-session (drop) → status returns to Disconnected, timer
  keeps polling for reconnect.

## Packaging

`install.sh` (run once, uses `sudo`/`pkexec` for the privileged copies):

- Installs the `openvpn_manager` package and an `openvpn-manager` launcher script.
- Installs the polkit rule to `/etc/polkit-1/rules.d/50-openvpn-manager.rules`.
- Installs `openvpn-manager.desktop` and an icon into the user's
  `~/.local/share/applications/` and `~/.local/share/icons/` so it appears in the
  Hyprland app launcher.

## Testing

Unit tests (pytest), no display required:

- `format.py`: byte, speed, and duration formatting across edge values (0, sub-KB,
  TB, sub-second, multi-hour).
- `stats.py`: throughput diffing and cumulative totals via a fake stats source;
  interface detection via a temp `sys`-like directory.
- `vpn.py`: profile discovery against a temp directory; connect/disconnect/state
  command construction via an injected fake `run()`.

GTK widgets (`app.py`, `sparkline.py`) are kept thin so the tested logic lives in
the non-GTK modules; widgets are exercised manually.

## Open Questions

None. Design approved 2026-07-29.
