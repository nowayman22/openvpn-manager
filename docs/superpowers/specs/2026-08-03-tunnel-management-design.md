# OpenVPN Manager — Manual Tunnel Management & Refresh Fix

Date: 2026-08-03
Status: Approved

## Summary

Manage manual tunnel nodes from the topology view: right-click a node for a
context menu that can edit, delete, or add a child tunnel. Editing and deleting
apply to manual tunnels only; every node offers "Add child tunnel". Deletion
re-parents the removed node's children to its parent and asks for confirmation.
The Add Tunnel dialog gains a source picker so a new tunnel can be based on an
imported `.ovpn` profile instead of only free-form label/remote/protocol.

Also fixes a crash that silently kills the app's 1-second refresh timer: the ARP
sweep helper used non-existent `Gio.SubprocessFlags.STDOUT_DEVNULL` /
`STDERR_DEVNULL` constants, so the first sweep raised `AttributeError` inside
`_tick`. Because an unhandled exception in a GLib timeout callback permanently
removes that source, the whole refresh loop died: the status tab froze, a newly
connected tunnel never showed as Connected and never entered the topology, and
it could not be picked as a parent in the Add Tunnel dialog. Restarting the app
started a fresh timer, which is why the symptom disappeared on relaunch.

## Root Cause

`openvpn_manager/app.py:484` passed `Gio.SubprocessFlags.STDOUT_DEVNULL |
Gio.SubprocessFlags.STDERR_DEVNULL` to `Gio.Subprocess.new`. GLib 2.88's typelib
exposes `STDOUT_SILENCE` / `STDERR_SILENCE` (discard output), not `*_DEVNULL`.
The attribute access raised `AttributeError` on the first `_spawn_quiet` call —
i.e. the first ARP sweep from the Topology tab. Verified live: the pipeline
built the full tree correctly once the sweep was out of the picture, and a
GLib test confirmed a timeout source is removed permanently when its callback
raises.

## Goals

- Right-click a topology node → context menu: Edit/Delete for manual tunnels,
  Add child tunnel for every node.
- Edit reuses the Add dialog, pre-filled; parent list excludes self and
  descendants to prevent cycles.
- Delete asks for confirmation and re-parents children to the deleted node's
  parent; nothing is lost.
- Add Tunnel dialog offers a source picker: Free-form or an imported `.ovpn`
  profile (pre-fills label/remote/protocol from the profile's `.conf`).
- The refresh crash is fixed and `_tick` is guarded so no single exception can
  ever kill the refresh timer again.
- Pure backend: new list operations live in `topology.py` (no GTK, injectable,
  unit-tested).

## Non-Goals

- Live connection status for manual tunnels (they keep the neutral dot).
- Click-to-connect gestures in the diagram.
- Editing or deleting auto-detected tunnels / LAN devices via the UI.
- LLDP/SNMP switch discovery.

## Backend Changes — `topology.py`

New pure functions (no GTK, operate on the `list[Device]` returned by
`load_manual_tunnels`):

- `delete_manual_tunnel(tunnels, tunnel_id) -> list[Device]`
  Returns a new list without the removed tunnel. Any tunnel whose
  `parent_id == tunnel_id` is re-parented to the removed node's parent
  (default `"pc"`). Missing id → unchanged list.
- `update_manual_tunnel(tunnels, tunnel_id, *, label, parent_id, remote,
  protocol) -> list[Device]`
  Returns a new list with the matching tunnel's fields replaced (id
  preserved). Missing id → unchanged list.
- `descendant_ids(tunnels, tunnel_id) -> set[str]`
  All ids reachable by following `parent_id` chains from `tunnel_id`.
  Used to build the edit dialog's parent list (exclude self + descendants).

App flow stays `load → edit/delete → save → refresh` using the existing
`save_manual_tunnels` / `load_manual_tunnels`.

## Diagram Changes — `topology_diagram.py`

`TopologyDiagram` already owns a `Gtk.GestureClick`. Extend `_on_click` to
branch on the pressed button:

- Left click → existing hover/select behavior (unchanged).
- Right click (`gesture.get_current_button() == Gdk.BUTTON_SECONDARY`) → if a
  node is under the cursor, call `on_context_menu(device, x, y)`.

The diagram gains an `on_context_menu` callback (constructor or setter).

## View Changes — `topology_view.py`

`TopologyView` gains an `on_context_menu` callback parameter, forwarded to the
diagram (same wiring pattern as `on_scan` / `on_add_tunnel` today).

## App Changes — `app.py`

- **Fix:** `_spawn_quiet` uses `Gio.SubprocessFlags.STDOUT_SILENCE |
  Gio.SubprocessFlags.STDERR_SILENCE`.
- **Guard:** wrap the body of `_tick` in `try/except Exception` so a transient
  error logs and skips the tick instead of killing the timer.
- **`_on_node_context(device, x, y)`** builds a `Gtk.Popover` anchored to the
  diagram at `(x, y)` via `set_pointing_to(Gdk.Rectangle(...))`, filled with
  `Gtk.ModelButton`s:
  - Manual tunnel → **Edit…**, **Delete…**
  - Every node → **Add child tunnel…** (opens the Add dialog with that node
    pre-selected as parent)
- **`_on_edit_tunnel(device)`** — the Add dialog reused, pre-filled from the
  device. The source picker is not shown in edit mode; fields are edited
  directly. The parent combo lists all devices minus the edited node and its
  descendants (`descendant_ids`). On save: `update_manual_tunnel` → save →
  refresh → toast.
- **`_on_delete_tunnel(device)`** — `Adw.MessageDialog` confirm naming the
  tunnel and noting children will be re-parented. On confirm:
  `delete_manual_tunnel` → save → refresh → toast.
- **`_on_add_tunnel(parent_id=None, source_profile=None)`** — extended with a
  source picker: a combo listing "Free-form" + each imported `.ovpn` profile.
  Picking a profile pre-fills label (profile name), remote, and protocol
  (`OpenVPN`) from the profile's `.conf` via the existing `vpn.parse_remote`.
  A selected `parent_id` pre-selects the Connect-from combo.

## Error Handling

- `topology.toml` missing or malformed → `[]`, no crash (unchanged).
- Deleting an unknown id → no-op (list unchanged).
- A right-click on empty canvas → no menu.
- Transient exception inside `_tick` → logged, tick skipped; timer survives.
- Empty label/remote in the free-form add dialog → validation error shown in
  the dialog, dialog stays open.

## Testing

`tests/test_topology.py` extended:

- `delete_manual_tunnel`: removes the node; re-parents its children to its
  parent; missing id → unchanged list.
- `update_manual_tunnel`: replaces label/parent_id/remote/protocol, preserves
  id; missing id → unchanged.
- `descendant_ids`: multi-level chains; leaf returns empty set; self-reference
  does not loop.

GTK widgets (`topology_diagram.py`, `topology_view.py`, `app.py` popover and
dialogs) verified manually (project convention). Existing suite must still pass.

Manual verification:

- Launch app. Right-click the PC node → "Add child tunnel"; Add dialog opens
  with PC pre-selected.
- Right-click a manual tunnel → Edit; change label; node updates.
- Right-click a manual tunnel that has a child → Delete; confirm; child is
  re-parented to the deleted node's parent; node gone; TOML updated.
- Add Tunnel → pick an imported `.ovpn` profile in the source picker; label,
  remote, protocol pre-fill; node appears; TOML has the saved entry; survives
  restart.
- Connect a VPN from the Status tab; confirm the pill turns Connected, the
  tunnel appears in the Topology tree, and switching between tabs keeps both
  updating (the original freeze is gone).
