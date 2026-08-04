# Tree Topology Diagram & Tunnel Chaining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat PreferencesGroup list with a Cairo-drawn horizontal tree diagram (Style B: flat cards + gradients) and add manual-tunnel chaining with persistence.

**Architecture:** `topology.py` gets `Device.id`/`parent_id`/`manual`/`protocol`, `Edge`, `compute_layout`, `load/save_manual_tunnels`, and a rewritten `build_topology`. A new `topology_diagram.py` (DrawingArea, same pattern as Sparkline) paints the tree. `topology_view.py` is rebuilt around the diagram with an "Add Tunnel" button. `app.py` gains an add-tunnel dialog.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`, `uuid`), GTK4 / libadwaita 1.x, Cairo, pytest via `.venv/bin/python -m pytest`.

## Global Constraints

- `requires-python = ">=3.11"` — no new deps.
- `topology.py` must not import GTK. Pure functions injectable for tests.
- Manual tunnels persist to `~/.config/openvpn-manager/topology.toml` (TOML array). Missing/malformed file → empty, no crash.
- GTK widgets verified manually (project convention); only `topology.py` is unit-tested.
- Tests run with `.venv/bin/python -m pytest -q` (currently 63 passing).
- Style B: flat rounded cards, gradient fills, drop shadows, dashed tunnel edges, solid physical edges.
- Only connected OpenVPN profiles appear as auto-detected tunnel nodes (existing behavior unchanged).

---

### Task 1: Extend Device, Topology, and add Edge dataclass

**Files:**
- Modify: `openvpn_manager/topology.py:12-43`
- Modify: `tests/test_topology.py` — update `test_device_defaults` and `test_build_topology_shape`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Device(id, kind, label, ..., parent_id=None, manual=False, protocol=None)`.
  - `Edge(source_id, target_id, style)`.
  - `Topology(hostname, local_ips, root=None, devices=[], edges=[], lan=None, tunnels=[])` — `lan` and `tunnels` kept for backward compat with existing callers, but nullable/deprecated.
  - `build_topology` updated to set `root`, `devices`, `edges` from the new model.

- [ ] **Step 1: Update tests**

Replace the existing `test_device_defaults` and `test_build_topology_shape` in `tests/test_topology.py`:

```python
def test_device_defaults():
    dev = Device(id="tunnel:work", kind="tunnel", label="work")
    assert dev.ip is None
    assert dev.mac is None
    assert dev.vendor is None
    assert dev.detail is None
    assert dev.parent_id is None
    assert dev.manual is False
    assert dev.protocol is None


def test_build_topology_shape():
    root = Device(id="pc", kind="pc", label="myhost")
    lan_dev = Device(id="lan:192.168.1.1", kind="router", label="Router",
                     ip="192.168.1.1", parent_id="pc")
    topo = build_topology("myhost", ["192.168.1.101"], root=root,
                          devices=[root, lan_dev],
                          edges=[Edge(source_id="pc", target_id="lan:192.168.1.1", style="solid")])
    assert topo.hostname == "myhost"
    assert topo.local_ips == ["192.168.1.101"]
    assert topo.root is root
    assert len(topo.devices) == 2
    assert len(topo.edges) == 1
```

Also append this test:

```python
def test_device_id_default():
    dev = Device(kind="tunnel", label="work")
    # id defaults to kind:label when not given
    assert dev.id == "tunnel:work"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py::test_device_defaults tests/test_topology.py::test_build_topology_shape tests/test_topology.py::test_device_id_default -q`
Expected: FAIL — `Device.__init__() got an unexpected keyword argument 'id'`

- [ ] **Step 3: Write the implementation**

In `openvpn_manager/topology.py`, replace the `Device`, `Segment`, `Topology`, `Edge`, and `build_topology` definitions:

