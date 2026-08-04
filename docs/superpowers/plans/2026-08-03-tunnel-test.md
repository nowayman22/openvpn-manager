# Tunnel Context Menu Fix & Connectivity Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the right-click context menu (crashes on every click because `Gtk.ModelButton` was removed in GTK 4.22) and add an on-demand connectivity test for tunnel nodes: right-click a tunnel → "Test connectivity…" → TCP connect on the protocol port, else ping, run off the UI thread, reported via toast and a persistent status dot on the tunnel card.

**Architecture:** The test logic is a pure, injectable function `test_tunnel(remote, protocol, ...)` in `topology.py`, unit-tested with fake sockets/runners. The GTK side: `app.py` builds the popover with `Gtk.Button` instead of the removed `Gtk.ModelButton`, runs the test in a `threading.Thread`, and marshals the result back with `GLib.idle_add`. `topology_diagram.py` stores per-device test status and paints a colored dot on tunnel cards.

**Tech Stack:** Python 3.11+ (stdlib `socket`, `subprocess`), GTK4 / libadwaita 1.x, Cairo, pytest via `.venv/bin/python -m pytest`.

## Global Constraints

- `requires-python = ">=3.11"` — no new deps.
- `topology.py` must not import GTK. Pure functions injectable for tests.
- Tests run with `.venv/bin/python -m pytest -q`. Baseline: `82 passed, 1 failed` — the failure is `tests/test_vpn.py::test_helper_path_points_at_shipped_script`, pre-existing and environmental (the root-owned helper is installed at `/usr/lib/openvpn-manager/helper.sh`, so `helper_path()` prefers it and the fallback-path assertion fails). Do not treat it as a regression; the suite must stay at `82 + new` passing with that one known failure.
- The venv has no `gi`, so GTK modules are never imported by tests; GTK verification uses `/usr/bin/python3`.
- GTK widgets verified manually (project convention) plus the small `/tmp` scripts given in the tasks; only `topology.py` is unit-tested.
- The test checks the tunnel's `detail` (remote endpoint). It succeeds if either a TCP connect (known protocol port) or a ping succeeds.
- Status dot colors: amber = testing, green = ok, red = fail, faint gray outline = never tested. The existing auto-tunnel green dot (top-right) is untouched.

---

### Task 1: Pure `test_tunnel` function

**Files:**
- Modify: `openvpn_manager/topology.py` — add `import socket`, `import subprocess` at top; append `_PROTO_PORTS`, `_ping_replies`, `_ping_reason`, `test_tunnel` at end
- Modify: `tests/test_topology.py` — append tests

**Interfaces:**
- Consumes: nothing new (module already imports `vpn`).
- Produces:
  - `test_tunnel(remote, protocol, *, timeout=3, connect=socket.create_connection, ping_runner=subprocess.run) -> tuple[str, str]`
  - Returns `("ok", <what worked>)` or `("fail", <reason>)`.
  - `_PROTO_PORTS = {"SSH": 22, "OpenVPN": 1194, "WireGuard": 51820, "Other": 443}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topology.py`:

