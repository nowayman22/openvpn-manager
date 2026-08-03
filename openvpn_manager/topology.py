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
