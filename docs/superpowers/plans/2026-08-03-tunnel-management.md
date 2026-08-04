# Manual Tunnel Management & Refresh Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manage manual tunnel nodes from the topology view (right-click Edit / Delete / Add child), base new tunnels on imported `.ovpn` profiles, and fix a crash that silently kills the app's refresh timer.

**Architecture:** Pure list operations (`delete_manual_tunnel`, `update_manual_tunnel`, `descendant_ids`) go in `topology.py` and are unit-tested. The Cairo diagram detects right-clicks and reports the node via a callback; `app.py` builds a `Gtk.Popover` menu and reuses one shared add/edit dialog. The crash fix is a one-line flag correction plus a defensive guard on `_tick`.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`, `uuid`), GTK4 / libadwaita 1.x, Cairo, pytest via `.venv/bin/python -m pytest`.

## Global Constraints

- `requires-python = ">=3.11"` — no new deps.
- `topology.py` must not import GTK. Pure functions injectable for tests.
- Manual tunnels persist to `~/.config/openvpn-manager/topology.toml` (TOML array). Missing/malformed file → empty, no crash.
- Tests run with `.venv/bin/python -m pytest -q` (currently 74 passing). The venv has no `gi`, so GTK modules are never imported by tests; GTK verification uses `/usr/bin/python3`.
- GTK widgets verified manually (project convention); only `topology.py` is unit-tested.
- Style B diagram unchanged: flat rounded cards, gradients, dashed tunnel edges.
- Deleting a manual tunnel re-parents its children to the deleted node's parent. Confirmation dialog required.
- Only manual tunnels (a `manual=True` `Device` of `kind="tunnel"`) are editable/deletable. Auto-detected tunnels, the PC, and LAN devices are read-only but every node offers "Add child tunnel".

---

### Task 1: Fix the refresh-crash and guard the tick timer

**Files:**
- Modify: `openvpn_manager/app.py:484` (broken flags)
- Modify: `openvpn_manager/app.py:393-411` (`_tick`)

**Interfaces:**
- Consumes: nothing.
- Produces: `_tick` survives any exception (returns `True` always); `_spawn_quiet` uses valid flags so the ARP sweep no longer raises.

**Root cause:** `Gio.SubprocessFlags.STDOUT_DEVNULL` / `STDERR_DEVNULL` do not exist in GLib 2.88. The first ARP sweep raises `AttributeError` inside `_tick`; an unhandled exception in a GLib timeout callback permanently removes that source, killing the app's 1-second refresh loop.

- [ ] **Step 1: Verify the broken line**

Run: `.venv/bin/python -m pytest -q`
Expected: `74 passed` (the bug only fires in the running GTK app, not under pytest).

- [ ] **Step 2: Fix the flag names**

In `openvpn_manager/app.py`, `_spawn_quiet` (line ~484), change:

```python
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_DEVNULL |
            Gio.SubprocessFlags.STDERR_DEVNULL)
```

to:

```python
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_SILENCE |
            Gio.SubprocessFlags.STDERR_SILENCE)
```

- [ ] **Step 3: Verify the corrected expression**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gio','2.0'); from gi.repository import Gio; print(bool(Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE))"`
Expected: prints `True` (no `AttributeError`).

- [ ] **Step 4: Guard `_tick`**

In `openvpn_manager/app.py`, replace the body of `_tick` (currently lines 393-411) so the refresh work is wrapped and the timer always survives:

