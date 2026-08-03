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
