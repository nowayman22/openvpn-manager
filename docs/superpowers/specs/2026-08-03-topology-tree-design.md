# OpenVPN Manager — Tree Topology Diagram & Tunnel Chaining

Date: 2026-08-03
Status: Approved

## Summary

Replace the flat PreferencesGroup topology list with a Cairo-drawn horizontal tree diagram (Style B: flat cards with gradient fills, drop shadows, bracket-style connectors). Extend the topology data model to support tunnel-to-tunnel chaining — both auto-detected chained tunnels and user-added manual tunnels. Add an "Add Tunnel" button to the topology view with a dialog for creating manual tunnel nodes, persisted to an app config file.

## Goals

- **Tree diagram**: left-to-right horizontal layout with rounded card nodes, gradient fills, drop shadows, solid connectors for physical links and dashed for tunnels. Cairo-drawn (same pattern as the existing `Sparkline`), scrollable canvas.
- **Tunnel chaining**: when an auto-detected tunnel's remote IP matches another tunnel's assigned IP, they are linked as parent/child. When a manual tunnel's parent is an existing tunnel node, it appears nested.
- **Add Tunnel dialog**: pick a parent node from a combo row, enter a label, remote endpoint, and protocol. On confirm the node appears in the tree and is persisted.
- **Persistence**: manual tunnels saved to `~/.config/openvpn-manager/topology.toml`. Config dir created on first save. Auto-detected tunnels are never written — only manual ones.
- **Pure backend**: all layout math, edge generation, and config I/O live in `topology.py` (no GTK, injectable, unit-testable).
- Existing features unchanged: Status dashboard, tick-based refresh, ARP sweep, tunnel auto-detection, Omarchy theming all continue to work.

## Non-Goals

- Editing or deleting manual tunnels via the UI (can be added later; the TOML file is human-editable).
- Live connection status for manual tunnels (they show a neutral dot, not a green "active" dot like auto-detected OpenVPN tunnels).
- Drawing tools or click-to-connect gestures in the diagram. Nodes are clickable for selection but the add flow goes through the dialog.
- LLDP/SNMP-based switch discovery. The LAN is still gateway + devices from the ARP cache.

## Data Model

### `topology.py` changes

```python
@dataclass
class Device:
    id: str           # unique: "pc", "lan:<ip>", "tun:<profile>", "manual:<uuid>"
    kind: str         # "pc" | "router" | "device" | "tunnel"
    label: str
    ip: str | None = None
    mac: str | None = None
    vendor: str | None = None
    detail: str | None = None
    parent_id: str | None = None
    manual: bool = False
    protocol: str | None = None  # "SSH" | "WireGuard" | "OpenVPN" | "Other", manual tunnels only

@dataclass
class Edge:
    source_id: str
    target_id: str
    style: str    # "solid" | "dashed"

@dataclass
class Topology:
    hostname: str
    local_ips: list[str]
    root: Device              # id="pc"
    devices: list[Device]     # all nodes (pc, router, lan devices, tunnels)
    edges: list[Edge]         # all connections
```

`Segment` keeps its current fields (`subnet`, `gateway`, `devices`) for the LAN grouping but is no longer rendered directly — `build_topology` flattens it into the `Device`/`Edge` tree.

### New topology functions

- `build_topology(hostname, local_ips, lan, auto_tunnels, manual_tunnels) -> Topology` — composes the full tree. Assigns IDs: `"pc"`, `"lan:<ip>"`, `"tun:<profile>"`, `"manual:<uuid>"`. Links: PC→gateway (solid), gateway→devices (solid), PC→auto tunnels (dashed), parent→child for manual tunnels (dashed).
- `chain_tunnels(tunnels) -> list[tuple]` - returns `(parent_tunnel_id, child_tunnel_id)` pairs where a tunnel's remote IP matches another tunnel's assigned IP.
- `load_manual_tunnels(path=None) -> list[Device]` — reads `~/.config/openvpn-manager/topology.toml` (default), returns parsed Device list with `manual=True`. Missing file → empty list.
- `save_manual_tunnels(path, devices) -> None` — writes only manual-tunnel Devices to the TOML file, creates parent dirs.
- `compute_layout(topo, font_context) -> list[NodeBox]` — assigns each node a `(x, y, w, h)` bounding box. Left-to-right by tree depth. Same-level nodes stacked vertically with padding. Tunnel branches below physical branches. Returns a flat list of boxes. Pure function, testable with mock font metrics.

## Tree Diagram Widget

### `openvpn_manager/topology_diagram.py`

New `Gtk.DrawingArea` subclass (same pattern as `Sparkline`).

