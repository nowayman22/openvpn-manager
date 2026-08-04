"""Network topology discovery. No GTK imports.

All system reads go through injectable readers so this module is testable
without touching the real network.
"""

import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

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


_TUNNEL_IFACE_PREFIXES = ("tun", "tap", "ppp", "wg", "vpn", "ipsec",
                          "gretap", "erspan")


def _is_tunnel_iface(iface):
    return iface.startswith(_TUNNEL_IFACE_PREFIXES)


def gateway_and_subnet(route_text):
    """Return (gateway_ip, cidr) from /proc/net/route, or (None, None).

    Address fields are hex little-endian: 0101A8C0 is 192.168.1.1. The
    gateway is the lowest-metric default route on a physical interface,
    falling back to any default route (so a VPN that overrides the default
    route does not hide the LAN gateway). The subnet is the connected route
    on that interface with the largest prefix below /32: a /32 host route to
    the gateway is common and is not the LAN subnet.
    """
    defaults = []
    connected = {}
    for line in route_text.splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[0] == "Iface":
            continue
        iface, dest, gw, _flags, _ref, _use, metric_hex, mask = parts[:8]
        if dest == "00000000" and gw != "00000000":
            try:
                metric = int(metric_hex, 16)
            except ValueError:
                metric = 1 << 30
            defaults.append((metric, iface, gw, _is_tunnel_iface(iface)))
        elif gw == "00000000" and dest != "00000000":
            prefix = _mask_to_prefix(mask)
            net = _hex_ip(dest)
            if net is not None and prefix < 32:
                cur = connected.get(iface)
                if cur is None or prefix > cur[1]:
                    connected[iface] = (net, prefix)
    if not defaults:
        return None, None
    physical = [d for d in defaults if not d[3]]
    _metric, iface, gw, _tunnel = min(physical or defaults, key=lambda d: d[0])
    info = connected.get(iface)
    cidr = f"{info[0]}/{info[1]}" if info else None
    return _hex_ip(gw), cidr


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


def sweep_argv(cidr):
    """Build the pkexec argv that runs the privileged ARP sweep."""
    return ["pkexec", vpn.helper_path(), "scan", cidr]


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
    for t in auto_tunnels:
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


@dataclass
class NodeBox:
    """A device's bounding box in the layout."""
    device_id: str
    x: float
    y: float
    w: float
    h: float


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


def compute_layout(topo, col_width=160, node_pad=12, level_pad=80):
    """Compute bounding boxes for every device in a left-to-right tree layout.

    Devices are positioned by tree depth (column): root at x=0, children at
    x = parent.x + col_width + level_pad. Same-depth siblings are stacked
    vertically. The parent is vertically centered across its children.

    Returns a list of NodeBox, one per device in topo.devices.
    """
    children = {}
    for d in topo.devices:
        pid = d.parent_id or "pc"
        if pid == d.id:
            continue
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