```python
    def _tick(self):
        try:
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
```

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `74 passed`.

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "fix: use valid SubprocessFlags and guard refresh timer"
```

---

### Task 2: Pure manual-tunnel edit/delete helpers

**Files:**
- Modify: `openvpn_manager/topology.py` — append three functions
- Modify: `tests/test_topology.py` — append tests

**Interfaces:**
- Consumes: Task 1 `Device` dataclass (already present).
- Produces:
  - `delete_manual_tunnel(tunnels, tunnel_id) -> list[Device]`
  - `update_manual_tunnel(tunnels, tunnel_id, *, label, parent_id, remote, protocol) -> list[Device]`
  - `descendant_ids(tunnels, tunnel_id) -> set[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topology.py`:

```python
def test_delete_manual_tunnel_reparents_children():
    tunnels = [
        Device(id="manual:a", kind="tunnel", label="a", parent_id="pc", manual=True),
        Device(id="manual:b", kind="tunnel", label="b", parent_id="manual:a", manual=True),
        Device(id="manual:c", kind="tunnel", label="c", parent_id="manual:a", manual=True),
    ]
    out = topology.delete_manual_tunnel(tunnels, "manual:a")
    assert [t.id for t in out] == ["manual:b", "manual:c"]
    assert all(t.parent_id == "pc" for t in out)


def test_delete_manual_tunnel_reparents_to_removed_parent():
    tunnels = [
        Device(id="manual:a", kind="tunnel", label="a", parent_id="tun:home", manual=True),
        Device(id="manual:b", kind="tunnel", label="b", parent_id="manual:a", manual=True),
    ]
    out = topology.delete_manual_tunnel(tunnels, "manual:a")
    assert len(out) == 1
    assert out[0].id == "manual:b"
    assert out[0].parent_id == "tun:home"


def test_delete_manual_tunnel_missing_id():
    tunnels = [Device(id="manual:a", kind="tunnel", label="a", manual=True)]
    out = topology.delete_manual_tunnel(tunnels, "manual:nope")
    assert out == tunnels


def test_update_manual_tunnel():
    tunnels = [Device(id="manual:a", kind="tunnel", label="a", parent_id="pc",
                      manual=True, protocol="SSH", detail="old.example.com")]
    out = topology.update_manual_tunnel(
        tunnels, "manual:a", label="b", parent_id="tun:home",
        remote="new.example.com", protocol="WireGuard")
    assert len(out) == 1
    assert out[0].id == "manual:a"
    assert out[0].label == "b"
    assert out[0].parent_id == "tun:home"
    assert out[0].detail == "new.example.com"
    assert out[0].protocol == "WireGuard"
    assert out[0].manual is True


def test_update_manual_tunnel_missing_id():
    tunnels = [Device(id="manual:a", kind="tunnel", label="a", manual=True)]
    out = topology.update_manual_tunnel(
        tunnels, "manual:nope", label="x", parent_id="pc",
        remote="r.example.com", protocol="SSH")
    assert out == tunnels


def test_descendant_ids_multilevel():
    tunnels = [
        Device(id="pc", kind="pc", label="host"),
        Device(id="manual:a", kind="tunnel", label="a", parent_id="pc", manual=True),
        Device(id="manual:b", kind="tunnel", label="b", parent_id="manual:a", manual=True),
        Device(id="manual:c", kind="tunnel", label="c", parent_id="manual:b", manual=True),
        Device(id="manual:d", kind="tunnel", label="d", parent_id="pc", manual=True),
    ]
    assert topology.descendant_ids(tunnels, "manual:a") == {"manual:b", "manual:c"}
    assert topology.descendant_ids(tunnels, "manual:d") == set()


def test_descendant_ids_leaf():
    tunnels = [Device(id="manual:a", kind="tunnel", label="a", parent_id="pc", manual=True)]
    assert topology.descendant_ids(tunnels, "manual:a") == set()


def test_descendant_ids_cycle_terminates():
    tunnels = [
        Device(id="manual:a", kind="tunnel", label="a", parent_id="manual:b", manual=True),
        Device(id="manual:b", kind="tunnel", label="b", parent_id="manual:a", manual=True),
    ]
    assert topology.descendant_ids(tunnels, "manual:a") == {"manual:b"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "manual or descendant"`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'delete_manual_tunnel'`.

- [ ] **Step 3: Write the implementation**

Append to the end of `openvpn_manager/topology.py`:

```python
def delete_manual_tunnel(tunnels, tunnel_id):
    """Remove a manual tunnel, re-parenting its children to its parent.

    Children (tunnels whose parent_id is the removed tunnel) are re-parented
    to the removed tunnel's parent, or "pc" when it had none. Returns a new
    list; the input list is not modified structurally. Missing id returns the
    input list unchanged.
    """
    removed = None
    out = []
    for t in tunnels:
        if t.id == tunnel_id:
            removed = t
            continue
        out.append(t)
    if removed is None:
        return tunnels
    parent = removed.parent_id or "pc"
    for t in out:
        if t.parent_id == tunnel_id:
            t.parent_id = parent
    return out


def update_manual_tunnel(tunnels, tunnel_id, *, label, parent_id, remote,
                         protocol):
    """Replace the matching manual tunnel's editable fields in place.

    The tunnel's id and manual flag are preserved. Missing id returns the
    input list unchanged.
    """
    for t in tunnels:
        if t.id == tunnel_id:
            t.label = label
            t.parent_id = parent_id
            t.protocol = protocol
            t.detail = remote
    return tunnels


def descendant_ids(tunnels, tunnel_id):
    """Return every id reachable from tunnel_id by following parent_id links.

    Used to build a parent picker that excludes a node and its whole subtree
    (prevents cycles). A cycle terminates because visited ids are tracked.
    """
    children = {}
    for t in tunnels:
        children.setdefault(t.parent_id or "pc", []).append(t.id)
    result = set()
    stack = list(children.get(tunnel_id, []))
    while stack:
        cid = stack.pop()
        if cid in result or cid == tunnel_id:
            continue
        result.add(cid)
        stack.extend(children.get(cid, []))
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "manual or descendant"`
Expected: `12 passed` (8 new tests + 4 existing manual-persistence tests that match the filter).

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `82 passed`.

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add manual tunnel edit/delete helpers"
```

---

### Task 3: Diagram right-click detection

**Files:**
- Modify: `openvpn_manager/topology_diagram.py` — `__init__`, `_on_click`

**Interfaces:**
- Consumes: nothing new.
- Produces: `TopologyDiagram(on_context_menu=None)` — calls
  `on_context_menu(device, x, y)` when the right button is released over a node,
  where `device` is the `Device` object and `(x, y)` are widget-relative.

- [ ] **Step 1: Write the code**

In `openvpn_manager/topology_diagram.py`:

Change `__init__` signature and store the callback:

```python
    def __init__(self, on_context_menu=None):
        super().__init__()
        self._on_context_menu = on_context_menu
        self._topo = None
        self._boxes = []
        self._hover_id = None
        self._total_w = 200
        self._total_h = 200
        self.set_draw_func(self._draw)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_click)
        self.add_controller(click)
```

Replace `_on_click` with a version that branches on the button:

```python
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
```

- [ ] **Step 2: Verify the module imports**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); from openvpn_manager.topology_diagram import TopologyDiagram; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `82 passed` (diagram is not unit-tested by convention).

- [ ] **Step 4: Commit**

```bash
git add openvpn_manager/topology_diagram.py
git commit -m "feat: detect right-click on topology nodes"
```

---

### Task 4: Forward context menu through the view

**Files:**
- Modify: `openvpn_manager/topology_view.py` — `__init__`, diagram construction

**Interfaces:**
- Consumes: Task 3 `TopologyDiagram(on_context_menu=...)`.
- Produces: `TopologyView(on_scan=None, on_add_tunnel=None, on_context_menu=None)` — passes `on_context_menu` straight to the diagram.

- [ ] **Step 1: Write the code**

In `openvpn_manager/topology_view.py`, change the constructor and diagram wiring:

```python
    def __init__(self, on_scan=None, on_add_tunnel=None, on_context_menu=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._on_scan = on_scan
        self._on_add_tunnel = on_add_tunnel
        self._on_context_menu = on_context_menu
```

and:

```python
        self._diagram = TopologyDiagram(on_context_menu=on_context_menu)
```

- [ ] **Step 2: Verify the module imports**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from openvpn_manager.topology_view import TopologyView; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `82 passed`.

- [ ] **Step 4: Commit**

```bash
git add openvpn_manager/topology_view.py
git commit -m "feat: forward context menu callback through topology view"
```

---

### Task 5: Context popover, edit/delete dialogs, and Add-dialog source picker

**Files:**
- Modify: `openvpn_manager/app.py` — imports, `TopologyView` construction, `_on_add_tunnel`, new `_on_node_context` / `_on_edit_tunnel` / `_on_delete_tunnel`

**Interfaces:**
- Consumes: Task 2 `delete_manual_tunnel`, `update_manual_tunnel`, `descendant_ids`; Task 4 `TopologyView(on_context_menu=...)`; existing `vpn.parse_remote`, `vpn.CLIENT_DIR`, `load_manual_tunnels`, `save_manual_tunnels`, `Device`.
- Produces: working context menu, edit/delete flows, and Add-dialog source picker.

- [ ] **Step 1: Update imports**

In `openvpn_manager/app.py`, replace the `from .topology import (...)` block (lines 23-24) with:

```python
from .topology import (Device, build_tree_topology, delete_manual_tunnel,
                       descendant_ids, load_manual_tunnels,
                       save_manual_tunnels, update_manual_tunnel)
```

- [ ] **Step 2: Pass `on_context_menu` to the view**

In `Window.__init__` (currently line ~153), change:

```python
        self._topo_view = TopologyView(on_scan=self._on_scan_requested,
                                       on_add_tunnel=self._on_add_tunnel)
```

to:

```python
        self._topo_view = TopologyView(on_scan=self._on_scan_requested,
                                       on_add_tunnel=self._on_add_tunnel,
                                       on_context_menu=self._on_node_context)
```

- [ ] **Step 3: Refactor `_on_add_tunnel` into a shared dialog builder**

Replace the entire current `_on_add_tunnel` method (lines 534-599) with:

```python
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

        # Source picker (add mode only): Free-form or an imported .ovpn profile.
        source_combo = None
        if tunnel is None:
            source_combo = Adw.ComboRow(title="Source")
            source_combo.set_model(Gtk.StringList.new(
                ["Free-form", *self._profiles]))
            source_combo.set_selected(0)
            body.append(source_combo)

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
        body.append(parent_combo)

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
        body.append(proto_combo)

        def on_source_changed(*_args):
            idx = source_combo.get_selected()
            if idx <= 0 or idx > len(self._profiles):
                return
            profile = self._profiles[idx - 1]
            remote, _proto = vpn.parse_remote(
                f"{vpn.CLIENT_DIR}/{profile}.conf")
            label_entry.set_text(profile)
            if remote:
                remote_entry.set_text(remote)
            proto_combo.set_selected(proto_names.index("OpenVPN"))

        if source_combo is not None:
            source_combo.connect("notify::selected", on_source_changed)
            if source_profile is not None and source_profile in self._profiles:
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
            existing = load_manual_tunnels()
            if tunnel is not None:
                existing = update_manual_tunnel(
                    existing, tunnel.id, label=label, parent_id=parent_id_sel,
                    remote=remote, protocol=proto)
                save_manual_tunnels(existing)
                self._refresh_topology()
                self._toast.add_toast(
                    Adw.Toast.new(f"Tunnel '{label}' updated"))
            else:
                import uuid
                new_tun = Device(
                    id=f"manual:{uuid.uuid4().hex[:8]}",
                    kind="tunnel", label=label, parent_id=parent_id_sel,
                    manual=True, protocol=proto, detail=remote)
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

        if device.manual:
            edit_btn = Gtk.ModelButton(label="Edit…")
            edit_btn.connect("clicked", lambda *_: (
                popover.popdown(), self._on_edit_tunnel(device)))
            box.append(edit_btn)
            delete_btn = Gtk.ModelButton(label="Delete…")
            delete_btn.connect("clicked", lambda *_: (
                popover.popdown(), self._on_delete_tunnel(device)))
            box.append(delete_btn)

        add_child_btn = Gtk.ModelButton(label="Add child tunnel…")
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
```

- [ ] **Step 4: Verify the module imports**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_version('Gdk','4.0'); from openvpn_manager.app import Window; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `82 passed`.

- [ ] **Step 6: Manual verification**

Run: `.venv/bin/python -m openvpn_manager`

Note: `_tick` will report errors to stderr for any transient failure but keep running (the guard). Verify:

- Right-click the PC node → menu with "Add child tunnel…" only (PC is not manual).
- Add a manual tunnel under PC (label + remote), confirm; node appears; `~/.config/openvpn-manager/topology.toml` has the entry.
- Right-click the new manual tunnel → Edit, Delete, Add child tunnel all present. Edit → change the label → node updates, TOML reflects it.
- Add a second manual tunnel chained under the first (via Add child tunnel). Delete the parent → confirmation dialog notes the child; confirm → parent gone, child now connects to PC (or the parent's parent).
- Add Tunnel → pick an imported `.ovpn` profile in the Source picker → label/remote/protocol pre-fill; node appears.
- Connect a VPN from the Status tab → pill turns Connected, tunnel appears in the Topology tree; switch between Status and Topology tabs repeatedly — both keep updating (the freeze is gone).
- Restart the app → manual tunnels persist.

- [ ] **Step 7: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "feat: add tunnel context menu, edit/delete dialogs, and profile source picker"
```
