# Network Topology View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Network Topology view to the OpenVPN Manager app showing this machine, its LAN (gateway router + discovered devices), and connected OpenVPN tunnels, reached through a header view switcher.

**Architecture:** A pure module `openvpn_manager/topology.py` (no GTK, injectable readers/runners, following `vpn.py`) parses `/proc/net/route`, `/proc/net/arp`, `ip -6 neigh show`, and `ip -o -4 addr show` into a tree model (`Topology` → `Segment` → `Device`). The active ARP sweep is a new `helper.sh scan <cidr>` subcommand run via `pkexec` that populates the kernel neighbor cache (prefers `arping`, falls back to a broadcast ping); the app then re-reads the passive cache. A new GTK widget `topology_view.py` renders the model as libadwaita groups; `app.py` wraps the existing dashboard in an `Adw.ViewStack` with an `Adw.ViewSwitcher` and refreshes the topology on the existing 1-second tick.

**Tech Stack:** Python 3.11+ (stdlib only, no new deps), GTK4 / libadwaita 1.x, bash (`arping`/`ping` for the sweep), pytest via `.venv/bin/python -m pytest`.

## Global Constraints

- `requires-python = ">=3.11"` — stdlib only, add no dependencies.
- `topology.py` must not import GTK. All system reads go through injectable readers/runner parameters, matching the `vpn.py` pattern.
- The `helper.sh scan` subcommand must keep the existing `set -euo pipefail` guard and never write outside the sweep's ARP cache.
- GTK widgets are verified manually (existing project convention); only `topology.py` is unit-tested.
- Tests run with `.venv/bin/python -m pytest -q` (currently 42 passing). The sweep fallback must be silent: missing `arping`, cancelled `pkexec`, or unreadable proc files degrade to the passive neighbor read, never a crash.
- Only connected OpenVPN profiles appear as tunnel nodes. The view shows no disconnected profiles.

---

### Task 1: Topology data model

**Files:**
- Create: `openvpn_manager/topology.py`
- Create: `tests/test_topology.py`

**Interfaces:**
- Produces (later tasks add to this module):
  - `Device` dataclass — `kind: str` (`"router"` | `"device"` | `"tunnel"`), `label: str`, optional `ip`, `mac`, `vendor`, `detail`.
  - `Segment` dataclass — `subnet: str`, `gateway: Device`, `devices: list[Device]`.
  - `Topology` dataclass — `hostname: str`, `local_ips: list[str]`, `lan: Segment | None`, `tunnels: list[Device]`.
  - `build_topology(hostname, local_ips=None, lan=None, tunnels=None) -> Topology`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_topology.py`:

```python
from openvpn_manager import topology
from openvpn_manager.topology import Device, Segment, build_topology


def test_build_topology_shape():
    lan = Segment(subnet="192.168.1.0/24",
                  gateway=Device(kind="router", label="Router", ip="192.168.1.1"))
    topo = build_topology("myhost", ["192.168.1.101"], lan, [])
    assert topo.hostname == "myhost"
    assert topo.local_ips == ["192.168.1.101"]
    assert topo.lan is lan
    assert topo.tunnels == []


def test_build_topology_defaults():
    topo = build_topology("myhost")
    assert topo.local_ips == []
    assert topo.lan is None
    assert topo.tunnels == []


def test_device_defaults():
    dev = Device(kind="tunnel", label="work")
    assert dev.ip is None
    assert dev.mac is None
    assert dev.vendor is None
    assert dev.detail is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'openvpn_manager.topology'`

- [ ] **Step 3: Write the implementation**

Create `openvpn_manager/topology.py`:

```python
"""Network topology discovery. No GTK imports.

All system reads go through injectable readers so this module is testable
without touching the real network.
"""

from dataclasses import dataclass, field

from . import vpn


@dataclass
class Device:
    """One node in the topology: a router, a LAN device, or a tunnel."""
    kind: str                # "router" | "device" | "tunnel"
    label: str
    ip: str | None = None
    mac: str | None = None
    vendor: str | None = None
    detail: str | None = None


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
    lan: Segment | None = None
    tunnels: list[Device] = field(default_factory=list)