```python
from dataclasses import dataclass, field

from . import vpn


@dataclass
class Device:
    """One node in the topology: a router, a LAN device, or a tunnel."""
    kind: str                # "pc" | "router" | "device" | "tunnel"
    label: str
    ip: str | None = None
    mac: str | None = None
    vendor: str | None = None
    detail: str | None = None
    id: str | None = None           # unique; defaults to f"{kind}:{label}"
    parent_id: str | None = None
    manual: bool = False
    protocol: str | None = None     # "SSH" | "WireGuard" | "OpenVPN" | "Other"

    def __post_init__(self):
        if self.id is None:
            self.id = f"{self.kind}:{self.label}"


@dataclass
class Edge:
    """A connection between two devices in the topology tree."""
    source_id: str
    target_id: str
    style: str    # "solid" | "dashed"


@dataclass
class Segment:
    """A directly-connected subnet: its gateway and the devices on it."""
    subnet: str
    gateway: Device
    devices: list[Device] = field(default_factory=list)


@dataclass
class Topology:
    """The full topology model rooted at this machine."""
    hostname: str
    local_ips: list[str] = field(default_factory=list)
    root: Device | None = None
    devices: list[Device] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    # Deprecated: kept for backward compat; tree rendering uses root/devices/edges.
    lan: Segment | None = None
    tunnels: list[Device] = field(default_factory=list)


def build_topology(hostname, local_ips=None, root=None, devices=None,
                   edges=None, lan=None, tunnels=None):
    """Compose a Topology from its parts (thin wrapper, testable)."""
    return Topology(hostname=hostname,
                    local_ips=list(local_ips or []),
                    root=root, lan=lan,
                    devices=list(devices or []),
                    edges=list(edges or []),
                    tunnels=list(tunnels or []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: all 66 passed (3 new/updated tests, 63 existing)

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `66 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add Device.id, Edge, and extend Topology for tree rendering"
```

---

### Task 2: Rewrite build_topology for tree + add chain_tunnels

**Files:**
- Modify: `openvpn_manager/topology.py` — replace `build_topology` (if not done in Task 1) and add `build_tree_topology`, `chain_tunnels`
- Modify: `tests/test_topology.py` — add tests

**Interfaces:**
- Consumes: Task 1 `Device`/`Edge`/`Topology`.
- Produces:
  - `build_tree_topology(hostname, local_ips, lan, auto_tunnels, manual_tunnels=None) -> Topology` — assigns IDs, builds the full device/edge tree.
  - `chain_tunnels(tunnels) -> list[tuple[str, str]]` — returns `(parent_id, child_id)` pairs where a tunnel's remote IP matches another tunnel's local IP.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topology.py`:

```python
def test_build_tree_topology_shape():
    lan = Segment(subnet="192.168.1.0/24",
                  gateway=Device(kind="router", label="Router", ip="192.168.1.1"),
                  devices=[Device(kind="device", label="Pi", ip="192.168.1.211")])
    auto_tun = [Device(kind="tunnel", label="home", ip="10.8.0.2", detail="vpn.example.com")]
    topo = topology.build_tree_topology("myhost", ["192.168.1.101"], lan, auto_tun)
    assert topo.root.id == "pc"
    assert topo.root.kind == "pc"
    assert len(topo.devices) == 4  # pc + router + Pi + tunnel
    ids = {d.id for d in topo.devices}
    assert "lan:192.168.1.1" in ids
    assert "lan:192.168.1.211" in ids
    assert "tun:home" in ids
    # PC -> router (solid), PC -> tunnel (dashed), router -> Pi (solid)
    styles = {(e.source_id, e.target_id): e.style for e in topo.edges}
    assert styles[("pc", "lan:192.168.1.1")] == "solid"
    assert styles[("pc", "tun:home")] == "dashed"
    assert styles[("lan:192.168.1.1", "lan:192.168.1.211")] == "solid"


def test_build_tree_topology_no_lan():
    topo = topology.build_tree_topology("myhost", ["192.168.1.101"], None, [])
    assert topo.root.id == "pc"
    assert len(topo.devices) == 1  # just pc
    assert topo.edges == []


def test_build_tree_topology_manual_tunnels():
    manual = [Device(kind="tunnel", label="relay", id="manual:relay",
                     parent_id="tun:home", manual=True, protocol="SSH")]
    lan = Segment(subnet="192.168.1.0/24",
                  gateway=Device(kind="router", label="Router", ip="192.168.1.1"))
    auto_tun = [Device(kind="tunnel", label="home", ip="10.8.0.2", detail="vpn.example.com")]
    topo = topology.build_tree_topology("myhost", [], lan, auto_tun, manual)
    ids = {d.id for d in topo.devices}
    assert "manual:relay" in ids
    styles = {(e.source_id, e.target_id): e.style for e in topo.edges}
    assert styles[("tun:home", "manual:relay")] == "dashed"


def test_chain_tunnels():
    tunnels = [
        Device(kind="tunnel", label="outer", ip="10.8.0.2", detail="vpn.example.com"),
        Device(kind="tunnel", label="inner", ip="10.5.0.1", detail="10.8.0.2"),
    ]
    pairs = topology.chain_tunnels(tunnels)
    assert len(pairs) == 1
    assert pairs[0] == ("tun:outer", "tun:inner")


def test_chain_tunnels_no_match():
    tunnels = [
        Device(kind="tunnel", label="a", ip="10.8.0.2"),
        Device(kind="tunnel", label="b", ip="10.9.0.1"),
    ]
    assert topology.chain_tunnels(tunnels) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "tree or chain"`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'build_tree_topology'`

- [ ] **Step 3: Write the implementation**

Append to `openvpn_manager/topology.py`:

```python
def chain_tunnels(tunnels):
    """Return (parent_id, child_id) pairs for nested VPNs.

    A tunnel whose detail (remote endpoint) matches another tunnel's assigned
    IP is considered a child (chained through the first tunnel).
    """
    pairs = []
    for child in tunnels:
        if not child.detail or not child.ip:
            continue
        for parent in tunnels:
            if parent is child:
                continue
            if parent.ip == child.detail:
                pairs.append((parent.id, child.id))
    return pairs


