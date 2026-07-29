# OpenVPN Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a native GTK4/libadwaita desktop app that manages OpenVPN client profiles and visualizes live usage (throughput, session data, uptime, connection info), launchable from the Hyprland app menu.

**Architecture:** A Python package `openvpn_manager/` split into non-GTK logic modules (`format`, `stats`, `vpn`) that are unit-tested, plus thin GTK widgets (`sparkline`, `app`) exercised manually. Privileged actions use a polkit rule (connect/disconnect) and `pkexec` (import). Packaging installs a `.desktop` launcher, icon, and polkit rule.

**Tech Stack:** Python 3, PyGObject (GTK 4, libadwaita, GLib/Gio), Cairo, pytest, systemd `openvpn-client@` template units, polkit.

## Global Constraints

- Platform: Arch Linux, Hyprland/Wayland. System Python 3 with system-installed `python-gobject`, GTK 4, libadwaita (already present).
- Tests must not import `gi` (no display in CI); all tested logic lives in `format.py`, `stats.py`, `vpn.py`.
- Manage only `openvpn-client@<profile>` systemd units. Client config dir: `/etc/openvpn/client/`.
- Reading state/stats requires no root; only connect/disconnect (polkit rule) and import (`pkexec`) are privileged.
- Every process invocation in `vpn.py` goes through the injectable `run(argv)` so it is testable with a fake runner.
- Commit after every task with a message ending: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- `openvpn_manager/__init__.py` — package marker, version.
- `openvpn_manager/format.py` — pure formatting helpers (bytes, speed, duration).
- `openvpn_manager/stats.py` — interface detection + throughput `Sampler`.
- `openvpn_manager/vpn.py` — profile discovery, systemd control, import (via injectable `run`).
- `openvpn_manager/sparkline.py` — `Gtk.DrawingArea` throughput graph.
- `openvpn_manager/app.py` — `Adw.Application` + main window, refresh timer, wiring.
- `openvpn_manager/__main__.py` — `python -m openvpn_manager` entry point.
- `tests/test_format.py`, `tests/test_stats.py`, `tests/test_vpn.py` — unit tests.
- `packaging/50-openvpn-manager.rules` — polkit rule.
- `packaging/openvpn-manager.desktop` — desktop entry.
- `packaging/openvpn-manager.svg` — app icon.
- `openvpn-manager` — launcher script.
- `install.sh` — installer.
- `pyproject.toml` — project metadata + pytest config.

---

### Task 1: Project scaffold and formatting helpers