```python
class _FakeSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _tcp_raises(msg):
    def raiser(*_args, **_kwargs):
        raise OSError(msg)
    return raiser


def _ping_runner(retcode, stdout="", stderr=""):
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], retcode,
                                           stdout=stdout, stderr=stderr)
    return runner


def test_test_tunnel_tcp_connect_success():
    sock = _FakeSocket()
    status, msg = topology.test_tunnel(
        "10.8.0.9", "SSH", connect=lambda *a, **k: sock)
    assert status == "ok"
    assert "TCP 10.8.0.9:22" in msg
    assert sock.closed


def test_test_tunnel_tcp_fails_ping_succeeds():
    status, msg = topology.test_tunnel(
        "10.8.0.9", "SSH", connect=_tcp_raises("timed out"),
        ping_runner=_ping_runner(0, stdout=(
            "64 bytes from 10.8.0.9: icmp_seq=1 ttl=64 time=0.3 ms\n"
            "64 bytes from 10.8.0.9: icmp_seq=2 ttl=64 time=0.3 ms\n")))
    assert status == "ok"
    assert "2/2" in msg


def test_test_tunnel_both_fail():
    status, msg = topology.test_tunnel(
        "10.8.0.9", "SSH", connect=_tcp_raises("timed out"),
        ping_runner=_ping_runner(1, stderr="ping: destination unreachable"))
    assert status == "fail"
    assert "timed out" in msg


def test_test_tunnel_no_port_uses_ping():
    status, msg = topology.test_tunnel(
        "10.8.0.9", None,
        ping_runner=_ping_runner(0, stdout=(
            "64 bytes from 10.8.0.9: icmp_seq=1 ttl=64 time=0.3 ms\n")))
    assert status == "ok"
    assert "ping" in msg


def test_test_tunnel_unknown_protocol_falls_back_to_ping():
    status, msg = topology.test_tunnel(
        "example.com", "Quic",
        ping_runner=_ping_runner(0, stdout=(
            "64 bytes from example.com: icmp_seq=1 ttl=50 time=1.0 ms\n")))
    assert status == "ok"
    assert "1/2" in msg


def test_test_tunnel_empty_remote():
    status, msg = topology.test_tunnel("", "SSH")
    assert status == "fail"
    assert "no remote" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "test_tunnel"`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'test_tunnel'`.

- [ ] **Step 3: Write the implementation**

At the top of `openvpn_manager/topology.py`, change the imports from:

```python
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
```

to:

```python
import socket
import subprocess
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
```

Append to the end of `openvpn_manager/topology.py`:

```python
_PROTO_PORTS = {"SSH": 22, "OpenVPN": 1194, "WireGuard": 51820, "Other": 443}


def _ping_replies(stdout):
    """Count ICMP reply lines in `ping -c` output."""
    return sum(1 for line in stdout.splitlines() if "bytes from" in line)


def _ping_reason(cp):
    """Last non-empty error line from a failed ping, if any."""
    for stream in (cp.stderr, cp.stdout):
        if stream:
            lines = [ln.strip() for ln in stream.splitlines() if ln.strip()]
            if lines:
                return lines[-1]
    return None


def test_tunnel(remote, protocol, *, timeout=3,
                connect=socket.create_connection,
                ping_runner=subprocess.run):
    """Probe a tunnel's remote endpoint and report whether it responds.

    Tries a TCP connect first when the protocol has a known port, then falls
    back to a short ICMP ping (exit 0 means at least one reply arrived).
    Returns (status, message) with status "ok" or "fail"; the message names
    what succeeded or the last error. connect and ping_runner are injectable
    so tests never touch the network.
    """
    if not remote:
        return "fail", "no remote endpoint"

    port = _PROTO_PORTS.get(protocol)
    if port:
        try:
            sock = connect((remote, port), timeout)
            sock.close()
            return "ok", f"TCP {remote}:{port}"
        except OSError as exc:
            tcp_err = str(exc) or type(exc).__name__
    else:
        tcp_err = None

    try:
        cp = ping_runner(["ping", "-c", "2", "-W", "2", remote],
                         capture_output=True, text=True)
    except OSError as exc:
        return "fail", f"ping failed: {exc}"
    if cp.returncode == 0:
        return "ok", f"ping {remote} ({_ping_replies(cp.stdout)}/2 replies)"
    reason = tcp_err or _ping_reason(cp) or f"ping {remote}: no reply"
    return "fail", reason
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "test_tunnel"`
Expected: `6 passed`.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `88 passed, 1 failed` (the known pre-existing `test_helper_path_points_at_shipped_script` failure only).

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add pure tunnel connectivity test function"
```

---

### Task 2: Fix the right-click context menu crash

