# OpenVPN Manager — Manual Tunnel Connect (Daisy-Chained SOCKS)

Date: 2026-08-04
Status: Approved

## Summary

Manual tunnels are currently visualization-only: adding one stores a node in
`topology.toml` and the only live action is "Test connectivity" (a TCP/ping
probe). This change makes manual tunnels actually connectable.

- **SSH tunnels** establish a real SOCKS5 proxy on the local machine via a
  background `ssh -N -D` process. Nested tunnels route through their parent's
  SOCKS port (`nc -X 5 -x` ProxyCommand), forming a strict daisy chain: the
  proxy at the deepest node reaches hosts behind every hop.
- **OpenVPN manual tunnels** bind to an imported `.ovpn` profile and connect
  through the existing `systemctl start openvpn-client@<profile>` path.
- **WireGuard** manual tunnels are deferred: they keep Test connectivity and
  the parent-chain gate, but Connect is disabled.
- Connecting a nested tunnel auto-connects every ancestor first (nearest to
  pc first), one click brings up the whole chain. Disconnecting a node
  cascades to its descendants.

## Goals

- A "Connect" / "Disconnect" action on manual SSH and profile-bound OpenVPN
  tunnels in the topology context menu.
- A live, per-node connection state shown on the existing tunnel status dot.
- Auto-connect of the ancestor chain; cascade disconnect of descendants.
- Pure, unit-testable backend: argv construction, chain resolution, port
  allocation, and state mapping live in a new `tunnels.py` with no GTK.
- No duplicate topology nodes when a manual OpenVPN tunnel binds to a profile
  that the auto-discovery would also surface.

## Non-Goals

- WireGuard connect (deferred; Connect stays disabled with a note).
- VPN-over-SSH: an OpenVPN unit always starts from the local machine, so an
  OpenVPN tunnel nested under an SSH tunnel is a visual hierarchy, not a
  routed hop. The SSH ancestor is not used for the VPN's connectivity.
- Auto-reconnect across app restarts; SOCKS ports are reassigned fresh each
  run.
- Password-based SSH auth (BatchMode, keys/agent only).
- Persisting SOCKS port assignment is optional per-tunnel, but reconnect uses
  whatever port is assigned at connect time.

## Connection Model

Only SSH tunnels establish SOCKS proxies. Each connected SSH tunnel runs one
background `ssh` process with `-N -D 127.0.0.1:<port>`.

The ssh command is:

```
ssh -N -D 127.0.0.1:<port>
    -o BatchMode=yes
    -o ExitOnForwardFailure=yes
    -o ServerAliveInterval=30
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
    [-o ProxyCommand=nc -X 5 -x 127.0.0.1:<parentPort> %h %p]
    <user>@<remote>
```

The `ProxyCommand` hop is added only when the tunnel's parent is a connected
SSH tunnel; it routes the connection through the parent's local SOCKS port so
the child's proxy is layered on top of the parent's.

Hop resolution by parent kind:

| Parent                  | Child connection                                    |
|-------------------------|-----------------------------------------------------|
| pc (local machine)      | direct ssh, no hop                                  |
| SSH tunnel              | ProxyCommand through parent's SOCKS port            |
| OpenVPN (auto or bound) | unit must be systemd-active; child ssh connects directly (VPN routing) |
| WireGuard               | not connectable (deferred)                          |