**Rendering (Style B - flat cards):**
- **Nodes**: rounded rectangles (`rx=10`) with a two-stop linear gradient fill (top-left to bottom-right). Border stroke (1.5px) colored by kind: accent for PC, warning for router, neutral-dark for devices, success for auto-tunnels, a distinct muted tone for manual tunnels. Drop shadow via blurred dark underlay rect.
- **Labels**: two lines — main label (bold, 12px) and sublabel (IP/detail, 10px, dim). All text via `cairo.TextExtents`.
- **Edges**: straight horizontal/vertical connector lines. Solid for physical (PC→router, router→devices), dashed for all tunnels (auto + manual). 2px stroke width, accent-colored.

**Layout:**
- `compute_layout` returns node boxes. The diagram widget calls it, sets its own `set_content_width/height` from the total extent, and paints.
- **Scrollable**: the widget is placed inside the caller's `Gtk.ScrolledWindow`. The DrawingArea sets its natural size to the layout extent.

**Interaction:**
- Hit-testing on mouse motion: if cursor is inside a node rect, apply a hover highlight (brighter border).
- Click on a node: emit a signal or callback for selection (future use; initially just visual feedback).
- `set_topology(topo) -> None` — invalidates and redraws.

## Topology View

### `openvpn_manager/topology_view.py`

Rebuilt around the diagram widget:

- Header row: hostname + local IPs (same as today's `_host_label`).
- `Gtk.ScrolledWindow` containing the `TopologyDiagram`.
- Action bar with two buttons: "Scan network" (same as today) and "Add Tunnel" (new, `suggested-action` class).

```python
class TopologyView(Gtk.Box):
    def __init__(self, on_scan=None, on_add_tunnel=None):
        ...
    def set_topology(self, topo):
        self._diagram.set_topology(topo)
```

## Add Tunnel Dialog

### In `app.py`

Method `_on_add_tunnel(selected_parent_id=None)`. If the diagram has a selected node, pre-select it as the parent.

The dialog is an `Adw.MessageDialog` with:

| Field | Widget | Default |
|-------|--------|---------|
| Connect from | `Adw.ComboRow` of all tree nodes | Selected node, or first |
| Label | `Gtk.Entry` placeholder="my-tunnel" | empty |
| Remote | `Gtk.Entry` placeholder="10.5.0.1 or hostname" | empty |
| Protocol | `Adw.ComboRow`: SSH, WireGuard, OpenVPN, Other | SSH |

Responses: "Cancel" and "Add". On "Add": validate non-empty label and remote, create a `Device(kind="tunnel", manual=True, protocol=...)`, append it, call `save_manual_tunnels`, refresh topology, show a "Tunnel added" toast. On validation failure, show the error inline (set dialog body text).

## Persistence Format

`~/.config/openvpn-manager/topology.toml`:

```toml
[[tunnels]]
parent_id = "tun:vpnbook-de20"
label = "nested-ssh"
remote = "10.5.0.1"
protocol = "SSH"

[[tunnels]]
parent_id = "pc"
label = "custom-relay"
remote = "relay.example.com"
protocol = "WireGuard"
```

## App Integration

### `app.py` changes

- `_on_add_tunnel(selected_parent_id=None)` — opens the dialog, handles persistence and refresh.
- `_refresh_topology` extended: call `load_manual_tunnels()`, merge with auto-detected devices via `build_topology`.
- `topology_view` construction passes `on_add_tunnel=self._on_add_tunnel`.
- Existing `_tick`, `_on_scan_requested`, `_spawn_quiet`, `_maybe_refresh_topology` unchanged.

## Error Handling

- `topology.toml` missing → empty `[]`, no crash.
- Malformed TOML → skip that entry, log nothing (silent degrade).
- Config dir uncreatable (permissions) → manual tunnels stay in-memory only for the session, not persisted. No crash.
- Empty label or remote in dialog → validation error shown in the dialog, prevent close.
- Cairo rendering errors → caught, diagram area left blank. No crash.

## Testing

`tests/test_topology.py` extended:

- `compute_layout`: known tree → correct x/y positions, no overlaps, children one level deeper than parent.
- `chain_tunnels`: matching IPs → linked pair; no match → empty list.
- `load_manual_tunnels`: valid TOML file → correct Device list; missing file → `[]`; malformed → skipped entry.
- `save_manual_tunnels` / `load_manual_tunnels` roundtrip.
- `build_topology`: PC root + LAN + auto tunnels + manual tunnels → correct devices list and edge set; manual tunnel's parent_id preserved.
- Existing `Device` creation tests updated for new fields (`id`, `parent_id`, `manual`, `protocol`).

GTK widgets (`topology_diagram.py`, `topology_view.py`) verified manually (project convention). Existing 63-test suite must still pass.

Manual verification: launch app, confirm tree diagram renders, click "Add Tunnel", fill in fields, confirm node appears and persists across restart (check `~/.config/openvpn-manager/topology.toml`). With a VPN connected, confirm the tunnel node appears in the tree with a dashed edge from PC. With a chained tunnel (manual tunnel whose parent is the active VPN), confirm nested rendering.