**Files:**
- Modify: `openvpn_manager/app.py:684-696` (`_on_node_context`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_on_node_context(device, x, y)` builds a `Gtk.Popover` whose items are `Gtk.Button` widgets (no `Gtk.ModelButton`), so right-clicking a node no longer raises.

**Root cause:** GTK 4.22 removed `GtkModelButton`. `Gtk.ModelButton(label=...)` at `app.py:684/688/693` raises `AttributeError: 'gi.repository.Gtk' object has no attribute 'ModelButton'` inside the click callback, so the popover never appears.

- [ ] **Step 1: Write the code**

In `openvpn_manager/app.py`, replace the three `Gtk.ModelButton` creations in `_on_node_context` (currently lines 684-696) with `Gtk.Button` + `flat` class + start-aligned text:

```python
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
```

- [ ] **Step 2: Verify the module imports**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_version('Gdk','4.0'); from openvpn_manager.app import Window; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Regression check with a scripted right-click**

Save to `/tmp/verify_popover.py`:

```python
"""Regression: right-clicking a node must show a menu, not raise."""
import sys
sys.path.insert(0, "/home/nikits/Projects/PC")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib

from openvpn_manager.topology import Device, build_tree_topology
from openvpn_manager.app import Window, OpenVpnManagerApp


class StubGesture:
    def __init__(self, button):
        self._button = button

    def get_current_button(self):
        return self._button


def main():
    app = OpenVpnManagerApp()
    win = Window(app)
    win.present()
    topo = build_tree_topology(
        "host", ["10.0.0.5"], None, [],
        [Device(id="manual:abc", kind="tunnel", label="nested", parent_id="pc",
                manual=True, protocol="SSH", detail="10.8.0.9")])
    win._topo_view.set_topology(topo)
    diag = win._topo_view._diagram
    loop = GLib.MainLoop()

    def step():
        box = next(b for b in diag._boxes if b.device_id == "manual:abc")
        win._context_popover = None
        diag._on_click(StubGesture(Gdk.BUTTON_SECONDARY), 1,
                       box.x + 10, box.y + 10)
        GLib.timeout_add(300, check)
        return False

    def check():
        pop = win._context_popover
        assert pop is not None, "popover was not created"
        assert pop.get_visible(), "popover did not become visible"
        labels = [c.get_label() for c in pop.get_child()]
        print("menu items:", labels)
        assert "Edit…" in labels and "Add child tunnel…" in labels
        loop.quit()
        return False

    GLib.timeout_add(400, step)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Run: `/usr/bin/python3 /tmp/verify_popover.py`
Expected: prints `menu items: ['Edit…', 'Delete…', 'Add child tunnel…']` and exits 0 (no `AttributeError`).

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `88 passed, 1 failed` (the known pre-existing failure only).

- [ ] **Step 5: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "fix: replace removed Gtk.ModelButton with Gtk.Button in context menu"
```

---

### Task 3: Test-status dot on tunnel cards

**Files:**
- Modify: `openvpn_manager/topology_diagram.py` — `__init__`, `set_topology`, `_draw_node`; add `set_tunnel_status`

**Interfaces:**
- Consumes: Task 2 diagram (unchanged construction).
- Produces:
  - `set_tunnel_status(device_id: str, status: str)` — `status` is `"testing"` | `"ok"` | `"fail"`; stores and redraws.
  - `TopologyDiagram._tunnel_status: dict[str, str]` — device_id → status; pruned in `set_topology` to ids still present.

- [ ] **Step 1: Write the code**

In `openvpn_manager/topology_diagram.py`:

Add `self._tunnel_status = {}` to `__init__` (next to `self._boxes = []`):

```python
        self._topo = None
        self._boxes = []
        self._tunnel_status = {}
        self._hover_id = None
```

In `set_topology`, prune statuses for devices that no longer exist, right after `self._topo = topo`:

```python
    def set_topology(self, topo):
        self._topo = topo
        live = {d.id for d in topo.devices}
        self._tunnel_status = {k: v for k, v in self._tunnel_status.items()
                               if k in live}
        self._boxes = compute_layout(topo, col_width=NODE_W,
                                     node_pad=NODE_PAD, level_pad=LEVEL_PAD)
```

Add a public method (place it after `set_topology`):

```python
    def set_tunnel_status(self, device_id, status):
        """Record a tunnel connectivity test result and redraw."""
        self._tunnel_status[device_id] = status
        self.queue_draw()
```

At the end of `_draw_node`, after the existing auto-tunnel green dot block (the `if dev.kind == "tunnel" and not dev.manual:` block), add the test-status dot (bottom-left corner, mirrored away from the auto dot's top-right):

```python
        # connectivity test status dot (bottom-left)
        if dev.kind == "tunnel":
            status = self._tunnel_status.get(dev.id)
            sx, sy = x + 14, y + h - 14
            if status == "testing":
                cr.set_source_rgb(0.95, 0.65, 0.10)   # amber
                cr.arc(sx, sy, 5, 0, 2 * 3.14159)
                cr.fill()
            elif status == "ok":
                cr.set_source_rgb(0.45, 0.75, 0.35)   # green
                cr.arc(sx, sy, 5, 0, 2 * 3.14159)
                cr.fill()
            elif status == "fail":
                cr.set_source_rgb(0.85, 0.35, 0.32)   # red
                cr.arc(sx, sy, 5, 0, 2 * 3.14159)
                cr.fill()
            else:
                # never tested: faint outline
                cr.set_source_rgba(0.45, 0.48, 0.55, 0.5)
                cr.arc(sx, sy, 4, 0, 2 * 3.14159)
                cr.set_line_width(1.0)
                cr.stroke()
```

- [ ] **Step 2: Verify the module imports**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); from openvpn_manager.topology_diagram import TopologyDiagram; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `88 passed, 1 failed` (the known pre-existing failure only; diagram is not unit-tested by convention).

- [ ] **Step 4: Commit**

```bash
git add openvpn_manager/topology_diagram.py
git commit -m "feat: paint connectivity test status dot on tunnel cards"
```

---

### Task 4: Wire the Test menu item and async runner

**Files:**
- Modify: `openvpn_manager/app.py` — imports, `_on_node_context`, new `_on_test_tunnel` / `_finish_tunnel_test`

**Interfaces:**
- Consumes: Task 1 `topology.test_tunnel(remote, protocol, *, timeout=3, connect=..., ping_runner=...) -> (status, message)`; Task 3 `diagram.set_tunnel_status(device_id, status)`; existing `self._topo_view._diagram`, `self._toast`.
- Produces: right-clicking a tunnel node shows "Test connectivity…" (disabled when `device.detail` is empty); `_on_test_tunnel(device)` runs the test off the UI thread and `_finish_tunnel_test(device, status, message)` updates the dot and shows a toast.

- [ ] **Step 1: Add the import**

In `openvpn_manager/app.py`, change the stdlib imports (line 3-7) from:

```python
import getpass
import os
import socket
import subprocess
import time
```

to:

```python
import getpass
import os
import socket
import subprocess
import threading
import time
```

- [ ] **Step 2: Add the Test item to the context menu**

In `_on_node_context`, insert a Test button for tunnel devices *before* the `if device.manual:` block (so it sits above Edit/Delete):

```python
        if device.kind == "tunnel":
            test_btn = Gtk.Button(label="Test connectivity…")
            test_btn.add_css_class("flat")
            test_btn.set_halign(Gtk.Align.START)
            test_btn.set_sensitive(bool(device.detail))
            test_btn.connect("clicked", lambda *_: (
                popover.popdown(), self._on_test_tunnel(device)))
            box.append(test_btn)

        if device.manual:
```

- [ ] **Step 3: Add the async test handlers**

Append two methods to `Window` (e.g. right after `_on_delete_tunnel`):

```python
    def _on_test_tunnel(self, device):
        self._topo_view._diagram.set_tunnel_status(device.id, "testing")
        self._toast.add_toast(
            Adw.Toast.new(f"Testing tunnel '{device.label}'…"))

        def work():
            status, message = topology.test_tunnel(
                device.detail, device.protocol)
            GLib.idle_add(self._finish_tunnel_test, device, status, message)

        threading.Thread(target=work, daemon=True).start()

    def _finish_tunnel_test(self, device, status, message):
        self._topo_view._diagram.set_tunnel_status(device.id, status)
        if status == "ok":
            self._toast.add_toast(Adw.Toast.new(
                f"Tunnel '{device.label}' reachable — {message}"))
        else:
            self._toast.add_toast(Adw.Toast.new(
                f"Tunnel '{device.label}' no response — {message}"))
```

- [ ] **Step 4: Verify the module imports**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_version('Gdk','4.0'); from openvpn_manager.app import Window; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Verify the popover now includes Test**

Re-run the Task 2 script:

Run: `/usr/bin/python3 /tmp/verify_popover.py`
Expected: prints `menu items: ['Test connectivity…', 'Edit…', 'Delete…', 'Add child tunnel…']` and exits 0.

- [ ] **Step 6: Verify the async test flow with a stubbed runner**

Save to `/tmp/verify_test_flow.py`:

```python
"""Stub test_tunnel to return ok; check the dot turns green and a toast fires."""
import sys
sys.path.insert(0, "/home/nikits/Projects/PC")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib

import openvpn_manager.topology as topology
from openvpn_manager.topology import Device, build_tree_topology
from openvpn_manager.app import Window, OpenVpnManagerApp


def main():
    app = OpenVpnManagerApp()
    win = Window(app)
    win.present()
    topo = build_tree_topology(
        "host", ["10.0.0.5"], None, [],
        [Device(id="manual:abc", kind="tunnel", label="nested", parent_id="pc",
                manual=True, protocol="SSH", detail="10.8.0.9")])
    win._topo_view.set_topology(topo)
    dev = next(d for d in topo.devices if d.id == "manual:abc")
    diag = win._topo_view._diagram

    # Stub the network check.
    topology.test_tunnel = lambda *a, **k: ("ok", "TCP 10.8.0.9:22")

    win._on_test_tunnel(dev)
    loop = GLib.MainLoop()
    GLib.timeout_add(400, lambda: (
        print("status:", diag._tunnel_status.get("manual:abc")),
        loop.quit(), False))
    loop.run()
    assert diag._tunnel_status.get("manual:abc") == "ok"
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Run: `/usr/bin/python3 /tmp/verify_test_flow.py`
Expected: prints `status: ok` and exits 0.

- [ ] **Step 7: Manual verification**

Run: `python3 -m openvpn_manager`

- Right-click a node → the menu appears (regression). Manual tunnel: Test, Edit, Delete, Add child. PC/router/LAN device: Add child only.
- Right-click a tunnel with a remote → Test connectivity… → amber dot + `Testing tunnel 'x'…` toast; within a few seconds the dot turns green/red and the result toast names TCP or ping (e.g. `Tunnel 'x' reachable — TCP 10.8.0.9:22` or `…no response — timed out`).
- Right-click a tunnel with no remote (edge case, e.g. an auto tunnel whose `.conf` has no `remote` line) → the item is grayed out.
- For a chained tunnel whose remote is only reachable through a parent, verify the test reflects the parent being up vs down.
- Switch tabs / let the 1s refresh run → surviving tunnels keep their dots; a deleted or gone tunnel's dot disappears.
- Restart the app → manual tunnels persist; dots reset to never-tested (correct, tests are on-demand).

- [ ] **Step 8: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "feat: add tunnel connectivity test to context menu"
```