def build_tree_topology(hostname, local_ips, lan, auto_tunnels,
                        manual_tunnels=None):
    """Build a full tree Topology from discovered and manual devices.

    Auto-detected devices get IDs computed from kind+label (or kind+ip for
    LAN devices). Manual devices already carry their ID from persistence.
    The PC (id="pc") is the root. LAN gateway attaches to PC; each LAN device
    attaches to the gateway. Each auto tunnel attaches to PC (dashed edge),
    then chain_tunnels rewires nested ones. Manual tunnels keep their stored
    parent_id.
    """
    manual_tunnels = list(manual_tunnels or [])
    devices = []
    edges = []

    root = Device(id="pc", kind="pc", label=hostname)
    devices.append(root)

    # Set parent_id on auto-tunnel devices and assign IDs
    for i, t in enumerate(auto_tunnels):
        t.id = f"tun:{t.label}"
        t.parent_id = "pc"
        devices.append(t)

    # Chain: re-parent nested auto-tunnels
    for parent_id, child_id in chain_tunnels(auto_tunnels):
        for t in auto_tunnels:
            if t.id == child_id:
                t.parent_id = parent_id
                break

    if lan is not None:
        gw = lan.gateway
        gw.id = f"lan:{gw.ip}"
        gw.kind = "router"
        gw.parent_id = "pc"
        devices.append(gw)
        edges.append(Edge(source_id="pc", target_id=gw.id, style="solid"))

        for dev in lan.devices:
            dev.id = f"lan:{dev.ip}"
            dev.parent_id = gw.id
            devices.append(dev)
            edges.append(Edge(source_id=gw.id, target_id=dev.id, style="solid"))
    else:
        gw = None

    # Auto-tunnel edges (dashed)
    for t in auto_tunnels:
        edges.append(Edge(source_id=t.parent_id, target_id=t.id, style="dashed"))

    # Manual tunnels: keep their stored parent_id, append to devices+edges
    for mt in manual_tunnels:
        mt.kind = "tunnel"
        mt.manual = True
        devices.append(mt)
        parent = mt.parent_id or "pc"
        edges.append(Edge(source_id=parent, target_id=mt.id, style="dashed"))

    return Topology(hostname=hostname, local_ips=list(local_ips or []),
                    root=root, devices=devices, edges=edges,
                    lan=lan, tunnels=list(auto_tunnels))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "tree or chain"`
Expected: `5 passed`

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `71 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add tree topology builder and tunnel chaining"
```

---

### Task 3: Manual tunnel persistence

**Files:**
- Modify: `openvpn_manager/topology.py` — append `load_manual_tunnels`, `save_manual_tunnels`
- Modify: `tests/test_topology.py` — append tests

**Interfaces:**
- Consumes: Task 1 `Device`.
- Produces:
  - `load_manual_tunnels(path=None) -> list[Device]` — reads TOML array of `[[tunnels]]` entries.
  - `save_manual_tunnels(devices, path=None) -> None` — writes manual-only devices.
  - Default path: `Path.home() / ".config" / "openvpn-manager" / "topology.toml"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topology.py`:

```python
import tomllib