def build_topology(hostname, local_ips=None, lan=None, tunnels=None):
    """Compose a Topology from its parts (thin wrapper, testable)."""
    return Topology(hostname=hostname, local_ips=list(local_ips or []),
                    lan=lan, tunnels=list(tunnels or []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: `3 passed`

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `45 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add topology data model"
```

---

### Task 2: MAC OUI vendor lookup

**Files:**
- Modify: `openvpn_manager/topology.py` (append)
- Modify: `tests/test_topology.py` (append tests)

**Interfaces:**
- Consumes: `Device` from Task 1.
- Produces: `vendor(mac: str | None, oui_text: str | None = None) -> str | None` — vendor name from the MAC OUI; `None` when unrecognized. Reads `/usr/share/ieee-data/oui.txt` when present (overrides the bundled map); `oui_text` injects file content for tests.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology.py`:

```python
def test_vendor_known(monkeypatch):
    monkeypatch.setattr(topology, "_oui_cache", {"AA:BB:CC": "Acme"})
    assert topology.vendor("aa:bb:cc:dd:ee:ff") == "Acme"


def test_vendor_unknown(monkeypatch):
    monkeypatch.setattr(topology, "_oui_cache", {"AA:BB:CC": "Acme"})
    assert topology.vendor("00:00:00:00:00:01") is None


def test_vendor_injects_oui_text():
    text = "AA:BB:CC\tAcme Widgets\n"
    assert topology.vendor("aa:bb:cc:dd:ee:ff", oui_text=text) == "Acme Widgets"


def test_vendor_bad_mac():
    assert topology.vendor(None) is None
    assert topology.vendor("") is None
    assert topology.vendor("nope") is None


def test_vendor_fallback_map(monkeypatch):
    monkeypatch.setattr(topology, "_oui_cache", dict(topology._OUI_FALLBACK))
    assert topology.vendor("B8:27:EB:12:34:56") == "Raspberry Pi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'vendor'`

- [ ] **Step 3: Write the implementation**

Append to `openvpn_manager/topology.py`:

```python
#: Bundled MAC OUI prefix -> vendor map for common home/network devices.
#: Prefixes are the first three bytes of a MAC (IEEE OUI registry). When the
#: system ieee-data database exists it overrides this map.
_OUI_FALLBACK = {
    "B8:27:EB": "Raspberry Pi",
    "DC:A6:32": "Raspberry Pi",
    "E4:5F:01": "Raspberry Pi",
    "00:0C:29": "VMware",
    "00:50:56": "VMware",
    "F8:FF:C2": "Apple",
    "3C:22:FB": "Apple",
    "A4:83:E7": "Apple",
    "CC:20:E8": "Apple",
    "00:13:CE": "TP-Link",
    "50:C7:BF": "TP-Link",
    "74:DA:38": "TP-Link",
    "58:6D:8F": "Netgear",
    "A0:63:91": "Netgear",
    "C0:3F:0E": "Netgear",
    "00:05:9A": "Cisco",
    "F8:0F:F9": "Ubiquiti",
    "24:A4:3C": "Ubiquiti",
    "78:44:FD": "Amazon",
    "EC:8E:B5": "Google",
    "28:6E:D4": "Google",
}

_OUI_PATH = "/usr/share/ieee-data/oui.txt"
_oui_cache = None


def _normalize_oui(mac):
    """Return a MAC's OUI prefix as 'AA:BB:CC', or None if unparseable."""
    if not mac:
        return None
    cleaned = mac.strip().replace("-", "").replace(":", "").upper()
    if len(cleaned) < 6 or any(c not in "0123456789ABCDEF" for c in cleaned):
        return None
    return ":".join(cleaned[i:i + 2] for i in range(0, 6, 2))


def _parse_oui(text):
    """Parse ieee-data oui.txt content into {prefix: vendor_name}."""
    result = {}
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        prefix = parts[0].strip().upper()
        name = parts[1].strip()
        if prefix and name:
            result[prefix] = name
    return result


def _oui_table(oui_text=None):
    """Return the OUI prefix -> vendor table.

    When oui_text is given (tests) it is parsed directly; otherwise the
    system ieee-data database is read when present, falling back to the
    bundled map. The table is cached.
    """
    global _oui_cache
    if oui_text is not None:
        return _parse_oui(oui_text)
    if _oui_cache is None:
        try:
            with open(_OUI_PATH) as fh:
                _oui_cache = _parse_oui(fh.read())
        except OSError:
            _oui_cache = dict(_OUI_FALLBACK)
    return _oui_cache


def vendor(mac, oui_text=None):
    """Return a vendor name for a MAC address, or None when unrecognized."""
    prefix = _normalize_oui(mac)
    if prefix is None:
        return None
    return _oui_table(oui_text).get(prefix)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: `8 passed`

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `50 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add MAC OUI vendor lookup"
```

---

### Task 3: Default gateway and subnet parsing

**Files:**
- Modify: `openvpn_manager/topology.py` (append)
- Modify: `tests/test_topology.py` (append tests)

**Interfaces:**
- Consumes: Task 1 module structure.
- Produces: `gateway_and_subnet(route_text: str) -> tuple[str | None, str | None]` — `(gateway_ip, cidr)` from `/proc/net/route`, or `(None, None)` when there is no default gateway.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology.py`:

```python
import textwrap


ROUTE = textwrap.dedent("""\
    Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
    wlan0	00000000	0101A8C0	0003	0	0	600	00000000	0	0	0
    wlan0	0001A8C0	00000000	0001	0	0	600	00FFFFFF	0	0	0
""").strip()


def test_gateway_and_subnet():
    gw, cidr = topology.gateway_and_subnet(ROUTE)
    assert gw == "192.168.1.1"
    assert cidr == "192.168.1.0/24"


def test_gateway_and_subnet_no_default():
    assert topology.gateway_and_subnet("") == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'gateway_and_subnet'`

- [ ] **Step 3: Write the implementation**

Append to `openvpn_manager/topology.py`:

```python
def _hex_ip(hexval):
    """Convert a /proc/net/route hex address field to a dotted quad."""
    try:
        raw = int(hexval, 16).to_bytes(4, "big")
    except (ValueError, OverflowError):
        return None
    return ".".join(str(byte) for byte in reversed(raw))


def _mask_to_prefix(mask_hex):
    """Convert a /proc/net/route netmask field to a prefix length."""
    try:
        value = int(mask_hex, 16)
    except ValueError:
        return 24
    return bin(value).count("1")


def gateway_and_subnet(route_text):
    """Return (gateway_ip, cidr) from /proc/net/route, or (None, None).

    Address fields are hex little-endian: 0101A8C0 is 192.168.1.1. The subnet
    is the directly-connected route on the default gateway's interface.
    """
    default = None
    connected = {}
    for line in route_text.splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[0] == "Iface":
            continue
        iface, dest, gw, _flags, _ref, _use, metric_hex, mask = parts[:8]
        if dest == "00000000" and gw != "00000000":
            metric = int(metric_hex, 16)
            if default is None or metric < default[0]:
                default = (metric, iface, gw)
        elif gw == "00000000" and dest != "00000000":
            net = _hex_ip(dest)
            if net is not None:
                connected[iface] = f"{net}/{_mask_to_prefix(mask)}"
    if default is None:
        return None, None
    _metric, iface, gw = default
    return _hex_ip(gw), connected.get(iface)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: `10 passed`

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `52 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: parse default gateway and LAN subnet"
```

---

### Task 4: Neighbor cache parsing and LAN segment build

**Files:**
- Modify: `openvpn_manager/topology.py` (append)
- Modify: `tests/test_topology.py` (append tests)

**Interfaces:**
- Consumes: `vendor` (Task 2), `Device`/`Segment` (Task 1).
- Produces:
  - `neighbors(arp_text: str, ndisc_text: str, own_ips=()) -> list[Device]` — parse `/proc/net/arp` + `ip -6 neigh show` into labeled Devices, excluding own IPs; a host in both caches is kept once (IPv4 wins); the gateway is kept here so `build_segment` can pick it out.
  - `build_segment(gateway_ip: str | None, cidr: str | None, devices: list[Device]) -> Segment | None` — split devices into the gateway router (by IP) and the LAN list; `None` when `gateway_ip` is falsy.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology.py`:

```python
ARP = textwrap.dedent("""\
    IP address       HW type     Flags       HW address            Mask     Device
    192.168.1.1      0x1         0x2         e8:9f:80:8d:a0:c1     *        wlan0
    192.168.1.211    0x1         0x2         e0:01:c7:f7:df:74     *        wlan0
    192.168.1.240    0x1         0x2         00:00:00:00:00:00     *        wlan0
""")

NDISC = textwrap.dedent("""\
    fe80::ea9f:80ff:fe8d:9749 dev wlan0 lladdr e8:9f:80:8d:97:49 router STALE
    fe80::1 dev wlan0 lladdr 11:22:33:44:55:66 STALE
""")


def test_neighbors_parses_arp_and_ndisc():
    devices = topology.neighbors(ARP, NDISC)
    by_ip = {d.ip: d for d in devices}
    assert "192.168.1.1" in by_ip
    assert "192.168.1.211" in by_ip
    assert "192.168.1.240" not in by_ip  # incomplete MAC is skipped
    assert by_ip["192.168.1.211"].mac == "e0:01:c7:f7:df:74"


def test_neighbors_excludes_own_ips():
    devices = topology.neighbors(ARP, NDISC, own_ips=("192.168.1.211",))
    assert all(d.ip != "192.168.1.211" for d in devices)


def test_build_segment_splits_gateway():
    devices = topology.neighbors(ARP, NDISC)
    seg = topology.build_segment("192.168.1.1", "192.168.1.0/24", devices)
    assert seg.subnet == "192.168.1.0/24"
    assert seg.gateway.ip == "192.168.1.1"
    assert seg.gateway.kind == "router"
    assert all(d.ip != "192.168.1.1" for d in seg.devices)


def test_build_segment_no_gateway():
    assert topology.build_segment(None, None, []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'neighbors'`

- [ ] **Step 3: Write the implementation**

Append to `openvpn_manager/topology.py`:

```python
def _arp_entries(arp_text):
    """Yield (ip, mac) pairs from /proc/net/arp text."""
    for line in arp_text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] == "IP":
            continue
        ip, mac = parts[0], parts[3]
        if mac != "00:00:00:00:00:00":
            yield ip, mac


def _ndisc_entries(ndisc_text):
    """Yield (ip, mac) pairs from `ip -6 neigh show` text."""
    for line in ndisc_text.splitlines():
        parts = line.split()
        if not parts or "::" not in parts[0]:
            continue
        for i, part in enumerate(parts):
            if part == "lladdr" and i + 1 < len(parts):
                yield parts[0], parts[i + 1]
                break


def neighbors(arp_text, ndisc_text, own_ips=()):
    """Parse neighbor caches into labeled Devices, excluding own addresses.

    A host that appears in both caches is kept once (the IPv4 entry wins).
    Labels are the vendor name from the MAC OUI when recognized, else
    "Device". The gateway is intentionally kept so build_segment can pick it
    out; it is not shown among the LAN devices.
    """
    own = set(own_ips)
    by_mac = {}
    for ip, mac in _arp_entries(arp_text):
        if ip not in own and mac not in by_mac:
            by_mac[mac] = (ip, mac)
    for ip, mac in _ndisc_entries(ndisc_text):
        if ip not in own and mac not in by_mac:
            by_mac[mac] = (ip, mac)
    devices = []
    for ip, mac in by_mac.values():
        name = vendor(mac)
        devices.append(Device(kind="device", label=name or "Device",
                              ip=ip, mac=mac, vendor=name))
    return devices


def build_segment(gateway_ip, cidr, devices):
    """Split neighbor Devices into a gateway router and the LAN device list."""
    if not gateway_ip:
        return None
    gw_mac = None
    rest = []
    for dev in devices:
        if dev.ip == gateway_ip:
            gw_mac = dev.mac
        else:
            rest.append(dev)
    name = vendor(gw_mac)
    gateway = Device(kind="router", label=name or "Router",
                     ip=gateway_ip, mac=gw_mac, vendor=name)
    return Segment(subnet=cidr or gateway_ip, gateway=gateway, devices=rest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: `14 passed`

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `56 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: parse neighbor caches and build LAN segment"
```

---

### Task 5: Local and tunnel interface address parsing

**Files:**
- Modify: `openvpn_manager/topology.py` (append)
- Modify: `tests/test_topology.py` (append tests)

**Interfaces:**
- Consumes: Task 1 module structure.
- Produces:
  - `local_ips(addr_text: str) -> list[str]` — IPv4 addresses on up physical interfaces (skips `lo`, `tun*`, `tap*`) from `ip -o -4 addr show`.
  - `tun_ips(addr_text: str) -> list[str]` — IPv4 addresses on `tun*`/`tap*` interfaces.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology.py`:

```python
ADDR = textwrap.dedent("""\
    1: lo    inet 127.0.0.1/8 scope host lo
    4: wlan0    inet 192.168.1.101/24 metric 600 brd 192.168.1.255 scope global dynamic wlan0
    15: tun0    inet 10.8.0.2/24 scope global tun0
""")


def test_local_ips_skips_lo_and_tunnels():
    assert topology.local_ips(ADDR) == ["192.168.1.101"]


def test_tun_ips():
    assert topology.tun_ips(ADDR) == ["10.8.0.2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'local_ips'`

- [ ] **Step 3: Write the implementation**

Append to `openvpn_manager/topology.py`:

```python
def _addr_entries(addr_text):
    """Yield (iface, ip) pairs from `ip -o -4 addr show` text."""
    iface = None
    for line in addr_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            iface = parts[1].rstrip(":")
        if iface is None:
            continue
        for i, part in enumerate(parts):
            if part == "inet" and i + 1 < len(parts):
                yield iface, parts[i + 1].split("/")[0]


def local_ips(addr_text):
    """IPv4 addresses on up physical interfaces (not loopback or tunnels)."""
    return [ip for iface, ip in _addr_entries(addr_text)
            if not iface.startswith(("tun", "tap", "lo"))]


def tun_ips(addr_text):
    """IPv4 addresses on tun/tap (tunnel) interfaces."""
    return [ip for iface, ip in _addr_entries(addr_text)
            if iface.startswith(("tun", "tap"))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: `16 passed`

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `58 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: parse local and tunnel interface addresses"
```

---

### Task 6: Connected tunnel detection

**Files:**
- Modify: `openvpn_manager/topology.py` (append)
- Modify: `tests/test_topology.py` (append tests)

**Interfaces:**
- Consumes: `Device` (Task 1), `vpn.is_active`, `vpn.parse_remote` from the existing `openvpn_manager/vpn.py`.
- Produces: `connected_tunnels(profiles, tunnel_ips=(), runner=vpn.run, client_dir=vpn.CLIENT_DIR) -> list[Device]` — one `Device(kind="tunnel")` per profile whose systemd unit is active. `ip` is the attributed tun interface address (best-effort with multiple tunnels); `detail` is the remote endpoint from the profile config. `runner` and `client_dir` are injectable.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology.py`:

```python
import subprocess


def test_connected_tunnels_active(tmp_path):
    (tmp_path / "work.conf").write_text("remote vpn.example.com 1194\nproto udp\n")
    (tmp_path / "home.conf").write_text("remote home.example.net 1194\n")

    def fake_runner(argv):
        unit = argv[-1]
        state = "active" if unit.endswith("@work") else "inactive"
        return subprocess.CompletedProcess(argv, 0, stdout=state + "\n", stderr="")

    tunnels = topology.connected_tunnels(
        ["home", "work"], tunnel_ips=["10.8.0.2"],
        runner=fake_runner, client_dir=str(tmp_path))
    assert len(tunnels) == 1
    assert tunnels[0].kind == "tunnel"
    assert tunnels[0].label == "work"
    assert tunnels[0].ip == "10.8.0.2"
    assert tunnels[0].detail == "vpn.example.com"


def test_connected_tunnels_none_active(tmp_path):
    def fake_runner(argv):
        return subprocess.CompletedProcess(argv, 0, stdout="inactive\n", stderr="")

    assert topology.connected_tunnels(["work"], runner=fake_runner,
                                      client_dir=str(tmp_path)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'connected_tunnels'`

- [ ] **Step 3: Write the implementation**

Append to `openvpn_manager/topology.py`:

```python
def connected_tunnels(profiles, tunnel_ips=(), runner=vpn.run,
                      client_dir=vpn.CLIENT_DIR):
    """Build a Device per connected OpenVPN profile.

    tunnel_ips are IPv4 addresses on tun/tap interfaces, attributed to active
    tunnels in order; this is best-effort when more than one tunnel is up.
    runner and client_dir are injectable for tests.
    """
    tunnels = []
    for profile in profiles:
        if vpn.is_active(profile, runner) != "active":
            continue
        remote, _proto = vpn.parse_remote(f"{client_dir}/{profile}.conf")
        ip = tunnel_ips[len(tunnels)] if len(tunnels) < len(tunnel_ips) else None
        tunnels.append(Device(kind="tunnel", label=profile, ip=ip,
                              detail=remote))
    return tunnels
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: `18 passed`

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `60 passed`

- [ ] **Step 6: Commit**

```bash
git add openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: detect connected OpenVPN tunnels"
```

---

### Task 7: Active ARP sweep — `helper.sh scan` and `sweep_argv`

**Files:**
- Modify: `openvpn_manager/helper.sh`
- Modify: `openvpn_manager/topology.py` (append)
- Modify: `tests/test_topology.py` (append test)

**Interfaces:**
- Consumes: `vpn.helper_path` from the existing `openvpn_manager/vpn.py`.
- Produces: `sweep_argv(cidr: str) -> list[str]` — `["pkexec", <helper.sh>, "scan", cidr]`. The helper populates the kernel ARP cache; the app re-reads `/proc/net/arp` afterwards (no helper output parsing needed).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology.py`:

```python
def test_sweep_argv():
    argv = topology.sweep_argv("192.168.1.0/24")
    assert argv[0] == "pkexec"
    assert argv[1].endswith("helper.sh")
    assert argv[2] == "scan"
    assert argv[3] == "192.168.1.0/24"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: FAIL with `AttributeError: module 'openvpn_manager.topology' has no attribute 'sweep_argv'`

- [ ] **Step 3: Write the implementation**

Append to `openvpn_manager/topology.py`:

```python
def sweep_argv(cidr):
    """Build the pkexec argv that runs the privileged ARP sweep."""
    return ["pkexec", vpn.helper_path(), "scan", cidr]
```

Then edit `openvpn_manager/helper.sh`. The file currently ends with a `case` statement whose last arm is `*)`. Insert the `scan)` arm before `*)`:

```bash
    scan)
        # Populate the kernel ARP cache for a /24 subnet. The app re-reads
        # /proc/net/arp afterwards, so this only needs every live host to
        # answer an ARP request. Prefers arping; falls back to a broadcast
        # ping when arping is not installed.
        cidr="${2:?cidr required (e.g. 192.168.1.0/24)}"
        base="${cidr%/*}"
        prefix="${cidr#*/}"
        if [ "$prefix" != "24" ]; then
            echo "unsupported prefix: $prefix (only /24 is swept)" >&2
            exit 3
        fi
        net="${base%.*}"
        if command -v arping >/dev/null 2>&1; then
            seq 1 254 | xargs -P 32 -I{} \
                sh -c "arping -c 1 -w 1 '$net.{}' >/dev/null 2>&1" || true
        else
            # Hosts that answer the broadcast ping refresh their ARP entry.
            ping -b -c 2 -W 1 "${net}.255" >/dev/null 2>&1 || true
        fi
        ;;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topology.py -q`
Expected: `19 passed`

- [ ] **Step 5: Verify the helper script syntax and sweep manually**

Run: `bash -n openvpn_manager/helper.sh`
Expected: no output, exit 0.

Then, from a normal-user terminal on the target network (this project's machine), run:

```bash
pkexec openvpn_manager/helper.sh scan 192.168.1.0/24
```

Expected: after polkit approval, the command returns (exit 0) and `cat /proc/net/arp` shows entries for live hosts on the LAN, including the gateway. The app will re-read this cache, so no output is printed by the helper.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `61 passed`

- [ ] **Step 7: Commit**

```bash
git add openvpn_manager/helper.sh openvpn_manager/topology.py tests/test_topology.py
git commit -m "feat: add privileged ARP sweep helper command"
```

---

### Task 8: Topology view widget

**Files:**
- Create: `openvpn_manager/topology_view.py`

**Interfaces:**
- Consumes: `Topology`, `Segment`, `Device` from `topology.py` (Tasks 1-7).
- Produces: `TopologyView(on_scan=None)` — a `Gtk.Box` with `set_topology(topo: Topology) -> None` that renders the model into a header row, a Network group (gateway + devices), and a VPN Tunnels group. `on_scan` is called when the "Scan network" button is clicked. Widgets are verified manually (project convention; no unit tests).

- [ ] **Step 1: Write the widget**

Create `openvpn_manager/topology_view.py`:

```python
"""Network topology view as a libadwaita tree of groups."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

_ICONS = {
    "router": "network-wired-symbolic",
    "device": "network-transmit-receive-symbolic",
    "tunnel": "network-vpn-symbolic",
}


class TopologyView(Gtk.Box):
    """Renders a Topology model: this machine, the LAN, and VPN tunnels."""

    def __init__(self, on_scan=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._on_scan = on_scan
        self._lan_children = []
        self._vpn_children = []

        self._host_label = Gtk.Label(halign=Gtk.Align.START, wrap=True)
        self._host_label.add_css_class("title-3")
        self.append(self._host_label)

        self._lan_group = Adw.PreferencesGroup()
        self._lan_group.set_title("Network")
        self.append(self._lan_group)

        self._vpn_group = Adw.PreferencesGroup()
        self._vpn_group.set_title("VPN Tunnels")
        self.append(self._vpn_group)

        if self._on_scan is not None:
            scan_btn = Gtk.Button(label="Scan network")
            scan_btn.connect("clicked", lambda *_: self._on_scan())
            self.append(scan_btn)

    def set_topology(self, topo):
        """Render a Topology model, replacing the previous contents."""
        self._host_label.set_text(self._host_text(topo))
        self._lan_group.set_title(
            f"Network · {topo.lan.subnet}" if topo.lan else "Network")
        self._render_lan(topo)
        self._render_vpn(topo)

    @staticmethod
    def _host_text(topo):
        ips = ", ".join(topo.local_ips)
        return f"{topo.hostname} · {ips}" if ips else topo.hostname

    def _lan_rows(self, topo):
        if topo.lan is None:
            return [self._hint("No LAN detected")]
        rows = [self._device_row(topo.lan.gateway, "router")]
        rows.extend(self._device_row(dev, "device") for dev in topo.lan.devices)
        return rows

    def _vpn_rows(self, topo):
        if not topo.tunnels:
            return [self._hint("No VPN tunnel connected")]
        return [self._device_row(t, "tunnel") for t in topo.tunnels]

    @staticmethod
    def _device_row(dev, kind):
        row = Adw.ActionRow(title=dev.label)
        row.set_subtitle(" · ".join(filter(None, [dev.ip, dev.mac, dev.detail])))
        icon = Gtk.Image(icon_name=_ICONS.get(
            kind, "network-transmit-receive-symbolic"))
        icon.add_css_class("dim-label")
        row.add_prefix(icon)
        if kind == "tunnel":
            dot = Gtk.Label(label="●")
            dot.add_css_class("success")
            row.add_suffix(dot)
        return row

    @staticmethod
    def _hint(text):
        label = Gtk.Label(label=text, halign=Gtk.Align.START)
        label.add_css_class("dim-label")
        return label

    def _render_lan(self, topo):
        for child in self._lan_children:
            self._lan_group.remove(child)
        self._lan_children = self._lan_rows(topo)
        for child in self._lan_children:
            self._lan_group.add(child)

    def _render_vpn(self, topo):
        for child in self._vpn_children:
            self._vpn_group.remove(child)
        self._vpn_children = self._vpn_rows(topo)
        for child in self._vpn_children:
            self._vpn_group.add(child)
```

- [ ] **Step 2: Verify the module imports**

Run: `.venv/bin/python -c "from openvpn_manager.topology_view import TopologyView; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `61 passed` (unchanged — GTK widgets are not unit-tested by convention)

- [ ] **Step 4: Commit**

```bash
git add openvpn_manager/topology_view.py
git commit -m "feat: add topology view widget"
```

---

### Task 9: Wire the topology view into the app window

**Files:**
- Modify: `openvpn_manager/app.py`

**Interfaces:**
- Consumes: `topology.build_topology`, `topology.gateway_and_subnet`, `topology.local_ips`, `topology.neighbors`, `topology.build_segment`, `topology.tun_ips`, `topology.connected_tunnels`, `topology.sweep_argv` (Tasks 1-7); `TopologyView` (Task 8); existing `vpn.discover_profiles` output via `self._profiles`.

- [ ] **Step 1: Add imports and window state**

In `openvpn_manager/app.py`, replace the top stdlib imports (lines 3-5):

```python
import getpass
import os
import socket
import subprocess
import time
```

Replace the project imports block:

```python
from . import palette
from . import topology
from . import vpn
from .format import human_bytes, human_duration, human_speed
from .palette import load_palette, palette_to_css, sparkline_colors
from .sparkline import Sparkline
from .stats import Sampler, detect_iface
from .topology_view import TopologyView
```

In `Window.__init__`, right after `self._iface = None`, add:

```python
        self._topology_sweeping = False
        self._topology_last_sweep = None
        self._cidr = None
```

- [ ] **Step 2: Add the view stack and switcher**

In `Window.__init__`, replace the block that attaches `box` to the toast and the toolbar. The current code is:

```python
        self._toast.set_child(box)
        toolbar.set_content(self._toast)
        self.set_content(toolbar)
```

Replace it with:

```python
        self._stack = Adw.ViewStack()
        self._stack.add_titled(box, "status", "Status")

        self._topo_view = TopologyView(on_scan=self._on_scan_requested)
        topo_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            margin_top=12, margin_bottom=12,
                            margin_start=12, margin_end=12)
        topo_page.append(self._topo_view)
        self._stack.add_titled(topo_page, "topology", "Topology")

        self._toast.set_child(self._stack)
        toolbar.set_content(self._toast)
        self.set_content(toolbar)

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        header.set_title_widget(switcher)
```

- [ ] **Step 3: Add the topology refresh methods**

In the `Window` class, after `_iface_ip` (the last method before the class ends), add:

```python
    def _maybe_refresh_topology(self):
        if self._stack.get_visible_child_name() != "topology":
            return
        self._refresh_topology()
        now = time.monotonic()
        if (not self._topology_sweeping and
                (self._topology_last_sweep is None or
                 now - self._topology_last_sweep >= 60)):
            self._on_scan_requested()

    def _on_scan_requested(self):
        if self._topology_sweeping or self._cidr is None:
            return
        self._topology_sweeping = True
        self._spawn_quiet(topology.sweep_argv(self._cidr),
                          on_done=self._on_sweep_done)

    def _on_sweep_done(self):
        self._topology_sweeping = False
        self._topology_last_sweep = time.monotonic()
        self._refresh_topology()

    def _spawn_quiet(self, argv, on_done=None):
        try:
            proc = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_DEVNULL |
                Gio.SubprocessFlags.STDERR_DEVNULL)
        except GLib.Error:
            if on_done:
                on_done()
            return

        def done(p, res):
            try:
                p.wait_finish(res)
            except GLib.Error:
                pass
            if on_done:
                on_done()

        proc.wait_async(None, done)

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
        tunnels = topology.connected_tunnels(
            self._profiles, topology.tun_ips(addr_text))
        hostname = socket.gethostname() or "This machine"
        self._topo_view.set_topology(
            topology.build_topology(hostname, own, lan, tunnels))

    @staticmethod
    def _read_proc(path):
        try:
            with open(path) as fh:
                return fh.read()
        except OSError:
            return ""

    @staticmethod
    def _run_ip(*argv):
        try:
            cp = subprocess.run(argv, capture_output=True, text=True)
            return cp.stdout or ""
        except OSError:
            return ""
```

- [ ] **Step 4: Drive the refresh from the existing tick**

In `_tick`, change the final `return True` to:

```python
        self._maybe_refresh_topology()
        return True
```

- [ ] **Step 5: Verify the module imports and the suite**

Run: `.venv/bin/python -c "from openvpn_manager.app import Window; print('ok')"`
Expected: `ok`

Run: `.venv/bin/python -m pytest -q`
Expected: `61 passed`

- [ ] **Step 6: Manual verification**

Run: `.venv/bin/python -m openvpn_manager`
Expected:
- The header shows a "Status | Topology" switcher; Status still shows the existing dashboard, status pill, and menu.
- Switching to Topology shows a header row with the hostname and local IP (e.g. `hostname · 192.168.1.101`), a "Network · 192.168.1.0/24" group with the gateway router row (vendor label or "Router", IP, MAC) and device rows (vendor/IP/MAC), and a "VPN Tunnels" group.
- Within about a second of opening Topology, the first sweep runs (polkit prompt appears once); after approving, idle LAN hosts that answered `arping` appear. Devices unknown to the bundled OUI map show as "Device".
- With an OpenVPN profile connected, a tunnel row appears under VPN Tunnels with a success-colored dot, the remote endpoint, and the tun interface IP.
- A "Scan network" button on the view triggers a fresh sweep.
- Error cases do not crash: no default gateway, no profiles, or an empty client dir still render the view.

- [ ] **Step 7: Commit**

```bash
git add openvpn_manager/app.py
git commit -m "feat: add network topology view to the app window"
```
