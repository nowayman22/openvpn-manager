# OpenVPN Manager — Tunnel Context Menu Fix & Connectivity Test

Date: 2026-08-03
Status: Approved

## Summary

Two changes to the Topology tab.

First, a fix: the right-click context menu never appears because it builds its
menu items with `Gtk.ModelButton`, a widget that was removed in GTK 4.22. Every
right-click on a node raises `AttributeError` inside the `pressed` callback, so
the popover is never shown and the user cannot add or remove tunnels by
right-clicking.

Second, a feature: a way to tell whether a tunnel node actually works. Tunnel
nodes get a "Test connectivity…" context-menu item that checks the tunnel's
remote endpoint (TCP connect where the protocol has a known port, otherwise a
ping). The result shows in a toast and as a persistent colored dot on the tunnel
card in the diagram, so chained tunnels ("tunnels connected from my other
tunnel") can be verified at a glance.

## Root Cause

`openvpn_manager/app.py:684-693` passes `Gtk.ModelButton(label=...)` to build the
context-menu items. GTK 4.10 deprecated `GtkModelButton` and GTK 4.22 removed it
from the typelib; `'gi.repository.Gtk' object has no attribute 'ModelButton'`
(verified live with a synthetic right-click driving `_on_click` → `_on_node_context`).
The uncaught exception aborts the popover before it is built, so no menu appears.
Hit detection and click routing are correct; only the widget creation fails.

## Goals

- Right-clicking any topology node shows the context menu again (Edit/Delete for
  manual tunnels, Add child tunnel for every node).
- Every tunnel node offers "Test connectivity…" (disabled when it has no remote
  endpoint). PC / router / LAN-device nodes do not.
- The test runs without freezing the UI (background thread), reports a toast,
  and leaves a durable status dot on the tunnel card: amber = testing, green =
  reachable, red = failed, dim = never tested.
- The check succeeds if either the TCP connect *or* the ping succeeds, and the
  message names which one worked.
- Pure backend: the test logic lives in `topology.py` (no GTK, injectable,
  unit-tested).

## Non-Goals

- Live, automatic status polling. The test is on-demand only.
- Testing non-tunnel nodes (PC/router/LAN devices have no remote endpoint).
- Editing or deleting auto-detected tunnels / LAN devices via the UI (unchanged).
- Replacing the auto-tunnel top-right green dot (systemd "connected"). The new
  test dot is drawn bottom-right and is semantically distinct.

## Backend Changes — `topology.py`

New pure function (no GTK; follows the `connected_tunnels` pattern of injectable
runners so tests never touch the network):

```
test_tunnel(remote, protocol, *, timeout=3,
            connect=socket.create_connection,
            ping_runner=subprocess.run) -> (status, message)
```

Port map: SSH → 22, OpenVPN → 1194, WireGuard → 51820, Other → 443.

1. When `protocol` has a known port and `remote` is set, try
   `socket.create_connection((remote, port), timeout)`; success →
   `("ok", f"TCP {remote}:{port}")`.
2. Otherwise (or if the connect failed), run
   `ping -c 2 -W 2 <remote>` via the injectable runner. Exit 0 → count reply
   lines → `("ok", f"ping {remote} ({n}/2 replies)")`.
3. Both failed → `("fail", <reason>)`, preferring the TCP error text, else the
   ping error, else a generic "no response".
4. `remote` empty → `("fail", "no remote endpoint")`.

Imports added to `topology.py`: `socket`, `subprocess`.

## Diagram Changes — `topology_diagram.py`

`TopologyDiagram` gains:

- `self._tunnel_status: dict[str, str]` — device_id → `"testing"` | `"ok"` |
  `"fail"`.
- `set_tunnel_status(device_id, status)` — stores the status and redraws.
- In `_draw_node`, for tunnel devices, draw a small status dot at the
  bottom-right corner (`x + w - 14`, `y + h - 14`, radius 5): amber for
  `"testing"`, green for `"ok"`, red for `"fail"`, dim gray (outline only) when
  the tunnel was never tested. The existing auto-tunnel green dot (top-right)
  is untouched.
- In `set_topology`, prune status entries whose device id is no longer present
  (tunnel deleted or auto-tunnel gone), so stale dots never persist.

## App Changes — `app.py`

- **Fix:** in `_on_node_context`, replace each `Gtk.ModelButton(label=...)`
  with `Gtk.Button(label=...)` plus `add_css_class("flat")` inside the same
  vertical box. Menu items and callbacks are unchanged.
- **New menu item** for tunnel devices: "Test connectivity…". Added with
  `set_sensitive(False)` when `device.detail` is empty. Lives above Edit/Delete.
- **`_on_test_tunnel(device)`** — shows a toast `Testing 'x'…`, calls
  `set_tunnel_status(device.id, "testing")`, then runs
  `topology.test_tunnel(device.detail, device.protocol)` in a `threading.Thread`
  (daemon) and marshals the result to the main thread with `GLib.idle_add`.
- **`_finish_tunnel_test(device, status, message)`** — updates the diagram dot
  via `set_tunnel_status` and shows the result toast, e.g.
  `Tunnel 'x' reachable — TCP 10.8.0.9:22` or
  `Tunnel 'x' no response — destination unreachable`.
- `import threading` added.

## Error Handling

- No remote endpoint → the Test item is disabled.
- Hostname does not resolve / timeout / ICMP filtered → `"fail"` with the last
  reason; the dot turns red and the toast explains. Filtered ICMP with a working
  TCP port still reports ok (either check succeeding is enough).
- `ping` missing or fails to spawn → `"fail"`, caught via `OSError`.
- A topology refresh prunes statuses for removed tunnels; surviving tunnels keep
  their last result.

## Testing

`tests/test_topology.py` extended (injected fakes, no real network):

- TCP connect succeeds → ok, message names the port.
- TCP connect raises → ping runner exits 0 → ok, message counts replies.
- TCP raises and ping exits non-zero → fail with reason.
- TCP skipped (no known port for `protocol=None` or `"SSH"`-absent protocol) →
  ping decides.
- Empty remote → fail "no remote endpoint".
- Reply counting parses `bytes from` lines correctly.

GTK widgets (`topology_diagram.py` status dot, `app.py` popover and async
handler) verified manually per project convention. Existing suite must still
pass.

Manual verification:

- Launch app. Right-click a node → menu appears (regression check for the
  ModelButton crash); manual tunnel shows Edit/Delete; Add child tunnel opens
  the dialog with the node pre-selected.
- Right-click a tunnel → Test connectivity…; amber dot + toast while running;
  dot turns green/red; toast names TCP or ping and the reason on failure.
- Verify a chained tunnel (child whose remote is only reachable through a
  parent) reflects the parent being up vs down.
- Refresh the topology; surviving tunnels keep their dots, removed ones clear.
