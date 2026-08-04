# OpenVPN Manager — Network Topology View

Date: 2026-08-03
Status: Approved

## Summary

Add a Network Topology view to the OpenVPN Manager app that shows the local
machine, the LAN it is attached to (gateway router and discovered devices),
and any connected OpenVPN tunnels. Discovery is fully automatic: passive reads
of the routing table and ARP/NDP neighbor caches plus an active ARP sweep of
the LAN subnet, run through a new `pkexec`-backed `helper.sh scan` subcommand
using the app's existing privilege pattern. The view is a hierarchical
tree/list rendered with libadwaita groups, reached through a header
`Adw.ViewSwitcher` alongside the existing Status dashboard.

## Goals

- Show "this machine" at the root with its hostname and local IPs.
- Show the LAN segment: the gateway router and every device discovered on the
  subnet, labelled by vendor where the MAC OUI is recognized.
- Show connected OpenVPN tunnels as rows (profile name, remote endpoint, VPN
  IP) under a VPN section.
- Fully automatic discovery; the user defines nothing by hand.
- Live: passive data refreshes with the existing 1-second tick; the active
  sweep runs on view open, every 60s while visible, and on a manual refresh
  button.
- Graceful degradation: missing tools, cancelled `pkexec`, or unreadable proc
  files fall back to the passive data without a crash.
- Purely additive: existing Status view behavior, backend modules, and tests
  are unchanged.

## Non-Goals

- Drawing a graph/canvas diagram. The user chose a hierarchical tree/list for
  this 460px-wide window; a diagram can be a follow-up.
- Discovering switch port topology via LLDP/SNMP. Home switches without a
  management IP are invisible to an ARP sweep and are intentionally not
  shown.
- Showing disconnected/idle OpenVPN profiles as tunnel nodes. Only connected
  tunnels appear (user decision).
- Probing the far side of a VPN tunnel to enumerate remote networks. Only the
  tunnel endpoint reachable from this machine is shown.
- Persisting or editing topology data.

## Data Model: `openvpn_manager/topology.py`

Pure module, no GTK imports, following the `vpn.py` testability pattern
(injectable readers and a runner).

```python
@dataclass
class Device:
    kind: str            # "router" | "device" | "tunnel"
    label: str           # e.g. "iPhone", "TP-Link Router", profile name
    ip: str | None
    mac: str | None
    vendor: str | None   # from OUI lookup; None -> label "Device"
    detail: str | None   # tunnel: remote endpoint, VPN IP, uptime

@dataclass
class Segment:
    subnet: str          # "192.168.1.0/24"
    gateway: Device
    devices: list[Device]

@dataclass
class Topology:
    hostname: str
    local_ips: list[str]
    lan: Segment | None
    tunnels: list[Device]
```

The tree shape is fixed: PC (root) → LAN segment → gateway + devices, and
PC → tunnels. The view renders this nesting; the module only produces the
model.

## Discovery Functions

- `gateway_and_subnet(route_text)` — parse `/proc/net/route` for the default
  gateway address and its interface; derive the subnet CIDR; then pair the
  gateway with its MAC from the ARP cache.
- `neighbors(arp_text, ndisc_text)` — parse `/proc/net/arp` (IPv4) and
  `/proc/net/ndisc_cache` (IPv6) into `Device`s, excluding the gateway MAC
  and the PC's own addresses.
- `vendor(mac)` — lookup against a small bundled OUI map of ~30 common
  home/network vendors (Apple, Google, Samsung, TP-Link, Netgear, Cisco,
  Ubiquiti, Raspberry Pi, etc.). Returns `None` for unknown prefixes; the
  view then labels the device "Device".
- `sweep(cidr, runner)` — run the pkexec helper `scan <cidr>`, parse its
  `ip<TAB>mac` output, and merge into the neighbor list. If the helper
  fails or is cancelled, returns the passive neighbors unchanged.
- `connected_tunnels(profiles, runner)` — for each profile where
  `vpn.is_active` is "active", build a `Device(kind="tunnel")` with the
  profile name, the remote endpoint from `vpn.parse_remote(conf)`, the VPN IP
  from the tun interface, and uptime from `vpn.unit_property`.