def test_save_and_load_manual_tunnels(tmp_path):
    path = tmp_path / "topology.toml"
    devs = [
        Device(id="manual:t1", kind="tunnel", label="relay", parent_id="pc",
               manual=True, protocol="WireGuard",
               ip=None, detail="relay.example.com"),
        Device(id="manual:t2", kind="tunnel", label="inner", parent_id="tun:home",
               manual=True, protocol="SSH",
               ip=None, detail="10.8.0.5"),
    ]
    topology.save_manual_tunnels(devs, path)
    loaded = topology.load_manual_tunnels(path)
    assert len(loaded) == 2
    assert loaded[0].id == "manual:t1"
    assert loaded[0].parent_id == "pc"
    assert loaded[0].protocol == "WireGuard"
    assert loaded[0].manual is True
    assert loaded[1].id == "manual:t2"
    assert loaded[1].parent_id == "tun:home"
    assert loaded[1].protocol == "SSH"


def test_load_manual_tunnels_missing_file(tmp_path):
    assert topology.load_manual_tunnels(tmp_path / "nope.toml") == []


def test_load_manual_tunnels_malformed(tmp_path):
    path = tmp_path / "topology.toml"
    path.write_text("not valid toml at all [[[")
    assert topology.load_manual_tunnels(path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "manual"`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'save_manual_tunnels'`

- [ ] **Step 3: Write the implementation**

At the top of `openvpn_manager/topology.py`, add the import:

```python
import tomllib
import uuid
from pathlib import Path
```

Append to the end of the file:

```python
_MANUAL_CONFIG_PATH = Path.home() / ".config" / "openvpn-manager" / "topology.toml"


def load_manual_tunnels(path=None):
    """Read manual tunnel definitions from a TOML config file.

    Returns a list of Device(kind="tunnel", manual=True) or [] when the file
    is missing or malformed.
    """
    if path is None:
        path = _MANUAL_CONFIG_PATH
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("tunnels")
    if not isinstance(entries, list):
        return []
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        dev = Device(
            id=entry.get("id") or f"manual:{uuid.uuid4().hex[:8]}",
            kind="tunnel",
            label=entry.get("label", "tunnel"),
            parent_id=entry.get("parent_id", "pc"),
            manual=True,
            protocol=entry.get("protocol"),
            detail=entry.get("remote"),
        )
        result.append(dev)
    return result


def save_manual_tunnels(devices, path=None):
    """Write manual-only Device entries to a TOML config file.

    Creates the parent directory on first save. Auto-detected devices are
    never written.
    """
    if path is None:
        path = _MANUAL_CONFIG_PATH
    entries = []
    for dev in devices:
        if not dev.manual:
            continue
        entries.append({
            "id": dev.id,
            "label": dev.label,
            "parent_id": dev.parent_id or "pc",
            "remote": dev.detail,
            "protocol": dev.protocol,
        })
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for entry in entries:
            fh.write("[[tunnels]]\n")
            for key, val in entry.items():
                if val is not None:
                    fh.write(f"{key} = {val!r}\n")
            fh.write("\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "manual"`
Expected: `3 passed`

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `74 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add manual tunnel persistence to TOML config"
```

---

### Task 4: Layout computation

**Files:**
- Modify: `openvpn_manager/topology.py` — append `compute_layout`
- Modify: `tests/test_topology.py` — append tests

**Interfaces:**
- Consumes: Task 1 `Device`/`Edge`/`Topology`.
- Produces:
  - `NodeBox` dataclass — `device_id: str`, `x: float`, `y: float`, `w: float`, `h: float`.
  - `compute_layout(topo, col_width=160, node_pad=12, level_pad=80) -> list[NodeBox]` — assigns positions left-to-right by tree depth.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topology.py`:

```python
from openvpn_manager.topology import NodeBox, Edge as TopoEdge


def test_compute_layout_positions():
    root = Device(id="pc", kind="pc", label="host")
    child = Device(id="tun:a", kind="tunnel", label="a", parent_id="pc")
    topo = Topology(hostname="host", root=root,
                    devices=[root, child],
                    edges=[TopoEdge(source_id="pc", target_id="tun:a", style="dashed")])
    boxes = topology.compute_layout(topo, col_width=140, node_pad=12, level_pad=60)
    by_id = {b.device_id: b for b in boxes}
    assert len(boxes) == 2
    assert by_id["pc"].x == 0
    assert by_id["tun:a"].x == 200  # col_width + level_pad
    # Same-level single child is vertically centered relative to parent
    assert by_id["tun:a"].y > by_id["pc"].y


def test_compute_layout_sibling_spacing():
    root = Device(id="pc", kind="pc", label="host")
    a = Device(id="tun:a", kind="tunnel", label="a", parent_id="pc")
    b = Device(id="tun:b", kind="tunnel", label="b", parent_id="pc")
    topo = Topology(hostname="host", root=root,
                    devices=[root, a, b],
                    edges=[TopoEdge(source_id="pc", target_id="tun:a", style="dashed"),
                           TopoEdge(source_id="pc", target_id="tun:b", style="dashed")])
    boxes = topology.compute_layout(topo)
    by_id = {b.device_id: b for b in boxes}
    # siblings have same x, different y
    assert by_id["tun:a"].x == by_id["tun:b"].x
    assert abs(by_id["tun:a"].y - by_id["tun:b"].y) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "layout"`
Expected: FAIL with `AttributeError` or `ImportError` on `NodeBox`

- [ ] **Step 3: Write the implementation**

Append to `openvpn_manager/topology.py`:

```python
@dataclass
class NodeBox:
    """A device's bounding box in the layout."""
    device_id: str
    x: float
    y: float
    w: float
    h: float


def compute_layout(topo, col_width=160, node_pad=12, level_pad=80):
    """Compute bounding boxes for every device in a left-to-right tree layout.

    Devices are positioned by tree depth (column): root at x=0, children at
    x = parent.x + col_width + level_pad. Same-depth siblings are stacked
    vertically. The parent is vertically centered across its children.
    Devices without matching parent edges float to the root level.

    Returns a list of NodeBox, one per device in topo.devices.
    """
    children = {}
    for d in topo.devices:
        pid = d.parent_id or "pc"
        children.setdefault(pid, []).append(d)

    boxes = {}

    def layout_subtree(device, x):
        subs = children.get(device.id, [])
        if not subs:
            node_y = 0.0
            for b in boxes.values():
                node_y = max(node_y, b.y + b.h + node_pad)
            boxes[device.id] = NodeBox(device.id, x, node_y, col_width, 56)
            return

        for child in subs:
            layout_subtree(child, x + col_width + level_pad)

        child_boxes = [boxes[ch.id] for ch in subs if ch.id in boxes]
        if child_boxes:
            top = min(b.y for b in child_boxes)
            bottom = max(b.y + b.h for b in child_boxes)
            center_y = (top + bottom) / 2
            boxes[device.id] = NodeBox(device.id, x, center_y - 28, col_width, 56)
        else:
            boxes[device.id] = NodeBox(device.id, x, 0.0, col_width, 56)

    if topo.root:
        layout_subtree(topo.root, 0)

    return sorted(boxes.values(), key=lambda b: (b.x, b.y))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q -k "layout"`
Expected: `2 passed`

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `76 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add tree layout computation"
```

---

### Task 5: Topology diagram widget

**Files:**
- Create: `openvpn_manager/topology_diagram.py`

**Interfaces:**
- Consumes: Task 1 `Topology`/`Device`/`Edge`, Task 4 `compute_layout`.
- Produces: `TopologyDiagram(Gtk.DrawingArea)` — `set_topology(topo)` invalidates and redraws.

- [ ] **Step 1: Write the widget**

Create `openvpn_manager/topology_diagram.py`:

```python
"""Horizontal tree diagram drawn with Cairo. Style B: flat cards + gradients."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402
import cairo

from .topology import compute_layout

DARK_BG = (0.05, 0.06, 0.10)
CARD_FILL_TOP = (0.18, 0.20, 0.25)
CARD_FILL_BOT = (0.10, 0.11, 0.15)
CARD_BORDER = (0.25, 0.28, 0.35)
PC_BORDER = (0.49, 0.68, 0.64)      # accent
ROUTER_BORDER = (0.85, 0.65, 0.34)  # warning
TUNNEL_AUTO_BORDER = (0.66, 0.71, 0.40)  # success
TUNNEL_MANUAL_BORDER = (0.50, 0.55, 0.65)
EDGE_COLOR = (0.49, 0.68, 0.64)

NODE_W = 160
NODE_H = 56
LEVEL_PAD = 80
NODE_PAD = 12
CORNER_RADIUS = 10


class TopologyDiagram(Gtk.DrawingArea):
    """Cairo-drawn horizontal tree diagram."""

    def __init__(self):
        super().__init__()
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

    def set_topology(self, topo):
        self._topo = topo
        self._boxes = compute_layout(topo, col_width=NODE_W,
                                     node_pad=NODE_PAD, level_pad=LEVEL_PAD)
        max_x = max((b.x + NODE_W for b in self._boxes), default=200)
        max_y = max((b.y + NODE_H for b in self._boxes), default=200)
        self._total_w = max_x + 40
        self._total_h = max_y + 40
        self.set_content_width(int(self._total_w))
        self.set_content_height(int(self._total_h))
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        cr.set_source_rgb(*DARK_BG)
        cr.paint()

        if not self._topo:
            return

        # edges first (behind nodes)
        by_id = {b.device_id: b for b in self._boxes}
        for edge in self._topo.edges:
            src = by_id.get(edge.source_id)
            dst = by_id.get(edge.target_id)
            if not src or not dst:
                continue
            cr.set_source_rgb(*EDGE_COLOR)
            cr.set_line_width(2.0)
            if edge.style == "dashed":
                cr.set_dash([6, 4])
            else:
                cr.set_dash([])
            # horizontal line from right edge of src to left edge of dst
            x1 = src.x + NODE_W
            y1 = src.y + NODE_H / 2
            x2 = dst.x
            y2 = dst.y + NODE_H / 2
            cr.move_to(x1, y1)
            cr.line_to(x1 + (x2 - x1) / 2, y1)
            cr.line_to(x1 + (x2 - x1) / 2, y2)
            cr.line_to(x2, y2)
            cr.stroke()
        cr.set_dash([])

        # nodes
        for b in self._boxes:
            dev = next((d for d in self._topo.devices if d.id == b.device_id), None)
            if dev is None:
                continue
            is_hover = self._hover_id == b.device_id
            self._draw_node(cr, dev, b, is_hover)

    def _draw_node(self, cr, dev, box, hover):
        x, y, w, h = box.x, box.y, box.w, box.h

        # shadow
        cr.save()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.rectangle(x + 3, y + 3, w, h)
        cr.fill()
        cr.restore()

        # gradient fill
        pat = cairo.LinearGradient(x, y, x + w, y + h)
        pat.add_color_stop_rgba(0, *CARD_FILL_TOP, 1)
        pat.add_color_stop_rgba(1, *CARD_FILL_BOT, 1)
        cr.set_source(pat)
        self._rounded_rect(cr, x, y, w, h)
        cr.fill()

        # border
        if dev.kind == "pc":
            border = PC_BORDER
        elif dev.kind == "router":
            border = ROUTER_BORDER
        elif dev.kind == "tunnel" and dev.manual:
            border = TUNNEL_MANUAL_BORDER
        elif dev.kind == "tunnel":
            border = TUNNEL_AUTO_BORDER
        else:
            border = CARD_BORDER
        cr.set_source_rgb(*border)
        cr.set_line_width(2.0 if hover else 1.5)
        self._rounded_rect(cr, x, y, w, h)
        cr.stroke()

        # text
        cr.set_source_rgb(0.90, 0.90, 0.85)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(12)
        label = dev.label if len(dev.label) <= 20 else dev.label[:19] + "…"
        ext = cr.text_extents(label)
        cr.move_to(x + (w - ext.width) / 2, y + 22)
        cr.show_text(label)

        cr.set_source_rgb(0.53, 0.53, 0.53)
        cr.set_font_size(10)
        sub = " · ".join(filter(None, [dev.ip, dev.detail]))
        if sub:
            sub = sub if len(sub) <= 28 else sub[:27] + "…"
            ext = cr.text_extents(sub)
            cr.move_to(x + (w - ext.width) / 2, y + 40)
            cr.show_text(sub)

        # green dot for active auto-tunnel
        if dev.kind == "tunnel" and not dev.manual:
            cr.set_source_rgb(0.60, 0.75, 0.35)
            cr.arc(x + w - 14, y + 14, 5, 0, 2 * 3.14159)
            cr.fill()

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r=CORNER_RADIUS):
        cr.new_path()
        cr.arc(x + r, y + r, r, 3.14159, 3 * 3.14159 / 2)
        cr.arc(x + w - r, y + r, r, 3 * 3.14159 / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 3.14159 / 2)
        cr.arc(x + r, y + h - r, r, 3.14159 / 2, 3.14159)
        cr.close_path()

    def _on_motion(self, _controller, x, _y):
        hit = None
        for b in self._boxes:
            if b.x <= x <= b.x + b.w:
                hit = b.device_id
                break
        if hit != self._hover_id:
            self._hover_id = hit
            self.queue_draw()

    def _on_click(self, _gesture, _n_press, x, y):
        for b in self._boxes:
            if b.x <= x <= b.x + b.w and b.y <= y <= b.y + b.h:
                self._hover_id = b.device_id
                self.queue_draw()
                return b.device_id
        self._hover_id = None
        self.queue_draw()
        return None
```

- [ ] **Step 2: Verify the module imports**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); from openvpn_manager.topology_diagram import TopologyDiagram; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `76 passed` (diagram is not unit-tested by convention)

- [ ] **Step 4: Commit**

```bash
git add openvpn_manager/topology_diagram.py
git commit -m "feat: add Cairo tree diagram widget"
```

---

### Task 6: Rebuild topology view around the diagram

**Files:**
- Modify: `openvpn_manager/topology_view.py` — full rewrite

**Interfaces:**
- Consumes: Task 5 `TopologyDiagram`, Task 1 `Topology`.
- Produces: `TopologyView(on_scan=None, on_add_tunnel=None)` — header + scrolled diagram + action buttons.

- [ ] **Step 1: Write the widget**

Replace `openvpn_manager/topology_view.py` entirely:

```python
"""Network topology view: tree diagram + action buttons."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .topology_diagram import TopologyDiagram


class TopologyView(Gtk.Box):
    """Renders a Topology as a horizontal tree diagram."""

    def __init__(self, on_scan=None, on_add_tunnel=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._on_scan = on_scan
        self._on_add_tunnel = on_add_tunnel

        self._host_label = Gtk.Label(halign=Gtk.Align.START, wrap=True)
        self._host_label.add_css_class("title-3")
        self.append(self._host_label)

        scroller = Gtk.ScrolledWindow()
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        self._diagram = TopologyDiagram()
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
```

- [ ] **Step 2: Verify the module imports**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from openvpn_manager.topology_view import TopologyView; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `76 passed`

- [ ] **Step 4: Commit**

```bash
git add openvpn_manager/topology_view.py
git commit -m "feat: rebuild topology view around tree diagram"
```

---

### Task 7: Wire add-tunnel dialog and updated refresh into app.py

**Files:**
- Modify: `openvpn_manager/app.py`

**Interfaces:**
- Consumes: Task 1 `build_tree_topology`, Task 3 `load_manual_tunnels`/`save_manual_tunnels`, Task 6 `TopologyView(on_add_tunnel=...)`.
- Produces: Working "Add Tunnel" dialog; topology refresh uses the tree builder.

- [ ] **Step 1: Add the import for manual tunnel persistence**

In `openvpn_manager/app.py`, change the `from . import topology` line to bring in the specific functions used:

```python
from .topology import (build_tree_topology, load_manual_tunnels,
                        save_manual_tunnels)
```

_Keep the existing `from . import topology` for `topology.gateway_and_subnet` etc., or replace it. Since `_refresh_topology` still calls `topology.gateway_and_subnet`, `topology.local_ips`, `topology.neighbors`, `topology.build_segment`, `topology.tun_ips`, `topology.connected_tunnels`, `topology.sweep_argv` — we keep `from . import topology` and add the extra imports._

So the imports become:

```python
from . import palette
from . import topology
from . import vpn
from .topology import (Device, build_tree_topology, load_manual_tunnels,
                        save_manual_tunnels)
```

- [ ] **Step 2: Rewrite `_refresh_topology` to use `build_tree_topology`**

Replace the current `_refresh_topology` method (lines 487-501) with:

```python
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
        hostname = socket.gethostname() or "This machine"
        topo = build_tree_topology(hostname, own, lan, auto_tunnels, manual)
        self._topo_view.set_topology(topo)
```

- [ ] **Step 3: Add the `_on_add_tunnel` dialog method**

Append to the `Window` class, before the `OpenVpnManagerApp` class:

```python
    def _on_add_tunnel(self):
        devices = self._topo_view._diagram._topo.devices if self._topo_view._diagram._topo else []
        device_labels = [f"{d.label} ({d.ip or d.id})" for d in devices]
        if not device_labels:
            return

        dialog = Adw.MessageDialog.new(self, "Add Tunnel", "Add a manual tunnel node to the topology tree.")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        parent_combo = Adw.ComboRow(title="Connect from")
        parent_combo.set_model(Gtk.StringList.new(device_labels))
        parent_combo.set_selected(0)
        body.append(parent_combo)

        label_entry = Gtk.Entry(placeholder_text="Label (e.g. nested-ssh)")
        body.append(label_entry)

        remote_entry = Gtk.Entry(placeholder_text="Remote (hostname or IP)")
        body.append(remote_entry)

        proto_combo = Adw.ComboRow(title="Protocol")
        proto_combo.set_model(Gtk.StringList.new(["SSH", "WireGuard", "OpenVPN", "Other"]))
        proto_combo.set_selected(0)
        body.append(proto_combo)

        dialog.set_extra_child(body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
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
            parent_id = devices[parent_idx].id if 0 <= parent_idx < len(devices) else "pc"
            proto = proto_combo.get_model().get_string(proto_combo.get_selected()) if proto_combo.get_selected() >= 0 else "SSH"
            import uuid
            new_tun = Device(
                id=f"manual:{uuid.uuid4().hex[:8]}",
                kind="tunnel", label=label, parent_id=parent_id,
                manual=True, protocol=proto, detail=remote)
            existing = load_manual_tunnels()
            existing.append(new_tun)
            save_manual_tunnels(existing)
            self._refresh_topology()
            self._toast.add_toast(Adw.Toast.new(f"Tunnel '{label}' added"))

        dialog.connect("response", on_response)
        dialog.present()
```

- [ ] **Step 4: Pass `on_add_tunnel` to the TopologyView**

In `Window.__init__`, change the `TopologyView(on_scan=...)` construction (line ~151) to:

```python
        self._topo_view = TopologyView(on_scan=self._on_scan_requested,
                                       on_add_tunnel=self._on_add_tunnel)
```

- [ ] **Step 5: Verify the module imports and the suite**

Run: `/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_version('Gdk','4.0'); from openvpn_manager.app import Window; print('ok')"`
Expected: `ok`

Run: `.venv/bin/python -m pytest -q`
Expected: `76 passed`

- [ ] **Step 6: Manual verification**

Run: `.venv/bin/python -m openvpn_manager`
Expected:
- Header shows Status | Topology switcher.
- Topology view shows a host header and a horizontal tree diagram (PC card on the left).
- If a VPN profile is connected, a tunnel card appears to the right of PC with a dashed edge and a green dot.
- LAN devices appear nested under the router card (if a LAN gateway was found).
- Clicking "Add Tunnel" opens a dialog; fill in label + remote, confirm, and a new card appears in the tree with a dashed edge from the selected parent.
- Check `~/.config/openvpn-manager/topology.toml` exists and contains the saved entry.
- Restart the app — the manual tunnel still appears.

- [ ] **Step 7: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "feat: add tunnel dialog and tree topology refresh"
```