**Files:**
- Create: `pyproject.toml`
- Create: `openvpn_manager/__init__.py`
- Create: `openvpn_manager/format.py`
- Test: `tests/test_format.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `human_bytes(n: int) -> str` — e.g. `1536` → `"1.5 KB"`, `0` → `"0 B"`.
  - `human_speed(bps: float) -> str` — e.g. `1_500_000.0` → `"1.4 MB/s"`.
  - `human_duration(seconds: int) -> str` — e.g. `95` → `"00:01:35"`, `3661` → `"01:01:01"`.

- [ ] **Step 1: Install pytest (system package)**

Run: `sudo pacman -S --needed --noconfirm python-pytest`
Expected: `python-pytest` installed; `python3 -m pytest --version` prints a version.

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "openvpn-manager"
version = "0.1.0"
description = "GTK4 OpenVPN client manager with live usage indicator"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Create `openvpn_manager/__init__.py`**

```python
"""OpenVPN Manager - GTK4 client manager with live usage indicator."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Write the failing test**

`tests/test_format.py`:

```python
from openvpn_manager.format import human_bytes, human_speed, human_duration


def test_human_bytes_zero():
    assert human_bytes(0) == "0 B"


def test_human_bytes_kb():
    assert human_bytes(1536) == "1.5 KB"


def test_human_bytes_mb():
    assert human_bytes(1_572_864) == "1.5 MB"


def test_human_bytes_gb():
    assert human_bytes(1_610_612_736) == "1.5 GB"


def test_human_speed_kb():
    assert human_speed(1536.0) == "1.5 KB/s"


def test_human_speed_zero():
    assert human_speed(0.0) == "0 B/s"


def test_human_duration_seconds():
    assert human_duration(95) == "00:01:35"


def test_human_duration_hours():
    assert human_duration(3661) == "01:01:01"


def test_human_duration_zero():
    assert human_duration(0) == "00:00:00"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `python3 -m pytest tests/test_format.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openvpn_manager.format'`.

- [ ] **Step 6: Write minimal implementation**

`openvpn_manager/format.py`:

```python
"""Pure formatting helpers. No GTK imports."""

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def _scale(n: float) -> tuple[float, str]:
    value = float(n)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return value, unit
        value /= 1024
    return value, _UNITS[-1]


def human_bytes(n: int) -> str:
    value, unit = _scale(n)
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def human_speed(bps: float) -> str:
    return f"{human_bytes(int(bps))}/s"


def human_duration(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_format.py -v`
Expected: PASS (9 passed).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml openvpn_manager/__init__.py openvpn_manager/format.py tests/test_format.py
git commit -m "feat: add project scaffold and formatting helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Interface detection and throughput sampler

**Files:**
- Create: `openvpn_manager/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `detect_iface(sys_net_dir: str = "/sys/class/net") -> str | None` — first interface name starting with `tun` or `tap` whose `operstate` is not `down`; else `None`.
  - `read_counters(iface: str, sys_net_dir: str = "/sys/class/net") -> tuple[int, int]` — returns `(rx_bytes, tx_bytes)`.
  - `class Sampler(iface: str, reader=read_counters, clock=time.monotonic)` with `sample() -> dict` returning keys `up_bps: float`, `down_bps: float`, `total_rx: int`, `total_tx: int`. First `sample()` establishes the baseline and returns zero speeds and zero totals. `up` maps to tx, `down` maps to rx.

- [ ] **Step 1: Write the failing test**

`tests/test_stats.py`:

```python
import os

from openvpn_manager.stats import detect_iface, read_counters, Sampler


def _make_net(tmp_path, name, operstate, rx, tx):
    d = tmp_path / name
    (d / "statistics").mkdir(parents=True)
    (d / "operstate").write_text(operstate + "\n")
    (d / "statistics" / "rx_bytes").write_text(str(rx) + "\n")
    (d / "statistics" / "tx_bytes").write_text(str(tx) + "\n")


def test_detect_iface_finds_tun(tmp_path):
    _make_net(tmp_path, "enp0s31f6", "up", 0, 0)
    _make_net(tmp_path, "tun0", "up", 10, 20)
    assert detect_iface(str(tmp_path)) == "tun0"


def test_detect_iface_ignores_down_tun(tmp_path):
    _make_net(tmp_path, "tun0", "down", 0, 0)
    assert detect_iface(str(tmp_path)) is None


def test_detect_iface_none_when_no_tunnel(tmp_path):
    _make_net(tmp_path, "wlan0", "up", 0, 0)
    assert detect_iface(str(tmp_path)) is None


def test_read_counters(tmp_path):
    _make_net(tmp_path, "tun0", "up", 111, 222)
    assert read_counters("tun0", str(tmp_path)) == (111, 222)


def test_sampler_first_sample_is_zero():
    counters = iter([(1000, 2000)])
    clock = iter([100.0])
    s = Sampler("tun0", reader=lambda i, d=None: next(counters), clock=lambda: next(clock))
    out = s.sample()
    assert out == {"up_bps": 0.0, "down_bps": 0.0, "total_rx": 0, "total_tx": 0}


def test_sampler_computes_speed_and_totals():
    counters = iter([(1000, 2000), (1000 + 500, 2000 + 100)])
    clock = iter([100.0, 102.0])  # 2 second gap
    s = Sampler("tun0", reader=lambda i, d=None: next(counters), clock=lambda: next(clock))
    s.sample()
    out = s.sample()
    assert out["down_bps"] == 250.0  # 500 rx / 2s
    assert out["up_bps"] == 50.0     # 100 tx / 2s
    assert out["total_rx"] == 500
    assert out["total_tx"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openvpn_manager.stats'`.

- [ ] **Step 3: Write minimal implementation**

`openvpn_manager/stats.py`:

```python
"""Interface detection and throughput sampling. No GTK imports."""

import os
import time


def detect_iface(sys_net_dir: str = "/sys/class/net") -> str | None:
    try:
        names = sorted(os.listdir(sys_net_dir))
    except FileNotFoundError:
        return None
    for name in names:
        if not (name.startswith("tun") or name.startswith("tap")):
            continue
        state_path = os.path.join(sys_net_dir, name, "operstate")
        try:
            state = open(state_path).read().strip()
        except OSError:
            state = "unknown"
        if state != "down":
            return name
    return None


def read_counters(iface: str, sys_net_dir: str = "/sys/class/net") -> tuple[int, int]:
    base = os.path.join(sys_net_dir, iface, "statistics")
    rx = int(open(os.path.join(base, "rx_bytes")).read().strip())
    tx = int(open(os.path.join(base, "tx_bytes")).read().strip())
    return rx, tx


class Sampler:
    def __init__(self, iface: str, reader=read_counters, clock=time.monotonic):
        self._iface = iface
        self._reader = reader
        self._clock = clock
        self._baseline: tuple[int, int] | None = None
        self._last: tuple[int, int] | None = None
        self._last_t: float | None = None

    def sample(self) -> dict:
        rx, tx = self._reader(self._iface)
        now = self._clock()
        if self._baseline is None:
            self._baseline = (rx, tx)
            self._last = (rx, tx)
            self._last_t = now
            return {"up_bps": 0.0, "down_bps": 0.0, "total_rx": 0, "total_tx": 0}
        dt = now - self._last_t if self._last_t is not None else 0.0
        drx = rx - self._last[0]
        dtx = tx - self._last[1]
        down_bps = drx / dt if dt > 0 else 0.0
        up_bps = dtx / dt if dt > 0 else 0.0
        self._last = (rx, tx)
        self._last_t = now
        return {
            "up_bps": up_bps,
            "down_bps": down_bps,
            "total_rx": rx - self._baseline[0],
            "total_tx": tx - self._baseline[1],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_stats.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add openvpn_manager/stats.py tests/test_stats.py
git commit -m "feat: add interface detection and throughput sampler

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: VPN backend (profiles, systemd control, import)

**Files:**
- Create: `openvpn_manager/vpn.py`
- Test: `tests/test_vpn.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CLIENT_DIR = "/etc/openvpn/client"`.
  - `unit_name(profile: str) -> str` → `f"openvpn-client@{profile}"`.
  - `discover_profiles(client_dir: str = CLIENT_DIR) -> list[str]` — sorted profile names from `*.conf` files (extension stripped); `[]` if dir missing.
  - `connect_argv(profile: str) -> list[str]` → `["systemctl", "start", "openvpn-client@<profile>"]`.
  - `disconnect_argv(profile: str) -> list[str]` → `["systemctl", "stop", "openvpn-client@<profile>"]`.
  - `import_argv(src: str, client_dir: str = CLIENT_DIR) -> list[str]` → `["pkexec", "cp", src, "<client_dir>/<basename-without-ext>.conf"]`.
  - `run(argv: list[str]) -> subprocess.CompletedProcess` — real runner (subprocess).
  - `is_active(profile: str, runner=run) -> str` — returns trimmed stdout of `systemctl is-active openvpn-client@<profile>` (e.g. `"active"`, `"inactive"`, `"activating"`, `"failed"`).
  - `unit_property(profile: str, prop: str, runner=run) -> str` — value of `systemctl show -p <prop> --value openvpn-client@<profile>`.
  - `parse_remote(conf_path: str) -> tuple[str | None, str | None]` — returns `(remote_host, proto)` parsed from the config's `remote` and `proto` lines; `(None, None)` if unreadable.

- [ ] **Step 1: Write the failing test**

`tests/test_vpn.py`:

```python
import subprocess

from openvpn_manager import vpn


def test_unit_name():
    assert vpn.unit_name("work") == "openvpn-client@work"


def test_discover_profiles(tmp_path):
    (tmp_path / "work.conf").write_text("")
    (tmp_path / "home.conf").write_text("")
    (tmp_path / "notes.txt").write_text("")
    assert vpn.discover_profiles(str(tmp_path)) == ["home", "work"]


def test_discover_profiles_missing_dir(tmp_path):
    assert vpn.discover_profiles(str(tmp_path / "nope")) == []


def test_connect_argv():
    assert vpn.connect_argv("work") == ["systemctl", "start", "openvpn-client@work"]


def test_disconnect_argv():
    assert vpn.disconnect_argv("work") == ["systemctl", "stop", "openvpn-client@work"]


def test_import_argv():
    argv = vpn.import_argv("/tmp/My VPN.ovpn", "/etc/openvpn/client")
    assert argv == ["pkexec", "cp", "/tmp/My VPN.ovpn", "/etc/openvpn/client/My VPN.conf"]


def test_is_active_uses_runner():
    calls = []

    def fake(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    assert vpn.is_active("work", runner=fake) == "active"
    assert calls[0] == ["systemctl", "is-active", "openvpn-client@work"]


def test_unit_property_uses_runner():
    def fake(argv):
        return subprocess.CompletedProcess(argv, 0, stdout="Sat 2026-07-29 10:00:00\n", stderr="")

    val = vpn.unit_property("work", "ActiveEnterTimestamp", runner=fake)
    assert val == "Sat 2026-07-29 10:00:00"


def test_parse_remote(tmp_path):
    conf = tmp_path / "work.conf"
    conf.write_text("client\nremote vpn.example.com 1194\nproto udp\n")
    assert vpn.parse_remote(str(conf)) == ("vpn.example.com", "udp")


def test_parse_remote_missing(tmp_path):
    assert vpn.parse_remote(str(tmp_path / "nope.conf")) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_vpn.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openvpn_manager.vpn'`.

- [ ] **Step 3: Write minimal implementation**

`openvpn_manager/vpn.py`:

```python
"""OpenVPN systemd backend. No GTK imports.