- `build_topology(hostname, local_ips, lan, tunnels)` — pure composition so
  tests can construct a `Topology` from fake inputs.

The VPN IP for a tunnel comes from `ip -o -4 addr show dev tun*`. With a
single active tunnel (the common case) the attribution is exact; with
multiple simultaneous tunnels the IP shown is best-effort (the tun iface
that matches by ordering) and is documented as a known limitation.

## Active Sweep: `helper.sh scan`

Extend the existing privileged helper with a `scan <cidr>` subcommand
(same `set -euo pipefail` structure, run via `pkexec`):

1. Parallel ARP sweep of the subnet: `arping -c 1 -w 1 <ip>` fanned out via
   `xargs -P`. Falls back to a broadcast ping (`ping -b <bcast> -c 1`) if
   `arping` is not installed.
2. After the sweep, dump refreshed entries from `ip neigh show` (root sees
   all) and print `ip<TAB>mac` per line to stdout.

The backend parses stdout. If `arping` and broadcast ping both fail, or
`pkexec` is cancelled, discovery falls back to the passive neighbor cache.

## View: `openvpn_manager/topology_view.py`

A GTK widget rendered with libadwaita groups:

- Header row: "This machine" with hostname and comma-separated local IPs.
- "Network" `Adw.PreferencesGroup` titled with the subnet. Gateway row with a
  router icon, then one `Adw.ActionRow` per device: vendor label, IP, MAC in
  dim-label.
- "VPN" `Adw.PreferencesGroup`: one row per connected tunnel, `network-vpn`
  icon, status dot in the themed success color, remote endpoint and VPN IP in
  the subtitle.
- Icons: Gtk symbolic names chosen per `Device.kind`
  (`computer-symbolic`, `network-wired-symbolic`,
  `network-server-symbolic`, `network-vpn-symbolic`).
- A "Scan network" button triggers an on-demand sweep.

Colors come from the existing semantic CSS classes (success, dim-label), so
the Omarchy palette theming applies to the topology view for free.

## UI Integration: `app.py`

- Wrap the existing dashboard content in an `Adw.ViewStack` page titled
  "Status".
- Add a second page "Topology" holding `TopologyView`.
- Put an `Adw.ViewSwitcher` in the header bar's title slot
  (`header.set_title_widget`), keeping the status pill (packed end) and the
  menu button (packed start).
- The existing 1-second `_tick` continues to drive the Status page; when the
  Topology page is visible it also refreshes the passive topology data.
- On switching to the Topology page, trigger an initial sweep; then sweep
  every 60s while visible. One sweep at a time; a running sweep is skipped,
  not queued.

## Error Handling

- Missing `arping`, failed or cancelled `pkexec` → passive neighbor cache
  only.
- Unreadable `/proc/net/route` or `/proc/net/arp` → empty/skipped; the view
  still shows the PC and any connected tunnels.
- No default gateway or no up physical interface → no LAN section; view shows
  PC + VPN section.
- Parse failure for a single entry → skip that entry, keep the rest.
- Multi-tunnel VPN IP attribution is best-effort (documented limitation).

## Testing

New `tests/test_topology.py` in the existing plain-pytest style, using
fixture text for `/proc/net/route`, `/proc/net/arp`, `/proc/net/ndisc_cache`,
and `helper.sh scan` output:

- `gateway_and_subnet`: default gateway parsed from a route fixture; missing
  default → `None`.
- `neighbors`: ARP + NDP fixtures parsed, gateway and own addresses excluded.
- `vendor`: known OUI → vendor; unknown → `None`.
- `sweep`: helper output merged over passive neighbors; failed runner → passive
  only.
- `connected_tunnels`: injected runner returns active/inactive; correct rows.
- `build_topology`: tree shape from fake inputs.

Existing pytest suite must still pass. Manual check: launch the app, open the
Topology view, confirm the gateway and LAN devices appear within a few
seconds of the sweep, and that a connected OpenVPN profile shows under VPN.
Run `pkexec helper.sh scan <subnet>` standalone to confirm the sweep works
before wiring it into the app.

## Open Questions

None.