`nc` must be SOCKS5-capable (`nc -X 5`), i.e. OpenBSD netcat (Arch's default).
Connect checks `nc` exists and errors clearly if not.

OpenVPN manual tunnels: Connect = `systemctl start openvpn-client@<profile>`,
Disconnect = `systemctl stop`, state = `systemctl is-active` — all via the
existing `vpn.py` helpers. If the profile needs credentials, reuse the
existing `needs_credentials` + credential-prompt flow before starting.

## Data Model & Persistence

`Device` (topology.py dataclass) gains three optional fields, appended after
`protocol` so existing positional construction is unaffected:

- `profile: str | None = None` — imported profile bound to an OpenVPN tunnel.
- `user: str | None = None` — SSH username; defaults to current user.
- `port: int | None = None` — fixed local SOCKS port; None = auto-assign.

`load_manual_tunnels` reads `profile`, `user`, `port` from each `[[tunnels]]`
entry; `save_manual_tunnels` writes them when not None. Existing config files
with none of these fields load unchanged.

## Backend — new `openvpn_manager/tunnels.py` (pure, no GTK)

Pure functions, all injectable for tests:

- `SSH_FLAGS(port, parent_port=None) -> list[str]` — the flag list above.
- `socks_proxy_argv(user, remote, port, parent_port=None) -> list[str]`
- `resolve_chain(topo, device_id) -> list[Device]` — ancestors from root to
  target (excluding target), for auto-connect ordering.
- `nearest_socks_ancestor(topo, device_id, connections) -> int | None` — the
  closest connected SSH ancestor's SOCKS port, or None.
- `next_free_port(used: set[int], start=1080) -> int`
- `is_connectable(device, profiles) -> bool` — SSH always; OpenVPN only when
  bound to an imported profile; WireGuard/Other never.
- `tree_descendants(devices, device_id) -> list[str]` — full-tree descendants
  for cascade disconnect (distinct from the manual-only `descendant_ids`).
- `dedupe_auto_tunnels(auto_tunnels, manual) -> list[Device]` — drop an
  auto-discovered tunnel whose label matches a manual tunnel's bound profile.

## State & Lifecycle — `TunnelManager`

A small GTK-free class owning connection state.

Registry: `{device_id: {"kind": "ssh" | "openvpn", "proc": Popen|None,
"port": int|None, "since": float, "message": str|None, "stage": str}}`.
Stages: `disconnected | connecting | connected | failed`.

- `connect(device, *, topo, profiles, runner=vpn.run)` — walks
  `resolve_chain` plus the target, connecting each in order (SSH: allocate
  port, Popen, register; OpenVPN: Popen `systemctl start`, register as
  connecting). Already-connected nodes are skipped. Returns a list of
  `(device_id, ok, message)` per attempted node. Stops at the first failure.
- `disconnect(device_id, *, topo)` — SSH: SIGTERM proc; OpenVPN: Popen
  `systemctl stop`. Cascades to `tree_descendants`.
- `status(device_id) -> (stage, message)` — SSH reads `proc.poll()`; a
  reader thread per proc drains stderr into `message` and flips to `failed`
  on nonzero exit. OpenVPN transitions: proc running → connecting; proc
  exited 0 and `is_active` == active → connected; proc exited nonzero →
  failed. `connected` for OpenVPN is re-derived from `is_active` each call.
- `ports_in_use() -> set[int]` — live ports for allocation.
- `clear()` — called on window close to tear down any running procs.

The app polls `status()` for every manual tunnel on its existing 1-second
tick and repaints dots, so a dropped ssh or stopped unit flips the node back
automatically.

## App Changes

- `Window.__init__` — create `self._tunnels = TunnelManager()`.
- `_tick` — after `_maybe_refresh_topology`, paint connection dots for all
  tunnels from `self._tunnels.status(device_id)`.
- Context menu `_on_node_context` — a single "Connect…"/"Disconnect…" toggle
  item based on current stage, only when `is_connectable`. Disabled (with a
  "not yet supported" note) for WireGuard and non-connectable OpenVPN
  tunnels.
- `_on_connect_tunnel(device)` / `_on_disconnect_tunnel(device)` — call the
  manager, then refresh topology + dots, toast the result of each step.
- `_tunnel_dialog` — new fields: for SSH, a "Username (SSH)" entry (default
  `getpass.getuser()`) and an optional "Local SOCKS port" entry (validated as
  an integer; invalid or already-in-use values fall back to auto-assign at
  connect time); the source picker now records `profile` on OpenVPN tunnels.
  Edit mode pre-fills and allows changing `user`/`port`/`profile`.
- `_refresh_topology` — pass manual tunnels to `dedupe_auto_tunnels` before
  building the tree.
- Window close — call `self._tunnels.clear()`.

## Diagram Changes — `topology_diagram.py`

Extend the tunnel status dot with connection stages. `set_tunnel_status`
already accepts `testing | ok | fail`; add `connecting | connected | failed |
disconnected`. Dot painting:

| stage              | dot                                  |
|--------------------|--------------------------------------|
| connected / ok     | green fill                           |
| connecting / testing | amber fill                         |
| failed / fail      | red fill                             |
| disconnected / none| faint outline (unchanged)            |

Connection state is painted on every tick and overrides any test-result dot;
the manual "Test connectivity" still reports its result via toast.

## Error Handling

- ssh auth/connection failure → node `failed`, toast shows the last stderr
  line.
- Port conflict → `next_free_port` picks the next free port.
- Missing `nc` → clear toast, nothing spawned.
- Parent not connectable (WireGuard) → toast explaining, nothing spawned.
- systemd start failure → toast from exit status.
- Parent dies while child is connected → next tick marks the child
  disconnected (its hop is gone).

## Testing

`tunnels.py` is pure and unit-tested in the existing suite (no GTK):

- `socks_proxy_argv` with and without a parent hop (ProxyCommand present with
  the parent port; absent when direct).
- `resolve_chain` ordering and empty chain for direct tunnels.
- `nearest_socks_ancestor` picks the closest connected SSH ancestor.
- `next_free_port` avoids used ports, respects start, wraps past a conflict.
- `tree_descendants` multi-level cascade.
- `dedupe_auto_tunnels` drops the bound profile's auto node, keeps others.
- `is_connectable` per protocol / profile binding.
- Device `profile`/`user`/`port` round-trip through `load`/`save_manual_tunnels`.

GTK wiring (dialog fields, context menu toggle, dot painting, popover) verified
manually per project convention. Existing suite must still pass.

Manual verification:

- Launch app. Add an SSH tunnel under pc → right-click → Connect. A SOCKS
  proxy appears on a free port; the dot turns green; the process is visible
  in `ps`.
- Add a second SSH tunnel under the first → Connect → the child's proxy
  routes through the parent's port; `ps` shows both processes; the child
  reaches hosts only visible behind the parent.
- Add an OpenVPN tunnel bound to an imported profile → Connect → systemd unit
  starts; dot turns green; no duplicate auto node appears in the tree.
- Disconnect a parent → its descendants flip to disconnected in the same
  refresh.
- Right-click a WireGuard tunnel → Connect is disabled with a note.