All process execution goes through an injectable runner so this module is
testable without touching the real system.
"""

import glob
import os
import subprocess

CLIENT_DIR = "/etc/openvpn/client"


def unit_name(profile: str) -> str:
    return f"openvpn-client@{profile}"


def discover_profiles(client_dir: str = CLIENT_DIR) -> list[str]:
    names = []
    for path in glob.glob(os.path.join(client_dir, "*.conf")):
        names.append(os.path.splitext(os.path.basename(path))[0])
    return sorted(names)


def connect_argv(profile: str) -> list[str]:
    return ["systemctl", "start", unit_name(profile)]


def disconnect_argv(profile: str) -> list[str]:
    return ["systemctl", "stop", unit_name(profile)]


def import_argv(src: str, client_dir: str = CLIENT_DIR) -> list[str]:
    stem = os.path.splitext(os.path.basename(src))[0]
    dest = os.path.join(client_dir, stem + ".conf")
    return ["pkexec", "cp", src, dest]


def run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def is_active(profile: str, runner=run) -> str:
    cp = runner(["systemctl", "is-active", unit_name(profile)])
    return (cp.stdout or "").strip()


def unit_property(profile: str, prop: str, runner=run) -> str:
    cp = runner(["systemctl", "show", "-p", prop, "--value", unit_name(profile)])
    return (cp.stdout or "").strip()


def parse_remote(conf_path: str) -> tuple[str | None, str | None]:
    remote = None
    proto = None
    try:
        with open(conf_path) as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "remote" and len(parts) >= 2:
                    remote = parts[1]
                elif parts[0] == "proto" and len(parts) >= 2:
                    proto = parts[1]
    except OSError:
        return None, None
    return remote, proto
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vpn.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -v`
Expected: PASS (25 passed).

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/vpn.py tests/test_vpn.py
git commit -m "feat: add VPN systemd backend

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Sparkline throughput graph widget

**Files:**
- Create: `openvpn_manager/sparkline.py`

**Interfaces:**
- Consumes: nothing (pure GTK/Cairo).
- Produces:
  - `class Sparkline(Gtk.DrawingArea)` with `push(up_bps: float, down_bps: float) -> None` (appends a sample, caps history at 60, queues redraw) and internal Cairo draw that renders two auto-scaled lines (down in one accent color, up in another) across the widget width.

This task is GTK UI; it is verified by a manual smoke render, not unit tests (per Global Constraints, tests must not import `gi`).

- [ ] **Step 1: Write the widget**

`openvpn_manager/sparkline.py`:

```python
"""Rolling throughput graph as a GTK DrawingArea."""

import collections

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

MAX_SAMPLES = 60


class Sparkline(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_content_height(120)
        self.set_hexpand(True)
        self._down = collections.deque(maxlen=MAX_SAMPLES)
        self._up = collections.deque(maxlen=MAX_SAMPLES)
        self.set_draw_func(self._draw)

    def push(self, up_bps: float, down_bps: float) -> None:
        self._up.append(max(0.0, up_bps))
        self._down.append(max(0.0, down_bps))
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        peak = max([1.0, *self._up, *self._down])
        self._draw_series(cr, self._down, width, height, peak, (0.20, 0.60, 0.86))
        self._draw_series(cr, self._up, width, height, peak, (0.90, 0.49, 0.13))

    def _draw_series(self, cr, series, width, height, peak, rgb):
        if len(series) < 2:
            return
        n = len(series)
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
```

- [ ] **Step 2: Manual smoke test**

Run:
```bash
python3 -c "
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk, Adw, GLib
from openvpn_manager.sparkline import Sparkline
app = Adw.Application(application_id='dev.test.spark')
def on_activate(a):
    w = Adw.ApplicationWindow(application=a, default_width=400, default_height=200)
    s = Sparkline()
    import random
    [s.push(random.random()*1e6, random.random()*1e6) for _ in range(30)]
    w.set_content(s); w.present()
    GLib.timeout_add(800, lambda: (w.close(), a.quit(), False)[2])
app.connect('activate', on_activate); app.run([])
print('sparkline OK')
"
```
Expected: a window briefly appears with two colored lines, then prints `sparkline OK` with exit code 0.

- [ ] **Step 3: Commit**

```bash
git add openvpn_manager/sparkline.py
git commit -m "feat: add sparkline throughput graph widget

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Main application window and refresh loop

**Files:**
- Create: `openvpn_manager/app.py`
- Create: `openvpn_manager/__main__.py`

**Interfaces:**
- Consumes: `openvpn_manager.format` (`human_speed`, `human_bytes`, `human_duration`), `openvpn_manager.stats` (`detect_iface`, `Sampler`), `openvpn_manager.vpn` (`discover_profiles`, `is_active`, `connect_argv`, `disconnect_argv`, `import_argv`, `unit_property`, `parse_remote`, `CLIENT_DIR`), `openvpn_manager.sparkline.Sparkline`.
- Produces:
  - `class OpenVpnManagerApp(Adw.Application)` — application id `dev.nikits.OpenVpnManager`.
  - `main() -> int` — constructs and runs the app.

This task is GTK UI wiring, verified by a manual launch, not unit tests.

- [ ] **Step 1: Write the application**

`openvpn_manager/app.py`:

```python
"""Main GTK4/libadwaita application window."""

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
        self._profiles = vpn.discover_profiles()
        model = Gtk.StringList.new(self._profiles or ["(no profiles)"])
        self._profile_dropdown.set_model(model)
        self._profile_dropdown.set_sensitive(bool(self._profiles))
        self._action_btn.set_sensitive(bool(self._profiles))

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
        argv = vpn.disconnect_argv(profile) if state == "active" else vpn.connect_argv(profile)
        self._spawn(argv)

    def _on_import(self, _action, _param):
        dialog = Gtk.FileDialog(title="Import .ovpn profile")
        dialog.open(self, None, self._on_import_chosen)

    def _on_import_chosen(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path:
            self._spawn(vpn.import_argv(path), on_done=self._reload_profiles)

    def _spawn(self, argv, on_done=None):
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as exc:
            self._toast.add_toast(Adw.Toast.new(str(exc)))
            return

        def done(p, res):
            try:
                ok, _out, err = p.communicate_utf8_finish(res)
                if p.get_exit_status() != 0:
                    self._toast.add_toast(Adw.Toast.new((err or "command failed").strip()))
                elif on_done:
                    on_done()
            except GLib.Error as exc:
                self._toast.add_toast(Adw.Toast.new(str(exc)))

        proc.communicate_utf8_async(None, None, done)

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
```

`openvpn_manager/__main__.py`:

```python
import sys

from .app import main

sys.exit(main())
```

- [ ] **Step 2: Verify it imports and launches**

Run:
```bash
python3 -c "from openvpn_manager.app import OpenVpnManagerApp; print('import OK')"
```
Expected: `import OK`.

Then launch interactively and confirm the window shows the profile dropdown, Connect button, and usage section:
```bash
python3 -m openvpn_manager
```
Expected: window opens. With no profiles it shows `(no profiles)` and a disabled Connect button. Close the window; exit code 0.

- [ ] **Step 3: Commit**

```bash
git add openvpn_manager/app.py openvpn_manager/__main__.py
git commit -m "feat: add main application window and refresh loop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Packaging, polkit rule, and installer

**Files:**
- Create: `packaging/50-openvpn-manager.rules`
- Create: `packaging/openvpn-manager.desktop`
- Create: `packaging/openvpn-manager.svg`
- Create: `openvpn-manager` (launcher script)
- Create: `install.sh`

**Interfaces:**
- Consumes: the `openvpn_manager` package from prior tasks.
- Produces: an installed app launchable from the Hyprland app menu, plus a polkit rule enabling password-less `openvpn-client@` control.

- [ ] **Step 1: Write the polkit rule**

`packaging/50-openvpn-manager.rules` (installed to `/etc/polkit-1/rules.d/`; `PROJECT_USER` is substituted by `install.sh`):

```javascript
// Allow the installing user to manage only openvpn-client@ units without a password.
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        subject.user == "PROJECT_USER") {
        var unit = action.lookup("unit");
        if (unit && unit.indexOf("openvpn-client@") === 0) {
            return polkit.Result.YES;
        }
    }
});
```

- [ ] **Step 2: Write the launcher script**

`openvpn-manager` (references `PROJECT_DIR`, substituted by `install.sh`):

```bash
#!/usr/bin/env bash
exec python3 -m openvpn_manager "$@"
```

Note: the installed copy runs from within `PROJECT_DIR` via `PYTHONPATH`; see the desktop entry and installer below.

- [ ] **Step 3: Write the desktop entry**

`packaging/openvpn-manager.desktop` (`PROJECT_DIR` substituted by `install.sh`):

```ini
[Desktop Entry]
Type=Application
Name=OpenVPN Manager
Comment=Manage OpenVPN profiles with live usage indicator
Exec=env PYTHONPATH=PROJECT_DIR python3 -m openvpn_manager
Icon=openvpn-manager
Terminal=false
Categories=Network;System;
```

- [ ] **Step 4: Write the icon**

`packaging/openvpn-manager.svg`:

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="24" fill="#241f31"/>
  <path d="M64 20 L100 34 V64 C100 92 82 104 64 110 C46 104 28 92 28 64 V34 Z"
        fill="#3584e4"/>
  <circle cx="64" cy="58" r="12" fill="#ffffff"/>
  <rect x="60" y="58" width="8" height="30" fill="#ffffff"/>
</svg>
```

- [ ] **Step 5: Write the installer**

`install.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(id -un)"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

echo "Installing OpenVPN Manager from $PROJECT_DIR"

mkdir -p "$APP_DIR" "$ICON_DIR"

install -m644 "$PROJECT_DIR/packaging/openvpn-manager.svg" "$ICON_DIR/openvpn-manager.svg"

sed "s|PROJECT_DIR|$PROJECT_DIR|g" "$PROJECT_DIR/packaging/openvpn-manager.desktop" \
    > "$APP_DIR/openvpn-manager.desktop"

echo "Installing polkit rule (needs root)..."
TMP_RULE="$(mktemp)"
sed "s|PROJECT_USER|$USER_NAME|g" "$PROJECT_DIR/packaging/50-openvpn-manager.rules" > "$TMP_RULE"
sudo install -m644 "$TMP_RULE" /etc/polkit-1/rules.d/50-openvpn-manager.rules
rm -f "$TMP_RULE"

update-desktop-database "$APP_DIR" 2>/dev/null || true
gtk4-update-icon-cache -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Done. Launch 'OpenVPN Manager' from your app menu."
```

- [ ] **Step 6: Run the installer and verify**

Run:
```bash
chmod +x install.sh openvpn-manager
./install.sh
```
Expected: prints "Done.", creates `~/.local/share/applications/openvpn-manager.desktop`, installs `/etc/polkit-1/rules.d/50-openvpn-manager.rules`, and the icon.

Verify:
```bash
test -f ~/.local/share/applications/openvpn-manager.desktop && echo "desktop OK"
grep -q "$(id -un)" /etc/polkit-1/rules.d/50-openvpn-manager.rules && echo "polkit OK"
gtk4-launch openvpn-manager 2>/dev/null &
sleep 2 && echo "launch attempted"
```
Expected: `desktop OK`, `polkit OK`, and the app window opens.

- [ ] **Step 7: Commit**

```bash
git add packaging/ openvpn-manager install.sh
git commit -m "feat: add packaging, polkit rule, and installer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** profile management (Task 3 + 5), connect/disconnect (Task 3/5 + polkit Task 6), import (Task 3/5 + pkexec), live throughput + graph (Task 2 + 4 + 5), session data / uptime / connection info (Task 2 + 5), empty/error states (Task 5 toast + `(no profiles)`), packaging + polkit + `.desktop` (Task 6), unit tests for logic modules (Tasks 1-3). All spec sections map to a task.
- **Privilege model:** connect/disconnect silent via polkit rule; import via `pkexec`; reads unprivileged. Matches spec.
- **Type consistency:** `Sampler.sample()` keys (`up_bps`, `down_bps`, `total_rx`, `total_tx`) are consumed identically in Task 5. `vpn` function names used in Task 5 match Task 3 signatures. `Sparkline.push(up_bps, down_bps)` order consistent between Task 4 and Task 5.
- **Cipher sourcing:** the spec lists best-effort cipher from journal. To keep Task 5 robust and unprivileged, the info line ships IP/remote/proto; cipher via journal is deferred as a non-blocking enhancement (documented here so it is not silently dropped). If desired it can be added to `_info()` later without interface changes.
